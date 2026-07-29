"""FastAPI server for the YNAB Dashboard. Serves the API and the built React frontend."""
# ruff: noqa: E402  (load_env() must run before router imports so env is populated at import time)

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.security import HTTPBasic, HTTPBasicCredentials
    from fastapi.staticfiles import StaticFiles
except ImportError:
    import sys

    print(
        "Error: dashboard dependencies are not installed.\n"
        "Reinstall with: uv tool install 'ynab-tools[dashboard]'\n"
        "Or if running from source: uv tool install --editable '.[dashboard]'",
        file=sys.stderr,
    )
    sys.exit(1)

from ynab_tools.config import load_env

logger = logging.getLogger(__name__)

# Module-level slot so the interval is accessible at startup and to sync_status
_sync_interval_minutes: int = 0
# Written by the background thread (asyncio.to_thread / ThreadPoolExecutor),
# read by the event-loop thread in /api/sync-status. Safe under CPython's GIL
# (single object reference write is atomic), but would need a lock under
# free-threaded builds.
_last_auto_sync_at: str | None = None

_http_basic = HTTPBasic(auto_error=False)


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_http_basic)) -> None:
    password = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    if not password:
        return
    if credentials is None or not secrets.compare_digest(credentials.password.encode(), password.encode()):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


_LOOPBACK_ALIASES = {"localhost", ""}


def is_loopback_host(host: str) -> bool:
    """True when `host` binds the loopback interface only.

    Anything that is not provably loopback (a hostname we cannot resolve
    here, a wildcard like 0.0.0.0 or ::) is treated as non-loopback, so an
    unrecognized value fails safe rather than silently skipping the
    password requirement below.
    """
    h = (host or "").strip().strip("[]").lower()
    if h in _LOOPBACK_ALIASES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def require_password_for_remote_bind(host: str) -> None:
    """Refuse to serve the dashboard off-machine without a password.

    Every route returns budget data - balances, income, payees, net worth -
    and `_require_auth` is a no-op when DASHBOARD_PASSWORD is unset. On
    loopback that is fine, but binding 0.0.0.0 with no password publishes a
    complete financial history to anyone on the network. Previously that
    combination only printed a warning, which is easy to miss in LaunchAgent
    logs, so it now fails closed: bind off-loopback and you must set a
    password. Call after load_env() so config.json's dashboard_password is
    visible.
    """
    if is_loopback_host(host) or os.environ.get("DASHBOARD_PASSWORD", "").strip():
        return
    raise SystemExit(
        f"Refusing to bind {host} without authentication.\n"
        "The dashboard serves your full budget data and has no password set.\n"
        "Set DASHBOARD_PASSWORD (or the dashboard_password key in "
        "~/.config/ynab-tools/config.json), or bind 127.0.0.1 instead.\n"
        "See docs/dashboard.md."
    )


load_env()

from .api.account_health import router as account_health_router
from .api.actions import router as actions_router
from .api.admin import router as admin_router
from .api.audit import router as audit_router
from .api.budget_fit import router as budget_fit_router
from .api.calibration import router as calibration_router
from .api.categories_list import router as categories_router
from .api.churn import router as churn_router
from .api.custom_report import router as custom_report_router
from .api.debt_trend import router as debt_trend_router
from .api.fund import router as fund_router
from .api.group_trend import group_trend_router
from .api.health_ratios import router as health_ratios_router
from .api.home_spending import router as home_spending_router
from .api.income import router as income_router
from .api.month_end import router as month_end_router
from .api.months import router as months_router
from .api.needs_attention import router as needs_attention_router
from .api.net_worth import router as net_worth_router
from .api.net_worth_trend import router as net_worth_trend_router
from .api.overspend import router as overspend_router
from .api.overspend_plan import router as overspend_plan_router
from .api.overview import router as overview_router
from .api.overview_bundle import router as overview_bundle_router
from .api.paycheck_funding_api import router as paycheck_funding_router
from .api.planned_actions import router as planned_actions_router
from .api.primary_action import router as primary_action_router
from .api.retirement import router as retirement_router
from .api.savings_progress import router as savings_progress_router
from .api.sinking_funds import router as sinking_funds_router
from .api.spending_pace import router as spending_pace_router
from .api.subscriptions import router as subscriptions_router
from .api.summary import router as summary_router
from .api.sync_status import router as sync_status_router
from .api.trends import router as trends_router
from .api.two_pot import router as two_pot_router
from .api.unapproved import router as unapproved_router
from .api.upcoming import router as upcoming_router
from .api.variance import router as variance_router
from .api.version import router as version_router

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


def _take_net_worth_snapshot() -> None:
    """Open a DB connection, take a net worth snapshot, and close. Called via asyncio.to_thread."""
    from ynab_tools.db import get_connection
    from ynab_tools.reports.net_worth import take_snapshot

    conn = get_connection()
    try:
        take_snapshot(conn)
        conn.commit()
    finally:
        conn.close()


async def _auto_sync_loop(interval_minutes: int) -> None:
    """Run run_sync() in a thread every interval_minutes. Errors are logged, not raised."""
    from ynab_tools.sync import run_sync

    global _last_auto_sync_at
    _uvlog = logging.getLogger("uvicorn")
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await asyncio.to_thread(run_sync, months=12, full=False)
            _last_auto_sync_at = datetime.now(UTC).isoformat()
            _uvlog.info("Auto-sync completed")
        except Exception as exc:
            _uvlog.warning("Auto-sync failed: %s", exc)
            continue
        try:
            await asyncio.to_thread(_take_net_worth_snapshot)
            _uvlog.info("Net worth snapshot taken")
        except Exception as exc:
            _uvlog.warning("Net worth snapshot failed: %s", exc)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if _sync_interval_minutes > 0:
        task = asyncio.create_task(_auto_sync_loop(_sync_interval_minutes))
        # Use uvicorn's logger so the message appears in server output
        logging.getLogger("uvicorn").info("Auto-sync enabled: every %d minutes", _sync_interval_minutes)
    yield
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="YNAB Dashboard",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
    dependencies=[Depends(_require_auth)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=["*"],
)

app.include_router(account_health_router, prefix="/api")
app.include_router(overview_bundle_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(budget_fit_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(churn_router, prefix="/api")
app.include_router(calibration_router, prefix="/api")
app.include_router(categories_router, prefix="/api")
app.include_router(custom_report_router, prefix="/api")
app.include_router(debt_trend_router, prefix="/api")
app.include_router(fund_router, prefix="/api")
app.include_router(health_ratios_router, prefix="/api")
app.include_router(home_spending_router, prefix="/api")
app.include_router(income_router, prefix="/api")
app.include_router(paycheck_funding_router, prefix="/api")
app.include_router(planned_actions_router, prefix="/api")
app.include_router(upcoming_router, prefix="/api")
app.include_router(month_end_router, prefix="/api")
app.include_router(months_router, prefix="/api")
app.include_router(needs_attention_router, prefix="/api")
app.include_router(net_worth_router, prefix="/api")
app.include_router(net_worth_trend_router, prefix="/api")
app.include_router(overview_router, prefix="/api")
app.include_router(primary_action_router, prefix="/api")
app.include_router(retirement_router, prefix="/api")
app.include_router(savings_progress_router, prefix="/api")
app.include_router(sinking_funds_router, prefix="/api")
app.include_router(spending_pace_router, prefix="/api")
app.include_router(subscriptions_router, prefix="/api")
app.include_router(summary_router, prefix="/api")
app.include_router(sync_status_router, prefix="/api")
app.include_router(trends_router, prefix="/api")
app.include_router(two_pot_router, prefix="/api")
app.include_router(unapproved_router, prefix="/api")
app.include_router(variance_router, prefix="/api")
app.include_router(overspend_router, prefix="/api")
app.include_router(overspend_plan_router, prefix="/api")
app.include_router(group_trend_router, prefix="/api")
app.include_router(version_router, prefix="/api")

# Mount built Vite assets when the frontend has been built
_assets_dir = FRONTEND_DIST / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")


@app.get("/", response_model=None, include_in_schema=False)
@app.get("/{full_path:path}", response_model=None, include_in_schema=False)
async def spa_fallback(full_path: str = ""):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {
        "message": "YNAB Dashboard API is running.",
        "note": "Frontend not built yet. Run: cd ynab_tools/dashboard/frontend && npm run build",
        "api_docs": "/api/docs",
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="ynab-dashboard", description="Start the YNAB local web dashboard")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    p.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    p.add_argument(
        "--sync-interval",
        type=int,
        default=None,
        metavar="MINUTES",
        help=(
            "Auto-sync interval in minutes (0 = disabled, default: 0). "
            "Also configurable via config.json key dashboard_sync_interval_minutes "
            "or YNAB_DASHBOARD_SYNC_INTERVAL env var."
        ),
    )
    args = p.parse_args()

    # Resolution order: config.json < env var < explicit CLI flag.
    # load_env() populates YNAB_DASHBOARD_SYNC_INTERVAL from config.json via
    # setdefault, so existing env vars always win over config.json.
    from ynab_tools.config import load_env

    load_env()
    global _sync_interval_minutes
    try:
        env_interval = int(os.environ.get("YNAB_DASHBOARD_SYNC_INTERVAL", "0"))
    except ValueError:
        env_interval = 0
    # CLI flag (not None) takes precedence over env/config
    interval = args.sync_interval if args.sync_interval is not None else env_interval
    _sync_interval_minutes = max(0, interval)

    require_password_for_remote_bind(args.host)
    if not os.environ.get("DASHBOARD_PASSWORD", "").strip():
        print(
            "Warning: dashboard is running without authentication. "
            "Set DASHBOARD_PASSWORD to require a password. "
            "See docs/dashboard.md."
        )

    uvicorn.run(
        "ynab_tools.dashboard.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )

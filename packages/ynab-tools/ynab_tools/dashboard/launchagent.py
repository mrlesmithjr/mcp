"""macOS LaunchAgent management for the ynab-dashboard service."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ynab_tools.config import DB_PATH

PLIST_LABEL = "com.ynab-tools.dashboard"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"

_LEGACY_LABEL = "com.mrlesmithjr.ynab-dashboard"
_LEGACY_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LEGACY_LABEL}.plist"
LOG_PATH = DB_PATH.parent / "dashboard.log"
_GUI_TARGET = f"gui/{os.getuid()}"
_SERVICE_TARGET = f"{_GUI_TARGET}/{PLIST_LABEL}"


def _find_binary() -> Path | None:
    found = shutil.which("ynab-dashboard")
    return Path(found) if found else None


def _is_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
    )
    return result.returncode == 0


def _write_plist(binary: Path, host: str, port: int, sync_interval: int, reload_flag: bool) -> None:
    args: list[str] = [str(binary), "--host", host, "--port", str(port)]
    if sync_interval > 0:
        args += ["--sync-interval", str(sync_interval)]
    if reload_flag:
        args.append("--reload")

    path_dirs = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]

    plist: dict = {
        "Label": PLIST_LABEL,
        "ProgramArguments": args,
        "EnvironmentVariables": {"PATH": ":".join(path_dirs)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    PLIST_PATH.chmod(0o644)


def _remove_legacy() -> None:
    legacy_target = f"gui/{os.getuid()}/{_LEGACY_LABEL}"
    subprocess.run(["launchctl", "bootout", legacy_target], capture_output=True)
    if _LEGACY_PLIST_PATH.exists():
        _LEGACY_PLIST_PATH.unlink()
        print(f"Removed legacy plist {_LEGACY_PLIST_PATH}")


def _require_darwin(cmd: str) -> None:
    if sys.platform != "darwin":
        print(f"ynab dashboard {cmd} is currently macOS-only (uses LaunchAgent).")
        print("On Linux, start the dashboard directly: ynab dashboard start")
        print("A systemd service unit is planned -- see GitHub issue #102.")
        sys.exit(1)


def _check_dashboard_deps() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        print(f"Error: dashboard dependency missing: {e}")
        print("Reinstall with: uv tool install 'ynab-tools[dashboard]'")
        print("Or from source: uv tool install --editable '.[dashboard]'")
        sys.exit(1)


def cmd_install(host: str, port: int, sync_interval: int, reload_flag: bool) -> None:
    _require_darwin("install")

    _check_dashboard_deps()

    # Refuse before writing the plist: a LaunchAgent restarts on every login,
    # so an unauthenticated off-loopback bind installed here would keep
    # re-publishing budget data with only a log-file warning to show for it.
    from ynab_tools.config import load_env

    from .server import require_password_for_remote_bind

    load_env()
    require_password_for_remote_bind(host)

    binary = _find_binary()
    if binary is None:
        print("Error: ynab-dashboard binary not found.")
        print("Install with: uv tool install 'ynab-tools[dashboard]'")
        sys.exit(1)

    _remove_legacy()
    _write_plist(binary, host, port, sync_interval, reload_flag)

    if _is_loaded():
        subprocess.run(["launchctl", "bootout", _SERVICE_TARGET], capture_output=True)

    result = subprocess.run(
        ["launchctl", "bootstrap", _GUI_TARGET, str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error loading LaunchAgent: {result.stderr.strip()}")
        sys.exit(1)

    print(f"Dashboard installed and running at http://{host}:{port}")
    if sync_interval > 0:
        print(f"Auto-sync: every {sync_interval} minutes")
    print(f"Logs: {LOG_PATH}")
    print(f"Plist: {PLIST_PATH}")

    from ynab_tools.config import load_env

    load_env()
    if not os.environ.get("DASHBOARD_PASSWORD", "").strip():
        print(
            "Warning: dashboard is running without authentication. "
            "Set DASHBOARD_PASSWORD to require a password. "
            "See docs/dashboard.md."
        )


def cmd_uninstall() -> None:
    _require_darwin("uninstall")
    if _is_loaded():
        subprocess.run(["launchctl", "bootout", _SERVICE_TARGET], capture_output=True)
        print("LaunchAgent stopped.")

    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        print(f"Removed {PLIST_PATH}")
    else:
        print("No plist found -- nothing to remove.")

    _remove_legacy()


def cmd_restart() -> None:
    _require_darwin("restart")
    if not _is_loaded() and not PLIST_PATH.exists():
        print("Dashboard is not installed. Run: ynab dashboard install")
        sys.exit(1)

    subprocess.run(["launchctl", "bootout", _SERVICE_TARGET], capture_output=True)
    # Give launchd a moment to release the port before re-registering
    time.sleep(2)

    result = subprocess.run(
        ["launchctl", "bootstrap", _GUI_TARGET, str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error restarting LaunchAgent: {result.stderr.strip()}")
        sys.exit(1)

    print("Dashboard restarted.")


def cmd_status() -> None:
    _require_darwin("status")
    running = _is_loaded()
    print(f"Status: {'running' if running else 'stopped'}")

    if PLIST_PATH.exists():
        try:
            with open(PLIST_PATH, "rb") as f:
                plist = plistlib.load(f)
            prog_args = plist.get("ProgramArguments", [])
            host = "127.0.0.1"
            port = 8000
            for i, arg in enumerate(prog_args):
                if arg == "--host" and i + 1 < len(prog_args):
                    host = prog_args[i + 1]
                if arg == "--port" and i + 1 < len(prog_args):
                    port = prog_args[i + 1]
            print(f"URL: http://{host}:{port}")
        except Exception:
            pass
        print(f"Plist: {PLIST_PATH}")
    else:
        print("Plist: not installed")

    print(f"Logs: {LOG_PATH}")

    from ynab_tools.config import load_env

    load_env()
    if not os.environ.get("DASHBOARD_PASSWORD", "").strip():
        print(
            "Warning: dashboard is running without authentication. "
            "Set DASHBOARD_PASSWORD to require a password. "
            "See docs/dashboard.md."
        )

    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        if lines:
            print("\nRecent log:")
            for line in lines[-10:]:
                print(f"  {line}")


def cmd_logs(n: int = 50) -> None:
    _require_darwin("logs")
    if not LOG_PATH.exists():
        print(f"No log file yet at {LOG_PATH}")
        return
    lines = LOG_PATH.read_text(errors="replace").splitlines()
    for line in lines[-n:]:
        print(line)

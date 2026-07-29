"""POST /api/sync - trigger a delta sync against the YNAB API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from ._common import get_credentials

router = APIRouter(tags=["actions"])


@router.post("/sync")
async def trigger_sync() -> dict[str, Any]:
    """Trigger a delta sync against the YNAB API."""
    get_credentials()  # validate credentials before starting sync
    try:
        from ynab_tools.sync import run_sync

        await asyncio.to_thread(run_sync, months=12, full=False)
        return {"ok": True, "synced_at": datetime.now(UTC).isoformat()}
    except SystemExit:
        raise HTTPException(
            status_code=503,
            detail="YNAB credentials not configured. Run 'ynab configure' first.",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Sync failed: {exc}")

import hashlib
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@router.get("/version")
async def get_version() -> dict[str, str]:
    index = _FRONTEND_DIST / "index.html"
    if not index.exists():
        return {"build_id": "dev"}
    content = index.read_bytes()
    return {"build_id": hashlib.sha1(content).hexdigest()[:8]}

from pathlib import Path

from fastapi import APIRouter
from starlette.responses import FileResponse

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@router.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    # DECISION: GET / serves static/index.html via FileResponse with path relative to package.
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

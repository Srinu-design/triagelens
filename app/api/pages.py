from pathlib import Path

from fastapi import APIRouter
from starlette.responses import FileResponse

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


# HEAD too, so uptime checks and link previews get 200 rather than 405
@router.api_route("/", methods=["GET", "HEAD"], response_class=FileResponse)
async def index() -> FileResponse:
    # DECISION: GET / serves static/index.html via FileResponse with path relative to package.
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

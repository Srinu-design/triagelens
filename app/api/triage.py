from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.errors import ApiError
from app.schemas import PreviewIn
from app.services.triage import preview
from app.session import current_session_id

router = APIRouter()


@router.post("/api/triage/preview")
async def preview_endpoint(
    request: Request,
    body: PreviewIn,
    session_id: Annotated[UUID, Depends(current_session_id)],
):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(
            422, "VALIDATION_ERROR", "Invalid Content-Type", "Content-Type must be application/json"
        )
    return await preview(request.app.state, session_id, body)

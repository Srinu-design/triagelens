from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.errors import ApiError
from app.limits import check_llm_limit, limit_preview
from app.schemas import PreviewIn
from app.services.triage import preview
from app.session import current_session_id

router = APIRouter()


@router.post("/api/triage/preview", dependencies=[Depends(limit_preview)])
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
    allow_llm = await check_llm_limit(request)
    res = await preview(request.app.state, session_id, body, allow_llm=allow_llm)
    telemetry = res.get("telemetry", {})
    request.state.embed_ms = telemetry.get("embed_ms")
    request.state.db_ms = telemetry.get("db_ms")
    decision = res.get("decision", {})
    request.state.verdict = decision.get("verdict")
    suggestion = res.get("suggestion", {})
    request.state.classifier_source = suggestion.get("source")
    scrubbed = res.get("scrubbed", {})
    request.state.redaction_total = scrubbed.get("total")
    return res

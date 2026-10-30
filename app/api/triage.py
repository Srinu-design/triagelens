from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.limits import check_llm_limit, rate_limit
from app.schemas import PreviewIn, require_json
from app.services.triage import preview
from app.session import current_session_id

router = APIRouter()


@router.post("/api/triage/preview", dependencies=[Depends(rate_limit("preview"))])
async def preview_endpoint(
    request: Request,
    body: PreviewIn,
    session_id: Annotated[UUID, Depends(current_session_id)],
):
    require_json(request)
    allow_llm = await check_llm_limit(request)
    res = await preview(request.app.state, session_id, body, allow_llm=allow_llm)
    # fields for the one access-log line (RequestLogMiddleware)
    request.state.embed_ms = res["telemetry"]["embed_ms"]
    request.state.db_ms = res["telemetry"]["db_ms"]
    request.state.verdict = res["decision"]["verdict"]
    request.state.classifier_source = res["suggestion"]["source"]
    request.state.redaction_total = res["scrubbed"]["total"]
    return res

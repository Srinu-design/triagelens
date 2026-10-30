from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.limits import limit_search
from app.services.search import CategoryFilter, search
from app.session import current_session_id

router = APIRouter()


@router.get("/api/search", dependencies=[Depends(limit_search)])
async def search_endpoint(
    request: Request,
    session_id: Annotated[UUID, Depends(current_session_id)],
    # DECISION: Search and ANN query text is bounded to 1–500 characters.
    q: Annotated[str, Query(min_length=1, max_length=500)],
    category: CategoryFilter = "",
    explain: Annotated[int, Query(ge=0, le=1)] = 0,
):
    res = await search(
        request.app.state, q=q, sid=session_id, category=category or None, explain=bool(explain)
    )
    telemetry = res.get("telemetry", {})
    request.state.embed_ms = telemetry.get("embed_ms")
    request.state.db_ms = telemetry.get("db_ms")
    return res

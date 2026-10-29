from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.services.search import CategoryFilter, search
from app.session import current_session_id

router = APIRouter()


@router.get("/api/search")
async def search_endpoint(
    request: Request,
    session_id: Annotated[UUID, Depends(current_session_id)],
    # DECISION: Search and ANN query text is bounded to 1–500 characters.
    q: Annotated[str, Query(min_length=1, max_length=500)],
    category: CategoryFilter = "",
    explain: Annotated[int, Query(ge=0, le=1)] = 0,
):
    return await search(
        request.app.state, q=q, sid=session_id, category=category or None, explain=bool(explain)
    )

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.errors import ApiError
from app.limits import limit_ann_lab
from app.services.search import CategoryFilter, ann_lab
from app.session import current_session_id

router = APIRouter()


@router.get("/api/ann-lab", dependencies=[Depends(limit_ann_lab)])
async def ann_lab_endpoint(
    request: Request,
    session_id: Annotated[UUID, Depends(current_session_id)],
    q: Annotated[str, Query(min_length=1, max_length=500)],
    category: CategoryFilter = "",
    force: Annotated[int, Query(ge=0, le=1)] = 1,
    ef_search: int = 5,
    iterative: Literal["off", "strict_order", "relaxed_order"] = "off",
):
    if ef_search not in {5, 10, 40}:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid request", "ef_search must be 5, 10 or 40.")
    return await ann_lab(
        request.app.state,
        q=q,
        sid=session_id,
        category=category or None,
        force=force,
        ef_search=ef_search,
        iterative=iterative,
    )

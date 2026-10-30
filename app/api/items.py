import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.limits import rate_limit
from app.schemas import ItemIn, ItemOut, require_json
from app.services.items import commit_item, get_item
from app.session import current_session_id

router = APIRouter(prefix="/api/items", tags=["items"])

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@router.post("", status_code=201, dependencies=[Depends(rate_limit("commit"))])
async def create_item(
    request: Request,
    item: ItemIn,
    session_id: Annotated[UUID, Depends(current_session_id)],
) -> JSONResponse:
    require_json(request)
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key or not IDEMPOTENCY_KEY_RE.match(idempotency_key):
        raise ApiError(
            status=400,
            code="IDEMPOTENCY_KEY_MISSING",
            title="Idempotency-Key Missing",
            detail="Idempotency-Key header must be 16–64 characters of [A-Za-z0-9_-]",
        )

    response_data, is_replay = await commit_item(
        engine=request.app.state.engine,
        session_id=session_id,
        idempotency_key=idempotency_key,
        item=item,
        embedder=request.app.state.embedder,
    )

    if is_replay:
        return JSONResponse(
            status_code=200,
            content=response_data,
            headers={"Idempotent-Replayed": "true"},
        )
    return JSONResponse(status_code=201, content=response_data)


@router.get("/{code}", response_model=ItemOut)
async def read_item(
    request: Request,
    code: str,
    session_id: Annotated[UUID, Depends(current_session_id)],
) -> dict:
    return await get_item(
        engine=request.app.state.engine,
        session_id=session_id,
        code=code,
    )

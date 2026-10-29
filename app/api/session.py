from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.schemas import SessionResetOut
from app.services.items import reset_session
from app.session import current_session_id

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("/reset", response_model=SessionResetOut)
async def reset(
    request: Request,
    session_id: Annotated[UUID, Depends(current_session_id)],
) -> dict[str, int]:
    return await reset_session(
        engine=request.app.state.engine,
        session_id=session_id,
    )

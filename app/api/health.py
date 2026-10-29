from fastapi import APIRouter, Request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.db import pgvector_supported, pgvector_version
from app.errors import make_problem_response

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    state = request.app.state
    version = None
    db = "ok"
    try:
        version = await pgvector_version(state.engine)
    except (SQLAlchemyError, OSError):
        db = "degraded"
    redis = "ok"
    try:
        await state.redis.ping()
    except (RedisError, OSError):
        redis = "degraded"
    body = {
        "db": db,
        "pgvector": version,
        "model": state.embedder.model_name,
        "model_ready": state.model_ready,
        "redis": redis,
    }
    if db != "ok" or not pgvector_supported(version) or not state.model_ready:
        return make_problem_response(
            503, "NOT_READY", "Not ready", "Database or embedding model is not ready.", **body
        )
    return body

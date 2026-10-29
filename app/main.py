import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.health import router as health_router
from app.api.items import router as items_router
from app.api.session import router as session_router
from app.config import Settings
from app.db import create_engine, pgvector_supported, pgvector_version
from app.errors import register_error_handlers
from app.services.embeddings import make_embedder
from app.session import SessionMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings, null_pool=settings.app_env == "test")
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        app.state.engine = engine
        app.state.redis = redis
        app.state.model_ready = False
        warmup = None
        try:
            version = await pgvector_version(engine)
            if not pgvector_supported(version):
                raise RuntimeError(f"pgvector >= 0.8.0 is required; found {version}")
            app.state.embedder = make_embedder(settings)

            async def warm_model():
                try:
                    await app.state.embedder.embed("TriageLens warm-up")
                except Exception:
                    # DECISION: Failed warm-up keeps liveness available and readiness false.
                    logger.error("Embedding model warm-up failed")
                else:
                    app.state.model_ready = True

            warmup = asyncio.create_task(warm_model())
            app.state.warmup_task = warmup
            yield
        finally:
            if warmup is not None:
                warmup.cancel()
                with suppress(asyncio.CancelledError):
                    await warmup
            app.state.model_ready = False
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(SessionMiddleware)
    app.include_router(health_router)
    app.include_router(items_router)
    app.include_router(session_router)
    return app


app = create_app()

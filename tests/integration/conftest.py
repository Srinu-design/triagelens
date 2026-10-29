import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import create_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed_load import seed_load  # noqa: E402
from app.services.embeddings import FastEmbedder  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    database_url = os.environ["TEST_DATABASE_URL"]
    if make_url(database_url).database != "triagelens_test":
        raise RuntimeError("Integration tests require database triagelens_test")
    return Settings(
        database_url=database_url,
        redis_url=os.environ["TEST_REDIS_URL"],
        model_dir=os.environ["MODEL_DIR"],
        app_env="test",
        embedding_backend="fastembed",
        rate_limit_enabled=False,
    )


@pytest.fixture(scope="session")
def migrated_database(settings):
    env = {**os.environ, "DATABASE_URL": settings.database_url, "RATE_LIMIT_ENABLED": "false"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def embedder(settings):
    model = FastEmbedder(settings)
    await model.embed("Integration test warm-up")
    return model


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seeded_database(settings, migrated_database, embedder):
    engine = create_engine(settings, null_pool=True)
    try:
        await seed_load(engine, embedder, if_empty=True)
        yield
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def engine(settings, seeded_database):
    engine = create_engine(settings, null_pool=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def clean_non_seed_rows(engine):
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM idempotency_keys"))
        await connection.execute(text("DELETE FROM votes"))
        await connection.execute(text("DELETE FROM intake_items WHERE owner_session IS NOT NULL"))
        await connection.execute(text("DELETE FROM sessions"))


@pytest_asyncio.fixture(autouse=True)
async def clean_database(engine):
    await clean_non_seed_rows(engine)
    yield
    await clean_non_seed_rows(engine)


@pytest.fixture
def application(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    return create_app(settings)


@pytest_asyncio.fixture
async def client(application):
    async with application.router.lifespan_context(application):
        await application.state.warmup_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            yield client

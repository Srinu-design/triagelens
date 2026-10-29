import asyncio

import httpx
import pytest

from app.main import create_app


async def test_readiness_waits_for_warmup(settings, embedder, monkeypatch):
    release = asyncio.Event()

    class DelayedEmbedder:
        model_name = embedder.model_name

        async def embed(self, text):
            await release.wait()
            return await embedder.embed(text)

    monkeypatch.setattr("app.main.make_embedder", lambda settings: DelayedEmbedder())
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/readyz")
            assert response.status_code == 503
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["code"] == "NOT_READY"
            assert response.json()["model_ready"] is False
            assert "set-cookie" not in response.headers
            release.set()
            await app.state.warmup_task
            response = await client.get("/readyz")
            assert response.status_code == 200
            assert response.json() == {
                "db": "ok",
                "pgvector": "0.8.7",
                "model": embedder.model_name,
                "model_ready": True,
                "redis": "ok",
            }


async def test_startup_refuses_old_pgvector(settings, monkeypatch):
    async def old_version(engine):
        return "0.7.4"

    monkeypatch.setattr("app.main.pgvector_version", old_version)
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="0.7.4"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail")


async def test_startup_refuses_hash_outside_tests(settings):
    config = settings.model_copy(update={"app_env": "dev", "embedding_backend": "hash"})
    app = create_app(config)
    with pytest.raises(ValueError, match="test"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail")


async def test_redis_failure_does_not_block_startup_or_readiness(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    app = create_app(settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    async with app.router.lifespan_context(app):
        await app.state.warmup_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["redis"] == "degraded"


async def test_model_failure_keeps_readiness_false(settings, monkeypatch):
    class BrokenEmbedder:
        model_name = "broken-model"

        async def embed(self, text):
            raise RuntimeError("Model unavailable")

    monkeypatch.setattr("app.main.make_embedder", lambda settings: BrokenEmbedder())
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await app.state.warmup_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/healthz")).status_code == 200
            response = await client.get("/readyz")
            assert response.status_code == 503
            assert response.json()["model_ready"] is False


async def test_database_failure_returns_problem_details(client, monkeypatch):
    async def unavailable(engine):
        raise OSError("Database unavailable")

    monkeypatch.setattr("app.api.health.pgvector_version", unavailable)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["code"] == "NOT_READY"
    assert response.json()["db"] == "degraded"
    assert response.json()["pgvector"] is None

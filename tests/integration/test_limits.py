import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.main import create_app


@pytest.fixture
def rl_app(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    configured = settings.model_copy(update={"rate_limit_enabled": True})
    return create_app(configured)


@pytest.fixture(autouse=True)
def frozen_window(monkeypatch):
    # a test that straddles a fixed-window boundary would see its counter reset
    monkeypatch.setattr("app.limits.time", SimpleNamespace(time=lambda: 1_800_000_030))


@pytest.fixture(autouse=True)
async def clean_redis(settings):
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    try:
        keys = await redis.keys("tl:rl:*")
        if keys:
            await redis.delete(*keys)
        yield
        keys = await redis.keys("tl:rl:*")
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_search_rate_limit(rl_app):
    async with rl_app.router.lifespan_context(rl_app):
        await rl_app.state.warmup_task
        transport = httpx.ASGITransport(app=rl_app, client=("10.0.0.1", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(120):
                res = await client.get("/api/search?q=query")
                assert res.status_code == 200

            res = await client.get("/api/search?q=query")
            assert res.status_code == 429
            assert res.headers.get("Retry-After") is not None
            assert res.headers["Retry-After"].isdigit()
            problem = res.json()
            assert problem["code"] == "RATE_LIMITED"
            assert problem["status"] == 429


@pytest.mark.asyncio
async def test_preview_rate_limit(rl_app):
    async with rl_app.router.lifespan_context(rl_app):
        await rl_app.state.warmup_task
        transport = httpx.ASGITransport(app=rl_app, client=("10.0.0.2", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"title": "Sample title", "description": "Sample description"}
            for _ in range(30):
                res = await client.post("/api/triage/preview", json=body)
                assert res.status_code == 200

            res = await client.post("/api/triage/preview", json=body)
            assert res.status_code == 429
            assert res.headers.get("Retry-After") is not None
            assert res.headers["Retry-After"].isdigit()
            assert res.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_items_commit_rate_limit(rl_app):
    async with rl_app.router.lifespan_context(rl_app):
        await rl_app.state.warmup_task
        transport = httpx.ASGITransport(app=rl_app, client=("10.0.0.3", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            item_body = {
                "title": "Rate limit item test",
                "description": "Item description for commit test",
                "category": "BUG_REPORT",
                "action": "NEW",
            }
            for i in range(30):
                headers = {"Idempotency-Key": f"commit-key-rl-{i:016d}"}
                res = await client.post("/api/items", json=item_body, headers=headers)
                assert res.status_code == 201

            headers = {"Idempotency-Key": "commit-key-rl-overflow001"}
            res = await client.post("/api/items", json=item_body, headers=headers)
            assert res.status_code == 429
            assert res.headers.get("Retry-After") is not None
            assert res.headers["Retry-After"].isdigit()
            assert res.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_ann_lab_rate_limit(rl_app):
    async with rl_app.router.lifespan_context(rl_app):
        await rl_app.state.warmup_task
        transport = httpx.ASGITransport(app=rl_app, client=("10.0.0.4", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(30):
                res = await client.get("/api/ann-lab?q=query")
                assert res.status_code == 200

            res = await client.get("/api/ann-lab?q=query")
            assert res.status_code == 429
            assert res.headers.get("Retry-After") is not None
            assert res.headers["Retry-After"].isdigit()
            assert res.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_redis_outage_fails_open_for_search_and_closed_for_llm(rl_app, monkeypatch):
    async with rl_app.router.lifespan_context(rl_app):
        await rl_app.state.warmup_task
        # Simulate Redis outage
        monkeypatch.setattr(
            rl_app.state.redis,
            "eval",
            AsyncMock(side_effect=RedisConnectionError("Redis connection refused")),
        )

        transport = httpx.ASGITransport(app=rl_app, client=("10.0.0.5", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Search fails open (request succeeds)
            res = await client.get("/api/search?q=query")
            assert res.status_code == 200

            # 2. Preview forces allow_llm=False (returns rules source)
            res = await client.post(
                "/api/triage/preview",
                json={"title": "Error in auth", "description": "Login fails with 500"},
            )
            assert res.status_code == 200
            assert res.json()["suggestion"]["source"] == "rules"


@pytest.mark.asyncio
async def test_llm_per_ip_limit_20_then_rules(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    configured = settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "llm_base_url": "https://fake-llm.test/v1",
            "llm_api_key": "fake-key",
            "llm_model": "fake-model",
        }
    )
    app = create_app(configured)

    llm_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "category": "BUG_REPORT",
                            "severity": "HIGH",
                            "subsystem": "Auth",
                            "sentiment": "FRICTION",
                            "summary": "Login failure",
                        }
                    )
                }
            }
        ]
    }

    def fake_llm_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=llm_payload)

    async with app.router.lifespan_context(app):
        await app.state.warmup_task
        fake_http = httpx.AsyncClient(transport=httpx.MockTransport(fake_llm_transport))
        app.state.http = fake_http
        app.state.llm.http = fake_http

        transport = httpx.ASGITransport(app=app, client=("10.0.0.6", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"title": "Checkout error", "description": "Crash on payment screen"}
            for _ in range(20):
                res = await client.post("/api/triage/preview", json=body)
                assert res.status_code == 200
                assert res.json()["suggestion"]["source"] == "llm"

            # 21st request should fall back to rules
            res = await client.post("/api/triage/preview", json=body)
            assert res.status_code == 200
            assert res.json()["suggestion"]["source"] == "rules"

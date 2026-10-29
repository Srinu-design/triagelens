from typing import Annotated
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text

from app.db import read_transaction
from app.main import create_app
from app.session import current_session_id


async def test_first_request_creates_session(client, engine):
    response = await client.get("/missing")
    assert response.status_code == 404
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=86400" in cookie
    assert "Secure" not in cookie
    session_id = UUID(client.cookies["tl_session"])
    async with read_transaction(engine) as connection:
        assert (
            await connection.scalar(
                text("SELECT id FROM sessions WHERE id = :id"), {"id": session_id}
            )
            == session_id
        )


async def test_invalid_cookie_is_replaced(client):
    client.cookies.set("tl_session", "not-a-uuid")
    response = await client.get("/missing")
    assert "set-cookie" in response.headers
    UUID(response.cookies["tl_session"])


async def test_valid_cookie_is_reused(client, engine):
    session_id = uuid4()
    client.cookies.set("tl_session", str(session_id))
    response = await client.get("/missing")
    assert "set-cookie" not in response.headers
    async with read_transaction(engine) as connection:
        assert await connection.scalar(text("SELECT count(*) FROM sessions")) == 1


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/static/missing.css"])
async def test_probes_and_static_do_not_create_sessions(client, engine, path):
    response = await client.get(path)
    assert "set-cookie" not in response.headers
    async with read_transaction(engine) as connection:
        assert await connection.scalar(text("SELECT count(*) FROM sessions")) == 0


async def test_session_dependency(application, client):
    @application.get("/session-check")
    async def session_check(session_id: Annotated[UUID, Depends(current_session_id)]):
        return {"id": str(session_id)}

    response = await client.get("/session-check")
    assert response.json()["id"] == client.cookies["tl_session"]


async def test_problem_details(application, client):
    @application.get("/validation-check")
    async def validation_check(value: int):
        return {"value": value}

    for method, path, status, code in [
        ("GET", "/missing", 404, "NOT_FOUND"),
        ("POST", "/healthz", 405, "METHOD_NOT_ALLOWED"),
        ("GET", "/validation-check?value=secret-text", 422, "VALIDATION_ERROR"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == status
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == code
        assert "secret-text" not in response.text
        if status == 405:
            assert "GET" in response.headers["allow"]


async def test_production_cookie_is_secure(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    app = create_app(settings.model_copy(update={"app_env": "prod"}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/missing")
            assert "Secure" in response.headers["set-cookie"]
            assert "HttpOnly" in response.headers["set-cookie"]

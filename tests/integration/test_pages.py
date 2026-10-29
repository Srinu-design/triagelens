import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_get_root_serves_html_and_sets_session(client, engine):
    """GET / serves static/index.html, sets tl_session cookie, and creates a session row."""
    # Ensure clean state
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM sessions"))

    res = await client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "<!doctype html>" in res.text.lower()
    assert "TriageLens" in res.text
    assert "tl_session" in res.cookies

    # Verify session row created in database
    cookie_val = res.cookies.get("tl_session")
    assert cookie_val
    async with engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT id FROM sessions WHERE id = :id"), {"id": cookie_val})
        ).scalar_one_or_none()
        assert row is not None


@pytest.mark.asyncio
async def test_static_files_served(client):
    """Static assets /static/app.js, /static/styles.css, theme.js, favicon.svg are served."""
    res_app = await client.get("/static/app.js")
    assert res_app.status_code == 200
    assert "javascript" in res_app.headers.get(
        "content-type", ""
    ) or "text/" in res_app.headers.get("content-type", "")
    assert len(res_app.text) > 100

    res_css = await client.get("/static/styles.css")
    assert res_css.status_code == 200
    assert "css" in res_css.headers.get("content-type", "")
    assert len(res_css.text) > 100

    res_theme = await client.get("/static/theme.js")
    assert res_theme.status_code == 200

    res_fav = await client.get("/static/favicon.svg")
    assert res_fav.status_code == 200


@pytest.mark.asyncio
async def test_static_requests_create_no_session_row(client, engine):
    """Requests under /static/ bypass SessionMiddleware and create no session rows in DB."""
    # Start with empty sessions table
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM sessions"))

    res1 = await client.get("/static/app.js")
    assert res1.status_code == 200
    assert "tl_session" not in res1.cookies

    res2 = await client.get("/static/styles.css")
    assert res2.status_code == 200
    assert "tl_session" not in res2.cookies

    res3 = await client.get("/static/theme.js")
    assert res3.status_code == 200
    assert "tl_session" not in res3.cookies

    # Verify no session row was created
    async with engine.begin() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM sessions"))
        assert count == 0

import asyncio
from uuid import UUID

import httpx
import pytest
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text

from app.db import read_transaction, write_transaction


@pytest.mark.integration
async def test_vote_twice_same_vote_count_15(client, engine):
    key1 = "vote-key-0000000000000001"
    key2 = "vote-key-0000000000000002"
    body = {
        "title": "Dark mode please",
        "description": "Would love dark mode for checkout",
        "category": "FEATURE_REQUEST",
        "action": "VOTE",
        "master_code": "FEAT-201",
    }

    # First vote
    resp1 = await client.post("/api/items", json=body, headers={"Idempotency-Key": key1})
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["action"] == "VOTE"
    assert data1["master"] == "FEAT-201"
    assert data1["count_shown"] == 15
    assert data1["already_voted"] is False

    # Second vote with different key
    resp2 = await client.post("/api/items", json=body, headers={"Idempotency-Key": key2})
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["action"] == "VOTE"
    assert data2["master"] == "FEAT-201"
    assert data2["count_shown"] == 15
    assert data2["already_voted"] is True

    # Database has exactly one vote for this session
    session_id = UUID(client.cookies["tl_session"])
    async with read_transaction(engine) as conn:
        vote_count = await conn.scalar(
            text(
                "SELECT count(*) FROM votes v "
                "JOIN intake_items i ON i.id = v.item_id "
                "WHERE v.session_id = :sid AND i.ticket_code = 'FEAT-201'"
            ),
            {"sid": session_id},
        )
        assert vote_count == 1


@pytest.mark.integration
async def test_same_key_replay_returns_200_and_replayed_header(client):
    key = "replay-key-0000000000000001"
    body = {
        "title": "Checkout error 504",
        "description": "Server times out during peak hours",
        "category": "BUG_REPORT",
        "action": "NEW",
    }

    resp1 = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp1.status_code == 201
    assert "Idempotent-Replayed" not in resp1.headers
    data1 = resp1.json()
    assert data1["action"] == "NEW"
    assert data1["status"] == "OPEN"

    resp2 = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp2.status_code == 200
    assert resp2.headers.get("Idempotent-Replayed") == "true"
    assert resp2.json() == data1


@pytest.mark.integration
async def test_same_key_changed_body_returns_422_reused(client):
    key = "reused-key-0000000000000001"
    body1 = {
        "title": "Original issue title",
        "description": "Some description text",
        "category": "BUG_REPORT",
        "action": "NEW",
    }
    body2 = {
        "title": "Changed issue title",
        "description": "Some description text",
        "category": "BUG_REPORT",
        "action": "NEW",
    }

    resp1 = await client.post("/api/items", json=body1, headers={"Idempotency-Key": key})
    assert resp1.status_code == 201

    resp2 = await client.post("/api/items", json=body2, headers={"Idempotency-Key": key})
    assert resp2.status_code == 422
    assert resp2.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.integration
async def test_link_to_feature_returns_422_invalid_action(client):
    key = "invalid-action-key-000001"
    # LINK with master FEAT-201 is invalid because LINK requires a BUG_REPORT master
    body = {
        "title": "Night mode checkout",
        "description": "Would love dark mode",
        "category": "FEATURE_REQUEST",
        "action": "LINK",
        "master_code": "FEAT-201",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_ACTION"


@pytest.mark.integration
async def test_answer_to_enq_301_returns_faq_note(client):
    key = "answer-key-000000000000001"
    body = {
        "title": "Where can I get my invoice?",
        "description": "I need last month GST invoice",
        "category": "CLIENT_ENQUIRY",
        "action": "ANSWER",
        "master_code": "ENQ-301",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 201
    data = resp.json()
    assert data["action"] == "ANSWER"
    assert data["master"] == "ENQ-301"
    assert data["ticket_code"].startswith("ENQ-U")
    assert "Billing > Invoices" in data["resolution_note"]


@pytest.mark.integration
async def test_new_gets_bug_u_code(client):
    key = "new-bug-key-00000000000001"
    body = {
        "title": "App crashes on launch",
        "description": "Immediate crash when opening the application",
        "category": "BUG_REPORT",
        "action": "NEW",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 201
    data = resp.json()
    assert data["action"] == "NEW"
    assert data["ticket_code"].startswith("BUG-U")
    assert data["status"] == "OPEN"

    # Verify retrieval
    get_resp = await client.get(f"/api/items/{data['ticket_code']}")
    assert get_resp.status_code == 200
    item = get_resp.json()
    assert item["ticket_code"] == data["ticket_code"]
    assert item["category"] == "BUG_REPORT"
    assert item["status"] == "OPEN"
    assert item["master"] is None


@pytest.mark.integration
async def test_link_to_bug_105_raises_count_for_this_session_only(client, application):
    key = "link-bug-key-0000000000001"
    body = {
        "title": "Dark mode crash on payment",
        "description": "Android checkout screen closes instantly with dark theme",
        "category": "BUG_REPORT",
        "action": "LINK",
        "master_code": "BUG-105",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 201
    data = resp.json()
    assert data["action"] == "LINK"
    assert data["master"] == "BUG-105"
    assert data["count_shown"] == 6

    # Session A sees count 6
    get_a = await client.get("/api/items/BUG-105")
    assert get_a.status_code == 200
    assert get_a.json()["count_shown"] == 6

    # Session B sees count 5
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client_b:
        get_b = await client_b.get("/api/items/BUG-105")
        assert get_b.status_code == 200
        assert get_b.json()["count_shown"] == 5


@pytest.mark.integration
async def test_missing_or_short_key_returns_400(client):
    body = {
        "title": "Valid ticket title",
        "description": "Valid ticket description",
        "category": "BUG_REPORT",
        "action": "NEW",
    }

    # Missing header
    r1 = await client.post("/api/items", json=body)
    assert r1.status_code == 400
    assert r1.json()["code"] == "IDEMPOTENCY_KEY_MISSING"

    # Short key (15 chars)
    r2 = await client.post("/api/items", json=body, headers={"Idempotency-Key": "short-key-12345"})
    assert r2.status_code == 400
    assert r2.json()["code"] == "IDEMPOTENCY_KEY_MISSING"

    # Too long key (65 chars)
    r3 = await client.post("/api/items", json=body, headers={"Idempotency-Key": "k" * 65})
    assert r3.status_code == 400
    assert r3.json()["code"] == "IDEMPOTENCY_KEY_MISSING"

    # Invalid characters
    r4 = await client.post(
        "/api/items", json=body, headers={"Idempotency-Key": "invalid@key!12345678"}
    )
    assert r4.status_code == 400
    assert r4.json()["code"] == "IDEMPOTENCY_KEY_MISSING"


@pytest.mark.integration
async def test_bad_error_code_returns_422_invalid_error_code(client):
    key = "bad-err-key-00000000000001"
    body = {
        "title": "Valid ticket title",
        "description": "Valid ticket description",
        "category": "BUG_REPORT",
        "action": "NEW",
        "error_code": "baderror",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_ERROR_CODE"


@pytest.mark.integration
async def test_pii_in_title_stored_scrubbed(client, engine):
    key = "pii-scrub-key-000000000001"
    raw_title = "Checkout issue for test.user@example.com call 98765-43210"
    raw_desc = "Server 10.0.0.1 returned error key sk-live-123456789012"
    body = {
        "title": raw_title,
        "description": raw_desc,
        "category": "BUG_REPORT",
        "action": "NEW",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 201
    ticket_code = resp.json()["ticket_code"]

    async with read_transaction(engine) as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT title, description FROM intake_items WHERE ticket_code = :code"),
                    {"code": ticket_code},
                )
            )
            .mappings()
            .first()
        )

    assert row is not None
    assert "test.user@example.com" not in row["title"]
    assert "98765-43210" not in row["title"]
    assert "[REDACTED_EMAIL]" in row["title"]
    assert "[REDACTED_PHONE]" in row["title"]

    assert "sk-live-123456789012" not in row["description"]
    assert "[REDACTED_SECRET]" in row["description"]


@pytest.mark.integration
async def test_51st_item_returns_422_session_limit(client, engine):
    # Establish session
    await client.get("/missing")
    session_id = UUID(client.cookies["tl_session"])

    # Insert 50 rows directly in DB for this session
    dummy_vec = [0.0] * 384
    dummy_vec[0] = 1.0
    async with write_transaction(engine) as conn:
        for i in range(50):
            await conn.execute(
                text(
                    "INSERT INTO intake_items (ticket_code, category, title, description, "
                    "severity, subsystem, status, duplicate_of, base_count, owner_session, "
                    "embedding_model, embedding) VALUES (:code, 'BUG_REPORT', 'Setup bug', "
                    "'Setup desc', 'LOW', 'General', 'OPEN', NULL, 0, :sid, 'test', :vec)"
                ).bindparams(bindparam("vec", type_=HALFVEC(384))),
                {"code": f"BUG-DUMMY-{i}", "sid": session_id, "vec": dummy_vec},
            )

    key = "limit-test-key-00000000001"
    body = {
        "title": "The fifty-first item",
        "description": "Attempting to create item 51",
        "category": "BUG_REPORT",
        "action": "NEW",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 422
    assert resp.json()["code"] == "SESSION_LIMIT"


@pytest.mark.integration
async def test_concurrent_same_key_creates_single_item(client, engine):
    key = "concurrent-key-00000000001"
    body = {
        "title": "Concurrent ticket test",
        "description": "Ten concurrent requests with identical key and payload",
        "category": "BUG_REPORT",
        "action": "NEW",
    }

    # Establish session cookie first so all concurrent requests share the session
    await client.get("/missing")

    responses = await asyncio.gather(
        *[client.post("/api/items", json=body, headers={"Idempotency-Key": key}) for _ in range(10)]
    )

    statuses = [r.status_code for r in responses]
    assert 201 in statuses
    assert all(s in (200, 201) for s in statuses)

    replays = [r for r in responses if r.status_code == 200]
    assert len(replays) == 9
    for r in replays:
        assert r.headers.get("Idempotent-Replayed") == "true"

    session_id = UUID(client.cookies["tl_session"])
    async with read_transaction(engine) as conn:
        item_count = await conn.scalar(
            text("SELECT count(*) FROM intake_items WHERE owner_session = :sid"),
            {"sid": session_id},
        )
        assert item_count == 1
        key_count = await conn.scalar(
            text("SELECT count(*) FROM idempotency_keys WHERE session_id = :sid"),
            {"sid": session_id},
        )
        assert key_count == 1


@pytest.mark.integration
async def test_get_item_not_visible_returns_404(client, application):
    # Session A creates item
    key = "visible-test-key-000000001"
    body = {
        "title": "Private user bug",
        "description": "Belongs to session A only",
        "category": "BUG_REPORT",
        "action": "NEW",
    }
    resp = await client.post("/api/items", json=body, headers={"Idempotency-Key": key})
    assert resp.status_code == 201
    code = resp.json()["ticket_code"]

    # Session A can see it
    resp_a = await client.get(f"/api/items/{code}")
    assert resp_a.status_code == 200
    assert resp_a.json()["ticket_code"] == code

    # Session B cannot see it -> 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client_b:
        resp_b = await client_b.get(f"/api/items/{code}")
        assert resp_b.status_code == 404
        assert resp_b.json()["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_text_plain_body_returns_422(client):
    key = "plain-text-key-00000000001"
    resp = await client.post(
        "/api/items",
        content=b"plain text body",
        headers={"Content-Type": "text/plain", "Idempotency-Key": key},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

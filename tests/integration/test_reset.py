from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from app.db import read_transaction


@pytest.mark.integration
async def test_session_reset(client, application, engine):
    key_a1 = "session-a-key-000000000001"
    key_a2 = "session-a-key-000000000002"
    key_a3 = "session-a-key-000000000003"

    # Session A creates a new bug, links to BUG-105, and votes for FEAT-201
    resp_a_new = await client.post(
        "/api/items",
        json={
            "title": "Bug for session A",
            "description": "Session A specific bug report",
            "category": "BUG_REPORT",
            "action": "NEW",
        },
        headers={"Idempotency-Key": key_a1},
    )
    assert resp_a_new.status_code == 201
    code_a = resp_a_new.json()["ticket_code"]

    resp_a_link = await client.post(
        "/api/items",
        json={
            "title": "Dark mode crash again",
            "description": "Another dark mode crash report",
            "category": "BUG_REPORT",
            "action": "LINK",
            "master_code": "BUG-105",
        },
        headers={"Idempotency-Key": key_a2},
    )
    assert resp_a_link.status_code == 201
    assert resp_a_link.json()["count_shown"] == 6

    resp_a_vote = await client.post(
        "/api/items",
        json={
            "title": "Dark mode vote",
            "description": "Voting for dark mode",
            "category": "FEATURE_REQUEST",
            "action": "VOTE",
            "master_code": "FEAT-201",
        },
        headers={"Idempotency-Key": key_a3},
    )
    assert resp_a_vote.status_code == 201
    assert resp_a_vote.json()["count_shown"] == 15

    # Session B creates an item and votes for FEAT-201
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client_b:
        key_b1 = "session-b-key-000000000001"
        key_b2 = "session-b-key-000000000002"

        resp_b_new = await client_b.post(
            "/api/items",
            json={
                "title": "Bug for session B",
                "description": "Session B specific bug report",
                "category": "BUG_REPORT",
                "action": "NEW",
            },
            headers={"Idempotency-Key": key_b1},
        )
        assert resp_b_new.status_code == 201
        code_b = resp_b_new.json()["ticket_code"]

        resp_b_vote = await client_b.post(
            "/api/items",
            json={
                "title": "Dark mode vote B",
                "description": "Session B voting for dark mode",
                "category": "FEATURE_REQUEST",
                "action": "VOTE",
                "master_code": "FEAT-201",
            },
            headers={"Idempotency-Key": key_b2},
        )
        assert resp_b_vote.status_code == 201
        assert resp_b_vote.json()["count_shown"] == 15

        # Before reset: A sees FEAT-201 at 15
        get_a_feat = await client.get("/api/items/FEAT-201")
        assert get_a_feat.json()["count_shown"] == 15

        # Session A resets
        resp_reset = await client.post("/api/session/reset")
        assert resp_reset.status_code == 200
        reset_data = resp_reset.json()
        assert reset_data["deleted_items"] == 2
        assert reset_data["deleted_votes"] == 1

        # After reset: A sees FEAT-201 back at 14
        get_a_feat_after = await client.get("/api/items/FEAT-201")
        assert get_a_feat_after.json()["count_shown"] == 14

        # After reset: A sees BUG-105 back at 5
        get_a_bug_after = await client.get("/api/items/BUG-105")
        assert get_a_bug_after.json()["count_shown"] == 5

        # Session A's created item is now 404
        get_a_item = await client.get(f"/api/items/{code_a}")
        assert get_a_item.status_code == 404

        # Session B is untouched: FEAT-201 is still 15
        get_b_feat = await client_b.get("/api/items/FEAT-201")
        assert get_b_feat.json()["count_shown"] == 15

        # Session B's item is still visible
        get_b_item = await client_b.get(f"/api/items/{code_b}")
        assert get_b_item.status_code == 200
        assert get_b_item.json()["ticket_code"] == code_b

    # Verify session row for A is preserved
    session_a_id = UUID(client.cookies["tl_session"])
    async with read_transaction(engine) as conn:
        session_exists = await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM sessions WHERE id = :sid)"),
            {"sid": session_a_id},
        )
        assert session_exists is True

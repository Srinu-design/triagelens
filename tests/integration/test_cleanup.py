import uuid

import pytest
from sqlalchemy import text

from app.db import write_transaction
from app.services.cleanup import CLEANUP_ADVISORY_LOCK_ID, cleanup_idle_sessions


@pytest.mark.asyncio
async def test_cleanup_idle_sessions_cascades_and_preserves_seed(engine):
    old_session_id = uuid.uuid4()
    fresh_session_id = uuid.uuid4()

    async with write_transaction(engine) as conn:
        # Initial seed items count
        seed_count = await conn.scalar(
            text("SELECT count(*) FROM intake_items WHERE owner_session IS NULL")
        )
        assert seed_count > 0

        # Insert old session (idle > 24h) and fresh session
        await conn.execute(
            text(
                "INSERT INTO sessions (id, created_at, last_seen) "
                "VALUES (:old_id, now() - interval '25 hours', now() - interval '25 hours'), "
                "       (:fresh_id, now(), now())"
            ),
            {"old_id": old_session_id, "fresh_id": fresh_session_id},
        )

        # Get an existing seed item ID to vote on
        seed_item_id = await conn.scalar(
            text("SELECT id FROM intake_items WHERE owner_session IS NULL LIMIT 1")
        )

        # Insert items for both sessions
        dummy_embedding = [0.0] * 384
        await conn.execute(
            text(
                "INSERT INTO intake_items "
                "(ticket_code, category, title, description, owner_session, "
                " embedding_model, embedding) "
                "VALUES ('BUG-U1001', 'BUG_REPORT', 'Old bug', 'Old bug desc', :old_id, "
                "        'fastembed', :vec), "
                "       ('BUG-U1002', 'BUG_REPORT', 'Fresh bug', 'Fresh bug desc', :fresh_id, "
                "        'fastembed', :vec)"
            ),
            {"old_id": old_session_id, "fresh_id": fresh_session_id, "vec": dummy_embedding},
        )

        # Insert votes for both sessions
        await conn.execute(
            text(
                "INSERT INTO votes (item_id, session_id) "
                "VALUES (:item_id, :old_id), (:item_id, :fresh_id)"
            ),
            {"item_id": seed_item_id, "old_id": old_session_id, "fresh_id": fresh_session_id},
        )

        # Insert idempotency keys for both sessions
        await conn.execute(
            text(
                "INSERT INTO idempotency_keys "
                "(session_id, key, payload_hash, status_code, response_json) "
                "VALUES (:old_id, 'old-session-key-001', :hash_a, 201, '{}'), "
                "       (:fresh_id, 'fresh-session-key-01', :hash_b, 201, '{}')"
            ),
            {
                "old_id": old_session_id,
                "fresh_id": fresh_session_id,
                "hash_a": "a" * 64,
                "hash_b": "b" * 64,
            },
        )

    # Run session cleanup with ttl_hours = 24
    deleted = await cleanup_idle_sessions(engine, ttl_hours=24)
    assert deleted == 1

    async with write_transaction(engine) as conn:
        # Old session is deleted
        old_exists = await conn.scalar(
            text("SELECT count(*) FROM sessions WHERE id = :old_id"), {"old_id": old_session_id}
        )
        assert old_exists == 0

        # Old session's items, votes, and keys are gone
        old_items = await conn.scalar(
            text("SELECT count(*) FROM intake_items WHERE owner_session = :old_id"),
            {"old_id": old_session_id},
        )
        assert old_items == 0

        old_votes = await conn.scalar(
            text("SELECT count(*) FROM votes WHERE session_id = :old_id"),
            {"old_id": old_session_id},
        )
        assert old_votes == 0

        old_keys = await conn.scalar(
            text("SELECT count(*) FROM idempotency_keys WHERE session_id = :old_id"),
            {"old_id": old_session_id},
        )
        assert old_keys == 0

        # Fresh session and its rows are untouched
        fresh_exists = await conn.scalar(
            text("SELECT count(*) FROM sessions WHERE id = :fresh_id"),
            {"fresh_id": fresh_session_id},
        )
        assert fresh_exists == 1

        fresh_items = await conn.scalar(
            text("SELECT count(*) FROM intake_items WHERE owner_session = :fresh_id"),
            {"fresh_id": fresh_session_id},
        )
        assert fresh_items == 1

        fresh_votes = await conn.scalar(
            text("SELECT count(*) FROM votes WHERE session_id = :fresh_id"),
            {"fresh_id": fresh_session_id},
        )
        assert fresh_votes == 1

        fresh_keys = await conn.scalar(
            text("SELECT count(*) FROM idempotency_keys WHERE session_id = :fresh_id"),
            {"fresh_id": fresh_session_id},
        )
        assert fresh_keys == 1

        # Seed rows are completely untouched
        current_seed_count = await conn.scalar(
            text("SELECT count(*) FROM intake_items WHERE owner_session IS NULL")
        )
        assert current_seed_count == seed_count


@pytest.mark.asyncio
async def test_cleanup_lock_contention_returns_zero(engine):
    # Hold the advisory transaction lock on a separate connection
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            locked = await conn.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                {"lock_id": CLEANUP_ADVISORY_LOCK_ID},
            )
            assert locked is True

            # Concurrent cleanup call while lock is held must return 0
            count = await cleanup_idle_sessions(engine, ttl_hours=24)
            assert count == 0
        finally:
            await trans.rollback()

    # After releasing lock, cleanup can acquire lock normally
    count = await cleanup_idle_sessions(engine, ttl_hours=24)
    assert count >= 0

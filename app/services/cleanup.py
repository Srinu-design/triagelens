import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import write_transaction

logger = logging.getLogger(__name__)

# DECISION: Fixed integer constant for PostgreSQL advisory transaction lock during cleanup.
CLEANUP_ADVISORY_LOCK_ID = 84018401


async def cleanup_idle_sessions(engine: AsyncEngine, ttl_hours: int) -> int:
    """Deletes sessions whose last_seen is older than ttl_hours.
    Runs inside a write transaction guarded by pg_try_advisory_xact_lock.
    Returns the number of deleted session rows, or 0 if the lock was busy.
    """
    async with write_transaction(engine) as conn:
        locked = await conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": CLEANUP_ADVISORY_LOCK_ID},
        )
        if not locked:
            return 0

        res = await conn.execute(
            text("DELETE FROM sessions WHERE last_seen < now() - make_interval(hours => :ttl)"),
            {"ttl": ttl_hours},
        )
        return res.rowcount or 0

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import read_transaction, write_transaction


@pytest.mark.parametrize(
    "changes",
    [
        "error_code = ''",
        "status = 'OPEN', duplicate_of = "
        "(SELECT id FROM intake_items WHERE ticket_code = 'BUG-101')",
        "status = 'DUPLICATE', duplicate_of = NULL",
    ],
)
async def test_schema_rejects_invalid_items(engine, changes):
    with pytest.raises(IntegrityError):
        async with write_transaction(engine) as connection:
            await connection.execute(
                text(f"UPDATE intake_items SET {changes} WHERE ticket_code = 'BUG-102'")
            )


async def test_search_indexes_exist(engine):
    async with read_transaction(engine) as connection:
        indexes = set(
            (
                await connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'intake_items'")
                )
            ).scalars()
        )
    assert {"intake_hnsw", "intake_tsv_gin"} <= indexes


async def test_read_transaction_rejects_writes(engine):
    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with read_transaction(engine) as connection:
            await connection.execute(text("DELETE FROM sessions"))

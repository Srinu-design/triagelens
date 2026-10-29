import asyncio
import os
import sys

import pytest
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text

from app.db import read_transaction
from app.seed_data import SEED
from app.seed_load import seed_load


async def test_seed_rows(engine):
    async with read_transaction(engine) as connection:
        counts = dict(
            (
                await connection.execute(
                    text("SELECT category, count(*) FROM intake_items GROUP BY category")
                )
            ).all()
        )
        rows = (
            await connection.execute(
                text(
                    "SELECT ticket_code, category, title, description, error_code, severity, "
                    "subsystem, base_count, resolution_note, embedding_model, "
                    "vector_dims(embedding), owner_session, status FROM intake_items "
                    "ORDER BY ticket_code"
                )
            )
        ).all()
    assert counts == {"BUG_REPORT": 12, "FEATURE_REQUEST": 9, "CLIENT_ENQUIRY": 9}
    assert [tuple(row[:9]) for row in rows] == sorted(SEED)
    assert all(row[9:] == ("BAAI/bge-small-en-v1.5", 384, None, "OPEN") for row in rows)


async def test_paraphrase_cosine(engine, embedder):
    query = await embedder.embed("Mobile avatar upload screen hangs")
    async with read_transaction(engine) as connection:
        cosine = await connection.scalar(
            text(
                "SELECT 1 - (embedding <=> :qvec) FROM intake_items WHERE ticket_code = 'BUG-102'"
            ).bindparams(bindparam("qvec", type_=HALFVEC(384))),
            {"qvec": query},
        )
    assert cosine == pytest.approx(0.663, abs=0.01)


@pytest.mark.parametrize("arguments, expected_code", [(["--if-empty"], 0), ([], 1)])
async def test_seed_existing_database_cli(settings, engine, arguments, expected_code):
    env = {**os.environ, "DATABASE_URL": settings.database_url, "APP_ENV": "test"}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.seed_load",
        *arguments,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == expected_code, (stdout.decode(), stderr.decode())
    assert "already seeded" in stdout.decode()
    async with read_transaction(engine) as connection:
        assert await connection.scalar(text("SELECT count(*) FROM intake_items")) == 30


async def test_seed_refuses_existing_data(engine, embedder):
    with pytest.raises(ValueError, match="already seeded"):
        await seed_load(engine, embedder)

import argparse
import asyncio

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db import create_engine, write_transaction
from app.seed_data import SEED
from app.services.embeddings import Embedder, embed_ticket_text, make_embedder


async def seed_load(engine: AsyncEngine, embedder: Embedder, *, if_empty: bool = False) -> int:
    async with write_transaction(engine) as connection:
        # DECISION: A transaction advisory lock serializes concurrent seed-loader runs.
        await connection.execute(text("SELECT pg_advisory_xact_lock(728416301)"))
        exists = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM intake_items WHERE owner_session IS NULL)")
        )
        if exists:
            if if_empty:
                return 0
            raise ValueError("Database is already seeded.")
        # DECISION: The fixed, PII-free seed bypasses scrubbing; its literal text is preserved.
        embeddings = await embedder.embed_many([embed_ticket_text(row[2], row[3]) for row in SEED])
        columns = (
            "ticket_code",
            "category",
            "title",
            "description",
            "error_code",
            "severity",
            "subsystem",
            "base_count",
            "resolution_note",
        )
        rows = []
        for row, embedding in zip(SEED, embeddings, strict=True):
            rows.append(
                dict(zip(columns, row, strict=True))
                | {"embedding": embedding, "embedding_model": embedder.model_name}
            )
        await connection.execute(
            text(
                "INSERT INTO intake_items (ticket_code, category, title, description, error_code, "
                "severity, subsystem, base_count, resolution_note, embedding, embedding_model, "
                "owner_session, status) VALUES (:ticket_code, :category, :title, :description, "
                ":error_code, :severity, :subsystem, :base_count, :resolution_note, :embedding, "
                ":embedding_model, NULL, 'OPEN')"
            ).bindparams(bindparam("embedding", type_=HALFVEC(384))),
            rows,
        )
        return len(rows)


async def main(if_empty: bool) -> int:
    settings = Settings()
    engine = create_engine(settings)
    try:
        count = await seed_load(engine, make_embedder(settings), if_empty=if_empty)
        print(f"Inserted {count} seed rows." if count else "Database is already seeded; skipped.")
        return 0
    except ValueError as error:
        print(str(error))
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load the fixed TriageLens seed without deleting data"
    )
    parser.add_argument("--if-empty", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().if_empty)))

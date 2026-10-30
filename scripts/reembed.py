import asyncio
import sys
from pathlib import Path

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import create_engine, write_transaction  # noqa: E402
from app.services.embeddings import embed_ticket_text, make_embedder  # noqa: E402


async def reembed(engine, embedder) -> int:
    last_id = 0
    total = 0
    while True:
        # DECISION: Keyset batches bound memory and commit each completed batch independently.
        async with write_transaction(engine) as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT id, title, description FROM intake_items "
                        "WHERE id > :last_id ORDER BY id LIMIT 256"
                    ),
                    {"last_id": last_id},
                )
            ).all()
            if not rows:
                break
            embeddings = await embedder.embed_many(
                [embed_ticket_text(row.title, row.description) for row in rows]
            )
            await connection.execute(
                text(
                    "UPDATE intake_items SET embedding = :embedding, "
                    "embedding_model = :model WHERE id = :item_id"
                ).bindparams(bindparam("embedding", type_=HALFVEC(384))),
                [
                    {"item_id": row.id, "embedding": vector, "model": embedder.model_name}
                    for row, vector in zip(rows, embeddings, strict=True)
                ],
            )
            last_id = rows[-1].id
            total += len(rows)
    # Every row was rewritten: until a vacuum the HNSW graph is mostly dead copies, which can
    # starve index scans (VACUUM cannot run inside a transaction, hence AUTOCOMMIT).
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("VACUUM ANALYZE intake_items"))
    return total


async def main():
    settings = Settings()
    engine = create_engine(settings)
    try:
        count = await reembed(engine, make_embedder(settings))
        print(f"Re-embedded {count} items.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

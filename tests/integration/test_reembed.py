from uuid import uuid4

from sqlalchemy import text

from app.db import read_transaction, write_transaction
from scripts.reembed import reembed


async def test_reembed_uses_batches_and_updates_every_row(engine, embedder):
    session_id = uuid4()
    async with write_transaction(engine) as connection:
        await connection.execute(text("INSERT INTO sessions (id) VALUES (:id)"), {"id": session_id})
        await connection.execute(
            text(
                "INSERT INTO intake_items "
                "(ticket_code, category, title, description, owner_session, "
                "embedding, embedding_model) "
                "SELECT 'BUG-U' || n, category, title, description, :sid, embedding, 'old-model' "
                "FROM intake_items CROSS JOIN generate_series(1, 257) n "
                "WHERE ticket_code = 'BUG-102'"
            ),
            {"sid": session_id},
        )
    batch_sizes = []

    class RecordingEmbedder:
        model_name = embedder.model_name

        async def embed_many(self, texts):
            batch_sizes.append(len(texts))
            return await embedder.embed_many(texts)

    assert await reembed(engine, RecordingEmbedder()) == 287
    assert batch_sizes == [256, 31]
    async with read_transaction(engine) as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM intake_items WHERE embedding_model = :model"),
                {"model": embedder.model_name},
            )
            == 287
        )

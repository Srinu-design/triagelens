from uuid import uuid4

import httpx
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text

from app.db import write_transaction
from app.services.embeddings import embed_ticket_text


async def test_search_and_ann_respect_session_visibility(application, client, engine, embedder):
    session_a, session_b = uuid4(), uuid4()
    title = "Territory map turns violet on Firefox"
    description = "The territory map turns violet instead of displaying the sales area."
    vector = await embedder.embed(embed_ticket_text(title, description))
    async with write_transaction(engine) as connection:
        await connection.execute(
            text("INSERT INTO sessions (id) VALUES (:sid)"), {"sid": session_a}
        )
        await connection.execute(
            text(
                "INSERT INTO intake_items "
                "(ticket_code, category, title, description, owner_session, "
                "embedding, embedding_model) "
                "VALUES ('BUG-U-PRIVATE', 'BUG_REPORT', :title, :description, "
                ":sid, :vector, :model)"
            ).bindparams(bindparam("vector", type_=HALFVEC(384))),
            {
                "title": title,
                "description": description,
                "sid": session_a,
                "vector": vector,
                "model": embedder.model_name,
            },
        )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
            cookies={"tl_session": str(session_a)},
        ) as client_a,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
            cookies={"tl_session": str(session_b)},
        ) as client_b,
    ):
        params = {"q": title, "category": "BUG_REPORT"}
        response_a = await client_a.get("/api/search", params=params)
        response_b = await client_b.get("/api/search", params=params)
        assert response_a.status_code == response_b.status_code == 200
        for name in ("lexical", "semantic", "fused"):
            assert "BUG-U-PRIVATE" in [row["ticket_code"] for row in response_a.json()[name]]
            assert "BUG-U-PRIVATE" not in [row["ticket_code"] for row in response_b.json()[name]]
        for owner, browser in [(True, client_a), (False, client_b)]:
            response = await browser.get("/api/ann-lab", params={**params, "force": 0})
            assert response.status_code == 200
            assert ("BUG-U-PRIVATE" in response.json()["codes"]) is owner

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import read_transaction, write_transaction
from app.services.search import HYBRID_SQL, hybrid_search


async def result(client, query, category="", **params):
    response = await client.get("/api/search", params={"q": query, "category": category, **params})
    assert response.status_code == 200, response.text
    return response.json()


def codes(rows):
    return [row["ticket_code"] for row in rows]


async def test_opaque_code_is_pinned(client, engine, embedder):
    data = await result(client, "E_0X7F3A")
    for name in ("lexical", "fused"):
        assert data[name][0]["ticket_code"] == "BUG-109"
        assert data[name][0]["code_hit"] is True
    assert data["query"]["codes"] == ["E_0X7F3A"]
    assert "BUG-109" not in codes(data["semantic"])
    qvec = await embedder.embed("E_0X7F3A")
    async with read_transaction(engine) as connection:
        rows = await hybrid_search(
            connection,
            q="E_0X7F3A",
            qvec=qvec,
            codes=["E_0X7F3A"],
            sid=UUID(client.cookies["tl_session"]),
            category=None,
        )
    assert next(row["vec_rank"] for row in rows if row["ticket_code"] == "BUG-109") == 16


async def test_descriptive_code_tops_all_columns(client):
    data = await result(client, "ERR_PG_POOL_TIMEOUT_504")
    for name in ("lexical", "semantic", "fused"):
        assert data[name][0]["ticket_code"] == "BUG-101"


async def test_paraphrase_uses_semantics(client):
    data = await result(client, "Mobile avatar upload screen hangs")
    assert data["lexical"] == []
    for name in ("semantic", "fused"):
        assert data[name][0]["ticket_code"] == "BUG-102"
        assert data[name][0]["cosine"] == pytest.approx(0.663, abs=0.01)


@pytest.mark.parametrize(
    "category, expected, absent, cosine",
    [
        ("BUG_REPORT", "BUG-105", "FEAT-201", 0.847),
        ("FEATURE_REQUEST", "FEAT-201", "BUG-105", 0.868),
    ],
)
async def test_category_collision_is_filtered(client, category, expected, absent, cosine):
    data = await result(client, "Android checkout dark mode", category)
    assert data["query"]["category"] == category
    for name in ("lexical", "semantic", "fused"):
        assert data[name][0]["ticket_code"] == expected
        assert absent not in codes(data[name])
    assert data["fused"][0]["cosine"] == pytest.approx(cosine, abs=0.01)


async def test_category_collision_all_ties_on_rrf(client):
    data = await result(client, "Android checkout dark mode")
    first, second = data["fused"][:2]
    assert [first["ticket_code"], second["ticket_code"]] == ["FEAT-201", "BUG-105"]
    assert first["rrf"] == second["rrf"] == pytest.approx(0.03252, abs=0.0001)
    assert first["cosine"] > second["cosine"]


async def test_enquiry_query(client):
    data = await result(client, "how to download gst invoice", "CLIENT_ENQUIRY")
    assert data["fused"][0]["ticket_code"] == "ENQ-301"
    assert data["fused"][0]["cosine"] == pytest.approx(0.896, abs=0.01)


async def test_stopwords_return_semantic_results(client):
    data = await result(client, "the and of")
    assert data["lexical"] == []
    assert len(data["semantic"]) == 10


async def test_lexical_order_survives_more_than_twenty_matches(client, engine):
    query = "checkout API times out evening sales"
    baseline = await result(client, query, "BUG_REPORT")
    assert baseline["lexical"][0]["ticket_code"] == "BUG-101"
    sid = UUID(client.cookies["tl_session"])
    async with write_transaction(engine) as connection:
        await connection.execute(
            text(
                "INSERT INTO intake_items "
                "(ticket_code, category, title, description, owner_session, "
                "embedding, embedding_model) "
                "SELECT 'BUG-U-LOW-' || n, category, 'Background observation', "
                "'checkout API evening sales', :sid, embedding, embedding_model "
                "FROM intake_items CROSS JOIN generate_series(1, 30) n "
                "WHERE ticket_code = 'BUG-101'"
            ),
            {"sid": sid},
        )
    data = await result(client, query, "BUG_REPORT")
    assert len(data["lexical"]) == 10
    assert data["lexical"][0]["ticket_code"] == baseline["lexical"][0]["ticket_code"]
    assert any(code.startswith("BUG-U-LOW-") for code in codes(data["lexical"]))
    assert [row["fts_rank"] for row in data["lexical"]] == list(range(1, 11))


@pytest.mark.parametrize(
    "params",
    [
        {"q": "checkout", "category": "BAD_CATEGORY"},
        {"q": "checkout", "category": "BUG_REPORT' OR true --"},
        {"q": "x" * 501},
        {"q": ""},
        {},
        {"q": "checkout", "explain": "2"},
        {"q": "checkout", "explain": "-1"},
    ],
)
async def test_invalid_search_parameters(client, params):
    response = await client.get("/api/search", params=params)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_telemetry_and_response_shape(client):
    data = await result(client, "Android checkout dark mode", explain=1)
    assert set(data) == {"query", "lexical", "semantic", "fused", "telemetry"}
    assert data["query"]["category"] is None
    telemetry = data["telemetry"]
    assert telemetry["embed_ms"] > 0
    assert telemetry["db_ms"] > 0
    assert telemetry["pgvector"] == "0.8.7"
    assert telemetry["model"] == "BAAI/bge-small-en-v1.5"
    assert set(telemetry["index_sizes"]) == {"intake_hnsw", "intake_tsv_gin"}
    for size in telemetry["index_sizes"].values():
        assert size["bytes"] > 0
        assert isinstance(size["pretty"], str)
    plan = telemetry["plan"]
    assert "Seq Scan" in plan["node_types"]
    assert len(plan["node_types"]) == len(set(plan["node_types"]))
    assert len(plan["index_names"]) == len(set(plan["index_names"]))
    assert plan["uses_hnsw"] is ("intake_hnsw" in plan["index_names"])
    assert plan["uses_gin"] is ("intake_tsv_gin" in plan["index_names"])
    row = data["fused"][0]
    assert set(row) == {
        "ticket_code",
        "category",
        "title",
        "error_code",
        "severity",
        "subsystem",
        "count_shown",
        "resolution_note",
        "fts_rank",
        "vec_rank",
        "code_hit",
        "rrf",
        "cosine",
    }
    assert row["cosine"] == round(row["cosine"], 4)
    assert row["rrf"] == round(row["rrf"], 4)
    assert "plan" not in (await result(client, "checkout"))["telemetry"]


async def test_search_scrubs_before_embedding(client, application, embedder):
    seen = []

    class RecordingEmbedder:
        model_name = embedder.model_name

        async def embed(self, value):
            seen.append(value)
            return await embedder.embed(value)

    application.state.embedder = RecordingEmbedder()
    data = await result(client, "checkout contact alice@example.com")
    assert data["query"]["scrubbed"] == "checkout contact [REDACTED_EMAIL]"
    assert data["query"]["codes"] == []
    assert seen == [data["query"]["scrubbed"]]
    assert "alice@example.com" not in str(data)


async def test_hybrid_query_is_the_verbatim_spec():
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[2] / "docs/SPEC.md").read_text()
    expected = (
        spec.split("## Hybrid search SQL and ANN lab")[1].split("```sql\n")[1].split("```")[0]
    )
    assert HYBRID_SQL.lstrip("\n") == expected


async def test_hybrid_rejects_category_injection(engine, embedder):
    qvec = await embedder.embed("checkout")
    async with read_transaction(engine) as connection:
        with pytest.raises(ValueError, match="category"):
            await hybrid_search(
                connection,
                q="checkout",
                qvec=qvec,
                codes=[],
                sid=uuid4(),
                category="BUG_REPORT' OR true --",
            )


async def test_error_codes_are_exact_not_like_patterns(client, engine):
    sid = uuid4()
    async with write_transaction(engine) as connection:
        await connection.execute(text("INSERT INTO sessions (id) VALUES (:sid)"), {"sid": sid})
        await connection.execute(
            text(
                "INSERT INTO intake_items "
                "(ticket_code, category, title, description, owner_session, error_code, "
                "embedding, embedding_model) "
                "SELECT 'BUG-U-CODE', category, 'Opaque identifier', 'Different code', :sid, "
                "'E_A0X7F3A', embedding, embedding_model "
                "FROM intake_items WHERE ticket_code='BUG-109'"
            ),
            {"sid": sid},
        )
    client.cookies.set("tl_session", str(sid))
    data = await result(client, "E_0X7F3A")
    assert data["fused"][0]["ticket_code"] == "BUG-109"
    for name in ("lexical", "fused"):
        other_code = [row for row in data[name] if row["ticket_code"] == "BUG-U-CODE"]
        assert other_code
        assert all(not row["code_hit"] for row in other_code)

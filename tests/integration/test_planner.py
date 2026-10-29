import json
from uuid import uuid4

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.db import read_transaction
from app.services.search import HYBRID_SQL, visibility


def plan_nodes(plan):
    if isinstance(plan, str):
        plan = json.loads(plan)
    pending = [plan[0]["Plan"]]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.get("Plans", []))


async def test_default_semantic_plan_does_not_use_hnsw(engine, embedder):
    qvec = await embedder.embed("Mobile avatar upload screen hangs")
    async with read_transaction(engine) as connection:
        plan = await connection.scalar(
            text(
                "EXPLAIN (FORMAT JSON) SELECT i.id FROM intake_items i WHERE "
                + visibility(None)
                + " ORDER BY i.embedding <=> :qvec LIMIT 20"
            ).bindparams(bindparam("qvec", type_=HALFVEC(384))),
            {"qvec": qvec, "sid": uuid4()},
        )
    nodes = list(plan_nodes(plan))
    assert any(node["Node Type"] == "Seq Scan" for node in nodes)
    assert all(node.get("Index Name") != "intake_hnsw" for node in nodes)


async def test_lexical_prefilter_can_use_gin(engine, embedder):
    qvec = await embedder.embed("Unicode")
    async with read_transaction(engine) as connection:
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await connection.scalar(
            text(
                "EXPLAIN (FORMAT JSON)\n" + HYBRID_SQL.format(visibility=visibility(None))
            ).bindparams(
                bindparam("qvec", type_=HALFVEC(384)),
                bindparam("codes", type_=ARRAY(Text())),
            ),
            {"q": "Unicode", "qvec": qvec, "sid": uuid4(), "codes": []},
        )
    assert any(node.get("Index Name") == "intake_tsv_gin" for node in plan_nodes(plan))

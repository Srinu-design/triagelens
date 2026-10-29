import json
from time import perf_counter
from typing import Literal
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import read_transaction
from app.domain.codes import extract_codes
from app.domain.pii import scrub

CategoryFilter = Literal["", "BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"]
CATEGORIES = {"BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"}
ROW_FIELDS = (
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
)

HYBRID_SQL = r"""
WITH qlex AS (                                   -- distinct stemmed terms, each also as its own tsquery
  SELECT lexeme, to_tsquery('simple', quote_literal(lexeme)) AS term_q
  FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('english', :q))) t
),
q AS (                                           -- OR-query + minimum-should-match
  SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq,
         count(*) AS n_terms,
         GREATEST(1, LEAST(4, ceil(count(*) * 0.5)))::int AS need
  FROM qlex
),
hits AS (                                        -- GIN-friendly prefilter: any query term, or an exact code
  SELECT i.id, i.search_tsv, (i.error_code = ANY(:codes)) AS code_hit
  FROM intake_items i CROSS JOIN q
  WHERE {visibility}
    AND (i.search_tsv @@ q.tsq OR i.error_code = ANY(:codes))
),
lexical AS (
  SELECT h.id,
         COALESCE(h.code_hit, false) AS code_hit,
         ts_rank_cd(h.search_tsv, q.tsq) AS fts_score,
         ROW_NUMBER() OVER (ORDER BY h.code_hit DESC NULLS LAST,
                                     ts_rank_cd(h.search_tsv, q.tsq) DESC, h.id) AS fts_rank
  FROM hits h CROSS JOIN q
  WHERE h.code_hit
     OR (SELECT count(*) FROM qlex WHERE h.search_tsv @@ qlex.term_q) >= q.need
  ORDER BY fts_rank                              -- required: LIMIT without ORDER BY is arbitrary
  LIMIT 20
),
semantic AS (
  SELECT i.id, ROW_NUMBER() OVER (ORDER BY i.embedding <=> :qvec) AS vec_rank
  FROM intake_items i
  WHERE {visibility}
  ORDER BY i.embedding <=> :qvec
  LIMIT 20
),
fused AS (
  SELECT COALESCE(l.id, s.id) AS id, l.fts_rank, s.vec_rank,
         COALESCE(l.code_hit, false) AS code_hit,
         COALESCE(1.0 / (60 + l.fts_rank), 0) + COALESCE(1.0 / (60 + s.vec_rank), 0) AS rrf
  FROM lexical l FULL OUTER JOIN semantic s ON s.id = l.id
)
SELECT i.id, i.ticket_code, i.category, i.title, i.error_code, i.severity, i.subsystem,
       i.resolution_note,
       i.base_count + CASE i.category
         WHEN 'BUG_REPORT' THEN (SELECT count(*) FROM intake_items d
                                 WHERE d.duplicate_of = i.id AND d.owner_session = :sid)
         WHEN 'FEATURE_REQUEST' THEN (SELECT count(*) FROM votes v
                                      WHERE v.item_id = i.id AND v.session_id = :sid)
         ELSE 0 END AS count_shown,
       f.fts_rank, f.vec_rank, f.code_hit, f.rrf,
       1 - (i.embedding <=> :qvec) AS cosine
FROM fused f JOIN intake_items i ON i.id = f.id
ORDER BY f.code_hit DESC, f.rrf DESC, cosine DESC;
"""

ANN_SQL = """SELECT i.ticket_code
FROM intake_items i
WHERE {visibility}
ORDER BY i.embedding <=> :qvec
LIMIT 5"""


def visibility(category: str | None) -> str:
    if category is not None and category not in CATEGORIES:
        raise ValueError("Invalid search category")
    clause = "i.duplicate_of IS NULL AND (i.owner_session IS NULL OR i.owner_session = :sid)"
    if category is not None:
        clause += " AND i.category = :cat"
    return clause


def _hybrid_statement(category: str | None, *, explain: bool = False):
    sql = HYBRID_SQL.format(visibility=visibility(category))
    if explain:
        sql = "EXPLAIN (FORMAT JSON)\n" + sql
    return text(sql).bindparams(
        bindparam("qvec", type_=HALFVEC(384)),
        bindparam("codes", type_=ARRAY(Text())),
    )


async def hybrid_search(
    conn: AsyncConnection,
    *,
    q: str,
    qvec: list[float],
    codes: list[str],
    sid: UUID,
    category: str | None,
) -> list:
    """Run the reference SQL; the caller supplies a read-only transaction and scrubbed text."""
    statement = _hybrid_statement(category)
    await conn.execute(text("SET LOCAL hnsw.ef_search = 40"))
    await conn.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    result = await conn.execute(
        statement, {"q": q, "qvec": qvec, "codes": codes, "sid": sid, "cat": category}
    )
    return list(result.mappings().all())


def _plan_nodes(plan) -> list[dict]:
    if isinstance(plan, str):
        plan = json.loads(plan)
    pending = [plan[0]["Plan"]]
    nodes = []
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("Plans", []))
    return nodes


def _plan_summary(nodes: list[dict]) -> dict:
    indexes = sorted({node["Index Name"] for node in nodes if "Index Name" in node})
    # DECISION: Stable plan keys expose distinct node/index names and explicit HNSW/GIN flags.
    return {
        "node_types": sorted({node["Node Type"] for node in nodes}),
        "index_names": indexes,
        "uses_hnsw": "intake_hnsw" in indexes,
        "uses_gin": "intake_tsv_gin" in indexes,
    }


async def search(app_state, *, q: str, sid: UUID, category: str | None, explain: bool) -> dict:
    scrubbed, _ = scrub(q)
    codes = extract_codes(scrubbed)
    started = perf_counter()
    qvec = await app_state.embedder.embed(scrubbed)
    embed_ms = (perf_counter() - started) * 1000

    async with read_transaction(app_state.engine) as conn:
        started = perf_counter()
        rows = await hybrid_search(
            conn, q=scrubbed, qvec=qvec, codes=codes, sid=sid, category=category
        )
        db_ms = (perf_counter() - started) * 1000
        version = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        sizes = (
            (
                await conn.execute(
                    text(
                        "SELECT name, pg_relation_size(name::regclass) AS bytes, "
                        "pg_size_pretty(pg_relation_size(name::regclass)) AS pretty "
                        "FROM (VALUES ('intake_hnsw'), ('intake_tsv_gin')) AS indexes(name)"
                    )
                )
            )
            .mappings()
            .all()
        )
        # DECISION: Index sizes are keyed by index name, each with bytes and pretty fields.
        telemetry = {
            "embed_ms": round(embed_ms, 4),
            "db_ms": round(db_ms, 4),
            "pgvector": version,
            "model": app_state.embedder.model_name,
            "index_sizes": {
                row["name"]: {"bytes": row["bytes"], "pretty": row["pretty"]} for row in sizes
            },
        }
        if explain:
            plan = await conn.scalar(
                _hybrid_statement(category, explain=True),
                {"q": scrubbed, "qvec": qvec, "codes": codes, "sid": sid, "cat": category},
            )
            telemetry["plan"] = _plan_summary(_plan_nodes(plan))

    items = [{field: row[field] for field in ROW_FIELDS} for row in rows]
    for item in items:
        item["cosine"] = round(float(item["cosine"]), 4)
        item["rrf"] = round(float(item["rrf"]), 4)
    lexical = sorted(
        (row for row in items if row["fts_rank"] is not None), key=lambda row: row["fts_rank"]
    )
    semantic = sorted(
        (row for row in items if row["vec_rank"] is not None), key=lambda row: row["vec_rank"]
    )
    return {
        "query": {"scrubbed": scrubbed, "codes": codes, "category": category},
        "lexical": lexical[:10],
        "semantic": semantic[:10],
        "fused": items[:10],
        "telemetry": telemetry,
    }


async def ann_lab(
    app_state,
    *,
    q: str,
    sid: UUID,
    category: str | None,
    force: int,
    ef_search: int,
    iterative: str,
) -> dict:
    if (
        force not in {0, 1}
        or ef_search not in {5, 10, 40}
        or iterative not in {"off", "strict_order", "relaxed_order"}
    ):
        raise ValueError("Invalid ANN settings")
    clause = visibility(category)
    scrubbed, _ = scrub(q)
    qvec = await app_state.embedder.embed(scrubbed)
    sql = ANN_SQL.format(visibility=clause)
    parameters = {"qvec": qvec, "sid": sid, "cat": category}
    async with read_transaction(app_state.engine) as conn:
        await conn.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
        await conn.execute(text(f"SET LOCAL hnsw.iterative_scan = {iterative}"))
        if force:
            await conn.execute(text("SET LOCAL enable_seqscan = off"))
            await conn.execute(text("SET LOCAL enable_bitmapscan = off"))
            await conn.execute(text("SET LOCAL enable_sort = off"))
        plan = await conn.scalar(
            text("EXPLAIN (FORMAT JSON)\n" + sql).bindparams(bindparam("qvec", type_=HALFVEC(384))),
            parameters,
        )
        codes = list(
            (
                await conn.execute(
                    text(sql).bindparams(bindparam("qvec", type_=HALFVEC(384))), parameters
                )
            ).scalars()
        )
    nodes = _plan_nodes(plan)
    return {
        "rows_returned": len(codes),
        "codes": codes,
        "index_scan": any(
            node["Node Type"] == "Index Scan" and node.get("Index Name") == "intake_hnsw"
            for node in nodes
        ),
        "plan_nodes": sorted({node["Node Type"] for node in nodes}),
        "settings": {"force": force, "ef_search": ef_search, "iterative": iterative},
    }

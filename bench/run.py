import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
from pathlib import Path
from time import perf_counter
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import BigInteger, Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.config import Settings
from app.db import create_engine, read_transaction
from app.domain.codes import extract_codes
from app.domain.pii import scrub
from app.services.embeddings import embed_ticket_text, make_embedder
from app.services.search import HYBRID_SQL, hybrid_search, visibility
from bench.common import (
    BUILD_PATH,
    INDEXES,
    RESULTS_PATH,
    build_tickets,
    database_url,
    percentile,
    recall_at_10,
)

WARMUPS = 50
SID = UUID("00000000-0000-4000-8000-000000000007")


async def timed_ids(connection, statement, parameters):
    started = perf_counter()
    rows = list((await connection.execute(statement, parameters)).scalars())
    return rows, (perf_counter() - started) * 1000


def nearest_sql(column: str, *, filtered: bool = False, with_distances: bool = False):
    if column not in {"emb_f32", "emb_f16"}:
        raise ValueError("Invalid embedding column")
    where = " WHERE category = 'CLIENT_ENQUIRY'" if filtered else ""
    columns = f"id, {column} <=> :qvec AS distance" if with_distances else "id"
    return text(f"SELECT {columns} FROM bench_items{where} ORDER BY {column} <=> :qvec LIMIT 10")


async def ground_truth(engine, vectors, *, filtered=False):
    async with read_transaction(engine) as connection:
        await connection.execute(text("SET LOCAL enable_indexscan = off"))
        await connection.execute(text("SET LOCAL enable_bitmapscan = off"))
        statement = nearest_sql("emb_f32", filtered=filtered, with_distances=True)
        truth = []
        for vector in vectors:
            rows = (await connection.execute(statement, {"qvec": vector})).all()
            if len(rows) != 10:
                raise ValueError("Recall@10 requires at least ten eligible benchmark rows")
            truth.append(
                {
                    "ids": [row.id for row in rows],
                    "distances": [float(row.distance) for row in rows],
                    "d10": float(rows[9].distance),
                }
            )
        return truth


async def ann_trial(
    engine, vectors, truth, *, index_name, column, filtered, ef_search, iterative, drop=()
):
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # DECISION: Transactional index drops isolate each setup; rollback restores all indexes.
            for name in drop:
                if name not in INDEXES:
                    raise ValueError("Invalid benchmark index")
                await connection.execute(text(f"DROP INDEX {name}"))
            for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
                await connection.execute(text(f"SET LOCAL {setting} = off"))
            await connection.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
            await connection.execute(text(f"SET LOCAL hnsw.iterative_scan = {iterative}"))
            statement = nearest_sql(column, filtered=filtered)
            plan = await connection.scalar(
                text("EXPLAIN (FORMAT JSON) " + statement.text), {"qvec": vectors[0]}
            )
            if isinstance(plan, str):
                plan = json.loads(plan)
            if not any(node.get("Index Name") == index_name for node in walk_plan(plan[0]["Plan"])):
                raise RuntimeError(f"Planner did not select requested index {index_name}")
            for i in range(WARMUPS):
                await connection.execute(statement, {"qvec": vectors[i % len(vectors)]})
            times, scores, counts = [], [], []
            distances = text(
                "SELECT emb_f32 <=> :qvec FROM bench_items WHERE id = ANY(:ids)"
            ).bindparams(bindparam("ids", type_=ARRAY(BigInteger())))
            for vector, expected in zip(vectors, truth, strict=True):
                actual, elapsed = await timed_ids(connection, statement, {"qvec": vector})
                times.append(elapsed)
                exact_distances = list(
                    (await connection.execute(distances, {"qvec": vector, "ids": actual})).scalars()
                )
                scores.append(recall_at_10(exact_distances, expected["d10"]))
                counts.append(len(actual))
            return {
                "recall": sum(scores) / len(scores),
                "rows": sum(counts) / len(counts),
                "p50": percentile(times, 50),
                "p95": percentile(times, 95),
            }
        finally:
            await transaction.rollback()


class TimedHybridConnection:
    def __init__(self, connection):
        self.connection = connection
        self.elapsed_ms = 0.0

    async def execute(self, statement, parameters=None):
        if statement.text.startswith("SET LOCAL"):
            return await self.connection.execute(statement, parameters)
        # DECISION: Async SQLAlchemy execute buffers rows; time it without SET LOCAL round trips.
        started = perf_counter()
        result = await self.connection.execute(statement, parameters)
        self.elapsed_ms = (perf_counter() - started) * 1000
        return result


def walk_plan(root):
    yield root
    for child in root.get("Plans", []):
        yield from walk_plan(child)


async def hybrid_trial(engine, queries, vectors, category):
    async with read_transaction(engine) as connection:
        for i in range(WARMUPS):
            query, vector = queries[i % len(queries)], vectors[i % len(vectors)]
            await hybrid_search(
                connection,
                q=query,
                qvec=vector,
                codes=extract_codes(query),
                sid=SID,
                category=category,
            )
        timer = TimedHybridConnection(connection)
        times = []
        for query, vector in zip(queries, vectors, strict=True):
            await hybrid_search(
                timer, q=query, qvec=vector, codes=extract_codes(query), sid=SID, category=category
            )
            times.append(timer.elapsed_ms)
        plan = await connection.scalar(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)\n"
                + HYBRID_SQL.format(visibility=visibility(category))
            ).bindparams(
                bindparam("qvec", type_=HALFVEC(384)),
                bindparam("codes", type_=ARRAY(Text())),
            ),
            {
                "q": queries[0],
                "qvec": vectors[0],
                "codes": extract_codes(queries[0]),
                "sid": SID,
                "cat": category,
            },
        )
    if isinstance(plan, str):
        plan = json.loads(plan)
    roots = [
        node
        for node in walk_plan(plan[0]["Plan"])
        if node.get("Alias") == "l" or node.get("Subplan Name") in {"CTE lexical", "CTE hits"}
    ]
    lexical_nodes = [node for root in roots for node in walk_plan(root)]
    uses_gin = any(node.get("Index Name") == "intake_tsv_gin" for node in lexical_nodes)
    seq_scan = any(
        node["Node Type"] == "Seq Scan" and node.get("Relation Name") == "intake_items"
        for node in lexical_nodes
    )
    if not roots:
        access = "unidentified lexical subtree (see plan)"
    elif seq_scan:
        access = "Seq Scan on intake_items" + ("; GIN also present" if uses_gin else "")
    elif uses_gin:
        access = "GIN intake_tsv_gin"
    else:
        access = "other access (see plan)"
    return {
        "p50": percentile(times, 50),
        "p95": percentile(times, 95),
        "access": access,
        "uses_gin": uses_gin,
        "seq_scan": seq_scan,
        "plan": plan,
    }


def machine_info():
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        platform.processor(),
    )
    memory_kib = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    return {
        "CPU": cpu,
        "Cores": os.cpu_count(),
        "RAM GiB": round(memory_kib / 1024**2, 2),
        "OS": platform.platform(),
    }


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    return [
        "| " + " | ".join(map(cell, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(map(cell, row)) + " |" for row in rows],
        "",
    ]


async def run(queries_count: int, seed: int):
    settings = Settings()
    if settings.embedding_backend != "fastembed":
        raise ValueError("Benchmarks require the real fastembed model")
    # DECISION: Fresh connections keep cached ground-truth plans out of ANN trials.
    engine = create_engine(
        settings.model_copy(update={"database_url": database_url("BENCH_DATABASE_URL")}),
        null_pool=True,
    )
    app_engine = create_engine(
        settings.model_copy(update={"database_url": database_url("BENCH_APP_DATABASE_URL")}),
        null_pool=True,
    )
    try:
        async with read_transaction(engine) as connection:
            n = await connection.scalar(text("SELECT count(*) FROM bench_items"))
            if n < 10:
                raise ValueError("Generate at least ten benchmark rows first")
            pg = await connection.scalar(text("SHOW server_version"))
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            sizes = [
                (
                    name,
                    await connection.scalar(
                        text("SELECT pg_relation_size(CAST(:name AS regclass))"), {"name": name}
                    ),
                )
                for name in INDEXES
            ]
        async with read_transaction(app_engine) as connection:
            app_n = await connection.scalar(text("SELECT count(*) FROM intake_items"))
            distinct_texts = await connection.scalar(
                text("SELECT count(DISTINCT (title, description)) FROM intake_items")
            )
            owned = await connection.scalar(
                text("SELECT count(*) FROM intake_items WHERE owner_session = :sid"), {"sid": SID}
            )
            if app_n != n or owned:
                raise ValueError(
                    "Bench databases must contain matching generated rows and no SID-owned rows"
                )
        build = json.loads(BUILD_PATH.read_text()) if BUILD_PATH.exists() else {}
        tickets = build_tickets(queries_count, seed, queries=True)
        # DECISION: Query codes are appended to query text so the exact-code branch runs.
        queries = [
            scrub(
                embed_ticket_text(item.title, item.description)
                + (f"\n{item.error_code}" if item.error_code else "")
            )[0]
            for item in tickets
        ]
        embedder = make_embedder(settings)
        for i in range(WARMUPS):
            await embedder.embed(queries[i % len(queries)])
        vectors, embed_times = [], []
        for query in queries:
            started = perf_counter()
            vectors.append(await embedder.embed(query))
            embed_times.append((perf_counter() - started) * 1000)
        truth = await ground_truth(engine, vectors)
        filtered_truth = await ground_truth(engine, vectors, filtered=True)
        ann_results, filtered_results = [], []
        for effort in (40, 100):
            for name, column, filtered in [
                (INDEXES[0], "emb_f32", False),
                (INDEXES[1], "emb_f16", False),
                (INDEXES[2], "emb_f16", True),
            ]:
                result = await ann_trial(
                    engine,
                    vectors,
                    filtered_truth if filtered else truth,
                    index_name=name,
                    column=column,
                    filtered=filtered,
                    ef_search=effort,
                    iterative="off",
                    drop=(INDEXES[1],) if filtered else (),
                )
                ann_results.append((name, effort, "Enquiries" if filtered else "All", result))
                print(f"ANN {name} ef={effort}: {result}", flush=True)
            for iterative in ("off", "strict_order", "relaxed_order", "partial"):
                partial = iterative == "partial"
                result = await ann_trial(
                    engine,
                    vectors,
                    filtered_truth,
                    index_name=INDEXES[2] if partial else INDEXES[1],
                    column="emb_f16",
                    filtered=True,
                    ef_search=effort,
                    iterative="off" if partial else iterative,
                    drop=(INDEXES[1],) if partial else (INDEXES[2],),
                )
                filtered_results.append((iterative, effort, result))
                print(f"Filtered {iterative} ef={effort}: {result}", flush=True)
        hybrid_results = []
        for category in (None, "CLIENT_ENQUIRY"):
            result = await hybrid_trial(app_engine, queries, vectors, category)
            hybrid_results.append((category or "All", result))
            print(
                f"Hybrid {category or 'All'}: p50={result['p50']:.3f} p95={result['p95']:.3f}; "
                f"lexical={result['access']}",
                flush=True,
            )

        lines = [
            "Tickets are template-generated (`generate.py --seed 42`, queries `run.py --seed 7`). "
            "Recall and latency may not transfer to real ticket text.",
            "",
            # DECISION: Count tied neighbors by exact f32 distance, independent of returned IDs.
            f"Distinct ticket texts: {distinct_texts}. Recall@10 is distance-based because of ties "
            "(exact emb_f32 distance <= d10 + 1e-6); distance lookups are untimed.",
            "",
            "## Machine",
            "",
            *[f"- {key}: {value}" for key, value in machine_info().items()],
            "",
            "## Versions",
            "",
            f"- PostgreSQL: {pg}",
            f"- pgvector: {vector_version}",
            f"- fastembed: {importlib.metadata.version('fastembed')}",
            f"- Model: {embedder.model_name}",
            "",
            "## Parameters",
            "",
            f"N={n}; queries={queries_count}; generation seed={build.get('seed', 'unknown')}; "
            f"query seed={seed}; batch=256; m=16; ef_construction=64; ef_search=40/100; "
            f"warm-ups={WARMUPS} per shape; neighbors=10 (hybrid top-20 branches).",
            "",
            "Timers exclude embedding, SET LOCAL, transaction setup and index isolation "
            "from SQL latency. Ground truth disables index/bitmap scans on emb_f32. "
            "Partial-index recall uses exact filtered truth. "
            "All query tickets are evaluated under the enquiry filter. "
            "Recall accepts every returned row within the exact tenth-neighbor distance tolerance.",
            "",
            "## Build",
            "",
        ]
        lines += table(
            ["Rows", "Load seconds", "Rows/s"],
            [[n, f"{build.get('load_seconds', 0):.3f}", f"{build.get('rows_per_second', 0):.1f}"]],
        )
        lines += table(
            ["Index", "Bytes", "Build seconds"],
            [
                [name, size, f"{build.get('index_build_seconds', {}).get(name, 0):.3f}"]
                for name, size in sizes
            ],
        )
        lines += ["## ANN search", ""]
        lines += table(
            ["Index", "ef_search", "Scope", "Recall@10", "p50 ms", "p95 ms"],
            [
                [name, effort, scope, f"{r['recall']:.4f}", f"{r['p50']:.3f}", f"{r['p95']:.3f}"]
                for name, effort, scope, r in ann_results
            ],
        )
        lines += ["## Filtered enquiries", ""]
        lines += table(
            ["Mode", "ef_search", "Average rows", "Recall@10", "p95 ms"],
            [
                [mode, effort, f"{r['rows']:.2f}", f"{r['recall']:.4f}", f"{r['p95']:.3f}"]
                for mode, effort, r in filtered_results
            ],
        )
        lines += ["## Full hybrid query", ""]
        lines += table(
            [
                "Category",
                "SQL p50 ms",
                "SQL p95 ms",
                "Embed p50 ms",
                "Embed p95 ms",
                "Lexical plan",
            ],
            [
                [
                    category,
                    f"{r['p50']:.3f}",
                    f"{r['p95']:.3f}",
                    f"{percentile(embed_times, 50):.3f}",
                    f"{percentile(embed_times, 95):.3f}",
                    r["access"],
                ]
                for category, r in hybrid_results
            ],
        )
        for category, result in hybrid_results:
            lines += [
                f"### EXPLAIN (ANALYZE, BUFFERS): {category}",
                "",
                "```json",
                json.dumps(result["plan"], indent=2),
                "```",
                "",
            ]
        lines += ["## EC2 memory", ""]
        RESULTS_PATH.write_text("\n".join(lines))
        print(f"Wrote {RESULTS_PATH}")
    finally:
        await engine.dispose()
        await app_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Measure TriageLens benchmark indexes and hybrid SQL"
    )
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    arguments = parser.parse_args()
    if arguments.queries < 1:
        parser.error("--queries must be positive")
    asyncio.run(run(arguments.queries, arguments.seed))

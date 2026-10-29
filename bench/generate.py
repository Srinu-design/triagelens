import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import text

from app.config import Settings
from app.db import create_engine
from app.domain.pii import scrub
from app.domain.rules import classify
from app.services.embeddings import embed_ticket_text, make_embedder
from bench.common import BUILD_PATH, INDEXES, ROOT, build_tickets, database_url


async def generate(rows: int, seed: int):
    settings = Settings()
    if settings.embedding_backend != "fastembed":
        raise ValueError("Benchmarks require the real fastembed model")
    bench_url, app_url = database_url("BENCH_DATABASE_URL"), database_url("BENCH_APP_DATABASE_URL")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": app_url},
        check=True,
    )
    engine = create_engine(settings.model_copy(update={"database_url": bench_url}))
    app_engine = create_engine(settings.model_copy(update={"database_url": app_url}))
    embedder = make_embedder(settings)
    try:
        old = json.loads(BUILD_PATH.read_text()) if BUILD_PATH.exists() else {}
        async with engine.connect() as conn:
            exists = await conn.scalar(text("SELECT to_regclass('bench_items') IS NOT NULL"))
            count = await conn.scalar(text("SELECT count(*) FROM bench_items")) if exists else 0
        async with app_engine.connect() as conn:
            app_count = await conn.scalar(text("SELECT count(*) FROM intake_items"))
        if (
            count == app_count == rows
            and old.get("seed") == seed
            and old.get("model") == embedder.model_name
        ):
            print(f"Reusing {rows} rows with the same seed/model and existing build statistics.")
            return
        # DECISION: Recreate on row-count/seed/model changes; identical completed builds are reused.
        BUILD_PATH.unlink(missing_ok=True)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS bench_items"))
            await conn.execute(
                text(
                    "CREATE TABLE bench_items (id BIGINT, category TEXT, "
                    "emb_f32 vector(384), emb_f16 halfvec(384))"
                )
            )
        async with app_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE idempotency_keys, votes, intake_items, sessions "
                    "RESTART IDENTITY CASCADE"
                )
            )
        tickets = build_tickets(rows, seed)
        started = perf_counter()
        for offset in range(0, rows, 256):
            batch = tickets[offset : offset + 256]
            clean = [(scrub(item.title)[0], scrub(item.description)[0]) for item in batch]
            vectors = await embedder.embed_many([embed_ticket_text(*pair) for pair in clean])
            benchmark_rows, app_rows = [], []
            for item, (title, description), vector in zip(batch, clean, vectors, strict=True):
                labels = classify(title, description)
                benchmark_rows.append(
                    {
                        "id": item.id,
                        "category": item.category,
                        "vector32": vector,
                        "vector16": vector,
                    }
                )
                prefix = {"BUG_REPORT": "BUG", "FEATURE_REQUEST": "FEAT", "CLIENT_ENQUIRY": "ENQ"}
                app_rows.append(
                    {
                        "code": f"{prefix[item.category]}-B{item.id}",
                        "category": item.category,
                        "title": title,
                        "description": description,
                        "error_code": item.error_code,
                        "severity": labels["severity"],
                        "subsystem": labels["subsystem"],
                        "vector": vector,
                        "model": embedder.model_name,
                    }
                )
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO bench_items (id, category, emb_f32, emb_f16) "
                        "VALUES (:id, :category, :vector32, :vector16)"
                    ),
                    benchmark_rows,
                )
            async with app_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO intake_items (ticket_code, category, title, description, "
                        "error_code, severity, subsystem, embedding, embedding_model, "
                        "owner_session, status, base_count) VALUES "
                        "(:code, :category, :title, :description, :error_code, "
                        ":severity, :subsystem, :vector, :model, NULL, 'OPEN', 0)"
                    ),
                    app_rows,
                )
            done = offset + len(batch)
            print(
                f"Loaded {done}/{rows}: {done / (perf_counter() - started):.1f} rows/s", flush=True
            )
        load_seconds = perf_counter() - started
        builds = {}
        for name, column, operator, where in [
            (INDEXES[0], "emb_f32", "vector_cosine_ops", ""),
            (INDEXES[1], "emb_f16", "halfvec_cosine_ops", ""),
            (INDEXES[2], "emb_f16", "halfvec_cosine_ops", " WHERE category = 'CLIENT_ENQUIRY'"),
        ]:
            started = perf_counter()
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        f"CREATE INDEX {name} ON bench_items USING hnsw ({column} {operator}) "
                        f"WITH (m = 16, ef_construction = 64){where}"
                    )
                )
            builds[name] = perf_counter() - started
            print(f"Built {name}: {builds[name]:.3f} s", flush=True)
        for target, table in [(engine, "bench_items"), (app_engine, "intake_items")]:
            async with target.begin() as conn:
                await conn.execute(text(f"ANALYZE {table}"))
        stats = {
            "rows": rows,
            "seed": seed,
            "model": embedder.model_name,
            "generated_at": datetime.now(UTC).isoformat(),
            "batch_size": 256,
            "load_seconds": load_seconds,
            "rows_per_second": rows / load_seconds,
            "index_build_seconds": builds,
        }
        BUILD_PATH.write_text(json.dumps(stats, indent=2) + "\n")
        print(f"Load throughput: {stats['rows_per_second']:.1f} rows/s")
    finally:
        await engine.dispose()
        await app_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build isolated TriageLens benchmark databases")
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=42)
    arguments = parser.parse_args()
    if arguments.rows < 1:
        parser.error("--rows must be positive")
    asyncio.run(generate(arguments.rows, arguments.seed))

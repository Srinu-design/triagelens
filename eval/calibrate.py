import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import text

from app.config import Settings
from app.db import create_engine, read_transaction
from app.domain.codes import extract_codes
from app.domain.decision import Candidate, decide
from app.domain.pii import scrub
from app.services.embeddings import embed_ticket_text, make_embedder
from app.services.search import hybrid_search
from eval.code_cases import CODE_CASES
from eval.labeled_set import EVAL

GRID = ((0.80, 0.70), (0.78, 0.70), (0.75, 0.70), (0.735, 0.735))
EXPECTED_CODE_RESULTS = (
    ("DUPLICATE", "BUG-109"),
    ("DUPLICATE", "BUG-101"),
    ("DUPLICATE", "BUG-103"),
    ("REVIEW", "BUG-112"),
    ("REVIEW", "BUG-101"),
    ("REVIEW", "BUG-109"),
)
REPORT_PATH = Path(__file__).with_name("report.json")


def _counts(ranked, t_auto, t_review) -> dict:
    return {
        "auto_correct": sum(row["cosine"] >= t_auto and row["correct"] for row in ranked),
        "auto_wrong": sum(row["cosine"] >= t_auto and not row["correct"] for row in ranked),
        "review": sum(t_review <= row["cosine"] < t_auto for row in ranked),
        "missed": sum(
            row["truth"] is not None and (row["cosine"] < t_review or not row["correct"])
            for row in ranked
        ),
    }


async def run_calibration(engine, embedder, t_auto, t_review) -> dict:
    async with read_transaction(engine) as connection:
        seeds = (
            (
                await connection.execute(
                    text(
                        "SELECT ticket_code, category, embedding, embedding_model "
                        "FROM intake_items "
                        "WHERE owner_session IS NULL AND duplicate_of IS NULL ORDER BY id"
                    ).columns(embedding=HALFVEC(384))
                )
            )
            .mappings()
            .all()
        )
    if not seeds:
        raise ValueError("Seed the database before calibration.")
    # DECISION: Refuse mixed-model comparisons rather than publish meaningless calibration numbers.
    if any(row["embedding_model"] != embedder.model_name for row in seeds):
        raise ValueError("Seed embeddings use another model; re-embed before calibration.")

    cleaned = [
        (scrub(title)[0], scrub(description)[0], category, truth)
        for title, description, category, truth in EVAL
    ]
    queries = np.asarray(
        await embedder.embed_many(
            [embed_ticket_text(title, description) for title, description, _, _ in cleaned]
        ),
        dtype=np.float64,
    )
    vectors = np.asarray([row["embedding"] for row in seeds], dtype=np.float64)
    similarities = (queries @ vectors.T) / (
        np.linalg.norm(queries, axis=1)[:, None] * np.linalg.norm(vectors, axis=1)[None, :]
    )
    ranked = []
    for index, (title, _, category, truth) in enumerate(cleaned):
        eligible = [i for i, row in enumerate(seeds) if row["category"] == category]
        best = max(eligible, key=lambda i: similarities[index, i])
        closest = seeds[best]["ticket_code"]
        ranked.append(
            {
                "title": title,
                "truth": truth,
                "closest": closest,
                "cosine": float(similarities[index, best]),
                "correct": truth == closest,
            }
        )
    lookalike = max((row for row in ranked if not row["correct"]), key=lambda row: row["cosine"])
    weakest = min((row for row in ranked if row["correct"]), key=lambda row: row["cosine"])

    code_results = []
    sid = uuid4()
    async with read_transaction(engine) as connection:
        for case, expected in zip(CODE_CASES, EXPECTED_CODE_RESULTS, strict=True):
            title, description, category, truth = case
            clean_title, _ = scrub(title)
            clean_description, _ = scrub(description)
            query = embed_ticket_text(clean_title, clean_description)
            codes = extract_codes(query)
            qvec = await embedder.embed(query)
            rows = await hybrid_search(
                connection, q=query, qvec=qvec, codes=codes, sid=sid, category=category
            )
            decision = decide(
                [
                    Candidate(row["ticket_code"], float(row["cosine"]), row["code_hit"])
                    for row in rows
                ],
                t_auto=t_auto,
                t_review=t_review,
            )
            verdict, master = expected
            code_results.append(
                {
                    "title": clean_title,
                    "truth": truth or "a different bug",
                    "closest": decision["closest"],
                    "cosine": decision["cosine"],
                    "verdict": decision["verdict"],
                    "expected_verdict": verdict,
                    "expected_master": master,
                    "pass": decision["verdict"] == verdict and decision["master"] == master,
                }
            )

    duplicates = sum(truth is not None for _, _, _, truth in EVAL)
    return {
        "model": embedder.model_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "in_sample": True,
        "labeled_set": {
            "tickets": len(EVAL),
            "duplicates": duplicates,
            "new": len(EVAL) - duplicates,
        },
        "thresholds": {"t_auto": t_auto, "t_review": t_review},
        "configured": _counts(ranked, t_auto, t_review),
        "grid": [
            {"t_auto": auto, "t_review": review, **_counts(ranked, auto, review)}
            for auto, review in GRID
        ],
        "margin": {
            "closest_lookalike": {
                "title": lookalike["title"],
                "cosine": round(lookalike["cosine"], 4),
            },
            "weakest_duplicate": {"title": weakest["title"], "cosine": round(weakest["cosine"], 4)},
        },
        "top1": {"correct": sum(row["correct"] for row in ranked), "total": duplicates},
        "code_cases": code_results,
    }


def print_report(report):
    print("Calibration (in-sample)")
    print("Auto >= | Review >= | Auto-correct | Auto-wrong | Review | Missed")
    for row in report["grid"]:
        print(
            f"{row['t_auto']:.3f} | {row['t_review']:.3f} | {row['auto_correct']} | "
            f"{row['auto_wrong']} | {row['review']} | {row['missed']}"
        )
    for name, row in report["margin"].items():
        print(f"{name}: {row['cosine']:.4f} — {row['title']}")
    print(f"Top-1 duplicates: {report['top1']['correct']} / {report['top1']['total']}")
    print("Ticket | Truth | Closest (cosine) | Verdict | Pass")
    for row in report["code_cases"]:
        print(
            f"{row['title']} | {row['truth']} | {row['closest']} ({row['cosine']:.3f}) | "
            f"{row['verdict']} | {row['pass']}"
        )


async def main() -> int:
    settings = Settings()
    engine = create_engine(settings)
    try:
        report = await run_calibration(
            engine, make_embedder(settings), settings.dup_t_auto, settings.dup_t_review
        )
        print_report(report)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
        return int(
            report["configured"]["auto_wrong"] > 0
            or not all(row["pass"] for row in report["code_cases"])
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

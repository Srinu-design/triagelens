import logging
from collections import Counter
from time import perf_counter
from uuid import UUID

from app.db import read_transaction
from app.domain.codes import extract_codes
from app.domain.decision import Candidate, decide
from app.domain.pii import scrub
from app.schemas import PreviewIn
from app.services import search
from app.services.embeddings import embed_ticket_text

logger = logging.getLogger(__name__)

CANDIDATE_FIELDS = (
    "ticket_code",
    "title",
    "category",
    "cosine",
    "fts_rank",
    "vec_rank",
    "code_hit",
    "count_shown",
    "resolution_note",
)


async def preview(app_state, sid: UUID, body: PreviewIn, *, allow_llm: bool = True) -> dict:
    title, title_counts = scrub(body.title)
    description, description_counts = scrub(body.description)
    counts = dict(Counter(title_counts) + Counter(description_counts))
    total = sum(counts.values())
    # DECISION: Search q uses the same scrubbed title-newline-description text as embedding.
    query = embed_ticket_text(title, description)
    # DECISION: The explicit error code keeps the first slot when limiting to five unique codes.
    explicit_codes = [body.error_code] if body.error_code else []
    codes = list(dict.fromkeys(explicit_codes + extract_codes(query)))[:5]

    suggestion, source = await app_state.llm.suggest(title, description, allow_llm=allow_llm)
    category = body.category_override or suggestion.category
    started = perf_counter()
    qvec = await app_state.embedder.embed(query)
    embed_ms = (perf_counter() - started) * 1000
    async with read_transaction(app_state.engine) as connection:
        started = perf_counter()
        candidates = await search.hybrid_search(
            connection, q=query, qvec=qvec, codes=codes, sid=sid, category=category
        )
        all_categories = await search.hybrid_search(
            connection, q=query, qvec=qvec, codes=codes, sid=sid, category=None
        )
        db_ms = (perf_counter() - started) * 1000

    decision = decide(
        [
            Candidate(row["ticket_code"], float(row["cosine"]), row["code_hit"])
            for row in candidates
        ],
        t_auto=app_state.settings.dup_t_auto,
        t_review=app_state.settings.dup_t_review,
    )
    related_row = max(
        (row for row in all_categories if row["category"] != category),
        key=lambda row: row["cosine"],
        default=None,
    )
    related = None
    if related_row is not None:
        related = {field: related_row[field] for field in ("ticket_code", "category", "title")}
        related["cosine"] = round(float(related_row["cosine"]), 4)
    displayed = [{field: row[field] for field in CANDIDATE_FIELDS} for row in candidates[:5]]
    for row in displayed:
        row["cosine"] = round(float(row["cosine"]), 4)

    # DECISION: In production, request logs are handled exclusively by RequestLogMiddleware.
    # We retain this info log only when explicitly enabled (e.g. test_preview caplog).
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "preview",
            extra={
                "title_length": len(title),
                "description_length": len(description),
                "redaction_total": total,
                "verdict": decision["verdict"],
                "classifier_source": source,
            },
        )

    return {
        "scrubbed": {"title": title, "description": description, "counts": counts, "total": total},
        "suggestion": suggestion.model_dump() | {"source": source},
        "search_category": category,
        "candidates": displayed,
        "related": related,
        "decision": decision,
        "telemetry": {"embed_ms": round(embed_ms, 4), "db_ms": round(db_ms, 4)},
    }

import hashlib
import json
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import read_transaction, write_transaction
from app.domain import pii, rules
from app.errors import ApiError
from app.schemas import ItemIn
from app.services.embeddings import Embedder, embed_ticket_text

TICKET_PREFIXES = {"BUG_REPORT": "BUG-U", "FEATURE_REQUEST": "FEAT-U", "CLIENT_ENQUIRY": "ENQ-U"}


async def compute_count_shown(
    conn: AsyncConnection,
    item_id: int,
    category: str,
    base_count: int,
    session_id: UUID,
) -> int:
    query = text(
        """
        SELECT :base_count + CASE :category
          WHEN 'BUG_REPORT' THEN (
              SELECT count(*) FROM intake_items d
              WHERE d.duplicate_of = :item_id AND d.owner_session = :sid
          )
          WHEN 'FEATURE_REQUEST' THEN (
              SELECT count(*) FROM votes v
              WHERE v.item_id = :item_id AND v.session_id = :sid
          )
          ELSE 0 END
        """
    )
    val = await conn.scalar(
        query,
        {
            "base_count": base_count,
            "category": category,
            "item_id": item_id,
            "sid": session_id,
        },
    )
    return int(val or 0)


async def commit_item(
    engine: AsyncEngine,
    session_id: UUID,
    idempotency_key: str,
    item: ItemIn,
    embedder: Embedder,
) -> tuple[dict[str, Any], bool]:
    # 1. Canonical JSON of validated body and its SHA-256 hex
    canonical_json = json.dumps(item.model_dump(), sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    async with write_transaction(engine) as conn:
        # Step 1: Transaction-level advisory lock serializing same-key retries
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:sid || ':' || :key, 0))"),
            {"sid": str(session_id), "key": idempotency_key},
        )

        # Step 2: Idempotency lookup
        stored_key = (
            (
                await conn.execute(
                    text(
                        "SELECT payload_hash, status_code, response_json "
                        "FROM idempotency_keys WHERE session_id = :sid AND key = :key"
                    ),
                    {"sid": session_id, "key": idempotency_key},
                )
            )
            .mappings()
            .first()
        )

        if stored_key is not None:
            if stored_key["payload_hash"] == payload_hash:
                # DECISION: §14 asserts 200 for a replay; return 200 with Idempotent-Replayed: true
                # rather than the stored 201 status code.
                return stored_key["response_json"], True
            raise ApiError(
                status=422,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency-Key Reused",
                detail="Idempotency key has already been used with a different request payload",
            )

        # Step 3: Session item limit
        # DECISION: VOTE does not create an intake_item and therefore does not count
        # toward the 50 limit.
        if item.action != "VOTE":
            owned_count = await conn.scalar(
                text("SELECT count(*) FROM intake_items WHERE owner_session = :sid"),
                {"sid": session_id},
            )
            if (owned_count or 0) >= 50:
                raise ApiError(
                    status=422,
                    code="SESSION_LIMIT",
                    title="Session Limit Reached",
                    detail="Session already owns 50 items",
                )

        # Step 4: Validate action
        def invalid(detail: str) -> ApiError:
            return ApiError(
                status=422, code="INVALID_ACTION", title="Invalid Action", detail=detail
            )

        master_code = (item.master_code or "").strip()
        master = None
        if item.action == "NEW":
            if master_code:
                raise invalid("NEW action must not specify master_code")
        else:
            if not master_code:
                raise invalid(f"{item.action} action requires master_code")
            master = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, ticket_code, category, duplicate_of, resolution_note, "
                            "owner_session, base_count FROM intake_items "
                            "WHERE ticket_code = :master_code"
                        ),
                        {"master_code": master_code},
                    )
                )
                .mappings()
                .first()
            )
            if master is None:
                raise invalid(f"Master item {item.master_code} not found")
            if master["owner_session"] is not None and master["owner_session"] != session_id:
                raise invalid(f"Master item {item.master_code} is not visible to this session")
            if master["duplicate_of"] is not None:
                raise invalid(f"Master item {item.master_code} is already a duplicate")
            if master["category"] != item.category:
                raise invalid(
                    f"Item category {item.category} does not match master category "
                    f"{master['category']}"
                )
            if item.action == "LINK" and master["category"] != "BUG_REPORT":
                raise invalid("LINK action requires BUG_REPORT category")
            if item.action == "VOTE" and master["category"] != "FEATURE_REQUEST":
                raise invalid("VOTE action requires FEATURE_REQUEST category")
            if item.action == "ANSWER" and (
                master["category"] != "CLIENT_ENQUIRY" or not master["resolution_note"]
            ):
                raise invalid("ANSWER action requires CLIENT_ENQUIRY master with resolution_note")

        # Step 5: Execute action
        response_data: dict[str, Any]
        if item.action == "VOTE":
            res = await conn.execute(
                text(
                    "INSERT INTO votes (item_id, session_id) VALUES (:item_id, :sid) "
                    "ON CONFLICT (item_id, session_id) DO NOTHING RETURNING 1"
                ),
                {"item_id": master["id"], "sid": session_id},
            )
            already_voted = res.scalar() is None
            count_shown = await compute_count_shown(
                conn, master["id"], master["category"], master["base_count"], session_id
            )
            response_data = {
                "action": "VOTE",
                "master": master["ticket_code"],
                "count_shown": count_shown,
                "already_voted": already_voted,
            }
        else:
            # NEW, LINK and ANSWER insert an item; a vote never needs the scrub, model or rules.
            scrubbed_title, _ = pii.scrub(item.title)
            scrubbed_desc, _ = pii.scrub(item.description)
            embedding = await embedder.embed(embed_ticket_text(scrubbed_title, scrubbed_desc))
            # DECISION: Inserted items take severity and subsystem from rules.classify (scrubbed).
            classified = rules.classify(scrubbed_title, scrubbed_desc)
            next_id = await conn.scalar(
                text("SELECT nextval(pg_get_serial_sequence('intake_items', 'id'))")
            )
            # LINK masters are bugs and ANSWER masters enquiries (checked above), so the item's own
            # category picks the prefix for every action
            ticket_code = f"{TICKET_PREFIXES[item.category]}{next_id}"
            status = {"NEW": "OPEN", "LINK": "DUPLICATE", "ANSWER": "RESOLVED_AUTO"}[item.action]
            resolution_note = master["resolution_note"] if item.action == "ANSWER" else None
            await conn.execute(
                text(
                    "INSERT INTO intake_items (id, ticket_code, category, title, description, "
                    "error_code, severity, subsystem, status, duplicate_of, base_count, "
                    "resolution_note, owner_session, embedding_model, embedding) VALUES (:id, "
                    ":ticket_code, :category, :title, :description, :error_code, :severity, "
                    ":subsystem, :status, :duplicate_of, 0, :resolution_note, :owner_session, "
                    ":embedding_model, :embedding)"
                ).bindparams(bindparam("embedding", type_=HALFVEC(384))),
                {
                    "id": next_id,
                    "ticket_code": ticket_code,
                    "category": item.category,
                    "title": scrubbed_title,
                    "description": scrubbed_desc,
                    "error_code": item.error_code,
                    "severity": classified["severity"],
                    "subsystem": classified["subsystem"],
                    "status": status,
                    "duplicate_of": master["id"] if master is not None else None,
                    "resolution_note": resolution_note,
                    "owner_session": session_id,
                    "embedding_model": embedder.model_name,
                    "embedding": embedding,
                },
            )
            response_data = {"action": item.action, "ticket_code": ticket_code}
            if item.action == "NEW":
                response_data["status"] = "OPEN"
            else:
                response_data["master"] = master["ticket_code"]
            if item.action == "LINK":
                response_data["count_shown"] = await compute_count_shown(
                    conn, master["id"], master["category"], master["base_count"], session_id
                )
            if item.action == "ANSWER":
                response_data["resolution_note"] = resolution_note

        # Step 6: Store idempotency key row
        await conn.execute(
            text(
                "INSERT INTO idempotency_keys (session_id, key, payload_hash, status_code, "
                "response_json) VALUES (:sid, :key, :hash, 201, :response_json)"
            ).bindparams(bindparam("response_json", type_=JSONB)),
            {
                "sid": session_id,
                "key": idempotency_key,
                "hash": payload_hash,
                "response_json": response_data,
            },
        )
        return response_data, False


async def get_item(engine: AsyncEngine, session_id: UUID, code: str) -> dict[str, Any]:
    query = text(
        """
        SELECT
          i.ticket_code,
          i.category,
          i.title,
          i.description,
          i.error_code,
          i.severity,
          i.subsystem,
          i.status,
          m.ticket_code AS master,
          i.resolution_note,
          i.base_count + CASE i.category
            WHEN 'BUG_REPORT' THEN (
                SELECT count(*) FROM intake_items d
                WHERE d.duplicate_of = i.id AND d.owner_session = :sid
            )
            WHEN 'FEATURE_REQUEST' THEN (
                SELECT count(*) FROM votes v
                WHERE v.item_id = i.id AND v.session_id = :sid
            )
            ELSE 0 END AS count_shown
        FROM intake_items i
        LEFT JOIN intake_items m ON m.id = i.duplicate_of
        WHERE i.ticket_code = :code
          AND (i.owner_session IS NULL OR i.owner_session = :sid)
        """
    )
    async with read_transaction(engine) as conn:
        row = (await conn.execute(query, {"code": code, "sid": session_id})).mappings().first()

    if row is None:
        raise ApiError(
            status=404,
            code="NOT_FOUND",
            title="Item Not Found",
            detail=f"Item {code} not found",
        )

    return dict(row)


async def reset_session(engine: AsyncEngine, session_id: UUID) -> dict[str, int]:
    # DECISION: reset is naturally idempotent, so it requires no Idempotency-Key header.
    async with write_transaction(engine) as conn:
        del_items = await conn.execute(
            text("DELETE FROM intake_items WHERE owner_session = :sid"),
            {"sid": session_id},
        )
        del_votes = await conn.execute(
            text("DELETE FROM votes WHERE session_id = :sid"),
            {"sid": session_id},
        )
        await conn.execute(
            text("DELETE FROM idempotency_keys WHERE session_id = :sid"),
            {"sid": session_id},
        )
    return {
        "deleted_items": del_items.rowcount or 0,
        "deleted_votes": del_votes.rowcount or 0,
    }

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

        # Step 4: Scrub and embed text
        scrubbed_title, _ = pii.scrub(item.title)
        scrubbed_desc, _ = pii.scrub(item.description)
        ticket_text = embed_ticket_text(scrubbed_title, scrubbed_desc)
        embedding = await embedder.embed(ticket_text)

        # Step 5: Validate action
        master = None
        if item.action == "NEW":
            if item.master_code is not None and item.master_code.strip() != "":
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail="NEW action must not specify master_code",
                )
        else:
            if not item.master_code or not item.master_code.strip():
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail=f"{item.action} action requires master_code",
                )
            master = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, ticket_code, category, duplicate_of, resolution_note, "
                            "owner_session, base_count FROM intake_items "
                            "WHERE ticket_code = :master_code"
                        ),
                        {"master_code": item.master_code.strip()},
                    )
                )
                .mappings()
                .first()
            )

            if master is None:
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail=f"Master item {item.master_code} not found",
                )

            # Master must be visible to this session
            if master["owner_session"] is not None and master["owner_session"] != session_id:
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail=f"Master item {item.master_code} is not visible to this session",
                )

            # Master duplicate_of must be NULL
            if master["duplicate_of"] is not None:
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail=f"Master item {item.master_code} is already a duplicate",
                )

            # Master must match category
            if master["category"] != item.category:
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail=(
                        f"Item category {item.category} does not match master category "
                        f"{master['category']}"
                    ),
                )

            # Action-specific category rules
            if item.action == "LINK" and master["category"] != "BUG_REPORT":
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail="LINK action requires BUG_REPORT category",
                )
            if item.action == "VOTE" and master["category"] != "FEATURE_REQUEST":
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail="VOTE action requires FEATURE_REQUEST category",
                )
            if item.action == "ANSWER" and (
                master["category"] != "CLIENT_ENQUIRY" or not master["resolution_note"]
            ):
                raise ApiError(
                    status=422,
                    code="INVALID_ACTION",
                    title="Invalid Action",
                    detail="ANSWER action requires CLIENT_ENQUIRY master with resolution_note",
                )

        # Step 6: Execute action
        # DECISION: Inserted items take severity and subsystem from rules.classify on scrubbed text.
        classified = rules.classify(scrubbed_title, scrubbed_desc)
        severity = classified["severity"]
        subsystem = classified["subsystem"]

        # DECISION: Keep the insert local so all actions reuse the same validated, scrubbed fields.
        async def insert_item(
            *,
            item_id: int,
            ticket_code: str,
            status: str,
            duplicate_of: int | None = None,
            resolution_note: str | None = None,
        ) -> None:
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
                    "id": item_id,
                    "ticket_code": ticket_code,
                    "category": item.category,
                    "title": scrubbed_title,
                    "description": scrubbed_desc,
                    "error_code": item.error_code,
                    "severity": severity,
                    "subsystem": subsystem,
                    "status": status,
                    "duplicate_of": duplicate_of,
                    "resolution_note": resolution_note,
                    "owner_session": session_id,
                    "embedding_model": embedder.model_name,
                    "embedding": embedding,
                },
            )

        response_data: dict[str, Any]
        if item.action == "NEW":
            next_id = await conn.scalar(
                text("SELECT nextval(pg_get_serial_sequence('intake_items', 'id'))")
            )
            prefix = (
                "BUG-U"
                if item.category == "BUG_REPORT"
                else "FEAT-U"
                if item.category == "FEATURE_REQUEST"
                else "ENQ-U"
            )
            ticket_code = f"{prefix}{next_id}"
            await insert_item(
                item_id=next_id,
                ticket_code=ticket_code,
                status="OPEN",
            )
            response_data = {"action": "NEW", "ticket_code": ticket_code, "status": "OPEN"}

        elif item.action == "LINK":
            assert master is not None
            next_id = await conn.scalar(
                text("SELECT nextval(pg_get_serial_sequence('intake_items', 'id'))")
            )
            ticket_code = f"BUG-U{next_id}"
            await insert_item(
                item_id=next_id,
                ticket_code=ticket_code,
                status="DUPLICATE",
                duplicate_of=master["id"],
            )
            count_shown = await compute_count_shown(
                conn, master["id"], master["category"], master["base_count"], session_id
            )
            response_data = {
                "action": "LINK",
                "ticket_code": ticket_code,
                "master": master["ticket_code"],
                "count_shown": count_shown,
            }

        elif item.action == "VOTE":
            assert master is not None
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

        elif item.action == "ANSWER":
            assert master is not None
            next_id = await conn.scalar(
                text("SELECT nextval(pg_get_serial_sequence('intake_items', 'id'))")
            )
            ticket_code = f"ENQ-U{next_id}"
            await insert_item(
                item_id=next_id,
                ticket_code=ticket_code,
                status="RESOLVED_AUTO",
                duplicate_of=master["id"],
                resolution_note=master["resolution_note"],
            )
            response_data = {
                "action": "ANSWER",
                "ticket_code": ticket_code,
                "master": master["ticket_code"],
                "resolution_note": master["resolution_note"],
            }

        # Step 7: Store idempotency key row
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
          i.id,
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

    return {
        "ticket_code": row["ticket_code"],
        "category": row["category"],
        "title": row["title"],
        "description": row["description"],
        "error_code": row["error_code"],
        "severity": row["severity"],
        "subsystem": row["subsystem"],
        "status": row["status"],
        "master": row["master"],
        "resolution_note": row["resolution_note"],
        "count_shown": row["count_shown"],
    }


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

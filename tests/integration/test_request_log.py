import json
import logging

import pytest

EXPECTED_LOG_KEYS = {
    "request_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "embed_ms",
    "db_ms",
    "verdict",
    "classifier_source",
    "redaction_total",
}


@pytest.mark.asyncio
async def test_request_log_structure_and_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="triagelens.access"):
        caplog.clear()
        res = await client.post(
            "/api/triage/preview",
            json={"title": "Test preview", "description": "Crash on click"},
        )
        assert res.status_code == 200

    access_records = [r for r in caplog.records if r.name == "triagelens.access"]
    assert len(access_records) == 1

    entry = json.loads(access_records[0].message)
    assert set(entry.keys()) == EXPECTED_LOG_KEYS
    assert entry["method"] == "POST"
    assert entry["path"] == "/api/triage/preview"
    assert entry["status"] == 200
    assert isinstance(entry["latency_ms"], (int, float))
    assert entry["latency_ms"] >= 0
    assert entry["embed_ms"] is not None
    assert entry["db_ms"] is not None
    assert entry["verdict"] in ("DUPLICATE", "REVIEW", "NEW")
    assert entry["classifier_source"] in ("llm", "rules")
    assert entry["redaction_total"] == 0


@pytest.mark.asyncio
async def test_path_excludes_query_string(client, caplog):
    with caplog.at_level(logging.INFO, logger="triagelens.access"):
        caplog.clear()
        res = await client.get("/api/search?q=confidential+query+text&category=BUG_REPORT")
        assert res.status_code == 200

    access_records = [r for r in caplog.records if r.name == "triagelens.access"]
    assert len(access_records) == 1

    entry = json.loads(access_records[0].message)
    assert entry["path"] == "/api/search"
    assert "confidential" not in access_records[0].message
    assert "query" not in access_records[0].message


@pytest.mark.asyncio
async def test_request_id_reuse_and_generation(client, caplog):
    with caplog.at_level(logging.INFO, logger="triagelens.access"):
        # 1. Valid client request ID is reused
        caplog.clear()
        valid_id = "req-custom-client-id-12345"
        res = await client.get("/readyz", headers={"X-Request-ID": valid_id})
        assert res.headers.get("X-Request-ID") == valid_id
        entry = json.loads(caplog.records[-1].message)
        assert entry["request_id"] == valid_id

        # 2. Invalid client request ID (>64 chars or invalid characters)
        # is replaced with generated ID
        caplog.clear()
        invalid_id = "bad id with spaces and invalid symbols $$$$"
        res = await client.get("/readyz", headers={"X-Request-ID": invalid_id})
        generated_id = res.headers.get("X-Request-ID")
        assert generated_id is not None
        assert generated_id != invalid_id
        entry = json.loads(caplog.records[-1].message)
        assert entry["request_id"] == generated_id

        # 3. Missing client request ID is generated
        caplog.clear()
        res = await client.get("/readyz")
        gen_id = res.headers.get("X-Request-ID")
        assert gen_id is not None
        entry = json.loads(caplog.records[-1].message)
        assert entry["request_id"] == gen_id


@pytest.mark.asyncio
async def test_null_fields_when_not_applicable(client, caplog):
    with caplog.at_level(logging.INFO, logger="triagelens.access"):
        caplog.clear()
        res = await client.get("/readyz")
        assert res.status_code == 200

    access_records = [r for r in caplog.records if r.name == "triagelens.access"]
    assert len(access_records) == 1
    entry = json.loads(access_records[0].message)
    assert entry["embed_ms"] is None
    assert entry["db_ms"] is None
    assert entry["verdict"] is None
    assert entry["classifier_source"] is None
    assert entry["redaction_total"] is None


@pytest.mark.asyncio
async def test_pii_laden_search_and_preview_not_in_any_log(client, caplog):
    with caplog.at_level(logging.DEBUG):
        caplog.clear()

        # 1. PII-laden search query
        secret_email = "victim.user@secret-corp.internal"
        secret_token = "sk-live-998877665544332211"
        search_q = f"Account locked for {secret_email} with API key {secret_token}"
        res = await client.get(f"/api/search?q={search_q}")
        assert res.status_code == 200

        # 2. PII-laden preview
        preview_title = "Billing bug from ceo@topsecret.company"
        preview_desc = (
            f"Failed payout for {secret_email} (+91 9876543210). "
            f"Key is {secret_token}. Card 4111 1111 1111 1111."
        )
        res = await client.post(
            "/api/triage/preview",
            json={
                "title": preview_title,
                "description": preview_desc,
                "category_override": "BUG_REPORT",
            },
        )
        assert res.status_code == 200

    # Ensure no server log record contains the query text, the email or the title
    app_records = [record for record in caplog.records if not record.name.startswith("httpx")]
    all_log_text = " ".join(record.getMessage() for record in app_records)
    assert secret_email not in all_log_text
    assert secret_token not in all_log_text
    assert "ceo@topsecret.company" not in all_log_text
    assert preview_title not in all_log_text
    assert search_q not in all_log_text

import json
import logging

import pytest
from sqlalchemy import text

from app.db import read_transaction
from app.main import create_app
from app.services.llm import LLMClassifier
from app.services.triage import CANDIDATE_FIELDS

FEATURE = {
    "title": "Night mode for checkout on Android",
    "description": "Would love a dark theme when paying in the Android app.",
    "category_override": "FEATURE_REQUEST",
}
DEMO_IN = (
    "Checkout failed for rahul.sharma@example.com (+91 9876543210). "
    "Server 192.168.1.44 returned 500; key sk-live-998877665544 is in the logs. "
    "Refund to UPI demo.user@oksbi. PAN AAAPZ1234C, card 4111 1111 1111 1111, "
    "order ref 2345 6789 0125. Authorization: Bearer abcDEF123456.xyz"
)
DEMO_OUT = (
    "Checkout failed for [REDACTED_EMAIL] ([REDACTED_PHONE]). "
    "Server [REDACTED_IP] returned 500; key [REDACTED_SECRET] is in the logs. "
    "Refund to UPI [REDACTED_UPI]. PAN [REDACTED_PAN], card [REDACTED_CARD], "
    "order ref 2345 6789 0125. Authorization: [REDACTED_SECRET]"
)
DEMO_COUNTS = {"SECRET": 2, "EMAIL": 1, "UPI": 1, "CARD": 1, "PAN": 1, "PHONE": 1, "IP": 1}


@pytest.fixture(autouse=True)
def rules_only(client, application):
    application.state.llm = LLMClassifier(
        redis=application.state.redis, http=application.state.http
    )


async def get_preview(client, body):
    response = await client.post("/api/triage/preview", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def row_counts(engine):
    async with read_transaction(engine) as connection:
        return (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM intake_items), "
                    "(SELECT count(*) FROM votes), (SELECT count(*) FROM idempotency_keys)"
                )
            )
        ).one()


async def test_preview_has_no_item_vote_or_idempotency_side_effects(client, engine):
    before = await row_counts(engine)
    await get_preview(client, FEATURE)
    assert await row_counts(engine) == before


async def test_duplicate_feature_demo(client):
    data = await get_preview(client, FEATURE)
    assert set(data) == {
        "scrubbed",
        "suggestion",
        "search_category",
        "candidates",
        "related",
        "decision",
        "telemetry",
    }
    assert data["decision"]["verdict"] == "DUPLICATE"
    assert data["decision"]["master"] == "FEAT-201"
    assert data["decision"]["cosine"] == pytest.approx(0.88, abs=0.01)
    assert data["related"]["category"] == "BUG_REPORT"
    assert len(data["candidates"]) == 5
    assert all(set(row) == set(CANDIDATE_FIELDS) for row in data["candidates"])
    assert all(row["category"] == "FEATURE_REQUEST" for row in data["candidates"])
    assert data["candidates"][0]["count_shown"] == 14
    assert set(data["suggestion"]) == {
        "category",
        "severity",
        "subsystem",
        "sentiment",
        "summary",
        "source",
    }
    assert data["suggestion"]["source"] == "rules"
    assert set(data["telemetry"]) == {"embed_ms", "db_ms"}
    assert all(value > 0 for value in data["telemetry"].values())


async def test_lookalike_feature_demo_requires_review(client):
    data = await get_preview(
        client,
        {
            "title": "Dark mode for the web dashboard",
            "description": "A dark theme for the desktop web dashboard would be nice.",
            "category_override": "FEATURE_REQUEST",
        },
    )
    assert data["decision"]["verdict"] == "REVIEW"
    assert data["decision"]["closest"] == "FEAT-201"
    assert data["decision"]["cosine"] == pytest.approx(0.754, abs=0.01)


async def test_pii_demo_has_exact_redactions_and_rules_labels(client):
    data = await get_preview(
        client,
        {
            "title": "Payment failed and refund pending",
            "description": DEMO_IN,
            "category_override": "BUG_REPORT",
        },
    )
    assert data["scrubbed"]["description"] == DEMO_OUT
    assert data["scrubbed"]["counts"] == DEMO_COUNTS
    assert data["scrubbed"]["total"] == 8
    suggestion = data["suggestion"]
    assert (suggestion["category"], suggestion["severity"], suggestion["subsystem"]) == (
        "BUG_REPORT",
        "CRITICAL",
        "Payments",
    )
    assert suggestion["source"] == "rules"


async def test_category_defaults_to_suggestion(client):
    data = await get_preview(
        client, {key: value for key, value in FEATURE.items() if key != "category_override"}
    )
    assert data["search_category"] == data["suggestion"]["category"]


@pytest.mark.parametrize("error_code", ["invalid", "ERROR%", "A" * 65, 123])
async def test_bad_error_code_has_specific_problem_code(client, error_code):
    response = await client.post("/api/triage/preview", json=FEATURE | {"error_code": error_code})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_ERROR_CODE"
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(
    "change",
    [
        {"unknown": True},
        {"title": "ab"},
        {"title": "a" * 201},
        {"description": "a" * 4001},
        {"category_override": "INVALID"},
    ],
)
async def test_preview_schema_validation(client, change):
    response = await client.post("/api/triage/preview", json=FEATURE | change)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_explicit_error_code_uses_item_validator(client):
    data = await get_preview(
        client,
        {
            "title": "Safari users are signed out",
            "description": "Sessions end every five minutes while active.",
            "category_override": "BUG_REPORT",
            "error_code": " e_0x7f3a ",
        },
    )
    assert data["candidates"][0]["ticket_code"] == "BUG-109"
    assert data["candidates"][0]["code_hit"] is True
    assert "same error code" in data["decision"]["reasons"]


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/problem+json"])
async def test_preview_accepts_json_media_type_only(client, content_type):
    headers = {} if content_type is None else {"Content-Type": content_type}
    response = await client.post(
        "/api/triage/preview", content=json.dumps(FEATURE), headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_preview_accepts_json_charset_without_idempotency_key(client):
    response = await client.post(
        "/api/triage/preview",
        content=json.dumps(FEATURE),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert response.status_code == 200


async def test_preview_then_vote_increments_feature_once(client):
    data = await get_preview(client, FEATURE)
    response = await client.post(
        "/api/items",
        json={
            "title": FEATURE["title"],
            "description": FEATURE["description"],
            "category": data["search_category"],
            "action": "VOTE",
            "master_code": data["decision"]["master"],
        },
        headers={"Idempotency-Key": "preview-vote-00000000000001"},
    )
    assert response.status_code == 201
    assert response.json()["master"] == "FEAT-201"
    assert response.json()["count_shown"] == 15
    assert response.json()["already_voted"] is False


async def test_scrubbed_inputs_one_embed_and_safe_logs(client, application, embedder, caplog):
    classifier = application.state.llm
    classifier_inputs, embedding_inputs = [], []

    class RecordingClassifier:
        async def suggest(self, title, description, *, allow_llm=True):
            classifier_inputs.append((title, description))
            return await classifier.suggest(title, description, allow_llm=allow_llm)

    class RecordingEmbedder:
        async def embed(self, value):
            embedding_inputs.append(value)
            return await embedder.embed(value)

    application.state.llm = RecordingClassifier()
    application.state.embedder = RecordingEmbedder()
    with caplog.at_level(logging.INFO, logger="app.services.triage"):
        data = await get_preview(
            client,
            {
                "title": "Crash reported by alice@example.com",
                "description": DEMO_IN,
                "category_override": "BUG_REPORT",
            },
        )
    clean = data["scrubbed"]
    assert clean["counts"]["EMAIL"] == 2
    assert clean["total"] == 9
    assert classifier_inputs == [(clean["title"], clean["description"])]
    assert embedding_inputs == [f"{clean['title']}\n{clean['description']}"]
    record = next(record for record in caplog.records if record.name == "app.services.triage")
    assert record.redaction_total == 9
    assert record.classifier_source == "rules"
    assert record.title_length == len(clean["title"])
    assert record.verdict == data["decision"]["verdict"]
    assert "alice@example.com" not in str(record.__dict__)
    assert "sk-live-998877665544" not in str(record.__dict__)
    assert DEMO_OUT not in str(record.__dict__)


async def test_decision_uses_all_rows_and_related_uses_highest_cosine(client, monkeypatch):
    from app.services import search

    original = search.hybrid_search
    calls = []
    expected_related = []

    async def reordered(connection, **parameters):
        assert await connection.scalar(text("SHOW transaction_read_only")) == "on"
        calls.append((connection, parameters))
        rows = await original(connection, **parameters)
        if parameters["category"] is not None:
            best = max(rows, key=lambda row: row["cosine"])
            return [row for row in rows if row is not best] + [best]
        others = [row for row in rows if row["category"] != "FEATURE_REQUEST"]
        expected_related.append(max(others, key=lambda row: row["cosine"])["ticket_code"])
        return sorted(rows, key=lambda row: row["cosine"])

    monkeypatch.setattr(search, "hybrid_search", reordered)
    data = await get_preview(client, FEATURE)
    assert len(calls) == 2
    assert calls[0][0] is calls[1][0]
    assert [call[1]["category"] for call in calls] == ["FEATURE_REQUEST", None]
    assert all(call[1]["q"] == f"{FEATURE['title']}\n{FEATURE['description']}" for call in calls)
    assert "FEAT-201" not in [row["ticket_code"] for row in data["candidates"]]
    assert data["decision"]["master"] == "FEAT-201"
    assert data["decision"]["verdict"] == "DUPLICATE"
    assert data["related"]["ticket_code"] == expected_related[0]


async def test_preview_uses_configured_thresholds(client, application):
    application.state.settings = application.state.settings.model_copy(update={"dup_t_auto": 0.95})
    data = await get_preview(client, FEATURE)
    assert data["decision"]["verdict"] == "REVIEW"


async def test_lifespan_shares_and_closes_classifier_http(settings, embedder, monkeypatch):
    monkeypatch.setattr("app.main.make_embedder", lambda settings: embedder)
    configured = settings.model_copy(
        update={
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "test-key",
            "llm_model": "test-model",
            "llm_json_mode": True,
            "llm_daily_budget": 7,
        }
    )
    app = create_app(configured)
    async with app.router.lifespan_context(app):
        await app.state.warmup_task
        assert app.state.llm.http is app.state.http
        assert app.state.llm.redis is app.state.redis
        assert app.state.llm.enabled is True
        assert app.state.llm.base_url == configured.llm_base_url
        assert app.state.llm.api_key == configured.llm_api_key
        assert app.state.llm.model == configured.llm_model
        assert app.state.llm.json_mode is True
        assert app.state.llm.daily_budget == 7
        assert app.state.http.is_closed is False
    assert app.state.http.is_closed is True

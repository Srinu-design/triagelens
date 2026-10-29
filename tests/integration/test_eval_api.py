import json
from datetime import datetime

from app.api import eval as eval_api


async def test_eval_returns_committed_report(client):
    response = await client.get("/api/eval")
    assert response.status_code == 200
    report = response.json()
    assert report == json.loads(eval_api.REPORT_PATH.read_text())
    assert set(report) == {
        "model",
        "generated_at",
        "in_sample",
        "labeled_set",
        "thresholds",
        "configured",
        "grid",
        "margin",
        "top1",
        "code_cases",
    }
    assert datetime.fromisoformat(report["generated_at"]).tzinfo is not None
    assert report["in_sample"] is True
    assert report["labeled_set"] == {"tickets": 31, "duplicates": 23, "new": 8}
    assert report["configured"] == {"auto_correct": 21, "auto_wrong": 0, "review": 4, "missed": 0}
    assert len(report["grid"]) == 4
    assert len(report["code_cases"]) == 6
    assert all(
        set(row)
        == {
            "title",
            "truth",
            "closest",
            "cosine",
            "verdict",
            "expected_verdict",
            "expected_master",
            "pass",
        }
        for row in report["code_cases"]
    )


async def test_missing_report_is_problem_details(client, tmp_path, monkeypatch):
    monkeypatch.setattr(eval_api, "REPORT_PATH", tmp_path / "missing.json")
    response = await client.get("/api/eval")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_FOUND"

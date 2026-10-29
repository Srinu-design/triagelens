import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from eval import calibrate


async def test_calibration_reproduces_all_spec_numbers(engine, embedder):
    report = await calibrate.run_calibration(engine, embedder, 0.8, 0.7)
    assert report["model"] == "BAAI/bge-small-en-v1.5"
    assert datetime.fromisoformat(report["generated_at"]).tzinfo is not None
    assert report["in_sample"] is True
    assert report["labeled_set"] == {"tickets": 31, "duplicates": 23, "new": 8}
    assert report["thresholds"] == {"t_auto": 0.8, "t_review": 0.7}
    assert report["configured"] == {"auto_correct": 21, "auto_wrong": 0, "review": 4, "missed": 0}
    expected_grid = [
        (0.80, 0.70, 21, 0, 4, 0),
        (0.78, 0.70, 21, 0, 4, 0),
        (0.75, 0.70, 23, 1, 1, 0),
        (0.735, 0.735, 23, 1, 0, 0),
    ]
    fields = ("t_auto", "t_review", "auto_correct", "auto_wrong", "review", "missed")
    assert [tuple(row[field] for field in fields) for row in report["grid"]] == expected_grid
    margin = report["margin"]
    assert margin["closest_lookalike"]["title"] == "Dark mode for the web dashboard"
    assert margin["closest_lookalike"]["cosine"] == pytest.approx(0.754, abs=0.001)
    assert margin["weakest_duplicate"]["title"] == "Profile picture upload hangs on my phone"
    assert margin["weakest_duplicate"]["cosine"] == pytest.approx(0.755, abs=0.001)
    assert report["top1"] == {"correct": 23, "total": 23}
    expected_cases = [
        ("BUG-109", "DUPLICATE", 0.831),
        ("BUG-101", "DUPLICATE", 0.867),
        ("BUG-103", "DUPLICATE", 0.872),
        ("BUG-112", "REVIEW", 0.797),
        ("BUG-101", "REVIEW", 0.764),
        ("BUG-109", "REVIEW", 0.537),
    ]
    assert len(report["code_cases"]) == 6
    for index, (row, expected) in enumerate(zip(report["code_cases"], expected_cases, strict=True)):
        master, verdict, cosine = expected
        assert row["closest"] == row["expected_master"] == master
        assert row["verdict"] == row["expected_verdict"] == verdict
        assert row["cosine"] == pytest.approx(cosine, abs=0.01)
        assert row["truth"] == (master if index < 3 else "a different bug")
        assert row["pass"] is True


async def test_cli_fails_and_reports_unsafe_configured_thresholds(
    settings, embedder, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        calibrate, "Settings", lambda: settings.model_copy(update={"dup_t_auto": 0.75})
    )
    monkeypatch.setattr(calibrate, "make_embedder", lambda settings: embedder)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(calibrate, "REPORT_PATH", report_path)
    assert await calibrate.main() == 1
    report = json.loads(report_path.read_text())
    assert report["thresholds"] == {"t_auto": 0.75, "t_review": 0.7}
    assert report["configured"]["auto_wrong"] == 1
    assert any(not row["pass"] for row in report["code_cases"])
    output = capsys.readouterr().out
    assert "Calibration (in-sample)" in output
    assert "Top-1 duplicates: 23 / 23" in output


async def test_calibration_refuses_a_different_embedding_model(engine):
    with pytest.raises(ValueError, match="another model"):
        await calibrate.run_calibration(
            engine, SimpleNamespace(model_name="another-model"), 0.8, 0.7
        )

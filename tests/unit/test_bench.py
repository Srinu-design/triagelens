import re
from collections import Counter

import pytest

from bench.common import ERROR_CODES, build_tickets, database_url, percentile, recall_at_10


def test_builder_is_reproducible():
    assert build_tickets(100, 42) == build_tickets(100, 42)
    assert build_tickets(100, 42) != build_tickets(100, 7)


def test_category_mix_and_codes():
    tickets = build_tickets(10000, 42)
    counts = Counter(item.category for item in tickets)
    for category, expected in [
        ("BUG_REPORT", 0.7),
        ("FEATURE_REQUEST", 0.2),
        ("CLIENT_ENQUIRY", 0.1),
    ]:
        assert counts[category] / len(tickets) == pytest.approx(expected, abs=0.01)
    coded = [item for item in tickets if item.error_code]
    assert len(coded) / counts["BUG_REPORT"] == pytest.approx(0.1, abs=0.02)
    assert all(item.category == "BUG_REPORT" for item in coded)
    assert len(set(ERROR_CODES)) == 500
    assert all(item.error_code in ERROR_CODES for item in coded)
    assert all(re.fullmatch(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$", item.error_code) for item in coded)
    assert all(3 <= len(item.title) <= 200 and len(item.description) <= 4000 for item in tickets)


def test_queries_have_codes_across_categories():
    tickets = build_tickets(10000, 7, queries=True)
    coded = [item for item in tickets if item.error_code]
    assert len(coded) / len(tickets) == pytest.approx(0.1, abs=0.02)
    assert any(item.category != "BUG_REPORT" for item in coded)


@pytest.mark.parametrize(
    "actual, expected, score",
    [
        (list(range(10)), list(range(10)), 1.0),
        (list(range(8, 18)), list(range(10)), 0.2),
        ([], list(range(10)), 0.0),
        ([0] * 10, list(range(10)), 0.1),
    ],
)
def test_recall(actual, expected, score):
    assert recall_at_10(actual, expected) == score


@pytest.mark.parametrize("p, expected", [(0, 1), (50, 2.5), (95, 3.85), (100, 4)])
def test_percentile(p, expected):
    assert percentile([4, 2, 1, 3], p) == pytest.approx(expected)


@pytest.mark.parametrize("values, p", [([], 50), ([1], -1), ([1], 101)])
def test_invalid_percentile(values, p):
    with pytest.raises(ValueError):
        percentile(values, p)


def test_database_guard(monkeypatch):
    monkeypatch.setenv("BENCH_DATABASE_URL", "postgresql+asyncpg://localhost/triagelens")
    with pytest.raises(ValueError, match="triagelens_bench"):
        database_url("BENCH_DATABASE_URL")
    monkeypatch.setenv("BENCH_DATABASE_URL", "postgresql+asyncpg://localhost/triagelens_bench")
    assert database_url("BENCH_DATABASE_URL").endswith("/triagelens_bench")

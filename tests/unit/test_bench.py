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
    "distances, d10, score",
    [
        ([0.1] * 10, 0.1, 1.0),
        ([0.1, 0.2] + [0.3] * 8, 0.2, 0.2),
        ([], 0.2, 0.0),
        ([0.2, 0.2 + 0.5e-6, 0.2 + 2e-6], 0.2, 0.2),
        ([0.1] * 11, 0.1, 1.0),
    ],
)
def test_recall(distances, d10, score):
    assert recall_at_10(distances, d10) == score


def test_recall_accepts_different_ids_tied_at_tenth_distance():
    exact = [(item_id, 0.1) for item_id in range(1, 10)] + [(10, 0.2)]
    actual = [(101, 0.2), (102, 0.2)]
    assert not {item_id for item_id, _ in exact} & {item_id for item_id, _ in actual}
    assert recall_at_10([distance for _, distance in actual], exact[-1][1]) == 0.2


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

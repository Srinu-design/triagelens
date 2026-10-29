import pytest

from app.domain.decision import Candidate, decide


@pytest.mark.parametrize(
    ("candidates", "expected_verdict", "expected_master", "expected_closest"),
    [
        ([], "NEW", None, None),
        ([Candidate("BUG-101", 0.85, False)], "DUPLICATE", "BUG-101", "BUG-101"),
        ([Candidate("BUG-101", 0.75, False)], "REVIEW", "BUG-101", "BUG-101"),
        ([Candidate("BUG-101", 0.65, False)], "NEW", None, "BUG-101"),
        ([Candidate("BUG-101", 0.72, True)], "REVIEW", "BUG-101", "BUG-101"),
        ([Candidate("BUG-101", 0.85, True)], "DUPLICATE", "BUG-101", "BUG-101"),
        ([Candidate("BUG-101", 0.40, True)], "REVIEW", "BUG-101", "BUG-101"),
        (
            [Candidate("BUG-102", 0.90, False), Candidate("BUG-101", 0.75, True)],
            "REVIEW",
            "BUG-101",
            "BUG-101",
        ),
    ],
)
def test_decide_table(
    candidates: list[Candidate],
    expected_verdict: str,
    expected_master: str | None,
    expected_closest: str | None,
) -> None:
    result = decide(candidates)
    assert result["verdict"] == expected_verdict
    assert result["master"] == expected_master
    assert result["closest"] == expected_closest

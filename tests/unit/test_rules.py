from app.domain.pii import scrub
from app.domain.rules import classify
from app.seed_data import SEED
from eval.labeled_set import EVAL

DEMO_IN = (
    "Checkout failed for rahul.sharma@example.com (+91 9876543210). "
    "Server 192.168.1.44 returned 500; key sk-live-998877665544 is in the logs. "
    "Refund to UPI demo.user@oksbi. PAN AAAPZ1234C, card 4111 1111 1111 1111, "
    "order ref 2345 6789 0125. Authorization: Bearer abcDEF123456.xyz"
)


def test_rules_seed_accuracy() -> None:
    seed_correct = sum(1 for item in SEED if classify(item[2], item[3])["category"] == item[1])
    assert len(SEED) == 30
    assert seed_correct == 29


def test_rules_labeled_set_accuracy() -> None:
    eval_correct = sum(1 for item in EVAL if classify(item[0], item[1])["category"] == item[2])
    assert len(EVAL) == 31
    assert eval_correct == 26


def test_rules_pii_demo_ticket() -> None:
    demo_out, _ = scrub(DEMO_IN)
    result = classify("Payment failed and refund pending", demo_out)
    assert result["category"] == "BUG_REPORT"
    assert result["severity"] == "CRITICAL"
    assert result["subsystem"] == "Payments"

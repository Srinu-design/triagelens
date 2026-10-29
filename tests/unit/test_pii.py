import pytest

from app.domain.pii import luhn_valid, scrub, verhoeff_check_digit
from app.seed_data import SEED
from eval.code_cases import CODE_CASES
from eval.labeled_set import EVAL

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

DEMO_COUNTS = {
    "SECRET": 2,
    "EMAIL": 1,
    "UPI": 1,
    "CARD": 1,
    "PAN": 1,
    "PHONE": 1,
    "IP": 1,
}


def test_pii_demo_ticket() -> None:
    text, counts = scrub(DEMO_IN)
    assert text == DEMO_OUT
    assert counts == DEMO_COUNTS
    assert sum(counts.values()) == 8


def test_pii_aadhaar_runtime_built() -> None:
    base = "99990000111"
    chk = verhoeff_check_digit(base)
    aadhaar_num = f"{base[:4]} {base[4:8]} {base[8:]}{chk}"
    out, counts = scrub(aadhaar_num)
    assert out == "[REDACTED_AADHAAR]"
    assert counts == {"AADHAAR": 1}


@pytest.mark.parametrize(
    ("input_text", "expected_text", "expected_counts"),
    [
        ("Order 1234567890123 shipped", "Order 1234567890123 shipped", {}),
        ("Release 2.14.0 broke login", "Release 2.14.0 broke login", {}),
        ("Unix time 1729999999", "Unix time 1729999999", {}),
        ("tracking 9876543210123", "tracking 9876543210123", {}),
        ("call 98765-43210 now", "call [REDACTED_PHONE] now", {"PHONE": 1}),
        ("call 098765 43210", "call [REDACTED_PHONE]", {"PHONE": 1}),
        ("intl +1 415 555 0100", "intl [REDACTED_PHONE]", {"PHONE": 1}),
        ("ip 10.0.0.256 bad", "ip 10.0.0.256 bad", {}),
        ("error E_0X7F3A on build 512", "error E_0X7F3A on build 512", {}),
    ],
)
def test_pii_spec_table_cases(
    input_text: str, expected_text: str, expected_counts: dict[str, int]
) -> None:
    text, counts = scrub(input_text)
    assert text == expected_text
    assert counts == expected_counts


def test_pii_luhn_checksum() -> None:
    assert luhn_valid("4111111111111111") is True
    assert luhn_valid("79927398713") is True
    assert luhn_valid("4111111111111112") is False


def test_pii_verhoeff_checksum() -> None:
    assert verhoeff_check_digit("236") == "3"


def test_pii_scrub_idempotent() -> None:
    out1, _ = scrub(DEMO_IN)
    out2, counts2 = scrub(out1)
    assert out2 == out1
    assert counts2 == {}


def test_pii_seed_and_eval_unchanged() -> None:
    for item in SEED:
        title, desc = item[2], item[3]
        s_title, c_t = scrub(title)
        assert s_title == title
        assert c_t == {}
        s_desc, c_d = scrub(desc)
        assert s_desc == desc
        assert c_d == {}

    for item in EVAL:
        title, desc = item[0], item[1]
        s_title, c_t = scrub(title)
        assert s_title == title
        assert c_t == {}
        s_desc, c_d = scrub(desc)
        assert s_desc == desc
        assert c_d == {}

    for item in CODE_CASES:
        title, desc = item[0], item[1]
        s_title, c_t = scrub(title)
        assert s_title == title
        assert c_t == {}
        s_desc, c_d = scrub(desc)
        assert s_desc == desc
        assert c_d == {}

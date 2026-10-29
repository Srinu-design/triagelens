from app.domain.codes import extract_codes


def test_extract_codes_both_uppercased() -> None:
    res = extract_codes("see err_pg_pool_timeout_504 and E_0X7F3A")
    assert res == ["ERR_PG_POOL_TIMEOUT_504", "E_0X7F3A"]


def test_extract_codes_redacted_never_returned() -> None:
    text = "Got [REDACTED_EMAIL], [REDACTED_SECRET], and [REDACTED_CARD]"
    assert extract_codes(text) == []


def test_extract_codes_percent_extracts_nothing() -> None:
    assert extract_codes("100% discount on 50% orders") == []
    assert extract_codes("%") == []


def test_extract_codes_edge_cases() -> None:
    assert extract_codes("") == []
    assert extract_codes("just plain text with no error codes") == []

    # Capped at 5 unique codes sorted alphabetically
    many_codes = "err_one err_two err_three err_four err_five err_six err_seven"
    codes = extract_codes(many_codes)
    assert len(codes) == 5
    assert codes == sorted(codes)

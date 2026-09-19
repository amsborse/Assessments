import pytest

from assessments.redaction import mask_value, redact_obj, redact_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SSN 512-44-9012 on file", "SSN [ssn] on file"),
        ("call (207) 555-0142", "call [phone]"),
        ("mail jo@example.org now", "mail [email] now"),
        ("DOB: 03/14/1986", "[dob]"),
        ("acct 0012345678901", "acct [acct …8901]"),
        ("card 4111 1111 1111 1111 exp", "card [card] exp"),
    ],
)
def test_redact_text_patterns(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


def test_money_and_short_identifiers_are_not_pattern_redacted() -> None:
    # Business values stay readable; identifiers are protected by declared sensitivity instead.
    assert redact_text("balance $2,418.07 for member 12345") == "balance $2,418.07 for member 12345"


def test_redact_obj_scrubs_known_values_recursively() -> None:
    data = {"note": "typed harbor-demo", "items": ["member 12345", {"ssn": "512-44-9012"}], "n": 3}

    out = redact_obj(data, ["harbor-demo", "12345"])

    assert out == {
        "note": "typed [redacted]",
        "items": ["member [redacted]", {"ssn": "[ssn]"}],
        "n": 3,
    }


def test_mask_value_keeps_only_a_short_suffix() -> None:
    assert mask_value("2418.07") == "████07"
    assert mask_value("ab") == "████"

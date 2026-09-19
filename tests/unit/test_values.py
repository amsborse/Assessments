import pytest

from assessments.capability.schema import OutputSpec, ParamSpec, ValueType
from assessments.capability.values import (
    InvalidParams,
    parse_output,
    render,
    templatize,
    validate_params,
)

MEMBER = ParamSpec(type=ValueType.STRING, description="m", pattern=r"^\d{5,9}$")


def test_validate_params_canonicalizes_values() -> None:
    specs = {
        "member_id": MEMBER,
        "amount": ParamSpec(type=ValueType.MONEY, description="a"),
        "opened": ParamSpec(type=ValueType.DATE, description="d", required=False),
    }

    values = validate_params(
        specs, {"member_id": " 12345 ", "amount": "$1,025.50", "opened": "03/14/2024"}
    )

    assert values == {"member_id": "12345", "amount": "1025.50", "opened": "2024-03-14"}


def test_validate_params_reports_every_problem() -> None:
    specs = {
        "member_id": MEMBER,
        "product": ParamSpec(type=ValueType.ENUM, description="p", enum=["A", "B"]),
    }

    with pytest.raises(InvalidParams) as exc:
        validate_params(specs, {"member_id": "12ab", "product": "C", "extra": "x"})

    assert set(exc.value.errors) == {"member_id", "product", "extra"}


@pytest.mark.parametrize(
    ("type_", "raw", "expected"),
    [
        (ValueType.MONEY, "$2,418.07", "2418.07"),
        (ValueType.MONEY, "($15.00)", "-15.00"),
        (ValueType.INTEGER, "1,204", 1204),
        (ValueType.STRING, "  Jordan\n Avery ", "Jordan Avery"),
        (ValueType.DATE, "01/05/1968", "1968-01-05"),
    ],
)
def test_parse_output(type_: ValueType, raw: str, expected: object) -> None:
    assert parse_output(OutputSpec(type=type_, description="x"), raw) == expected


@pytest.mark.parametrize("raw", ["n/a", "", "$"])
def test_parse_money_rejects_non_numbers(raw: str) -> None:
    with pytest.raises(ValueError, match="not a number"):
        parse_output(OutputSpec(type=ValueType.MONEY, description="x"), raw)


def test_templatize_replaces_whole_values_longest_first() -> None:
    text = "member 123456 and 12345, id x12345"

    assert templatize(text, {"a": "12345", "b": "123456"}) == "member {{b}} and {{a}}, id x12345"


def test_render_is_the_inverse_of_templatize() -> None:
    examples = {"member_id": "12345"}

    assert render(templatize("Member 12345 detail", examples), examples) == "Member 12345 detail"

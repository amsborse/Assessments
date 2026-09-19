"""Typed parameter validation, output parsing, and {{template}} rendering."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from assessments.capability.schema import TEMPLATE_RE, OutputSpec, ParamSpec, ValueType

_MONEY_RE = re.compile(r"^\(?-?\$?\s*-?[\d,]+(\.\d+)?\)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


class InvalidParams(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def validate_params(specs: dict[str, ParamSpec], raw: dict[str, Any]) -> dict[str, str]:
    """Validate caller-supplied params against the contract. Returns canonical string values."""
    errors: dict[str, str] = {}
    values: dict[str, str] = {}
    for name in raw.keys() - specs.keys():
        errors[name] = "unknown parameter"
    for name, spec in specs.items():
        if name not in raw or raw[name] is None or str(raw[name]).strip() == "":
            if spec.required:
                errors[name] = "required"
            continue
        try:
            values[name] = _coerce_param(spec, raw[name])
        except ValueError as exc:
            errors[name] = str(exc)
    if errors:
        raise InvalidParams(errors)
    return values


def _coerce_param(spec: ParamSpec, raw: Any) -> str:
    text = str(raw).strip()
    match spec.type:
        case ValueType.INTEGER:
            if not re.fullmatch(r"-?\d+", text):
                raise ValueError("must be an integer")
        case ValueType.DECIMAL | ValueType.MONEY:
            text = str(parse_decimal(text))
        case ValueType.BOOLEAN:
            if text.lower() not in {"true", "false"}:
                raise ValueError("must be true or false")
            text = text.lower()
        case ValueType.DATE:
            text = parse_date(text)
        case ValueType.ENUM:
            if spec.enum is None or text not in spec.enum:
                raise ValueError(f"must be one of {spec.enum}")
        case ValueType.STRING:
            pass
    if spec.pattern and not re.fullmatch(spec.pattern, text):
        raise ValueError(f"must match {spec.pattern}")
    return text


def parse_decimal(text: str) -> Decimal:
    cleaned = text.strip()
    if not _MONEY_RE.match(cleaned):
        raise ValueError(f"not a number: {cleaned[:40]!r}")
    negative = cleaned.startswith("(") or "-" in cleaned
    digits = re.sub(r"[^\d.]", "", cleaned)
    try:
        value = Decimal(digits)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {cleaned[:40]!r}") from exc
    return -value if negative else value


def parse_date(text: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"not a date: {text[:40]!r}")


def parse_output(spec: OutputSpec, raw: str) -> str | int | bool:
    """Parse text read from the surface into the declared output type."""
    text = " ".join(raw.split())
    match spec.type:
        case ValueType.MONEY | ValueType.DECIMAL:
            return str(parse_decimal(text))
        case ValueType.INTEGER:
            if not re.fullmatch(r"-?[\d,]+", text):
                raise ValueError(f"not an integer: {text[:40]!r}")
            return int(text.replace(",", ""))
        case ValueType.DATE:
            return parse_date(text)
        case ValueType.BOOLEAN:
            if text.lower() in {"yes", "true", "y"}:
                return True
            if text.lower() in {"no", "false", "n"}:
                return False
            raise ValueError(f"not a boolean: {text[:40]!r}")
        case _:
            if not text:
                raise ValueError("empty value")
            return text


def render(template: str, params: dict[str, str]) -> str:
    """Substitute {{name}} placeholders. Unknown names are a programming error (schema-checked)."""
    return TEMPLATE_RE.sub(lambda m: params[m.group(1)], template)


def templatize(text: str, examples: dict[str, str]) -> str:
    """Replace concrete example values with {{param}} placeholders (canonicalization).

    Longest values first so '123456' is not partially replaced by '12345'. Values shorter than
    3 characters are skipped: too likely to collide with unrelated text.
    """
    for name, value in sorted(examples.items(), key=lambda kv: -len(kv[1])):
        if len(value) >= 3:
            text = re.sub(rf"(?<![\w]){re.escape(value)}(?![\w])", f"{{{{{name}}}}}", text)
    return text

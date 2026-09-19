"""Redaction of regulated data before anything is logged, persisted, or sent to a model.

Two layers:
1. Pattern-based (this module): SSNs, card/account numbers, emails, phone numbers, dates of
   birth — applied to every log line, snapshot, and persisted document.
2. Label-based (app profile + injected page script): values next to labels such as "SSN:" or
   "Address:" are masked in the DOM before snapshots/screenshots are taken.

Limits: pattern redaction cannot recognise free-text PII such as names embedded in prose; that is
what label-based redaction and declared sensitivities on inputs/outputs are for.
"""

import re
from collections.abc import Iterable
from typing import Any

MASK = "█"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b")),
    ("dob", re.compile(r"(?i)\b(dob|date of birth|birth ?date)\b\W{0,3}\d{1,2}/\d{1,2}/\d{2,4}")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_LONG_NUMBER = re.compile(r"\b\d{10,}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card_or_keep(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group(0)


def redact_text(text: str) -> str:
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[{kind}]", text)
    text = _CARD.sub(_card_or_keep, text)  # Luhn-valid digit runs are payment cards
    # Other long digit runs (account/routing numbers): keep last 4 for debuggability.
    return _LONG_NUMBER.sub(lambda m: f"[acct …{m.group(0)[-4:]}]", text)


def redact_values(text: str, secrets: Iterable[str]) -> str:
    """Remove exact known-sensitive values (resolved secrets, PII params)."""
    for value in sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return text


def redact_obj(obj: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact strings inside JSON-like structures."""
    values = tuple(secrets)
    if isinstance(obj, str):
        return redact_text(redact_values(obj, values))
    if isinstance(obj, dict):
        return {k: redact_obj(v, values) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact_obj(v, values) for v in obj]
    return obj


def mask_value(value: object) -> str:
    text = str(value)
    return f"{MASK * 4}{text[-2:]}" if len(text) > 4 else MASK * 4

import pytest

from assessments.capability.schema import ActionKind, Risk
from assessments.safety import Policy, Verdict

POLICY = Policy(
    allowed_hosts=("localhost", "bank.internal"),
    irreversible_patterns=(r"^confirm$", r"\btransfer\b"),
)
PAGE = "http://localhost:8001/main/inquiry"


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://localhost:8001/x", True),
        ("https://core.bank.internal/x", True),
        ("https://bank.internal.evil.com/x", False),
        ("https://evil.com/?q=localhost", False),
        ("data:text/html,hi", True),
    ],
)
def test_host_allowlist(url: str, allowed: bool) -> None:
    assert POLICY.host_allowed(url) is allowed


def test_irreversible_click_requires_approval_unless_authorized() -> None:
    held = POLICY.check_action(ActionKind.CLICK, page_url=PAGE, control_name="Confirm")
    authorized = POLICY.check_action(
        ActionKind.CLICK, page_url=PAGE, control_name="Confirm", irreversible_authorized=True
    )

    assert (held.verdict, held.risk) == (Verdict.REQUIRE_APPROVAL, Risk.IRREVERSIBLE)
    assert (authorized.verdict, authorized.risk) == (Verdict.ALLOW, Risk.IRREVERSIBLE)


@pytest.mark.parametrize("name", ["Search", "Continue", "Confirmation details"])
def test_ordinary_controls_are_safe(name: str) -> None:
    decision = POLICY.check_action(ActionKind.CLICK, page_url=PAGE, control_name=name)

    assert (decision.verdict, decision.risk) == (Verdict.ALLOW, Risk.SAFE)


def test_filling_is_never_irreversible_even_if_the_label_matches() -> None:
    decision = POLICY.check_action(ActionKind.FILL, page_url=PAGE, control_name="Transfer amount")

    assert decision.verdict is Verdict.ALLOW


def test_acting_off_allowlist_is_denied() -> None:
    decision = POLICY.check_action(ActionKind.CLICK, page_url="https://evil.com/", control_name="x")

    assert decision.verdict is Verdict.DENY


def test_disallowed_action_types_and_keys_are_denied() -> None:
    read_only = Policy(
        allowed_hosts=("localhost",), allowed_actions=frozenset({ActionKind.EXTRACT})
    )

    assert read_only.check_action(ActionKind.CLICK, page_url=PAGE).verdict is Verdict.DENY
    enter = POLICY.check_action(ActionKind.PRESS, page_url=PAGE, key="Enter")
    assert enter.verdict is Verdict.DENY
    assert "click the submit control" in enter.reason


def test_route_allowlist_limits_where_automation_may_act() -> None:
    policy = Policy(allowed_hosts=("localhost",), allowed_paths=("/", "/main/.*"))

    assert policy.check_action(ActionKind.CLICK, page_url=PAGE).verdict is Verdict.ALLOW
    admin = policy.check_action(ActionKind.CLICK, page_url="http://localhost:8001/admin/users")
    assert admin.verdict is Verdict.DENY
    assert "outside the allowlist" in admin.reason

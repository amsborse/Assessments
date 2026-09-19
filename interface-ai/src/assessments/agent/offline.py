"""Offline stand-in for the model, so the full pipeline runs without API access.

This is a *test double*, not discovery: a fixed script written against what is visible on screen
(roles and names), exercised through exactly the same loop, policy, recording and compiler as the
real model. The evidence in /evidence/ comes from the real Claude-driven run.
"""

from collections.abc import Callable
from typing import Any

from assessments.agent.decider import ScriptedDecider, ref_of
from assessments.surfaces.base import Observation

Script = list[Callable[[Observation], dict[str, Any]]]

MEMBER_SAVINGS_BALANCE: Script = [
    lambda o: {
        "tool": "fill_secret",
        "ref": ref_of(o, "textbox", "Operator ID:"),
        "secret": "operator_id",
        "reason": "Sign on: operator ID",
    },
    lambda o: {
        "tool": "fill_secret",
        "ref": ref_of(o, "textbox", "Password:"),
        "secret": "operator_password",
        "reason": "Sign on: password",
    },
    lambda o: {"tool": "click", "ref": ref_of(o, "button", "Sign On"), "reason": "Submit sign on"},
    lambda o: {
        "tool": "click",
        "ref": ref_of(o, "link", "Member Inquiry"),
        "reason": "Open member inquiry",
    },
    lambda o: {
        "tool": "fill",
        "ref": ref_of(o, "textbox", "Member #:"),
        "value": "{{member_id}}",
        "reason": "Enter the member number",
    },
    lambda o: {"tool": "click", "ref": ref_of(o, "button", "Search"), "reason": "Search"},
    lambda o: {
        "tool": "extract",
        "output": "savings_balance",
        "frame": "main",
        "locate": {"kind": "table_cell", "row_key": "Share Savings", "column": "Balance"},
        "reason": "Read the Share Savings balance",
    },
    lambda o: {
        "tool": "extract",
        "output": "member_name",
        "frame": "main",
        "locate": {"kind": "label", "label": "Name:"},
        "reason": "Read the member name",
    },
    lambda o: {"tool": "finish", "summary": "Read savings balance and name", "reason": "done"},
]

OPEN_SUB_ACCOUNT: Script = [
    *MEMBER_SAVINGS_BALANCE[:6],  # sign on, inquiry, search
    lambda o: {
        "tool": "click",
        "ref": ref_of(o, "link", "Open Sub-Account"),
        "reason": "Start the sub-account workflow",
    },
    lambda o: {
        "tool": "select",
        "ref": ref_of(o, "combobox", "Product:"),
        "option": "{{product}}",
        "reason": "Choose the product",
    },
    lambda o: {
        "tool": "fill",
        "ref": ref_of(o, "textbox", "Initial Deposit:"),
        "value": "{{initial_deposit}}",
        "reason": "Enter the initial deposit",
    },
    lambda o: {"tool": "click", "ref": ref_of(o, "button", "Continue"), "reason": "Review"},
    lambda o: {
        "tool": "click",
        "ref": ref_of(o, "button", "Confirm"),
        "reason": "Commit the sub-account (irreversible; needs approval)",
    },
    lambda o: {
        "tool": "extract",
        "output": "confirmation_number",
        "frame": "main",
        "locate": {"kind": "label", "label": "Confirmation #:"},
        "reason": "Read the confirmation number",
    },
    lambda o: {"tool": "finish", "summary": "Sub-account opened", "reason": "done"},
]

SCRIPTS: dict[str, Script] = {
    "harbor.member.savings_balance": MEMBER_SAVINGS_BALANCE,
    "harbor.member.open_sub_account": OPEN_SUB_ACCOUNT,
}


def offline_decider(capability_id: str) -> ScriptedDecider:
    if capability_id not in SCRIPTS:
        raise SystemExit(f"no offline script for {capability_id}; available: {sorted(SCRIPTS)}")
    return ScriptedDecider(SCRIPTS[capability_id])

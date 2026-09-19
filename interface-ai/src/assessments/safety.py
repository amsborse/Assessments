"""Guardrail policy: what automation may do, and how risky actions are treated.

Enforcement points (defense in depth):
1. `Policy.check_action` — every action the agent or replay engine takes, before it touches the
   surface.
2. Network interception on the surface — requests to non-allowlisted hosts are aborted even if a
   page script or link tries to leave the allowlist.

Risk model: fill/select/extract/navigate-within-allowlist are SAFE (no persistent effect until
submitted). Clicking a control whose name matches an irreversible pattern (app profile, plus a
generic fallback list) is IRREVERSIBLE. Irreversible actions are never taken autonomously:
- discovery → require a human approval (handoff) before the click;
- replay    → allowed only for an APPROVED capability *and* an explicit per-invocation
              `allow_irreversible=True`; otherwise require approval.
Humans acting through the operator console are recorded but not blocked: they are the control.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from assessments.capability.schema import ActionKind, Risk

GENERIC_IRREVERSIBLE = [
    r"\bconfirm\b",
    r"\bsubmit\b.*\b(payment|transfer|application)\b",
    r"\b(transfer|pay|post|approve|delete|remove|close account|disburse|wire)\b",
]
ALLOWED_KEYS = {"Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageDown"}


class Verdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    risk: Risk
    reason: str


@dataclass(frozen=True)
class Policy:
    allowed_hosts: tuple[str, ...]
    allowed_actions: frozenset[ActionKind] = frozenset(ActionKind)
    irreversible_patterns: tuple[str, ...] = tuple(GENERIC_IRREVERSIBLE)
    allowed_paths: tuple[str, ...] = (r".*",)

    def host_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme in {"data", "about", "blob"}:
            return True
        host = (parts.hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.allowed_hosts)

    def url_allowed(self, url: str) -> bool:
        path = urlsplit(url).path or "/"
        return self.host_allowed(url) and any(re.fullmatch(p, path) for p in self.allowed_paths)

    def classify(self, action: ActionKind, control_name: str | None) -> Risk:
        if action is not ActionKind.CLICK or not control_name:
            return Risk.SAFE
        name = " ".join(control_name.split())
        if any(re.search(p, name, re.IGNORECASE) for p in self.irreversible_patterns):
            return Risk.IRREVERSIBLE
        return Risk.SAFE

    def check_action(
        self,
        action: ActionKind,
        *,
        page_url: str,
        control_name: str | None = None,
        target_url: str | None = None,
        key: str | None = None,
        irreversible_authorized: bool = False,
    ) -> Decision:
        if action not in self.allowed_actions:
            return Decision(Verdict.DENY, Risk.SAFE, f"action type '{action}' is not allowlisted")
        if not self.url_allowed(page_url):
            return Decision(Verdict.DENY, Risk.SAFE, "current page is outside the allowlist")
        if target_url is not None and not self.url_allowed(target_url):
            return Decision(
                Verdict.DENY, Risk.SAFE, f"navigation target not allowlisted: {target_url}"
            )
        if action is ActionKind.PRESS and key not in ALLOWED_KEYS:
            return Decision(
                Verdict.DENY,
                Risk.SAFE,
                f"key '{key}' not allowed; click the submit control so its risk can be assessed",
            )
        risk = self.classify(action, control_name)
        if risk is Risk.IRREVERSIBLE and not irreversible_authorized:
            return Decision(
                Verdict.REQUIRE_APPROVAL, risk, f"'{control_name}' is classified irreversible"
            )
        return Decision(Verdict.ALLOW, risk, "allowed")

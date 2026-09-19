"""Control-transfer model for a live session.

Exactly one party may act on a session at a time. The state machine:

    AUTOMATION ──escalate()──▶ AWAITING_HUMAN ──claim()──▶ HUMAN ──release()──▶ AUTOMATION
         │                          │                                  (resolution decides
         └──────── end() ───▶ ENDED ◀── timeout / abort ───────────────  resume / skip / abort)

- Automation never acts unless `controller is AUTOMATION` (checked on every surface action).
- While AWAITING_HUMAN nobody acts: the surface is frozen with its state intact.
- The human holds control until they release it with an explicit resolution; automation then
  resumes on the *same* surface (same browser, cookies, page) and re-verifies before acting.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Controller(StrEnum):
    AUTOMATION = "automation"
    AWAITING_HUMAN = "awaiting_human"
    HUMAN = "human"
    ENDED = "ended"


class InterventionKind(StrEnum):
    STUCK = "stuck"  # discovery agent cannot make progress
    ESCALATION = "escalation"  # replay hit a state that needs a person
    APPROVAL = "approval"  # a risky/irreversible action needs a decision


class ResolutionAction(StrEnum):
    RESUME = "resume"  # human fixed the state; automation re-checks and continues
    SKIP_STEP = "skip_step"  # human performed the current step manually
    APPROVE = "approve"  # approve the pending risky action
    DENY = "deny"  # deny the pending risky action (run stops)
    ABORT = "abort"  # stop the run


class Resolution(BaseModel):
    action: ResolutionAction
    operator: str
    note: str = ""


class HumanAction(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    operator: str
    source: str  # "page" (captured in the live DOM) or "console" (operator API command)
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)


class InterventionRequest(BaseModel):
    id: str = Field(default_factory=lambda: f"iv_{uuid.uuid4().hex[:10]}")
    session_id: str
    run_id: str
    kind: InterventionKind
    reason: str
    context: dict[str, Any] = Field(default_factory=dict)  # goal/capability, step, state, url
    screenshot: str | None = None  # evidence path (redacted)
    allowed_resolutions: list[ResolutionAction]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "open"  # open → claimed → resolved
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    resolution: Resolution | None = None
    human_actions: list[HumanAction] = Field(default_factory=list)


class ControlError(RuntimeError):
    pass


EventSink = Callable[[str, dict[str, Any]], None]


class SessionControl:
    def __init__(self, session_id: str, on_event: EventSink | None = None) -> None:
        self.session_id = session_id
        self.controller = Controller.AUTOMATION
        self.intervention: InterventionRequest | None = None
        self.history: list[InterventionRequest] = []
        self._resolved: asyncio.Future[Resolution] | None = None
        self._on_event = on_event or (lambda _e, _d: None)

    # ---------------------------------------------------------------- guards
    def assert_automation(self) -> None:
        if self.controller is not Controller.AUTOMATION:
            raise ControlError(f"automation may not act: controller is {self.controller}")

    def assert_human(self, operator: str) -> InterventionRequest:
        iv = self.intervention
        if self.controller is not Controller.HUMAN or iv is None or iv.claimed_by != operator:
            raise ControlError(f"{operator} does not hold control of session {self.session_id}")
        return iv

    # ---------------------------------------------------------------- automation side
    async def escalate(self, request: InterventionRequest, timeout_s: float) -> Resolution:
        """Pause automation, publish the request, and wait for a human to resolve it."""
        self.assert_automation()
        loop = asyncio.get_running_loop()
        self._resolved = loop.create_future()
        self.intervention = request
        self.controller = Controller.AWAITING_HUMAN
        self._on_event("intervention_raised", request.model_dump(mode="json"))
        try:
            return await asyncio.wait_for(asyncio.shield(self._resolved), timeout=timeout_s)
        except TimeoutError:
            resolution = Resolution(
                action=ResolutionAction.ABORT, operator="system", note="handoff timed out"
            )
            self._finish(resolution)
            return resolution

    # ---------------------------------------------------------------- human side
    def claim(self, intervention_id: str, operator: str) -> InterventionRequest:
        iv = self._current(intervention_id)
        if self.controller is not Controller.AWAITING_HUMAN or iv.status != "open":
            raise ControlError(f"intervention {intervention_id} is {iv.status}, not claimable")
        iv.status, iv.claimed_by, iv.claimed_at = "claimed", operator, datetime.now(UTC)
        self.controller = Controller.HUMAN
        self._on_event("intervention_claimed", {"id": iv.id, "operator": operator})
        return iv

    def record_human_action(self, action: HumanAction) -> None:
        iv = self.assert_human(action.operator)
        iv.human_actions.append(action)
        self._on_event("human_action", action.model_dump(mode="json"))

    def release(self, intervention_id: str, resolution: Resolution) -> InterventionRequest:
        iv = self._current(intervention_id)
        self.assert_human(resolution.operator)
        if resolution.action not in iv.allowed_resolutions:
            raise ControlError(f"{resolution.action} not allowed; use {iv.allowed_resolutions}")
        self._finish(resolution)
        return iv

    def end(self) -> None:
        self.controller = Controller.ENDED
        if self._resolved is not None and not self._resolved.done():
            self._resolved.set_result(
                Resolution(action=ResolutionAction.ABORT, operator="system", note="session ended")
            )

    # ---------------------------------------------------------------- internals
    def _current(self, intervention_id: str) -> InterventionRequest:
        if self.intervention is None or self.intervention.id != intervention_id:
            raise ControlError(f"no active intervention {intervention_id}")
        return self.intervention

    def _finish(self, resolution: Resolution) -> None:
        iv = self.intervention
        if iv is not None:
            iv.status, iv.resolution = "resolved", resolution
            self.history.append(iv)
            self._on_event(
                "intervention_resolved",
                {
                    "id": iv.id,
                    "resolution": resolution.model_dump(mode="json"),
                    "human_actions": len(iv.human_actions),
                },
            )
        self.intervention = None
        # Control returns to automation, which decides from the resolution whether to continue.
        self.controller = Controller.AUTOMATION
        if self._resolved is not None and not self._resolved.done():
            self._resolved.set_result(resolution)

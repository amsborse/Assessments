import asyncio
from typing import Any

import pytest

from assessments.session.control import (
    ControlError,
    Controller,
    HumanAction,
    InterventionKind,
    InterventionRequest,
    Resolution,
    ResolutionAction,
    SessionControl,
)


def controller(control: SessionControl) -> Controller:
    return control.controller  # read via a call so mypy does not narrow across awaits


def request(control: SessionControl, **kw: Any) -> InterventionRequest:
    return InterventionRequest(
        session_id=control.session_id,
        run_id="r",
        kind=InterventionKind.ESCALATION,
        reason="blocked",
        allowed_resolutions=[ResolutionAction.RESUME, ResolutionAction.ABORT],
        **kw,
    )


async def test_full_handoff_cycle_returns_control_to_automation() -> None:
    events: list[str] = []
    control = SessionControl("s1", on_event=lambda t, _d: events.append(t))
    iv = request(control)

    waiting = asyncio.create_task(control.escalate(iv, timeout_s=5))
    await asyncio.sleep(0)
    assert controller(control) is Controller.AWAITING_HUMAN
    with pytest.raises(ControlError):
        control.assert_automation()  # nobody acts while waiting

    control.claim(iv.id, "jane")
    assert controller(control) is Controller.HUMAN
    control.record_human_action(HumanAction(operator="jane", source="console", kind="click"))
    control.release(iv.id, Resolution(action=ResolutionAction.RESUME, operator="jane"))

    resolution = await waiting
    assert resolution.action is ResolutionAction.RESUME
    assert controller(control) is Controller.AUTOMATION
    assert control.history[0].human_actions[0].kind == "click"
    assert events == [
        "intervention_raised",
        "intervention_claimed",
        "human_action",
        "intervention_resolved",
    ]


async def test_only_the_claiming_operator_can_act_or_release() -> None:
    control = SessionControl("s1")
    iv = request(control)
    waiting = asyncio.create_task(control.escalate(iv, timeout_s=5))
    await asyncio.sleep(0)
    control.claim(iv.id, "jane")

    with pytest.raises(ControlError):
        control.claim(iv.id, "bob")
    with pytest.raises(ControlError):
        control.record_human_action(HumanAction(operator="bob", source="console", kind="click"))
    with pytest.raises(ControlError):
        control.release(iv.id, Resolution(action=ResolutionAction.RESUME, operator="bob"))
    with pytest.raises(ControlError, match="not allowed"):
        control.release(iv.id, Resolution(action=ResolutionAction.APPROVE, operator="jane"))

    control.release(iv.id, Resolution(action=ResolutionAction.ABORT, operator="jane"))
    assert (await waiting).action is ResolutionAction.ABORT


async def test_unclaimed_escalation_times_out_as_abort() -> None:
    control = SessionControl("s1")

    resolution = await control.escalate(request(control), timeout_s=0.05)

    assert resolution.action is ResolutionAction.ABORT
    assert resolution.note == "handoff timed out"
    assert control.intervention is None


async def test_ending_the_session_unblocks_a_waiting_run() -> None:
    control = SessionControl("s1")
    waiting = asyncio.create_task(control.escalate(request(control), timeout_s=5))
    await asyncio.sleep(0)

    control.end()

    assert (await waiting).action is ResolutionAction.ABORT
    assert controller(control) is Controller.ENDED

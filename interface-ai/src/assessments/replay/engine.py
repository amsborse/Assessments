"""Deterministic replay: execute a capability with typed params, no model in the loop.

Per step:
  1. Wait (bounded) for the step's target to resolve uniquely. While waiting, scan known states.
  2. Check policy (allowlist, action type, irreversible gating).
  3. Act. Record if a fallback locator strategy had to be used (drift signal).
  4. Wait (bounded) for the step's checkpoint. While waiting, scan known states.
  5. Scan "sticky" states (dialogs, error pages) that can co-exist with a passing checkpoint.

Known states come from the app profile (shared by every tenant on the product) overlaid with the
capability's own. Each has a deliberate response: return a business outcome, recover (dismiss /
retry / restart), hand off to a human, or fail. Anything not recognised within the step's timeout
is a hard failure with the expected-vs-observed evidence attached.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError

from assessments.agent.decider import Decider, DecisionContext
from assessments.capability.profile import AppProfile, effective_states
from assessments.capability.schema import (
    ActionKind,
    Capability,
    Condition,
    KnownState,
    Literal_,
    OutcomeKind,
    ParamRef,
    ReviewStatus,
    Risk,
    SecretValueRef,
    Sensitivity,
    Step,
    Target,
    TextVisible,
)
from assessments.capability.values import InvalidParams, parse_output, render, validate_params
from assessments.redaction import mask_value
from assessments.replay.result import (
    DegradedLocator,
    ErrorCode,
    Handoff,
    Outcome,
    Recovery,
    ReplayResult,
    ReplayStatus,
    StepError,
)
from assessments.runs import RunRecorder
from assessments.safety import Policy, Verdict
from assessments.session.control import (
    InterventionKind,
    InterventionRequest,
    Resolution,
    ResolutionAction,
    SessionControl,
)
from assessments.surfaces.base import Resolution as TargetResolution
from assessments.surfaces.base import TargetNotFound
from assessments.surfaces.web.playwright_surface import PlaywrightWebSurface

logger = logging.getLogger(__name__)
POLL_S = 0.25


class _Terminal(Exception):
    """Stops the run with a final status."""

    def __init__(
        self,
        status: ReplayStatus,
        *,
        outcome: Outcome | None = None,
        error: StepError | None = None,
    ) -> None:
        super().__init__(status)
        self.status, self.outcome, self.error = status, outcome, error


class _Restart(Exception):
    pass


class _SkipStep(Exception):
    pass


@dataclass
class _Wait:
    """What a wait loop is waiting for, for error reporting."""

    what: str
    expected: list[str]


class ReplayEngine:
    def __init__(
        self,
        capability: Capability,
        profile: AppProfile,
        *,
        surface: PlaywrightWebSurface,
        policy: Policy,
        control: SessionControl,
        recorder: RunRecorder,
        base_url: str,
        secret_values: dict[str, str],
        allow_irreversible: bool = False,
        handoff_timeout_s: float = 300,
        propose_overlay: bool = False,
        assist: Decider | None = None,
        assist_budget: int = 1,
    ) -> None:
        self.cap = capability
        self.surface = surface
        self.policy = policy
        self.control = control
        self.rec = recorder
        self.base_url = base_url
        self.secret_values = secret_values
        self.allow_irreversible = allow_irreversible
        self.handoff_timeout_s = handoff_timeout_s
        self.propose_overlay = propose_overlay
        # Bounded, opt-in LLM repair of a single step whose target cannot be found at all.
        self.assist = assist
        self.assist_budget = assist_budget
        self._target_override: dict[str, Target] = {}
        self.states = effective_states(profile, capability)
        self.attempts: dict[str, int] = {}
        self.result = ReplayResult(
            run_id=recorder.run_id, capability=capability.ref, status=ReplayStatus.FAILED
        )
        self.params: dict[str, str] = {}
        self._irreversible_done = False
        # When a state was last handled (handoff, dismiss, retry, …). Waits restart their time
        # budget from here: time spent with a person or backing off is not the step's fault.
        self._handled_at = 0.0

    # ---------------------------------------------------------------- entry point
    async def run(self, raw_params: dict[str, Any]) -> ReplayResult:
        started = time.monotonic()
        self.rec.event(
            "run_started",
            kind="replay",
            capability=self.cap.ref,
            review=self.cap.review.status,
            params=sorted(raw_params),
            plan=[{"id": st.id, "intent": st.intent} for st in self.cap.steps],
        )
        try:
            self._prepare(raw_params)
            await self._execute()
            self.result.status = ReplayStatus.SUCCEEDED
        except _Terminal as stop:
            self.result.status, self.result.outcome, self.result.error = (
                stop.status,
                stop.outcome,
                stop.error,
            )
        except PlaywrightError as exc:
            self.result.status = ReplayStatus.FAILED
            self.result.error = await self._error(
                ErrorCode.SURFACE_ERROR, str(exc).splitlines()[0], None
            )
        self.result.dialogs = list(self.surface.dialogs)
        self.result.duration_ms = int((time.monotonic() - started) * 1000)
        self.rec.event("run_finished", **self.persisted_result())
        return self.result

    def persisted_result(self) -> dict[str, Any]:
        """The result as written to evidence: outputs masked per declared sensitivity."""
        data = self.result.model_dump(mode="json")
        data["outputs"] = {
            k: v if self.cap.outputs[k].sensitivity is Sensitivity.PUBLIC else mask_value(v)
            for k, v in self.result.outputs.items()
        }
        return data

    def _prepare(self, raw_params: dict[str, Any]) -> None:
        try:
            self.params = validate_params(self.cap.inputs, raw_params)
        except InvalidParams as exc:
            raise _Terminal(
                ReplayStatus.REJECTED,
                error=StepError(
                    code=ErrorCode.INVALID_INPUT,
                    message=str(exc),
                    details={"fields": exc.errors},
                ),
            ) from exc
        missing = [n for n in self.cap.secrets if not self.secret_values.get(n)]
        if missing:
            raise _Terminal(
                ReplayStatus.REJECTED,
                error=StepError(
                    code=ErrorCode.MISSING_SECRET,
                    message="credentials not configured: "
                    + ", ".join(self.cap.secrets[n].env for n in missing),
                ),
            )
        self.rec.add_sensitive(*self.secret_values.values())
        self.rec.add_sensitive(
            *(
                v
                for k, v in self.params.items()
                if self.cap.inputs[k].sensitivity is not Sensitivity.PUBLIC
            )
        )

    async def _execute(self) -> None:
        while True:
            try:
                await self.surface.open(urljoin(self.base_url, self.cap.entry_url))
                for step in self.cap.steps:
                    await self._run_step(step)
                    self.result.steps_completed += 1
                await self._verify_success()
                return
            except _Restart:
                self.result.steps_completed = 0
                self.result.outputs.clear()
                self.surface.drain_dialogs()
                self.rec.event("restart", reason="recoverable state requested a fresh start")

    # ---------------------------------------------------------------- one step
    async def _run_step(self, step: Step) -> None:
        self.rec.event(
            "step_started",
            step=step.id,
            action=step.action,
            intent=step.intent,
            target=step.target.description if step.target else None,
        )
        while True:
            try:
                resolution = await self._await_target(step) if step.target else None
                if resolution and resolution.rank > 0 and step.target and self.propose_overlay:
                    # Drift: re-derive verified strategies for this element *before* acting,
                    # while it is on screen. Recorded as a proposal, never applied silently.
                    fresh = await self.surface.retarget(step.target, self.params)
                    if fresh is not None:
                        self.result.proposed_targets[step.id] = fresh.model_dump(mode="json")
                await self._authorize(step)
                await self._perform(step)
                if resolution and resolution.rank > 0 and resolution.strategy and step.target:
                    degraded = DegradedLocator(
                        step_id=step.id,
                        target=step.target.description,
                        used=resolution.strategy.kind,
                        rank=resolution.rank,
                        tried=resolution.tried,
                    )
                    self.result.degraded_locators.append(degraded)
                    self.rec.event("locator_degraded", **degraded.model_dump())
                await self.surface.settle()
                if step.expect:
                    await self._await_conditions(
                        step,
                        step.expect,
                        _Wait("checkpoint", [_describe(c) for c in step.expect]),
                        step.timeout_ms,
                    )
                await self._scan_sticky(step)
                self.rec.event(
                    "step_passed",
                    step=step.id,
                    strategy=resolution.strategy.kind
                    if resolution and resolution.strategy
                    else None,
                )
                return
            except _SkipStep:
                self.rec.event("step_skipped", step=step.id, reason="performed by human operator")
                return

    async def _await_target(self, step: Step) -> TargetResolution:
        target = _req(step.target)
        budget = step.timeout_ms / 1000
        deadline = time.monotonic() + budget
        while True:
            resolution = await self.surface.resolve(target, self.params)
            if resolution.count == 1:
                return resolution
            await self._scan_states(step)
            deadline = max(deadline, self._handled_at + budget)
            if time.monotonic() > deadline:
                repaired = await self._assisted_repair(step, target)
                if repaired is not None:
                    return repaired
                raise _Terminal(
                    ReplayStatus.FAILED,
                    error=await self._error(
                        ErrorCode.TARGET_NOT_FOUND,
                        f"{target.description} not found within {step.timeout_ms}ms",
                        step,
                        expected=[f"exactly one {target.description}"],
                        details={"tried": resolution.tried},
                    ),
                )
            await asyncio.sleep(POLL_S)

    async def _await_conditions(
        self, step: Step | None, conditions: list[Condition], wait: _Wait, timeout_ms: int
    ) -> None:
        budget = timeout_ms / 1000
        deadline = time.monotonic() + budget
        while True:
            results = [await self.surface.check(c, self.params) for c in conditions]
            if all(results):
                return
            await self._scan_states(step)
            deadline = max(deadline, self._handled_at + budget)
            if time.monotonic() > deadline:
                unmet = [_describe(c) for c, ok in zip(conditions, results, strict=True) if not ok]
                where = f"after {step.id}" if step else "at end of flow"
                raise _Terminal(
                    ReplayStatus.FAILED,
                    error=await self._error(
                        ErrorCode.CHECKPOINT_FAILED,
                        f"{wait.what} not reached {where}",
                        step,
                        expected=unmet,
                    ),
                )
            await asyncio.sleep(POLL_S)

    async def _perform(self, step: Step) -> None:
        # Step shape (target/value/key/url/output present) is guaranteed by schema validation.
        if step.id in self._target_override:
            step = step.model_copy(update={"target": self._target_override[step.id]})
        match step.action:
            case ActionKind.CLICK:
                await self.surface.click(_req(step.target), self.params)
                if step.risk is Risk.IRREVERSIBLE:
                    self._irreversible_done = True
            case ActionKind.FILL:
                await self.surface.fill(_req(step.target), self.params, self._value(step))
            case ActionKind.SELECT:
                await self.surface.select(_req(step.target), self.params, self._value(step))
            case ActionKind.PRESS:
                await self.surface.press(_req(step.key))
            case ActionKind.NAVIGATE:
                url = urljoin(self.base_url, render(_req(step.url), self.params))
                await self.surface.open(url)
            case ActionKind.EXTRACT:
                output = _req(step.output)
                raw = await self.surface.read(_req(step.target), self.params)
                try:
                    self.result.outputs[output] = parse_output(self.cap.outputs[output], raw)
                except ValueError as exc:
                    raise _Terminal(
                        ReplayStatus.FAILED,
                        error=await self._error(
                            ErrorCode.OUTPUT_PARSE_ERROR,
                            f"{output}: {exc}",
                            step,
                            expected=[f"{output} as {self.cap.outputs[output].type}"],
                        ),
                    ) from exc

    def _value(self, step: Step) -> str:
        match step.value:
            case ParamRef(param=name):
                return self.params[name]
            case SecretValueRef(secret=name):
                return self.secret_values[name]
            case Literal_(literal=text):
                return render(text, self.params)
        raise ValueError(f"step {step.id} has no value")

    async def _assisted_repair(self, step: Step, target: Target) -> TargetResolution | None:
        """One bounded model call to re-find a missing element; never for irreversible steps.

        The model only chooses *which element*; the action and value come from the recorded step.
        The repaired target is verified like any recorded target and returned to the caller as a
        proposal (`proposed_targets`), so a person decides whether it becomes an overlay.
        """
        repairable = {ActionKind.CLICK, ActionKind.FILL, ActionKind.SELECT}
        if (
            self.assist is None
            or self.assist_budget <= 0
            or step.action not in repairable
            or step.risk is Risk.IRREVERSIBLE
        ):
            return None
        self.assist_budget -= 1
        obs = await self.surface.observe(screenshot=True)
        tool = {"click": "click", "fill": "fill", "select": "select"}[step.action]
        ctx = DecisionContext(
            goal=(
                f"Repair one step of a recorded flow. The step is: {step.intent}. It needs the "
                f"{step.action} target described as {target.description}, which can no longer "
                f"be found as recorded. Call `{tool}` on the element that plays this role on "
                "the current screen (any value you give is ignored). If nothing clearly "
                "matches, call request_help."
            ),
            inputs={},
            outputs={},
            secrets=[],
            history=[],
            feedback=[],
            observation=obs,
            step=1,
            max_steps=1,
        )
        decision = await self.assist.decide(ctx)
        action = decision.action
        ref = getattr(action, "ref", None)
        self.rec.event(
            "assisted_repair_decision",
            step=step.id,
            raw=decision.raw,
            error=decision.error,
            action=action.model_dump() if action else None,
        )
        if action is None or getattr(action, "tool", None) != tool or ref not in obs.elements:
            return None
        name = obs.elements[ref].name
        if self.policy.classify(ActionKind.CLICK, name) is Risk.IRREVERSIBLE:
            return None  # never let a repair land on a committing control
        fresh = await self.surface.target_for(ref, self.params)
        fresh = fresh.model_copy(update={"description": target.description})
        self._target_override[step.id] = fresh
        self.result.proposed_targets[step.id] = fresh.model_dump(mode="json")
        self.result.assisted_steps.append(
            {
                "step_id": step.id,
                "model": self.assist.model_name,
                "reason": getattr(action, "reason", ""),
                "chose": f'{obs.elements[ref].role} "{name}"',
            }
        )
        self.rec.event("assisted_repair", step=step.id, chose=name, target=fresh.description)
        resolution = await self.surface.resolve(fresh, self.params)
        return resolution if resolution.count == 1 else None

    async def _authorize(self, step: Step) -> None:
        authorized = self.allow_irreversible and self.cap.review.status is ReviewStatus.APPROVED
        name = _control_name(step)
        decision = self.policy.check_action(
            step.action,
            page_url=self.surface.current_url(),
            control_name=name,
            key=step.key,
            irreversible_authorized=authorized,
        )
        # The artifact's own risk marking is authoritative even if the name changed.
        if step.risk is Risk.IRREVERSIBLE and not authorized and decision.verdict is Verdict.ALLOW:
            decision = type(decision)(
                Verdict.REQUIRE_APPROVAL, Risk.IRREVERSIBLE, "step recorded as irreversible"
            )
        self.rec.event(
            "policy",
            step=step.id,
            verdict=decision.verdict,
            risk=decision.risk,
            reason=decision.reason,
        )
        if decision.verdict is Verdict.DENY:
            raise _Terminal(
                ReplayStatus.FAILED,
                error=await self._error(ErrorCode.POLICY_DENIED, decision.reason, step),
            )
        if decision.verdict is Verdict.REQUIRE_APPROVAL:
            resolution = await self._handoff(
                step,
                InterventionKind.APPROVAL,
                f"Approval required: {decision.reason}",
                None,
                [ResolutionAction.APPROVE, ResolutionAction.DENY, ResolutionAction.ABORT],
            )
            if resolution.action is not ResolutionAction.APPROVE:
                raise _Terminal(
                    ReplayStatus.ABORTED,
                    error=StepError(
                        code=ErrorCode.POLICY_DENIED,
                        message=f"operator did not approve: {resolution.note}",
                        step_id=step.id,
                        step_intent=step.intent,
                    ),
                )

    # ---------------------------------------------------------------- state handling
    async def _detect(self, kinds: set[OutcomeKind] | None = None) -> KnownState | None:
        for state in self.states:
            if kinds is not None and state.kind not in kinds:
                continue
            if all([await self.surface.check(c, self.params) for c in state.when]):
                return state
        return None

    async def _scan_sticky(self, step: Step | None) -> None:
        state = await self._detect(
            {OutcomeKind.BUSINESS, OutcomeKind.FAILURE, OutcomeKind.ESCALATE}
        )
        if state is not None:
            await self._handle(state, step)

    async def _scan_states(self, step: Step | None) -> None:
        state = await self._detect()
        if state is not None:
            await self._handle(state, step)  # raises for terminal states
            self._handled_at = time.monotonic()

    async def _handle(self, state: KnownState, step: Step | None) -> None:
        self.attempts[state.id] = self.attempts.get(state.id, 0) + 1
        attempt = self.attempts[state.id]
        step_id = step.id if step else "success"
        self.rec.event(
            "state_detected",
            step=step_id,
            state=state.id,
            kind=state.kind,
            response=state.response,
            attempt=attempt,
        )
        match state.kind:
            case OutcomeKind.BUSINESS:
                raise _Terminal(
                    ReplayStatus.BUSINESS_OUTCOME,
                    outcome=Outcome(code=state.id, description=state.description, step_id=step_id),
                )
            case OutcomeKind.FAILURE:
                raise _Terminal(
                    ReplayStatus.FAILED,
                    error=await self._error(
                        ErrorCode.KNOWN_FAILURE_STATE, state.description, step, state_id=state.id
                    ),
                )
            case OutcomeKind.ESCALATE:
                resolution = await self._handoff(
                    step,
                    InterventionKind.ESCALATION,
                    state.description,
                    state.id,
                    [ResolutionAction.RESUME, ResolutionAction.SKIP_STEP, ResolutionAction.ABORT],
                )
                self.attempts[state.id] = 0  # a human handled it; allow it to recur later
                if resolution.action is ResolutionAction.SKIP_STEP:
                    raise _SkipStep()
                return  # RESUME: the wait loop re-checks target/checkpoint on the same session
        # Recoverable.
        if attempt > state.max_attempts:
            raise _Terminal(
                ReplayStatus.FAILED,
                error=await self._error(
                    ErrorCode.RECOVERY_EXHAUSTED,
                    f"{state.id} persisted after {state.max_attempts} recovery attempt(s)",
                    step,
                    state_id=state.id,
                ),
            )
        self.result.recoveries.append(
            Recovery(state_id=state.id, response=state.response, step_id=step_id, attempt=attempt)
        )
        await asyncio.sleep(state.backoff_ms / 1000)
        match state.response:
            case "dismiss":
                if state.target is None:
                    raise ValueError(f"state {state.id} has no dismiss target")
                await self.surface.click(state.target, self.params)
                await self.surface.settle()
            case "retry_step":
                for cond in state.when:
                    if isinstance(cond, TextVisible):
                        await self.surface.reload_frames_with(cond.text)
                await self.surface.settle()
            case "restart":
                if self._irreversible_done or self.cap.side_effects is Risk.IRREVERSIBLE:
                    # Restarting could repeat a committed change: a person must decide.
                    await self._handoff(
                        step,
                        InterventionKind.ESCALATION,
                        f"{state.description} (restart unsafe: capability has side effects)",
                        state.id,
                        [ResolutionAction.RESUME, ResolutionAction.ABORT],
                    )
                    return
                raise _Restart()

    async def _handoff(
        self,
        step: Step | None,
        kind: InterventionKind,
        reason: str,
        state_id: str | None,
        allowed: list[ResolutionAction],
    ) -> Resolution:
        step_id = step.id if step else "success"
        png = await self.surface.screenshot(redacted=True)
        shot = self.rec.screenshot(f"handoff-{step_id}", png)
        request = InterventionRequest(
            session_id=self.control.session_id,
            run_id=self.rec.run_id,
            kind=kind,
            reason=reason,
            context={
                "capability": self.cap.ref,
                "step": step_id,
                "intent": step.intent if step else "final success checkpoint",
                "state": state_id,
                "screen": self.surface.current_url(),
            },
            screenshot=shot,
            allowed_resolutions=allowed,
        )
        resolution = await self.control.escalate(request, timeout_s=self.handoff_timeout_s)
        done = self.control.history[-1] if self.control.history else request
        self.rec.write_json(f"intervention-{request.id}.json", done.model_dump(mode="json"))
        self.result.handoffs.append(
            Handoff(
                intervention_id=request.id,
                kind=kind,
                reason=reason,
                resolution=resolution.action,
                operator=resolution.operator,
                human_actions=len(done.human_actions),
            )
        )
        if resolution.action is ResolutionAction.ABORT:
            raise _Terminal(
                ReplayStatus.ABORTED,
                error=StepError(
                    code=ErrorCode.HANDOFF_ABORTED,
                    message=resolution.note or "operator aborted",
                    step_id=step_id,
                    step_intent=step.intent if step else None,
                    state_id=state_id,
                ),
            )
        await self.surface.settle()
        return resolution

    # ---------------------------------------------------------------- success + evidence
    async def _verify_success(self) -> None:
        await self._await_conditions(
            None,
            self.cap.success,
            _Wait("success condition", [_describe(c) for c in self.cap.success]),
            10_000,
        )
        await self._scan_sticky(None)
        missing = sorted(set(self.cap.outputs) - set(self.result.outputs))
        if missing:
            raise _Terminal(
                ReplayStatus.FAILED,
                error=await self._error(
                    ErrorCode.OUTPUT_PARSE_ERROR, f"outputs not produced: {missing}", None
                ),
            )

    async def _error(
        self,
        code: ErrorCode,
        message: str,
        step: Step | None,
        *,
        expected: list[str] | None = None,
        state_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> StepError:
        evidence: dict[str, str] = {}
        observed: dict[str, Any] = {}
        try:
            observed["frame_titles"] = await self.surface.frame_titles()
            obs = await self.surface.observe(screenshot=True)
            evidence["screenshot"] = self.rec.screenshot("failure", obs.screenshot_png or b"")
            evidence["snapshot"] = self.rec.snapshot("failure", obs.text)
            observed["screen_excerpt"] = self.rec.scrub(obs.text[:600])
        except PlaywrightError:
            observed["note"] = "surface unavailable for evidence capture"
        unknown = [
            d
            for d in self.surface.dialogs
            if not any(_dialog_state_matches(s, d) for s in self.states)
        ]
        if unknown and code in {ErrorCode.CHECKPOINT_FAILED, ErrorCode.TARGET_NOT_FOUND}:
            code, message = (
                ErrorCode.UNEXPECTED_DIALOG,
                f"{message}; unexpected dialog: {unknown[-1]!r}",
            )
        return StepError(
            code=code,
            message=message,
            step_id=step.id if step else None,
            step_intent=step.intent if step else None,
            state_id=state_id,
            expected=expected or [],
            observed=observed,
            evidence=evidence,
            details=details or {},
        )


def _req[T](value: T | None) -> T:
    if value is None:
        raise ValueError("step is missing a required field")
    return value


def _control_name(step: Step) -> str | None:
    if step.target is None:
        return None
    for strategy in step.target.strategies:
        if strategy.kind == "role":
            return strategy.name
        if strategy.kind == "text":
            return strategy.text
    return step.target.description


def _describe(condition: Condition) -> str:
    data = condition.model_dump(exclude_none=True)
    kind = data.pop("kind")
    if "target" in data:
        return f"{kind}: {condition.target.description}"  # type: ignore[union-attr]
    return f"{kind}: {data}"


def _dialog_state_matches(state: KnownState, message: str) -> bool:
    import re

    return any(c.kind == "dialog" and re.search(c.message_regex, message) for c in state.when)


__all__ = ["ReplayEngine", "TargetNotFound"]

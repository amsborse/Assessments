"""LLM-driven discovery: observe → decide → (policy) → act, until the goal is met.

The loop records every successful action as a surface-neutral step (verified multi-strategy
target, templated value, risk, and the screen titles before/after for checkpoints). On success
the trace is compiled into a Capability artifact; the model transcript is kept only as evidence.

Stuck detection (→ human handoff): the model asks for help; 3 consecutive failed/invalid actions;
or the same action on the same screen 3 times in a row. Irreversible actions require approval.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Error as PlaywrightError

from assessments.agent.actions import (
    Click,
    Extract,
    Fill,
    FillSecret,
    Finish,
    Press,
    RequestHelp,
    Select,
    Wait,
)
from assessments.agent.decider import Decider, DecisionContext
from assessments.capability.schema import (
    TEMPLATE_RE,
    ActionKind,
    AppRef,
    Capability,
    Condition,
    KnownState,
    LabelStrategy,
    Literal_,
    OutputSpec,
    ParamRef,
    ParamSpec,
    Provenance,
    Risk,
    Scope,
    ScreenTitle,
    SecretRef,
    SecretValueRef,
    Sensitivity,
    Step,
    Strategy,
    TableCellStrategy,
    Target,
    TargetPresent,
    Value,
)
from assessments.capability.values import parse_output, render, templatize
from assessments.redaction import mask_value
from assessments.runs import RunRecorder
from assessments.safety import Policy, Verdict
from assessments.session.control import (
    InterventionKind,
    InterventionRequest,
    Resolution,
    ResolutionAction,
    SessionControl,
)
from assessments.surfaces.base import TargetNotFound
from assessments.surfaces.web.playwright_surface import PlaywrightWebSurface

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoverySpec:
    goal: str
    start_url: str
    capability_id: str
    title: str
    app: AppRef
    inputs: dict[str, ParamSpec]
    examples: dict[str, str]  # concrete input values for this run (never shown to the model)
    outputs: dict[str, OutputSpec]
    secrets: dict[str, SecretRef]
    outcomes: list[KnownState] = field(default_factory=list)
    max_steps: int = 30
    timeout_s: float = 900


@dataclass
class RecordedStep:
    action: ActionKind
    reason: str
    target: Target | None = None
    value: Value | None = None
    key: str | None = None
    output: str | None = None
    risk: Risk = Risk.SAFE
    titles_before: dict[str, str] = field(default_factory=dict)
    titles_after: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    status: str  # succeeded | failed | aborted
    reason: str
    run_id: str
    steps: list[RecordedStep]
    outputs: dict[str, Any]
    capability: Capability | None = None
    human_actions: int = 0


class _Abort(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


class DiscoveryAgent:
    def __init__(
        self,
        spec: DiscoverySpec,
        *,
        surface: PlaywrightWebSurface,
        decider: Decider,
        policy: Policy,
        control: SessionControl,
        recorder: RunRecorder,
        secret_values: dict[str, str],
        handoff_timeout_s: float,
    ) -> None:
        self.spec = spec
        self.surface = surface
        self.decider = decider
        self.policy = policy
        self.control = control
        self.rec = recorder
        self.secret_values = secret_values
        self.handoff_timeout_s = handoff_timeout_s
        self.goal = templatize(spec.goal, spec.examples)
        self.steps: list[RecordedStep] = []
        self.outputs: dict[str, Any] = {}
        self.history: list[str] = []
        self.human_actions = 0
        recorder.add_sensitive(*spec.examples.values(), *secret_values.values())

    # ---------------------------------------------------------------- main loop
    async def run(self) -> DiscoveryResult:
        started = time.monotonic()
        self.rec.event(
            "run_started",
            kind="discovery",
            goal=self.goal,
            model=self.decider.model_name,
            start_url=self.spec.start_url,
            inputs=list(self.spec.inputs),
            outputs=list(self.spec.outputs),
        )
        feedback: list[str] = []
        errors = 0
        repeats: list[tuple[str, str]] = []
        try:
            await self.surface.open(self.spec.start_url)
            for n in range(1, self.spec.max_steps + 1):
                if time.monotonic() - started > self.spec.timeout_s:
                    raise _Abort("failed", f"timeout after {self.spec.timeout_s:.0f}s")
                obs = await self.surface.observe()
                shot = self.rec.screenshot(f"step{n:02d}", obs.screenshot_png or b"")
                snap = self.rec.snapshot(f"step{n:02d}", obs.text)
                self.rec.event(
                    "observation", step=n, signature=obs.signature, screenshot=shot, snapshot=snap
                )
                for message in self.surface.drain_dialogs():
                    feedback.append(f"A dialog appeared and was dismissed: {message!r}")

                stuck = None
                if errors >= 3:
                    stuck = f"{errors} consecutive actions failed"
                elif len(repeats) >= 3 and len(set(repeats[-3:])) == 1:
                    stuck = "repeating the same action on the same screen"
                if stuck:
                    await self._handoff(InterventionKind.STUCK, stuck, n)
                    feedback.append(self._human_note())
                    errors, repeats = 0, []
                    continue

                ctx = DecisionContext(
                    goal=self.goal,
                    inputs={k: v.description for k, v in self.spec.inputs.items()},
                    outputs={
                        k: f"{v.description} ({v.type})" for k, v in self.spec.outputs.items()
                    },
                    secrets=list(self.spec.secrets),
                    history=self.history,
                    feedback=feedback,
                    observation=obs,
                    step=n,
                    max_steps=self.spec.max_steps,
                )
                decision = await self.decider.decide(ctx)
                action = decision.action
                self.rec.event(
                    "decision",
                    step=n,
                    raw=decision.raw,
                    error=decision.error,
                    action=action.model_dump() if action else None,
                )
                feedback = []
                if action is None:
                    errors += 1
                    feedback.append(f"Invalid response: {decision.error}. Call exactly one tool.")
                    continue
                if isinstance(action, Finish):
                    missing = sorted(set(self.spec.outputs) - set(self.outputs))
                    if missing:
                        errors += 1
                        feedback.append(f"Cannot finish: outputs not extracted yet: {missing}")
                        continue
                    self.rec.event("finish", step=n, summary=action.summary)
                    return self._succeed(action.summary)
                repeats.append((obs.signature, action.model_dump_json(exclude={"reason"})))
                try:
                    note = await self._act(n, action, obs.elements)
                    errors = 0
                    feedback.append(note)
                except (TargetNotFound, KeyError, ValueError, PlaywrightError) as exc:
                    errors += 1
                    message = str(exc).splitlines()[0][:300]
                    self.rec.event("action_failed", step=n, error=message)
                    self.history.append(f"{n}. {action.tool} FAILED: {message}")
                    feedback.append(f"Action failed: {message}")
            raise _Abort("failed", f"step budget ({self.spec.max_steps}) exhausted")
        except _Abort as stop:
            self.rec.event("run_finished", status=stop.status, reason=stop.reason)
            return DiscoveryResult(
                stop.status,
                stop.reason,
                self.rec.run_id,
                self.steps,
                self._masked_outputs(),
                human_actions=self.human_actions,
            )

    # ---------------------------------------------------------------- actions
    async def _act(self, n: int, action: Any, elements: dict[str, Any]) -> str:
        titles_before = await self.surface.frame_titles()
        ex = self.spec.examples
        step: RecordedStep
        match action:
            case Click(ref=ref, reason=reason):
                el = _element(elements, ref)
                target = await self.surface.target_for(ref, ex)
                risk = await self._authorize(n, ActionKind.CLICK, el.name)
                await self.surface.click(target, ex)
                step = RecordedStep(ActionKind.CLICK, reason, target=target, risk=risk)
                note = f"Clicked {target.description}."
            case Fill(ref=ref, value=value, reason=reason):
                _element(elements, ref)
                templ = self._template_value(value)
                target = await self.surface.target_for(ref, ex)
                await self._authorize(n, ActionKind.FILL, None)
                await self.surface.fill(target, ex, render(templ, ex))
                step = RecordedStep(ActionKind.FILL, reason, target=target, value=_value(templ))
                note = f"Filled {target.description} with {templ}."
            case FillSecret(ref=ref, secret=secret, reason=reason):
                _element(elements, ref)
                if secret not in self.spec.secrets:
                    raise ValueError(
                        f"unknown secret '{secret}'; available: {list(self.spec.secrets)}"
                    )
                target = await self.surface.target_for(ref, ex)
                await self._authorize(n, ActionKind.FILL, None)
                await self.surface.fill(target, ex, self.secret_values[secret])
                step = RecordedStep(
                    ActionKind.FILL, reason, target=target, value=SecretValueRef(secret=secret)
                )
                note = f"Filled {target.description} with secret '{secret}'."
            case Select(ref=ref, option=option, reason=reason):
                _element(elements, ref)
                templ = self._template_value(option)
                target = await self.surface.target_for(ref, ex)
                await self._authorize(n, ActionKind.SELECT, None)
                await self.surface.select(target, ex, render(templ, ex))
                step = RecordedStep(ActionKind.SELECT, reason, target=target, value=_value(templ))
                note = f"Selected {templ} in {target.description}."
            case Press(key=key, reason=reason):
                await self._authorize(n, ActionKind.PRESS, None, key=key)
                await self.surface.press(key)
                step = RecordedStep(ActionKind.PRESS, reason, key=key)
                note = f"Pressed {key}."
            case Extract(output=output, frame=frame, locate=locate, reason=reason):
                if output not in self.spec.outputs:
                    raise ValueError(
                        f"'{output}' is not a declared output: {list(self.spec.outputs)}"
                    )
                strategy: Strategy
                if locate.kind == "table_cell" and locate.row_key and locate.column:
                    strategy = TableCellStrategy(row_key=locate.row_key, column=locate.column)
                elif locate.kind == "label" and locate.label:
                    strategy = LabelStrategy(label=locate.label)
                else:
                    raise ValueError("locate needs row_key+column (table_cell) or label (label)")
                await self._authorize(n, ActionKind.EXTRACT, None)
                target = await self.surface.target_for_read(frame, strategy, ex)
                raw = await self.surface.read(target, ex)
                parsed = parse_output(self.spec.outputs[output], raw)
                self.outputs[output] = parsed
                step = RecordedStep(ActionKind.EXTRACT, reason, target=target, output=output)
                note = f"Extracted {output} = {parsed!r} from {target.description}."
            case Wait(seconds=seconds):
                await asyncio.sleep(seconds)
                self.history.append(f"{n}. wait {seconds}s")
                return f"Waited {seconds}s."
            case RequestHelp(reason=reason):
                await self._handoff(InterventionKind.STUCK, reason, n)
                self.history.append(f"{n}. request_help → human intervened")
                return self._human_note()
            case _:
                raise ValueError(f"unsupported action {action!r}")
        await self.surface.settle()
        step.titles_before = titles_before
        step.titles_after = await self.surface.frame_titles()
        self.steps.append(step)
        self.rec.event(
            "action",
            step=n,
            action=step.action,
            target=step.target.model_dump() if step.target else None,
            value=step.value.model_dump() if step.value else None,
            risk=step.risk,
            reason=step.reason,
            output=(step.output, self._mask_output(step.output)) if step.output else None,
        )
        self.history.append(
            f"{n}. {step.action} {step.target.description if step.target else step.key or ''}"
            f"{' = ' + _value_text(step.value) if step.value else ''} — ok ({step.reason})"
        )
        return note

    def _template_value(self, value: str) -> str:
        templ = templatize(value, self.spec.examples)  # canonicalize raw example values
        for name in TEMPLATE_RE.findall(templ):
            if name not in self.spec.inputs:
                raise ValueError(f"unknown input placeholder {{{{{name}}}}}")
        return templ

    async def _authorize(
        self, n: int, action: ActionKind, control_name: str | None, key: str | None = None
    ) -> Risk:
        decision = self.policy.check_action(
            action, page_url=self.surface.current_url(), control_name=control_name, key=key
        )
        self.rec.event(
            "policy",
            step=n,
            action=action,
            control=control_name,
            verdict=decision.verdict,
            risk=decision.risk,
            reason=decision.reason,
        )
        if decision.verdict is Verdict.DENY:
            raise ValueError(f"policy denied: {decision.reason}")
        if decision.verdict is Verdict.REQUIRE_APPROVAL:
            resolution = await self._handoff(
                InterventionKind.APPROVAL,
                f"Approval required: {decision.reason}",
                n,
                allowed=[ResolutionAction.APPROVE, ResolutionAction.DENY, ResolutionAction.ABORT],
            )
            if resolution.action is not ResolutionAction.APPROVE:
                raise ValueError(f"operator denied the irreversible action ({resolution.note})")
        return decision.risk

    async def _handoff(
        self,
        kind: InterventionKind,
        reason: str,
        n: int,
        allowed: list[ResolutionAction] | None = None,
    ) -> Resolution:
        png = await self.surface.screenshot(redacted=True)
        shot = self.rec.screenshot("handoff", png)
        request = InterventionRequest(
            session_id=self.control.session_id,
            run_id=self.rec.run_id,
            kind=kind,
            reason=reason,
            context={
                "goal": self.goal,
                "step": n,
                "screen": self.surface.current_url(),
                "recent_actions": self.history[-5:],
            },
            screenshot=shot,
            allowed_resolutions=allowed or [ResolutionAction.RESUME, ResolutionAction.ABORT],
        )
        resolution = await self.control.escalate(request, timeout_s=self.handoff_timeout_s)
        done = self.control.history[-1] if self.control.history else request
        self.human_actions += len(done.human_actions)
        self.rec.write_json(f"intervention-{request.id}.json", done.model_dump(mode="json"))
        if resolution.action is ResolutionAction.ABORT:
            raise _Abort("aborted", f"operator aborted: {resolution.note or kind}")
        self._last_intervention = done
        return resolution

    def _human_note(self) -> str:
        iv = getattr(self, "_last_intervention", None)
        acts = iv.human_actions if iv else []
        summary = "; ".join(f"{a.kind} {a.detail.get('name') or ''}".strip() for a in acts[-8:])
        return (
            f"A human operator took control and performed {len(acts)} action(s)"
            f"{': ' + summary if summary else ''}. Re-read the screen and continue."
        )

    def _mask_output(self, name: str | None) -> Any:
        if name is None or name not in self.outputs:
            return None
        spec = self.spec.outputs[name]
        return (
            self.outputs[name]
            if spec.sensitivity is Sensitivity.PUBLIC
            else mask_value(self.outputs[name])
        )

    def _masked_outputs(self) -> dict[str, Any]:
        return {k: self._mask_output(k) for k in self.outputs}

    # ---------------------------------------------------------------- compile
    def _succeed(self, summary: str) -> DiscoveryResult:
        capability = compile_capability(
            self.spec, self.steps, self.rec.run_id, self.decider.model_name, self.human_actions
        )
        self.rec.event(
            "run_finished",
            status="succeeded",
            summary=summary,
            outputs=self._masked_outputs(),
            capability=capability.ref,
        )
        return DiscoveryResult(
            "succeeded",
            summary,
            self.rec.run_id,
            self.steps,
            self._masked_outputs(),
            capability,
            self.human_actions,
        )


def _element(elements: dict[str, Any], ref: str) -> Any:
    if ref not in elements:
        raise KeyError(f"no element {ref} on the current screen")
    return elements[ref]


def _value(templ: str) -> Value:
    names = TEMPLATE_RE.findall(templ)
    if len(names) == 1 and TEMPLATE_RE.fullmatch(templ.strip()):
        return ParamRef(param=names[0])
    return Literal_(literal=templ)


def _value_text(value: Value | None) -> str:
    match value:
        case ParamRef(param=p):
            return f"{{{{{p}}}}}"
        case SecretValueRef(secret=s):
            return f"<secret:{s}>"
        case Literal_(literal=text):
            return repr(text)
    return ""


def _checkpoints(step: RecordedStep, examples: dict[str, str]) -> list[Condition]:
    """Every frame whose title changed (or appeared) must show its new title after the step."""
    expect: list[Condition] = []
    for key, title in step.titles_after.items():
        if title and step.titles_before.get(key) != title:
            scope = Scope(frames=key.split("/") if key else [])
            expect.append(ScreenTitle(title=templatize(title, examples), scope=scope))
    return expect


def compile_capability(
    spec: DiscoverySpec,
    steps: list[RecordedStep],
    run_id: str,
    model: str,
    human_actions: int = 0,
    version: int = 1,
) -> Capability:
    from datetime import UTC, datetime

    cap_steps = [
        Step(
            id=f"s{i:02d}",
            intent=rs.reason,
            action=rs.action,
            target=rs.target,
            value=rs.value,
            key=rs.key,
            output=rs.output,
            risk=rs.risk,
            expect=_checkpoints(rs, spec.examples),
        )
        for i, rs in enumerate(steps, 1)
    ]
    success: list[Condition] = [
        TargetPresent(target=s.target) for s in cap_steps if s.output and s.target
    ]
    # Final screen: the title of each frame holding an output (or every frame if none).
    output_frames = {"/".join(s.target.scope.frames) for s in cap_steps if s.output and s.target}
    last = steps[-1]
    for key, title in last.titles_after.items():
        if key and title and (not output_frames or key in output_frames):
            success.append(
                ScreenTitle(
                    title=templatize(title, spec.examples), scope=Scope(frames=key.split("/"))
                )
            )
    used_secrets = {s.value.secret for s in cap_steps if isinstance(s.value, SecretValueRef)}
    return Capability(
        id=spec.capability_id,
        version=version,
        title=spec.title,
        description=templatize(spec.goal, spec.examples),
        app=spec.app,
        entry_url=spec.start_url,
        inputs=spec.inputs,
        secrets={k: v for k, v in spec.secrets.items() if k in used_secrets},
        outputs=spec.outputs,
        steps=cap_steps,
        success=success,
        outcomes=spec.outcomes,
        provenance=Provenance(
            discovery_run_id=run_id,
            model=model,
            recorded_at=datetime.now(UTC),
            goal=templatize(spec.goal, spec.examples),
            human_steps=human_actions,
        ),
    )

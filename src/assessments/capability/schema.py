"""Capability artifact schema (`capability/1`).

A capability is the reusable, reviewable product of a discovery run: a typed contract (inputs,
outputs, business outcomes) plus a surface-neutral flow (steps with robust targets and
checkpoints). It deliberately contains no model transcript, no raw page content, and no secret
or example PII values — only references (`{"param": ...}`, `{"secret": ...}`).

Design rules:
- Everything the replay engine needs is explicit; nothing is inferred at replay time.
- Targets carry *several* independently verified locator strategies, ordered by robustness.
- Conditions and targets are surface-neutral (role/name/label/text/table cell); web-only
  details (CSS, frame names, URL patterns) are optional hints, so the same flow model maps to
  a desktop accessibility tree.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "capability/1"
TEMPLATE_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
IDENT = r"^[a-z][a-z0-9_]*$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- contract: inputs / outputs


class ValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    MONEY = "money"  # parsed from "$1,234.56" into a decimal string "1234.56"
    DATE = "date"
    BOOLEAN = "boolean"
    ENUM = "enum"


class Sensitivity(StrEnum):
    """Drives redaction in logs, evidence and persisted results."""

    PUBLIC = "public"  # safe to log verbatim
    INTERNAL = "internal"  # business data (e.g. balances): returned to caller, masked at rest
    PII = "pii"  # identifies a person (member #, name): never logged verbatim


class ParamSpec(Model):
    type: ValueType
    description: str
    required: bool = True
    pattern: str | None = Field(default=None, description="Regex the value must fully match.")
    enum: list[str] | None = None
    sensitivity: Sensitivity = Sensitivity.PII

    @model_validator(mode="after")
    def _enum_has_values(self) -> ParamSpec:
        if self.type is ValueType.ENUM and not self.enum:
            raise ValueError("enum params need `enum` values")
        if self.pattern is not None:
            re.compile(self.pattern)
        return self


class OutputSpec(Model):
    type: ValueType
    description: str
    sensitivity: Sensitivity = Sensitivity.INTERNAL


class SecretRef(Model):
    """A credential resolved at run time from the tenant's secret store (env in this demo)."""

    description: str
    env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")


# ---------------------------------------------------------------- targeting


class RoleStrategy(Model):
    kind: Literal["role"] = "role"
    role: str
    name: str


class LabelStrategy(Model):
    """Control whose visible label (for=, wrapping, aria, or adjacent table cell) matches."""

    kind: Literal["label"] = "label"
    label: str


class FieldNameStrategy(Model):
    """Form field `name` attribute: part of the server's POST contract, so stable across themes."""

    kind: Literal["field_name"] = "field_name"
    name: str


class TableCellStrategy(Model):
    """Cell located by row key (text of the row's key column) and column header text."""

    kind: Literal["table_cell"] = "table_cell"
    row_key: str
    column: str


class TextStrategy(Model):
    kind: Literal["text"] = "text"
    text: str
    element: Literal["any", "link", "button"] = "any"


class CssStrategy(Model):
    """Structural path. Last resort: brittle across tenants and versions."""

    kind: Literal["css"] = "css"
    selector: str


Strategy = Annotated[
    RoleStrategy
    | LabelStrategy
    | FieldNameStrategy
    | TableCellStrategy
    | TextStrategy
    | CssStrategy,
    Field(discriminator="kind"),
]

# Lower = more robust. Used to order strategies and to flag degraded matches during replay.
STRATEGY_RANK: dict[str, int] = {
    "role": 0,
    "label": 1,
    "field_name": 2,
    "table_cell": 3,
    "text": 4,
    "css": 9,
}


class Scope(Model):
    """Where on the surface to look: a frame path for web, a window/pane path for desktop."""

    frames: list[str] = Field(default_factory=list, description="Frame names, outermost first.")


class Target(Model):
    description: str = Field(description="Human-readable: what control this is.")
    scope: Scope = Field(default_factory=Scope)
    strategies: list[Strategy] = Field(min_length=1)

    @field_validator("strategies")
    @classmethod
    def _ordered_by_robustness(cls, value: list[Strategy]) -> list[Strategy]:
        return sorted(value, key=lambda s: STRATEGY_RANK[s.kind])


# ---------------------------------------------------------------- conditions / checkpoints


class TextVisible(Model):
    kind: Literal["text_visible"] = "text_visible"
    text: str = Field(description="Substring, or a regex if `regex` is true.")
    regex: bool = False
    scope: Scope | None = Field(default=None, description="None = search every frame.")


class TargetPresent(Model):
    kind: Literal["target_present"] = "target_present"
    target: Target


class DialogShown(Model):
    """A native modal dialog (alert/confirm) whose message matches."""

    kind: Literal["dialog"] = "dialog"
    message_regex: str


class ScreenTitle(Model):
    """The screen's title: document title of the frame (web) or window title (desktop)."""

    kind: Literal["screen_title"] = "screen_title"
    title: str
    scope: Scope = Field(default_factory=Scope)


class HttpStatus(Model):
    kind: Literal["http_status"] = "http_status"
    min_status: int = Field(ge=100, le=599)


Condition = Annotated[
    TextVisible | TargetPresent | ScreenTitle | DialogShown | HttpStatus,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------- steps


class ParamRef(Model):
    param: str = Field(pattern=IDENT)


class SecretValueRef(Model):
    secret: str = Field(pattern=IDENT)


class Literal_(Model):
    """A fixed value; may embed {{param}} placeholders."""

    literal: str


Value = ParamRef | SecretValueRef | Literal_


class Risk(StrEnum):
    SAFE = "safe"  # read / navigate / fill: no persistent effect
    IRREVERSIBLE = "irreversible"  # commits a change in the system of record


class ActionKind(StrEnum):
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    PRESS = "press"
    EXTRACT = "extract"
    NAVIGATE = "navigate"


class Step(Model):
    id: str = Field(pattern=r"^s\d{2,}$")
    intent: str = Field(description="Why this step exists, for reviewers.")
    action: ActionKind
    target: Target | None = None
    value: Value | None = None
    key: str | None = Field(default=None, description="For `press`.")
    url: str | None = Field(default=None, description="For `navigate`; may contain {{params}}.")
    output: str | None = Field(default=None, description="For `extract`: output name.")
    risk: Risk = Risk.SAFE
    expect: list[Condition] = Field(
        default_factory=list, description="Checkpoint: must hold after the step before moving on."
    )
    timeout_ms: int = Field(default=10_000, ge=500, le=120_000)

    @model_validator(mode="after")
    def _shape(self) -> Step:
        needs_target = {ActionKind.CLICK, ActionKind.FILL, ActionKind.SELECT, ActionKind.EXTRACT}
        if self.action in needs_target and self.target is None:
            raise ValueError(f"{self.action} step needs a target")
        if self.action in {ActionKind.FILL, ActionKind.SELECT} and self.value is None:
            raise ValueError(f"{self.action} step needs a value")
        if self.action is ActionKind.EXTRACT and not self.output:
            raise ValueError("extract step needs an output name")
        if self.action is ActionKind.PRESS and not self.key:
            raise ValueError("press step needs a key")
        if self.action is ActionKind.NAVIGATE and not self.url:
            raise ValueError("navigate step needs a url")
        return self


# ---------------------------------------------------------------- runtime states


class OutcomeKind(StrEnum):
    BUSINESS = "business_outcome"  # legitimate answer for the caller ("no such member")
    RECOVERABLE = "recoverable"  # handle and continue (dismiss, wait, retry, restart)
    ESCALATE = "escalate"  # needs a human (override, approval) — pause, hand off, resume
    FAILURE = "failure"  # stop with a debuggable error


class Response(StrEnum):
    RETURN_OUTCOME = "return_outcome"
    DISMISS = "dismiss"  # click `target` (interstitial) or accept a dialog
    RETRY_STEP = "retry_step"  # reload / re-run the current step after backoff
    RESTART = "restart"  # start the flow over (only for side-effect-free capabilities)
    HANDOFF = "handoff"
    FAIL = "fail"


class KnownState(Model):
    """A recognizable runtime state and the deliberate response to it."""

    id: str = Field(pattern=IDENT)
    description: str
    kind: OutcomeKind
    when: list[Condition] = Field(min_length=1, description="All must hold.")
    response: Response
    target: Target | None = Field(default=None, description="Control to click for `dismiss`.")
    max_attempts: int = Field(default=2, ge=1, le=5)
    backoff_ms: int = Field(default=1_000, ge=0, le=30_000)

    @model_validator(mode="after")
    def _consistent(self) -> KnownState:
        allowed = {
            OutcomeKind.BUSINESS: {Response.RETURN_OUTCOME},
            OutcomeKind.RECOVERABLE: {Response.DISMISS, Response.RETRY_STEP, Response.RESTART},
            OutcomeKind.ESCALATE: {Response.HANDOFF},
            OutcomeKind.FAILURE: {Response.FAIL},
        }
        if self.response not in allowed[self.kind]:
            raise ValueError(f"{self.kind} state cannot respond with {self.response}")
        return self


# ---------------------------------------------------------------- the artifact


class AppRef(Model):
    """Which application this runs against. `product` is the vendor product shared by tenants."""

    product: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    product_version: str = Field(description="Version range recorded against, e.g. '4.2'.")
    surface: Literal["web", "legacy_web", "desktop"] = "legacy_web"
    profile: str = Field(description="App profile id supplying shared known states / redaction.")


class ReviewStatus(StrEnum):
    DRAFT = "draft"  # produced by discovery; may only be replayed attended
    APPROVED = "approved"  # reviewed by a human; may be invoked unattended
    DEPRECATED = "deprecated"


class Review(Model):
    status: ReviewStatus = ReviewStatus.DRAFT
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    notes: str | None = None


class Provenance(Model):
    discovery_run_id: str
    model: str
    recorded_at: datetime
    goal: str = Field(description="The natural-language goal, with parameter values templated.")
    human_steps: int = Field(default=0, description="Steps performed by a human during discovery.")


class Capability(Model):
    schema_version: Literal["capability/1"] = "capability/1"
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: int = Field(ge=1)
    title: str
    description: str = Field(description="What it does, for the calling agent and reviewers.")
    app: AppRef
    entry_url: str = Field(description="Relative to the tenant's base URL, e.g. '/'.")
    inputs: dict[str, ParamSpec] = Field(default_factory=dict)
    secrets: dict[str, SecretRef] = Field(default_factory=dict)
    outputs: dict[str, OutputSpec] = Field(default_factory=dict)
    steps: list[Step] = Field(min_length=1)
    success: list[Condition] = Field(min_length=1, description="Final checkpoint.")
    outcomes: list[KnownState] = Field(
        default_factory=list,
        description="Capability-specific states; merged over the app profile's shared states.",
    )
    review: Review = Field(default_factory=Review)
    provenance: Provenance

    @property
    def side_effects(self) -> Risk:
        irreversible = any(s.risk is Risk.IRREVERSIBLE for s in self.steps)
        return Risk.IRREVERSIBLE if irreversible else Risk.SAFE

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    @model_validator(mode="after")
    def _references_resolve(self) -> Capability:
        step_ids = [s.id for s in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("duplicate step ids")
        for step in self.steps:
            for name in _templates_in(step):
                if name not in self.inputs:
                    raise ValueError(f"step {step.id} references unknown input {{{{{name}}}}}")
            if isinstance(step.value, ParamRef) and step.value.param not in self.inputs:
                raise ValueError(f"step {step.id} references unknown input {step.value.param}")
            if isinstance(step.value, SecretValueRef) and step.value.secret not in self.secrets:
                raise ValueError(f"step {step.id} references unknown secret {step.value.secret}")
            if step.output is not None and step.output not in self.outputs:
                raise ValueError(f"step {step.id} extracts undeclared output {step.output}")
        extracted = {s.output for s in self.steps if s.output}
        missing = set(self.outputs) - extracted
        if missing:
            raise ValueError(f"outputs never extracted: {sorted(missing)}")
        return self


def _templates_in(step: Step) -> set[str]:
    blob = step.model_dump_json(include={"target", "url", "expect", "value"})
    return set(TEMPLATE_RE.findall(blob))


def capability_json_schema() -> dict[str, Any]:
    return Capability.model_json_schema()

"""Replay result contract returned to the calling agent.

Status is the first thing a caller branches on:
- succeeded         → `outputs` holds every declared output, typed.
- business_outcome  → a legitimate answer (e.g. member_not_found); `outcome.code` is one of the
                      codes the capability declares. Not an error; do not retry.
- rejected          → nothing was done on the surface (invalid params, missing credentials).
- failed            → hard failure; `error` says which step, what was expected, what was
                      observed, and where the evidence is. Retrying blindly will not help.
- aborted           → a human (or handoff timeout) stopped the run.

Recoverable conditions never surface as a status: they are handled and listed in `recoveries`.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReplayStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BUSINESS_OUTCOME = "business_outcome"
    REJECTED = "rejected"
    FAILED = "failed"
    ABORTED = "aborted"


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    MISSING_SECRET = "missing_secret"  # noqa: S105 - an error code
    POLICY_DENIED = "policy_denied"
    TARGET_NOT_FOUND = "target_not_found"
    CHECKPOINT_FAILED = "checkpoint_failed"
    KNOWN_FAILURE_STATE = "known_failure_state"
    UNEXPECTED_DIALOG = "unexpected_dialog"
    OUTPUT_PARSE_ERROR = "output_parse_error"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    HANDOFF_ABORTED = "handoff_aborted"
    SURFACE_ERROR = "surface_error"


class StepError(BaseModel):
    code: ErrorCode
    message: str
    step_id: str | None = None
    step_intent: str | None = None
    state_id: str | None = None
    expected: list[str] = Field(default_factory=list)
    observed: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class Outcome(BaseModel):
    code: str
    description: str
    step_id: str | None = None


class Recovery(BaseModel):
    state_id: str
    response: str
    step_id: str
    attempt: int


class DegradedLocator(BaseModel):
    """A target resolved only via a fallback strategy: a drift signal worth reviewing."""

    step_id: str
    target: str
    used: str
    rank: int
    tried: list[str]


class Handoff(BaseModel):
    intervention_id: str
    kind: str
    reason: str
    resolution: str
    operator: str
    human_actions: int


class ReplayResult(BaseModel):
    run_id: str
    capability: str
    status: ReplayStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome: Outcome | None = None
    error: StepError | None = None
    recoveries: list[Recovery] = Field(default_factory=list)
    degraded_locators: list[DegradedLocator] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    dialogs: list[str] = Field(default_factory=list)
    steps_completed: int = 0
    duration_ms: int = 0

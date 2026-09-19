"""Confidence scoring: certify a capability by replaying golden cases repeatedly.

A capability earns unattended use by evidence, not by a reviewer's say-so alone. Certification
replays every golden case N times and scores:

- **pass rate** — runs whose status/outcome/outputs matched the golden expectation;
- **primary-locator rate** — step resolutions that used the first (most robust) strategy; falls
  as the UI drifts, before anything actually breaks;
- **redundancy** — steps whose target has a single strategy (or only a CSS path) are *fragile*;
- **latency** — p50/p95 run duration.

`confidence = pass_rate * primary_locator_rate`. Approval requires confidence ≥ 0.95 over at least
five runs per case (`capabilities approve` enforces it unless forced).

    catalog/certification/<capability>.json            golden cases
    catalog/capabilities/<id>/v<N>.certification.json  latest report for that version
"""

import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from assessments.capability.schema import Capability, Model

APPROVAL_CONFIDENCE = 0.95
APPROVAL_MIN_RUNS = 5


class Expectation(Model):
    status: str
    outcome: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)


class GoldenCase(Model):
    name: str
    params: dict[str, Any]
    expect: Expectation


class GoldenSet(Model):
    capability: str
    description: str = ""
    cases: list[GoldenCase] = Field(min_length=1)


class CaseResult(Model):
    name: str
    runs: int
    passed: int
    failures: list[str] = Field(default_factory=list, description="What differed, per failed run.")


class CertificationReport(Model):
    capability: str
    certified_at: datetime
    tenant: str | None
    runs_per_case: int
    cases: list[CaseResult]
    pass_rate: float
    primary_locator_rate: float
    fragile_steps: list[str]
    p50_ms: int
    p95_ms: int
    confidence: float
    eligible_for_approval: bool


def check(expect: Expectation, result: dict[str, Any]) -> str | None:
    """None if the replay result matches the expectation, else what differed."""
    if result["status"] != expect.status:
        return f"status {result['status']} != {expect.status}"
    if expect.outcome and (result.get("outcome") or {}).get("code") != expect.outcome:
        return f"outcome {(result.get('outcome') or {}).get('code')} != {expect.outcome}"
    for key, value in expect.outputs.items():
        if str(result.get("outputs", {}).get(key)) != str(value):
            return f"output {key} differed"
    return None


def fragile_steps(capability: Capability) -> list[str]:
    fragile = []
    for step in capability.steps:
        if step.target is None:
            continue
        kinds = [s.kind for s in step.target.strategies]
        if len(kinds) < 2 or kinds == ["css"]:
            fragile.append(step.id)
    return fragile


def score(
    capability: Capability,
    golden: GoldenSet,
    results: dict[str, list[dict[str, Any]]],
    *,
    tenant: str | None = None,
) -> CertificationReport:
    cases: list[CaseResult] = []
    durations: list[int] = []
    resolutions = degraded = 0
    for case in golden.cases:
        runs = results.get(case.name, [])
        failures = [f for r in runs if (f := check(case.expect, r)) is not None]
        cases.append(
            CaseResult(
                name=case.name, runs=len(runs), passed=len(runs) - len(failures), failures=failures
            )
        )
        for r in runs:
            durations.append(int(r.get("duration_ms", 0)))
            resolutions += int(r.get("steps_completed", 0))
            degraded += len(r.get("degraded_locators", []))
    total = sum(c.runs for c in cases)
    pass_rate = sum(c.passed for c in cases) / total if total else 0.0
    primary = 1 - degraded / resolutions if resolutions else 1.0
    confidence = round(pass_rate * primary, 4)
    ordered = sorted(durations) or [0]
    runs_per_case = min((c.runs for c in cases), default=0)
    return CertificationReport(
        capability=capability.ref,
        certified_at=datetime.now(UTC),
        tenant=tenant,
        runs_per_case=runs_per_case,
        cases=cases,
        pass_rate=round(pass_rate, 4),
        primary_locator_rate=round(primary, 4),
        fragile_steps=fragile_steps(capability),
        p50_ms=int(statistics.median(ordered)),
        p95_ms=ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        confidence=confidence,
        eligible_for_approval=confidence >= APPROVAL_CONFIDENCE
        and runs_per_case >= APPROVAL_MIN_RUNS,
    )


def golden_path(catalog_dir: Path, capability_id: str) -> Path:
    return catalog_dir / "certification" / f"{capability_id}.json"


def report_path(catalog_dir: Path, capability: Capability) -> Path:
    return (
        catalog_dir / "capabilities" / capability.id / f"v{capability.version}.certification.json"
    )


def load_report(catalog_dir: Path, capability: Capability) -> CertificationReport | None:
    path = report_path(catalog_dir, capability)
    if not path.exists():
        return None
    return CertificationReport.model_validate_json(path.read_text(encoding="utf-8"))

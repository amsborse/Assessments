from datetime import UTC, datetime
from typing import Any

import pytest

from assessments.capability.certification import GoldenSet, fragile_steps, score
from assessments.capability.overlay import Overlay, OverlayMismatch, apply_overlay
from assessments.capability.schema import Capability, Target, TargetPresent
from assessments.mcp_server import input_schema

from .test_schema import capability, target


def cap() -> Capability:
    return Capability.model_validate(
        capability(
            success=[
                {"kind": "screen_title", "title": "Detail"},
                {
                    "kind": "target_present",
                    "target": target(
                        {"kind": "table_cell", "row_key": "Savings", "column": "Balance"}
                    ),
                },
            ]
        )
    )


def overlay(**changes: Any) -> Overlay:
    data: dict[str, Any] = {
        "tenant": "bayside",
        "capability": "app.thing.read",
        "base_version": 1,
        "targets": {
            "s02": target({"kind": "table_cell", "row_key": "Savings", "column": "Current Balance"})
        },
        "provenance": {
            "proposed_from_run": "r",
            "proposed_at": datetime.now(UTC),
            "reason": "drift",
        },
    }
    data.update(changes)
    return Overlay.model_validate(data)


def test_overlay_replaces_step_targets_and_matching_checkpoints() -> None:
    patched = apply_overlay(cap(), overlay())

    new = Target.model_validate(
        target({"kind": "table_cell", "row_key": "Savings", "column": "Current Balance"})
    )
    assert patched.steps[1].target == new
    assert patched.steps[0] == cap().steps[0]  # untouched steps stay identical
    present = [c for c in patched.success if isinstance(c, TargetPresent)]
    assert [c.target for c in present] == [new]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"base_version": 2}, "not app.thing.read@v1"),
        ({"targets": {"s99": target({"kind": "text", "text": "x"})}}, "unknown steps"),
    ],
)
def test_overlay_must_match_version_and_steps(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(OverlayMismatch, match=message):
        apply_overlay(cap(), overlay(**changes))


def _golden() -> GoldenSet:
    return GoldenSet.model_validate(
        {
            "capability": "app.thing.read",
            "cases": [
                {
                    "name": "ok",
                    "params": {"member_id": "1"},
                    "expect": {"status": "succeeded", "outputs": {"balance": "10.00"}},
                },
                {
                    "name": "missing",
                    "params": {"member_id": "2"},
                    "expect": {"status": "business_outcome", "outcome": "member_not_found"},
                },
            ],
        }
    )


def _run(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "duration_ms": 1000,
        "steps_completed": 2,
        "degraded_locators": [],
        **extra,
    }


def test_certification_scores_pass_rate_and_locator_health() -> None:
    ok = _run("succeeded", outputs={"balance": "10.00"})
    drifted = _run(
        "succeeded", outputs={"balance": "10.00"}, degraded_locators=[{"step_id": "s01"}]
    )
    missing = _run("business_outcome", outcome={"code": "member_not_found"})
    wrong = _run("failed")

    report = score(
        cap(), _golden(), {"ok": [ok] * 4 + [drifted], "missing": [missing] * 4 + [wrong]}
    )

    assert report.pass_rate == 0.9  # 9 of 10 runs matched
    assert report.primary_locator_rate == 1 - 1 / 20  # one fallback in 20 step resolutions
    assert report.confidence == round(0.9 * 0.95, 4)
    assert report.cases[1].failures == ["status failed != business_outcome"]
    assert report.eligible_for_approval is False


def test_certification_requires_enough_runs_even_when_perfect() -> None:
    ok = _run("succeeded", outputs={"balance": "10.00"})
    missing = _run("business_outcome", outcome={"code": "member_not_found"})

    few = score(cap(), _golden(), {"ok": [ok], "missing": [missing]})
    many = score(cap(), _golden(), {"ok": [ok] * 5, "missing": [missing] * 5})

    assert (few.confidence, few.eligible_for_approval) == (1.0, False)
    assert (many.confidence, many.eligible_for_approval) == (1.0, True)


def test_single_strategy_targets_are_fragile() -> None:
    assert fragile_steps(cap()) == ["s01", "s02"]  # each test target has one strategy


def test_mcp_input_schema_exposes_types_and_constraints() -> None:
    schema = input_schema(cap())

    assert schema["required"] == ["member_id"]
    assert schema["properties"]["member_id"]["type"] == "string"
    assert schema["additionalProperties"] is False

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from assessments.capability.profile import AppProfile, effective_states
from assessments.capability.schema import Capability, KnownState, Target


def target(*strategies: dict[str, Any]) -> dict[str, Any]:
    return {"description": "x", "strategies": list(strategies)}


def capability(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "app.thing.read",
        "version": 1,
        "title": "t",
        "description": "d",
        "app": {"product": "app", "product_version": "1", "profile": "p"},
        "entry_url": "/",
        "inputs": {"member_id": {"type": "string", "description": "m"}},
        "outputs": {"balance": {"type": "money", "description": "b"}},
        "steps": [
            {
                "id": "s01",
                "intent": "type",
                "action": "fill",
                "target": target({"kind": "field_name", "name": "mbr"}),
                "value": {"param": "member_id"},
            },
            {
                "id": "s02",
                "intent": "read",
                "action": "extract",
                "output": "balance",
                "target": target({"kind": "table_cell", "row_key": "Savings", "column": "Balance"}),
            },
        ],
        "success": [{"kind": "screen_title", "title": "Detail"}],
        "provenance": {
            "discovery_run_id": "r",
            "model": "m",
            "recorded_at": datetime.now(UTC),
            "goal": "g",
        },
    }
    base.update(overrides)
    return base


def test_valid_capability_round_trips_through_json() -> None:
    cap = Capability.model_validate(capability())

    assert Capability.model_validate_json(cap.model_dump_json()) == cap
    assert cap.ref == "app.thing.read@v1"


def test_strategies_are_ordered_by_robustness_regardless_of_input_order() -> None:
    t = Target.model_validate(
        target(
            {"kind": "css", "selector": "body > a"},
            {"kind": "text", "text": "Go"},
            {"kind": "role", "role": "link", "name": "Go"},
        )
    )

    assert [s.kind for s in t.strategies] == ["role", "text", "css"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"inputs": {}}, "unknown input member_id"),
        (
            {
                "outputs": {
                    "balance": {"type": "money", "description": "b"},
                    "other": {"type": "string", "description": "o"},
                }
            },
            "never extracted",
        ),
        (
            {
                "secrets": {},
                "steps": [
                    {
                        "id": "s01",
                        "intent": "pw",
                        "action": "fill",
                        "target": target({"kind": "label", "label": "PW"}),
                        "value": {"secret": "pw"},
                    }
                ],
                "outputs": {},
            },
            "unknown secret pw",
        ),
    ],
)
def test_references_must_resolve(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Capability.model_validate(capability(**change))


def test_templates_in_targets_must_reference_declared_inputs() -> None:
    steps = capability()["steps"]
    steps[1]["target"] = target({"kind": "table_cell", "row_key": "{{acct}}", "column": "Balance"})

    with pytest.raises(ValidationError, match="unknown input"):
        Capability.model_validate(capability(steps=steps))


def test_step_shape_is_enforced() -> None:
    with pytest.raises(ValidationError, match="click step needs a target"):
        Capability.model_validate(
            capability(steps=[{"id": "s01", "intent": "x", "action": "click"}])
        )


@pytest.mark.parametrize(
    ("kind", "response"),
    [("business_outcome", "restart"), ("failure", "dismiss"), ("escalate", "return_outcome")],
)
def test_known_state_response_must_match_its_kind(kind: str, response: str) -> None:
    with pytest.raises(ValidationError, match="cannot respond"):
        KnownState.model_validate(
            {
                "id": "s",
                "description": "d",
                "kind": kind,
                "response": response,
                "when": [{"kind": "text_visible", "text": "x"}],
            }
        )


def test_capability_states_override_profile_states_by_id() -> None:
    def state(id_: str, text: str) -> dict[str, Any]:
        return {
            "id": id_,
            "description": text,
            "kind": "failure",
            "response": "fail",
            "when": [{"kind": "text_visible", "text": text}],
        }

    profile = AppProfile(
        id="p",
        product="app",
        description="d",
        known_states=[
            KnownState.model_validate(state("shared", "a")),
            KnownState.model_validate(state("common", "b")),
        ],
    )
    cap = Capability.model_validate(capability(outcomes=[state("common", "override")]))

    states = effective_states(profile, cap)

    assert [(s.id, s.description) for s in states] == [("common", "override"), ("shared", "a")]

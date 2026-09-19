"""Discovery loop behaviour with a scripted decider: recording, canonicalization, stuck handling."""

import asyncio
import shutil
from pathlib import Path
from typing import Any

import httpx2 as httpx

from assessments.agent.decider import ScriptedDecider, ref_of
from assessments.agent.offline import MEMBER_SAVINGS_BALANCE
from assessments.api.app import create_app
from assessments.capability.schema import ParamRef, Sensitivity
from assessments.config import Settings
from assessments.operator_sim import SimulatedOperator
from assessments.service import load_task, run_discovery

from .conftest import REPO, make_settings

TASK = load_task(REPO / "catalog" / "tasks" / "member_savings_balance.json")


def fresh_settings(tmp_path: Path, bank_a: str) -> Settings:
    shutil.copytree(REPO / "catalog" / "profiles", tmp_path / "catalog" / "profiles")
    return make_settings(tmp_path, bank_a)


async def test_raw_example_values_typed_by_the_model_are_canonicalized(
    tmp_path: Path, bank_a: str
) -> None:
    script = list(MEMBER_SAVINGS_BALANCE)
    script[4] = lambda o: {
        "tool": "fill",
        "ref": ref_of(o, "textbox", "Member #:"),
        "value": "12345",
        "reason": "model typed the raw value",
    }

    result, _ = await run_discovery(
        fresh_settings(tmp_path, bank_a),
        TASK,
        {"member_id": "12345"},
        ScriptedDecider(script),
        base_url=bank_a,
        headless=True,
    )

    assert result.status == "succeeded"
    assert result.capability is not None
    assert result.capability.steps[4].value == ParamRef(param="member_id")
    assert TASK.inputs["member_id"].sensitivity is Sensitivity.PII
    assert "12345" not in result.capability.model_dump_json()


async def test_repeated_failures_escalate_and_timeout_aborts(tmp_path: Path, bank_a: str) -> None:
    settings = fresh_settings(tmp_path, bank_a).model_copy(update={"handoff_timeout_s": 5})
    bad = [lambda o: {"tool": "click", "ref": "e999", "reason": "hallucinated ref"}] * 3

    result, saved = await run_discovery(
        settings, TASK, {"member_id": "12345"}, ScriptedDecider(bad), base_url=bank_a, headless=True
    )

    assert result.status == "aborted"
    assert "timed out" in result.reason
    assert saved is None
    events = (settings.data_dir / "runs" / result.run_id / "events.jsonl").read_text()
    assert '"type": "intervention_raised"' in events
    assert "3 consecutive actions failed" in events


async def test_request_help_hands_off_and_agent_continues(tmp_path: Path, bank_a: str) -> None:
    settings = fresh_settings(tmp_path, bank_a)
    script: list[Any] = [
        *MEMBER_SAVINGS_BALANCE[:3],
        lambda o: {"tool": "request_help", "reason": "unsure which menu entry to use"},
        *MEMBER_SAVINGS_BALANCE[3:],
    ]
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        sim = SimulatedOperator(client, think_s=0.1, resume_stuck=True)
        sim_task = asyncio.create_task(sim.run())
        try:
            result, _ = await run_discovery(
                settings,
                TASK,
                {"member_id": "12345"},
                ScriptedDecider(script),
                base_url=bank_a,
                headless=True,
            )
        finally:
            sim_task.cancel()

    assert result.status == "succeeded", result.reason
    assert len(sim.handled) == 1
    assert result.capability is not None
    assert len(result.capability.steps) == 8  # the help request is not part of the flow

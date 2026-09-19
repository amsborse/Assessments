"""Human-in-the-loop: escalation, control transfer on the live session, approval of risky steps.

The simulated operator acts only through the operator console API, served in-process (same
event loop as the browser) via an ASGI transport — the same wiring the CLI uses.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2 as httpx

from assessments.agent.offline import offline_decider
from assessments.api.app import create_app
from assessments.capability.schema import Risk
from assessments.capability.store import CapabilityStore
from assessments.config import Settings
from assessments.operator_sim import SimulatedOperator
from assessments.replay.result import ErrorCode, ReplayStatus
from assessments.service import load_task, run_discovery, run_replay

from .conftest import CAPABILITY, REPO

SUB_ACCOUNT = "harbor.member.open_sub_account"
SUB_PARAMS = {"member_id": "12345", "product": "Vacation Club", "initial_deposit": "25.00"}


@asynccontextmanager
async def operator(settings: Settings, **kw: Any) -> AsyncIterator[SimulatedOperator]:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        sim = SimulatedOperator(client, **{"think_s": 0.1, **kw})
        task = asyncio.create_task(sim.run())
        try:
            yield sim
        finally:
            task.cancel()


def _intervention_files(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    run_dir = settings.data_dir / "runs" / run_id
    return [json.loads(p.read_text(encoding="utf-8")) for p in run_dir.glob("intervention-*.json")]


async def test_restricted_record_escalates_and_human_override_resumes_same_session(
    settings: Settings,
) -> None:
    async with operator(settings, supervisor_id="sup01", supervisor_pin="4321"):
        result = await run_replay(
            settings,
            CAPABILITY,
            {"member_id": "20417"},
            base_url=settings.target_base_url,
            headless=True,
        )

    assert result.status is ReplayStatus.SUCCEEDED, result.error
    assert result.outputs["savings_balance"] == "7780.00"
    [handoff] = result.handoffs
    assert (handoff.kind, handoff.resolution) == ("escalation", "resume")
    [iv] = _intervention_files(settings, result.run_id)
    assert iv["context"]["state"] == "supervisor_override_required"
    console = [a for a in iv["human_actions"] if a["source"] == "console"]
    assert [a["kind"] for a in console] == ["click", "type", "click", "type", "click"]
    assert all("text" not in a["detail"] for a in console)  # typed values are never recorded
    assert "4321" not in json.dumps(iv)
    # What the person did is also captured in the page, with the element they touched.
    # Regression: the in-page recorder waited for a flag nothing set, so it never reported.
    page = [a for a in iv["human_actions"] if a["source"] == "page"]
    assert {"name": "Supervisor ID:", "length": 5} in [
        {"name": a["detail"]["name"], "length": a["detail"]["length"]}
        for a in page
        if a["kind"] == "fill"
    ]


async def test_operator_abort_stops_the_run(settings: Settings) -> None:
    async with operator(settings):  # no supervisor credentials → no playbook → abort
        result = await run_replay(
            settings,
            CAPABILITY,
            {"member_id": "20417"},
            base_url=settings.target_base_url,
            headless=True,
        )

    assert result.status is ReplayStatus.ABORTED
    assert result.error is not None
    assert result.error.code is ErrorCode.HANDOFF_ABORTED


async def test_unanswered_escalation_times_out_and_aborts(settings: Settings) -> None:
    settings = settings.model_copy(update={"handoff_timeout_s": 5})

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "20417"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.ABORTED
    assert result.error is not None
    assert "timed out" in result.error.message


async def _record_sub_account(settings: Settings, bank_a: str) -> None:
    task = load_task(REPO / "catalog" / "tasks" / "open_sub_account.json")
    async with operator(settings, approve=True) as sim:
        result, _ = await run_discovery(
            settings, task, SUB_PARAMS, offline_decider(SUB_ACCOUNT), base_url=bank_a, headless=True
        )
    assert result.status == "succeeded", result.reason
    assert len(sim.handled) == 1  # the Confirm click needed approval during discovery


async def test_irreversible_step_is_recorded_and_gated_on_replay(
    settings: Settings, bank_a: str
) -> None:
    await _record_sub_account(settings, bank_a)
    cap = CapabilityStore(settings.catalog_dir).load(SUB_ACCOUNT)
    risky = [s for s in cap.steps if s.risk is Risk.IRREVERSIBLE]
    assert [s.intent for s in risky] == ["Commit the sub-account (irreversible; needs approval)"]
    assert cap.side_effects is Risk.IRREVERSIBLE

    # Draft capability: an operator must approve the irreversible step, even with the flag.
    async with operator(settings, approve=False):
        denied = await run_replay(
            settings,
            SUB_ACCOUNT,
            SUB_PARAMS,
            base_url=bank_a,
            headless=True,
            allow_irreversible=True,
        )
    assert denied.status is ReplayStatus.ABORTED
    assert denied.error is not None
    assert denied.error.code is ErrorCode.POLICY_DENIED

    # Approved capability + explicit per-invocation consent: runs unattended.
    CapabilityStore(settings.catalog_dir).approve(SUB_ACCOUNT, reviewer="reviewer.alex")
    ok = await run_replay(
        settings, SUB_ACCOUNT, SUB_PARAMS, base_url=bank_a, headless=True, allow_irreversible=True
    )
    assert ok.status is ReplayStatus.SUCCEEDED, ok.error
    assert str(ok.outputs["confirmation_number"]).startswith("HC")
    assert ok.handoffs == []


async def test_business_validation_declared_by_the_task_is_an_outcome(
    settings: Settings, bank_a: str
) -> None:
    await _record_sub_account(settings, bank_a)

    result = await run_replay(
        settings,
        SUB_ACCOUNT,
        {**SUB_PARAMS, "initial_deposit": "1.00"},
        base_url=bank_a,
        headless=True,
    )

    assert result.status is ReplayStatus.BUSINESS_OUTCOME
    assert result.outcome is not None
    assert result.outcome.code == "deposit_below_minimum"


def test_recorded_artifacts_contain_no_example_values(recorded_catalog: Path) -> None:
    blob = "".join(p.read_text(encoding="utf-8") for p in recorded_catalog.rglob("v*.json"))
    assert "12345" not in blob
    assert "harbor-demo" not in blob
    assert "{{member_id}}" in blob or '"param": "member_id"' in blob


async def test_resume_after_a_slow_human_gets_a_fresh_step_timeout(settings: Settings) -> None:
    # Regression: the step deadline kept running during the handoff, so any person slower than the
    # step timeout (10s) came back to an already-expired wait and the run failed at the checkpoint.
    async with operator(settings, supervisor_id="sup01", supervisor_pin="4321", think_s=12):
        result = await run_replay(
            settings,
            CAPABILITY,
            {"member_id": "20417"},
            base_url=settings.target_base_url,
            headless=True,
        )

    assert result.status is ReplayStatus.SUCCEEDED, result.error
    assert [h.resolution for h in result.handoffs] == ["resume"]

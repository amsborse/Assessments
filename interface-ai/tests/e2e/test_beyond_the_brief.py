"""Tenant overlays from drift, bounded assisted repair, certification, and the MCP interface."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from assessments.agent.decider import ScriptedDecider, ref_of
from assessments.capability.store import CapabilityStore
from assessments.config import Settings
from assessments.mcp_server import build_server
from assessments.replay.result import ErrorCode, ReplayStatus
from assessments.service import certify, propose_overlay, run_replay

from .conftest import CAPABILITY


def add_tenant(settings: Settings, tenant: str, url: str) -> None:
    folder = settings.catalog_dir / "tenants"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{tenant}.json").write_text(
        json.dumps(
            {"id": tenant, "name": tenant, "product": "harbor-teller-console", "base_url": url}
        )
    )


async def test_drift_becomes_a_reviewed_overlay_that_removes_it(
    settings: Settings, bank_b: str
) -> None:
    add_tenant(settings, "bayside", bank_b)

    before, path = await propose_overlay(
        settings, CAPABILITY, {"member_id": "12345"}, tenant="bayside"
    )
    assert before.status is ReplayStatus.SUCCEEDED
    assert path is not None
    assert sorted(before.proposed_targets) == sorted(d.step_id for d in before.degraded_locators)
    proposal = json.loads(path.read_text(encoding="utf-8"))
    assert proposal["review"]["status"] == "draft"
    # The relabelled link is now found by its new role/name, not a structural path.
    s04 = proposal["targets"]["s04"]["strategies"][0]
    assert (s04["kind"], s04["name"]) == ("role", "Member Lookup")

    draft_ignored = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, tenant="bayside", headless=True
    )
    assert draft_ignored.overlay is None  # drafts are never applied unattended
    assert draft_ignored.degraded_locators

    from assessments.capability.overlay import TenantStore

    cap = CapabilityStore(settings.catalog_dir).load(CAPABILITY)
    TenantStore(settings.catalog_dir).approve_overlay("bayside", cap, "reviewer.alex")
    after = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, tenant="bayside", headless=True
    )

    assert after.status is ReplayStatus.SUCCEEDED
    assert after.overlay == "bayside (approved)"
    assert after.degraded_locators == []
    assert after.outputs["savings_balance"] == "2418.07"


async def test_missing_element_fails_without_assist_and_is_repaired_with_it(
    settings: Settings, bank_c: str
) -> None:
    plain = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, base_url=bank_c, headless=True
    )
    assert plain.status is ReplayStatus.FAILED
    assert plain.error is not None
    assert plain.error.code is ErrorCode.TARGET_NOT_FOUND
    assert plain.error.step_id == "s04"

    repair = ScriptedDecider(
        [
            lambda o: {
                "tool": "click",
                "ref": ref_of(o, "link", "Find a Member"),
                "reason": "the inquiry menu entry was renamed",
            }
        ]
    )
    assisted = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, base_url=bank_c, headless=True, assist=repair
    )

    assert assisted.status is ReplayStatus.SUCCEEDED, assisted.error
    assert [a["step_id"] for a in assisted.assisted_steps] == ["s04"]
    assert assisted.assisted_steps[0]["chose"] == 'link "Find a Member"'
    assert assisted.proposed_targets["s04"]["strategies"][0]["name"] == "Find a Member"


async def test_a_wrong_repair_is_caught_by_the_checkpoint_not_trusted(
    settings: Settings, bank_c: str
) -> None:
    # A confused model points the repair at the wrong link. The engine does not trust the model:
    bad = ScriptedDecider(
        [lambda o: {"tool": "click", "ref": ref_of(o, "link", "Sign Off"), "reason": "wrong"}]
    )
    result = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, base_url=bank_c, headless=True, assist=bad
    )

    # the next checkpoint is still verified, so the run fails loudly instead of "succeeding".
    assert result.status is ReplayStatus.FAILED
    assert result.assisted_steps  # the repair is visible in the result for review


async def test_certification_scores_golden_cases(settings: Settings) -> None:
    (settings.catalog_dir / "certification").mkdir(exist_ok=True)
    (settings.catalog_dir / "certification" / f"{CAPABILITY}.json").write_text(
        json.dumps(
            {
                "capability": CAPABILITY,
                "cases": [
                    {
                        "name": "member",
                        "params": {"member_id": "48213"},
                        "expect": {
                            "status": "succeeded",
                            "outputs": {"savings_balance": "15032.90"},
                        },
                    },
                    {
                        "name": "unknown",
                        "params": {"member_id": "99999"},
                        "expect": {"status": "business_outcome", "outcome": "member_not_found"},
                    },
                ],
            }
        )
    )

    report, path = await certify(settings, CAPABILITY, runs=2)

    assert path.exists()
    assert (report.pass_rate, report.primary_locator_rate, report.confidence) == (1.0, 1.0, 1.0)
    assert report.eligible_for_approval is False  # 2 runs per case is below the minimum of 5


async def test_mcp_lists_and_invokes_capabilities(settings: Settings) -> None:
    server = build_server(settings)

    def payload(result: Any) -> Any:
        return json.loads(result.content[0].text)

    listed = payload(await server.call_tool("list_capabilities", {}))
    draft = payload(
        await server.call_tool(
            "invoke_capability", {"name": CAPABILITY, "params": {"member_id": "48213"}}
        )
    )
    attended = payload(
        await server.call_tool(
            "invoke_capability",
            {"name": CAPABILITY, "params": {"member_id": "48213"}, "attended": True},
        )
    )

    assert listed["name"] == CAPABILITY or listed[0]["name"] == CAPABILITY
    assert draft["error"]["code"] == "capability_not_approved"
    assert attended["status"] == "succeeded"
    assert attended["outputs"]["savings_balance"] == "15032.90"


def test_generated_playwright_test_runs_standalone(
    settings: Settings, bank_a: str, tmp_path: Path
) -> None:
    from assessments.capability.codegen import generate_test

    cap = CapabilityStore(settings.catalog_dir).load(CAPABILITY)
    generated = tmp_path / "test_generated_flow.py"
    generated.write_text(generate_test(cap, {"member_id": "48213"}), encoding="utf-8")

    env = {**os.environ, "TARGET_BASE_URL": bank_a}
    run = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(generated)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert "1 passed" in run.stdout
    assert "assessments" not in generated.read_text(encoding="utf-8").split('"""', 2)[2]

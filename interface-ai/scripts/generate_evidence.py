"""Produce /evidence: two discovery runs plus replays that exercise every outcome class.

    uv run python scripts/generate_evidence.py                        # Claude via the API
    uv run python scripts/generate_evidence.py --decider claude-code  # Claude via Claude Code CLI
    uv run python scripts/generate_evidence.py --decider offline      # scripted stand-in

Everything runs in one process: two demo-bank tenants (in threads), the operator console API
(in-loop, for handoffs), and a simulated operator that acts only through that API. Ends by
rendering evidence/index.html (see evidence_dashboard.py).
"""

import argparse
import asyncio
import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn
from evidence_dashboard import build_dashboard

from assessments.agent.decider import AnthropicDecider, Decider
from assessments.agent.offline import offline_decider
from assessments.api.app import create_app
from assessments.capability.overlay import TenantStore
from assessments.capability.store import CapabilityStore
from assessments.config import Settings, get_settings
from assessments.logging import configure_logging
from assessments.mcp_server import build_server
from assessments.operator_sim import SimulatedOperator
from assessments.replay.result import ReplayResult
from assessments.service import certify, load_task, propose_overlay, run_discovery, run_replay
from demo_bank.app import create_demo_bank

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence"
BALANCE = "harbor.member.savings_balance"
SUB_ACCOUNT = "harbor.member.open_sub_account"
SUB_PARAMS = {"member_id": "12345", "product": "Vacation Club", "initial_deposit": "25.00"}

# (directory, title, member id, faults to inject, tenant)
REPLAYS: list[tuple[str, str, str, dict[str, int], str]] = [
    ("02-replay-success", "Replay for the recorded member", "12345", {}, "a"),
    ("03-replay-other-member", "Replay for a member the model never saw", "48213", {}, "a"),
    ("04-replay-invalid-input", "Malformed member number is rejected up front", "12ab", {}, "a"),
    ("05-replay-not-found", "Unknown member is a business answer, not a crash", "99999", {}, "a"),
    ("06-replay-fraud-alert-dialog", "Fraud-alert dialog stops disclosure", "55555", {}, "a"),
    (
        "07-replay-recover-interstitial",
        "Maintenance notice is acknowledged",
        "12345",
        {"notice": 1},
        "a",
    ),
    (
        "08-replay-recover-transient-503",
        "Transient host timeout is retried",
        "12345",
        {"unavailable": 2},
        "a",
    ),
    (
        "09-replay-session-expired-restart",
        "Expired session restarts the flow",
        "12345",
        {"expire_session": 1},
        "a",
    ),
    (
        "10-replay-hard-failure-app-error",
        "Application error page stops the run",
        "12345",
        {"server_error": 1},
        "a",
    ),
    (
        "11-replay-handoff-supervisor-override",
        "Supervisor override entered by a person",
        "20417",
        {},
        "a",
    ),
    ("12-replay-other-tenant-drift", "Same capability on a second credit union", "12345", {}, "b"),
]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(app: Any) -> str:
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


def _copy_run(settings: Settings, run_id: str, name: str) -> None:
    dest = EVIDENCE / "runs" / name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(settings.data_dir / "runs" / run_id, dest)


def _replay_entry(name: str, title: str, replay: ReplayResult) -> dict[str, Any]:
    return {
        "scenario": name,
        "title": title,
        "status": replay.status,
        "outcome": replay.outcome.code if replay.outcome else None,
        "error": replay.error.code if replay.error else None,
        "error_message": replay.error.message if replay.error else None,
        "recoveries": [f"{r.state_id}:{r.response}" for r in replay.recoveries],
        "handoffs": [f"{h.kind}:{h.resolution}" for h in replay.handoffs],
        "degraded_locators": [f"{d.step_id}:{d.used}" for d in replay.degraded_locators],
        "assisted_steps": [f"{a['step_id']}: {a['chose']}" for a in replay.assisted_steps],
        "overlay": replay.overlay,
        "duration_ms": replay.duration_ms,
    }


def _payload(result: Any) -> Any:
    """The JSON body of an MCP tool result (a list return arrives as one block per item)."""
    blocks = [json.loads(getattr(block, "text", "null")) for block in result.content]
    return blocks[0] if len(blocks) == 1 else blocks


def _make_decider(kind: str, capability_id: str) -> Decider:
    base = get_settings()
    if kind == "offline":
        return offline_decider(capability_id)
    if kind == "claude-code":
        from assessments.agent.claude_code import ClaudeCodeDecider

        return ClaudeCodeDecider(base.llm_model)
    if base.anthropic_api_key is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set; use --decider claude-code|offline")
    return AnthropicDecider(base.llm_model, base.anthropic_api_key.get_secret_value())


async def _beyond(
    settings: Settings,
    ref: str,
    tenants: dict[str, str],
    decider_kind: str,
    summary: dict[str, Any],
) -> None:
    """Tenant overlays from drift, bounded model repair, certification, and the MCP interface."""
    store = TenantStore(settings.catalog_dir)
    cap = CapabilityStore(settings.catalog_dir).load(ref)

    # Overlay: drift on Bayside becomes a draft overlay; once approved, replay has no drift.
    proposed, path = await propose_overlay(
        settings, ref, {"member_id": "12345"}, tenant="bayside", base_url=tenants["b"]
    )
    _copy_run(settings, proposed.run_id, "15-overlay-proposed-from-drift")
    summary["replays"].append(
        _replay_entry(
            "15-overlay-proposed-from-drift",
            "Drift on a second tenant becomes a draft overlay",
            proposed,
        )
    )
    if path is not None:
        store.approve_overlay("bayside", cap, reviewer="operator.sim (evidence script)")
        shutil.copy(path, EVIDENCE / "overlay-bayside.json")
    clean = await run_replay(
        settings,
        ref,
        {"member_id": "12345"},
        tenant="bayside",
        base_url=tenants["b"],
        headless=True,
    )
    _copy_run(settings, clean.run_id, "16-replay-with-approved-overlay")
    summary["replays"].append(
        _replay_entry(
            "16-replay-with-approved-overlay", "Approved overlay: same tenant, zero drift", clean
        )
    )

    # Assisted repair: a redesigned menu breaks every locator for one step.
    broken = await run_replay(
        settings, ref, {"member_id": "12345"}, base_url=tenants["c"], headless=True
    )
    _copy_run(settings, broken.run_id, "17-redesign-breaks-a-step")
    summary["replays"].append(
        _replay_entry(
            "17-redesign-breaks-a-step", "A redesigned menu: the recorded link is gone", broken
        )
    )
    repaired = await run_replay(
        settings,
        ref,
        {"member_id": "12345"},
        base_url=tenants["c"],
        headless=True,
        assist=_make_decider(decider_kind, BALANCE),
    )
    _copy_run(settings, repaired.run_id, "18-assisted-repair")
    summary["replays"].append(
        _replay_entry(
            "18-assisted-repair",
            "One bounded model repair re-finds it, proposed for review",
            repaired,
        )
    )

    # Certification: golden cases x5 give a confidence score that gates approval.
    report, report_file = await certify(settings, ref, runs=5, base_url=tenants["a"])
    shutil.copy(report_file, EVIDENCE / "certification.json")
    summary["certification"] = report.model_dump(mode="json")
    if report.eligible_for_approval:
        CapabilityStore(settings.catalog_dir).approve(
            ref,
            "operator.sim (evidence script)",
            f"certified: confidence {report.confidence} over {report.runs_per_case} runs/case",
        )

    # MCP: an agent discovers the catalog and invokes a capability by name.
    server = build_server(settings.model_copy(update={"target_base_url": tenants["a"]}))
    listed = await server.call_tool("list_capabilities", {})
    invoked = await server.call_tool(
        "invoke_capability", {"name": ref.split("@")[0], "params": {"member_id": "48213"}}
    )
    transcript = {
        "tools": [t.name for t in await server.list_tools()],
        "list_capabilities": _payload(listed),  # every capability, not just the first
        "invoke_capability": {
            "request": {"name": ref.split("@")[0], "params": {"member_id": "[redacted]"}},
            "response_status": _payload(invoked)["status"],
        },
    }
    (EVIDENCE / "mcp-session.json").write_text(
        json.dumps(transcript, indent=2) + "\n", encoding="utf-8"
    )
    summary["mcp"] = transcript["invoke_capability"]


async def main(decider_kind: str) -> int:
    work = REPO / "data" / "evidence-work"
    shutil.rmtree(work, ignore_errors=True)
    # A throwaway copy of the catalog: discoveries, approvals and overlays made here must not
    # add versions to the reviewed catalog in git.
    shutil.copytree(REPO / "catalog", work / "catalog")
    settings = get_settings().model_copy(
        update={
            "catalog_dir": work / "catalog",
            "data_dir": work / "data",
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "handoff_timeout_s": 90,
        }
    )
    tenants = {v: _serve(create_demo_bank(v)) for v in ("a", "b", "c")}
    summary: dict[str, Any] = {"discoveries": [], "replays": []}

    transport = httpx.ASGITransport(app=create_app(settings))
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://console") as console,
        httpx.AsyncClient() as http,
    ):
        sim = SimulatedOperator(console, supervisor_id="sup01", supervisor_pin="4321", think_s=1.0)
        sim_task = asyncio.create_task(sim.run())
        try:
            # 1. Discovery: read-only lookup.
            decider = _make_decider(decider_kind, BALANCE)
            task = load_task(REPO / "catalog" / "tasks" / "member_savings_balance.json")
            found, saved = await run_discovery(
                settings,
                task,
                {"member_id": "12345"},
                decider,
                base_url=tenants["a"],
                headless=True,
            )
            _copy_run(settings, found.run_id, "01-discovery")
            summary["discoveries"].append(
                {
                    "scenario": "01-discovery",
                    "title": "Discover: member savings balance lookup",
                    "status": found.status,
                    "reason": found.reason,
                    "model": decider.model_name,
                    "steps": len(found.steps),
                    "artifact": "evidence/runs/01-discovery/capability.json" if saved else None,
                }
            )
            if found.capability is None or saved is None:
                print(json.dumps(summary, indent=2))
                return 1
            shutil.copy(saved, EVIDENCE / "capability.json")

            # 2. Replays of that capability: every outcome class.
            for name, title, member, faults, tenant in REPLAYS:
                await http.post(f"{tenants[tenant]}/__faults", json=faults)
                replay = await run_replay(
                    settings,
                    found.capability.ref,
                    {"member_id": member},
                    base_url=tenants[tenant],
                    headless=True,
                )
                await http.post(f"{tenants[tenant]}/__faults", json={})
                _copy_run(settings, replay.run_id, name)
                summary["replays"].append(_replay_entry(name, title, replay))
                print(f"{name}: {replay.status}", file=sys.stderr)

            # 3. Discovery of a write flow: the Confirm click needs approval.
            decider = _make_decider(decider_kind, SUB_ACCOUNT)
            task = load_task(REPO / "catalog" / "tasks" / "open_sub_account.json")
            found2, saved2 = await run_discovery(
                settings, task, SUB_PARAMS, decider, base_url=tenants["a"], headless=True
            )
            _copy_run(settings, found2.run_id, "13-discovery-open-sub-account")
            summary["discoveries"].append(
                {
                    "scenario": "13-discovery-open-sub-account",
                    "title": "Discover: open a sub-account (irreversible, approved by a person)",
                    "status": found2.status,
                    "reason": found2.reason,
                    "model": decider.model_name,
                    "steps": len(found2.steps),
                    "artifact": "evidence/runs/13-discovery-open-sub-account/capability.json"
                    if saved2
                    else None,
                }
            )

            # 4. Replay of the draft write capability: approval is required again.
            if found2.capability is not None:
                replay = await run_replay(
                    settings,
                    found2.capability.ref,
                    {**SUB_PARAMS, "member_id": "48213"},
                    base_url=tenants["a"],
                    headless=True,
                    allow_irreversible=True,
                )
                _copy_run(settings, replay.run_id, "14-replay-sub-account-approval")
                summary["replays"].append(
                    _replay_entry(
                        "14-replay-sub-account-approval",
                        "Draft write capability waits for approval before committing",
                        replay,
                    )
                )

            # 5-7. Beyond the brief: overlays, assisted repair, certification, MCP.
            await _beyond(settings, found.capability.ref, tenants, decider_kind, summary)
        finally:
            sim_task.cancel()

    (EVIDENCE / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    build_dashboard(EVIDENCE)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decider", choices=["claude", "claude-code", "offline"], default="claude")
    args = parser.parse_args()
    configure_logging("WARNING")
    EVIDENCE.mkdir(exist_ok=True)
    raise SystemExit(asyncio.run(main(args.decider)))

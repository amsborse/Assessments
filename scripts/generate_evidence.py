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
from assessments.config import Settings, get_settings
from assessments.logging import configure_logging
from assessments.operator_sim import SimulatedOperator
from assessments.replay.result import ReplayResult
from assessments.service import load_task, run_discovery, run_replay
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
        "duration_ms": replay.duration_ms,
    }


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


async def main(decider_kind: str) -> int:
    work = REPO / "data" / "evidence-work"
    shutil.rmtree(work, ignore_errors=True)
    settings = get_settings().model_copy(
        update={
            "data_dir": work / "data",
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "handoff_timeout_s": 90,
        }
    )
    tenants = {"a": _serve(create_demo_bank("a")), "b": _serve(create_demo_bank("b"))}
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
                    "artifact": saved.as_posix() if saved else None,
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
                    "artifact": saved2.as_posix() if saved2 else None,
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

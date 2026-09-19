"""Command line: `assessments <command>`.

  demo-bank                       run the local legacy target app (port 8001; --variant a|b|c)
  discover TASK --example k=v     LLM-driven discovery -> capability artifact
  replay REF --param k=v          deterministic replay -> structured result
                                  (--tenant, --assist for an opt-in bounded model repair)
  certify REF --runs N            confidence score from repeated golden-case replays
  overlay propose|approve REF     per-tenant overlays proposed from locator drift
  capabilities list|show|approve  catalog management (approval requires certification)
  codegen REF --param k=v         standalone Playwright page object + pytest test
  mcp                             serve capabilities to AI agents over MCP (stdio)
  faults k=v ...                  inject runtime faults into the demo bank (test harness)
  serve                           API: capability catalog + operator console

discover/replay start the operator console in-process (same event loop as the browser), so an
escalation can be handled at http://HOST:PORT/operator while the run waits.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn
from pydantic import ValidationError

from assessments.api.app import create_app
from assessments.capability.store import CapabilityStore
from assessments.config import Settings, get_settings
from assessments.logging import configure_logging

logger = logging.getLogger("assessments.cli")


def _pairs(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"expected key=value, got {item!r}")
        out[key.strip()] = value
    return out


async def _with_console[T](
    settings: Settings, work: Awaitable[T], *, simulate_operator: bool = False
) -> T:
    config = uvicorn.Config(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(50):
        if server.started or task.done():
            break
        await asyncio.sleep(0.1)
    if server.started:
        print(f"operator console: http://{settings.host}:{settings.port}/operator", file=sys.stderr)
    else:
        print(
            "warning: operator console unavailable (port busy?); handoffs will time out",
            file=sys.stderr,
        )
    sim_task: asyncio.Task[None] | None = None
    if simulate_operator and server.started:
        from assessments.operator_sim import SimulatedOperator
        from assessments.secrets import env_value

        client = httpx.AsyncClient(base_url=f"http://{settings.host}:{settings.port}", timeout=30)
        sim = SimulatedOperator(
            client,
            supervisor_id=env_value("DEMO_SUPERVISOR_ID"),
            supervisor_pin=env_value("DEMO_SUPERVISOR_PIN"),
        )
        sim_task = asyncio.create_task(sim.run())
        print("simulated operator attached (acts via the console API)", file=sys.stderr)
    try:
        return await work
    finally:
        if sim_task is not None:
            sim_task.cancel()
        server.should_exit = True
        if not task.done():
            await task


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_discover(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.agent.decider import AnthropicDecider, Decider
    from assessments.agent.offline import offline_decider
    from assessments.service import load_task, run_discovery

    task = load_task(Path(args.task))
    examples = _pairs(args.example)
    decider: Decider
    if args.decider == "offline":
        decider = offline_decider(task.capability_id)
    elif args.decider == "claude-code":
        from assessments.agent.claude_code import ClaudeCodeDecider

        decider = ClaudeCodeDecider(settings.llm_model)
    else:
        if settings.anthropic_api_key is None:
            raise SystemExit(
                "ANTHROPIC_API_KEY is not set (put it in .env), or use --decider offline"
            )
        decider = AnthropicDecider(
            settings.llm_model, settings.anthropic_api_key.get_secret_value()
        )
    result, saved = asyncio.run(
        _with_console(
            settings,
            run_discovery(
                settings,
                task,
                examples,
                decider,
                base_url=args.base_url or settings.target_base_url,
                headless=not args.headed,
            ),
            simulate_operator=args.simulate_operator,
        )
    )
    _print(
        {
            "status": result.status,
            "reason": result.reason,
            "run_id": result.run_id,
            "evidence": str(settings.data_dir / "runs" / result.run_id),
            "capability": result.capability.ref if result.capability else None,
            "artifact": str(saved) if saved else None,
            "outputs (masked)": result.outputs,
            "human_actions": result.human_actions,
        }
    )
    return 0 if result.status == "succeeded" else 1


def cmd_replay(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.service import run_replay

    result = asyncio.run(
        _with_console(
            settings,
            run_replay(
                settings,
                args.ref,
                _pairs(args.param),
                base_url=args.base_url,
                headless=not args.headed,
                allow_irreversible=args.allow_irreversible,
                tenant=args.tenant,
                use_draft_overlay=args.allow_draft_overlay,
                assist=_decider(args.assist, settings) if args.assist else None,
            ),
            simulate_operator=args.simulate_operator,
        )
    )
    _print(result.model_dump(mode="json"))  # to the caller: real outputs; evidence is masked
    return {"succeeded": 0, "business_outcome": 0}.get(result.status, 1)


def _decider(kind: str, settings: Settings) -> Any:
    """A model decider for discovery or assisted repair."""
    if kind == "claude-code":
        from assessments.agent.claude_code import ClaudeCodeDecider

        return ClaudeCodeDecider(settings.llm_model)
    from assessments.agent.decider import AnthropicDecider

    if settings.anthropic_api_key is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set (put it in .env), or use claude-code")
    return AnthropicDecider(settings.llm_model, settings.anthropic_api_key.get_secret_value())


def cmd_overlay(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.capability.overlay import TenantStore
    from assessments.service import propose_overlay

    if args.action == "propose":
        result, path = asyncio.run(
            propose_overlay(settings, args.ref, _pairs(args.param), tenant=args.tenant)
        )
        _print(
            {
                "status": result.status,
                "drifted_steps": sorted(result.proposed_targets),
                "overlay": str(path) if path else None,
                "next": f"review the file, then: assessments overlay approve {args.ref} "
                f"--tenant {args.tenant} --reviewer <you>"
                if path
                else "no drift: nothing to propose",
            }
        )
        return 0
    cap = CapabilityStore(settings.catalog_dir).load(args.ref)
    overlay = TenantStore(settings.catalog_dir).approve_overlay(args.tenant, cap, args.reviewer)
    _print(
        {
            "overlay": f"{overlay.tenant} / {overlay.capability}@v{overlay.base_version}",
            "review": overlay.review.model_dump(mode="json"),
        }
    )
    return 0


def cmd_certify(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.service import certify

    report, path = asyncio.run(certify(settings, args.ref, runs=args.runs, tenant=args.tenant))
    _print({**report.model_dump(mode="json"), "report": str(path)})
    return 0 if report.eligible_for_approval else 1


def cmd_codegen(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.capability.codegen import generate_test

    cap = CapabilityStore(settings.catalog_dir).load(args.ref)
    code = generate_test(cap, _pairs(args.param))
    out = Path(args.out or f"test_{cap.id.replace('.', '_')}.py")
    out.write_text(code, encoding="utf-8")
    _print({"capability": cap.ref, "written": str(out)})
    return 0


def cmd_mcp(args: argparse.Namespace, settings: Settings) -> int:
    from assessments.mcp_server import build_server

    build_server(settings).run("stdio")
    return 0


def cmd_capabilities(args: argparse.Namespace, settings: Settings) -> int:
    store = CapabilityStore(settings.catalog_dir)
    if args.action == "list":
        from assessments.api.capabilities import contract

        _print([contract(settings, c) for c in store.list()])
    elif args.action == "show":
        _print(store.load(args.ref).model_dump(mode="json", exclude_none=True))
    elif args.action == "approve":
        from assessments.capability.certification import load_report

        report = load_report(settings.catalog_dir, store.load(args.ref))
        if not args.force and (report is None or not report.eligible_for_approval):
            got = (
                f"confidence {report.confidence} over {report.runs_per_case} runs"
                if report
                else "no certification"
            )
            raise SystemExit(
                f"not eligible for approval ({got}); run `assessments certify "
                f"{args.ref}` first, or pass --force with a reason in --notes"
            )
        cap = store.approve(args.ref, args.reviewer, args.notes)
        _print({"capability": cap.ref, "review": cap.review.model_dump(mode="json")})
    return 0


def cmd_faults(args: argparse.Namespace, settings: Settings) -> int:
    body = {k: int(v) for k, v in _pairs(args.faults).items()}
    base = args.base_url or settings.target_base_url
    response = httpx.post(f"{base}/__faults", json=body, timeout=5)
    response.raise_for_status()
    _print(response.json())
    return 0


def cmd_demo_bank(args: argparse.Namespace, settings: Settings) -> int:
    from demo_bank.app import create_demo_bank

    uvicorn.run(create_demo_bank(args.variant), host=args.host, port=args.port, log_config=None)
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="assessments",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="LLM-driven discovery run")
    d.add_argument("task", help="task spec JSON, e.g. catalog/tasks/member_savings_balance.json")
    d.add_argument(
        "--example",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="concrete input value for this run (never shown to the model)",
    )
    d.add_argument(
        "--decider",
        choices=["claude", "claude-code", "offline"],
        default="claude",
        help="claude = Anthropic API (ANTHROPIC_API_KEY); claude-code = local Claude Code CLI "
        "login (e.g. a Pro subscription); offline = scripted stand-in",
    )
    d.add_argument("--base-url", help="tenant app base URL (default TARGET_BASE_URL)")
    d.add_argument(
        "--simulate-operator",
        action="store_true",
        help="attach a simulated human operator that resolves handoffs via the console API",
    )
    d.add_argument("--headed", action="store_true", help="show the browser window")
    d.set_defaults(fn=cmd_discover)

    r = sub.add_parser("replay", help="deterministic replay of a capability")
    r.add_argument("ref", help="capability id, or id@vN")
    r.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    r.add_argument("--allow-irreversible", action="store_true")
    r.add_argument("--base-url")
    r.add_argument(
        "--simulate-operator",
        action="store_true",
        help="attach a simulated human operator that resolves handoffs via the console API",
    )
    r.add_argument("--headed", action="store_true")
    r.add_argument("--tenant", help="tenant id: uses its base URL and approved overlay")
    r.add_argument("--allow-draft-overlay", action="store_true")
    r.add_argument(
        "--assist",
        choices=["claude", "claude-code"],
        help="opt-in: one bounded model repair if a target cannot be found",
    )
    r.set_defaults(fn=cmd_replay)

    o = sub.add_parser("overlay", help="per-tenant overlays: propose from drift, approve")
    o.add_argument("action", choices=["propose", "approve"])
    o.add_argument("ref")
    o.add_argument("--tenant", required=True)
    o.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    o.add_argument("--reviewer", default="")
    o.set_defaults(fn=cmd_overlay)

    k = sub.add_parser("certify", help="confidence score from repeated golden-case replays")
    k.add_argument("ref")
    k.add_argument("--runs", type=int, default=5)
    k.add_argument("--tenant")
    k.set_defaults(fn=cmd_certify)

    g = sub.add_parser("codegen", help="emit a standalone Playwright page object + pytest test")
    g.add_argument("ref")
    g.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="example params baked into the generated test",
    )
    g.add_argument("--out")
    g.set_defaults(fn=cmd_codegen)

    m = sub.add_parser("mcp", help="serve capabilities to AI agents over MCP (stdio)")
    m.set_defaults(fn=cmd_mcp)

    c = sub.add_parser("capabilities", help="list / show / approve capabilities")
    c.add_argument("action", choices=["list", "show", "approve"])
    c.add_argument("ref", nargs="?")
    c.add_argument("--reviewer", default="")
    c.add_argument("--notes", default="")
    c.add_argument("--force", action="store_true", help="approve without certification")
    c.set_defaults(fn=cmd_capabilities)

    f = sub.add_parser("faults", help="inject faults into the demo bank, e.g. notice=1")
    f.add_argument("faults", nargs="*", metavar="NAME=COUNT")
    f.add_argument("--base-url")
    f.set_defaults(fn=cmd_faults)

    b = sub.add_parser("demo-bank", help="run the legacy-style demo target app")
    b.add_argument("--host", default="127.0.0.1")
    b.add_argument("--port", type=int, default=8001)
    b.add_argument(
        "--variant",
        choices=["a", "b", "c"],
        default="a",
        help="b = relabelled tenant; c = redesigned menu (for assisted repair)",
    )
    b.set_defaults(fn=cmd_demo_bank)

    s = sub.add_parser("serve", help="capability catalog API + operator console")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError as exc:
        configure_logging()
        # include_input=False: never echo raw values, which may be secrets.
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        logger.critical("invalid configuration", extra={"errors": errors})
        return 2
    configure_logging(settings.log_level)
    if args.command == "capabilities" and args.action != "list" and not args.ref:
        raise SystemExit("capabilities show/approve need a REF")
    if args.command == "capabilities" and args.action == "approve" and not args.reviewer:
        raise SystemExit("approve needs --reviewer")
    code: int = args.fn(args, settings)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

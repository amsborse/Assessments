"""Record a narrated video of the whole flow by filming the operator console.

    uv run --with imageio-ffmpeg python scripts/record_demo.py --decider claude-code

Scenes: the target app → Claude discovers a capability → the catalog renders it → replays
(new member, business outcome, recovery) → a person takes over the live session and hands it
back → tenant drift → drift becomes a reviewed overlay → a redesign is fixed by one bounded
model repair → the evidence overview. The two stretch-goal scenes carry a small "Stretch goal"
tag. Captions are drawn on the recording page only; the automation's own browser is never
touched. Writes evidence/demo.mp4 (and .webm).
"""

import argparse
import asyncio
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn
from playwright.async_api import Page, async_playwright

from assessments.agent.decider import AnthropicDecider, Decider
from assessments.agent.offline import offline_decider
from assessments.api.app import create_app
from assessments.capability.overlay import TenantStore
from assessments.capability.store import CapabilityStore
from assessments.config import Settings, get_settings
from assessments.logging import configure_logging
from assessments.service import load_task, propose_overlay, run_discovery, run_replay
from assessments.session.runtime import REGISTRY
from demo_bank.app import create_demo_bank

REPO = Path(__file__).resolve().parents[1]
CAPABILITY = "harbor.member.savings_balance"
W, H = 1440, 900

CAPTION_JS = """
([title, body, full, tag]) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div'); el.id = '__cap';
    document.body.appendChild(el);
    const st = document.createElement('style');
    st.textContent = `#__cap{position:fixed;left:0;right:0;bottom:0;z-index:99;background:#13233af2;color:#fff;
      padding:14px 28px 16px;font:15px/1.45 "Schibsted Grotesk","Segoe UI",sans-serif;
      box-shadow:0 -6px 24px rgba(0,0,0,.18)}
      #__cap b{display:block;font-size:19px;margin-bottom:2px}
      #__cap i{font-style:normal;font-size:12px;font-weight:600;color:#9ee6d6;
      border:1px solid #2f7d6d;border-radius:99px;padding:1px 9px;margin-left:12px;
      vertical-align:3px}
      #__cap.full{top:0;display:flex;flex-direction:column;justify-content:center;padding:0 12vw;
      background:#13233a}
      #__cap.full b{font-size:40px;line-height:1.15;margin-bottom:18px}
      #__cap.full span{font-size:20px;color:#c6d2e2;max-width:60ch}`;
    document.head.appendChild(st);
  }
  el.className = full ? 'full' : '';
  el.innerHTML = '<b></b><span></span>';
  el.querySelector('b').textContent = title; el.querySelector('span').textContent = body;
  if (tag) { const t = document.createElement('i'); t.textContent = tag; el.querySelector('b').appendChild(t); }
}
"""


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve_thread(app: Any) -> str:
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


STRETCH = "Stretch goal"


async def caption(
    page: Page, title: str, body: str = "", *, full: bool = False, tag: str = ""
) -> None:
    await page.evaluate(CAPTION_JS, [title, body, full, tag])


async def wait_for(predicate: Any, timeout: float = 120) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.3)
    raise TimeoutError("demo step timed out")


async def human_override(console: Page) -> None:
    """Act as the supervisor through the console UI: take control, fill the override, hand back."""
    iv_session = await wait_for(
        lambda: next((s for s in REGISTRY.all() if s.control.intervention is not None), None)
    )
    await console.wait_for_selector("text=Take control of this session", timeout=30_000)
    await asyncio.sleep(2.5)  # let the viewer read the request
    await console.click("text=Take control of this session")
    await console.wait_for_selector("#screen.interactive", timeout=15_000)
    await asyncio.sleep(1.5)

    async def click_field(selector: str) -> None:
        box = None
        for frame in iv_session.surface.page.frames:
            if await frame.locator(selector).count():
                box = await frame.locator(selector).first.bounding_box()
                break
        if box is None:
            raise LookupError(f"{selector} not on the automation page")
        img = await console.evaluate(
            "() => { const i = document.querySelector('#screen');"
            " const r = i.getBoundingClientRect();"
            " return [r.x, r.y, r.width, r.height, i.naturalWidth, i.naturalHeight]; }"
        )
        x, y, w, h, nw, nh = img
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await console.mouse.move(x + cx * w / nw, y + cy * h / nh, steps=12)
        await console.mouse.click(x + cx * w / nw, y + cy * h / nh)
        await asyncio.sleep(0.8)

    async def type_text(text: str) -> None:
        await console.click("#typed")
        await console.keyboard.type(text, delay=90)
        await console.click("#typer button[type=submit]")
        await asyncio.sleep(0.9)

    await click_field('input[name="supid"]')
    await type_text("sup01")
    await click_field('input[name="pin"]')
    await type_text("4321")
    await click_field('input[type="submit"][value="Override"]')
    await asyncio.sleep(2.0)
    await console.fill("#note", "Supervisor override entered")
    await asyncio.sleep(0.6)
    await console.click("text=Hand back and resume")


async def show_target(page: Page, bank: str) -> None:
    """A short look at the target itself: the legacy teller console, full size."""
    await page.goto(f"{bank}/")
    await caption(
        page,
        "The target: a legacy teller console",
        "Framesets, table layouts, no element ids: a core-banking screen with no API. "
        "All data is synthetic.",
    )
    await asyncio.sleep(3)
    await page.fill("input[name=opid]", "teller1")
    await page.fill("input[name=pw]", "harbor-demo")
    await page.click("input[type=submit]")
    await page.wait_for_url("**/console")
    main_frame = page.frame(name="main")
    if main_frame is not None:
        await main_frame.goto(f"{bank}/main/member?m=48213")
    await page.wait_for_timeout(600)
    await caption(
        page,
        "The target: a legacy teller console",
        "A member record: credits in green with +, debits in red in parentheses, so meaning "
        "never rests on colour alone.",
    )
    await asyncio.sleep(5)


async def main(decider_kind: str) -> Path:
    base = get_settings()
    work = REPO / "data" / "demo-work"
    shutil.rmtree(work, ignore_errors=True)
    (work / "catalog").mkdir(parents=True)
    for sub in ("profiles", "tasks"):
        shutil.copytree(REPO / "catalog" / sub, work / "catalog" / sub)
    settings: Settings = base.model_copy(
        update={
            "data_dir": work / "data",
            "catalog_dir": work / "catalog",
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "handoff_timeout_s": 180,
        }
    )
    bank_a = _serve_thread(create_demo_bank("a"))
    bank_b = _serve_thread(create_demo_bank("b"))
    bank_c = _serve_thread(create_demo_bank("c"))
    tenants_dir = work / "catalog" / "tenants"
    tenants_dir.mkdir(parents=True, exist_ok=True)
    for tenant, name, url in (
        ("bayside", "Bayside Federal Credit Union", bank_b),
        ("coastal", "Coastal Credit Union", bank_c),
    ):
        (tenants_dir / f"{tenant}.json").write_text(
            json.dumps(
                {"id": tenant, "name": name, "product": "harbor-teller-console", "base_url": url}
            )
        )

    console_port = _port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",
            port=console_port,
            log_config=None,
            log_level="warning",
        )
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    decider: Decider
    if decider_kind == "offline":
        decider = offline_decider(CAPABILITY)
    elif decider_kind == "claude-code":
        from assessments.agent.claude_code import ClaudeCodeDecider

        decider = ClaudeCodeDecider(base.llm_model)
    else:
        if base.anthropic_api_key is None:
            raise SystemExit("ANTHROPIC_API_KEY is not set")
        decider = AnthropicDecider(base.llm_model, base.anthropic_api_key.get_secret_value())

    video_dir = work / "video"
    async with async_playwright() as pw, httpx.AsyncClient() as http:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(video_dir),
            record_video_size={"width": W, "height": H},
        )
        console = await ctx.new_page()
        await console.goto(f"http://127.0.0.1:{console_port}/operator")
        await console.wait_for_timeout(1200)

        await caption(
            console,
            "Computer-use automation for legacy banking apps",
            "Claude works out how to do a task in a legacy teller console once. The run "
            "becomes a typed capability that replays deterministically, without the model. "
            "When automation can't safely continue, a person takes over the same live session.",
            full=True,
        )
        await asyncio.sleep(7)
        await show_target(console, bank_a)
        await console.goto(f"http://127.0.0.1:{console_port}/operator")
        await console.wait_for_timeout(800)

        await caption(
            console,
            "1. Discovery: Claude drives the live app",
            f"Goal: look up a member and read their savings balance ({decider.model_name}). "
            "Each step is one decision with a reason; credentials are typed by name only.",
        )
        task = load_task(REPO / "catalog" / "tasks" / "member_savings_balance.json")
        result, _ = await run_discovery(
            settings, task, {"member_id": "12345"}, decider, base_url=bank_a, headless=True
        )
        if result.capability is None:
            raise RuntimeError(f"discovery did not succeed: {result.reason}")
        ref = result.capability.ref
        await caption(
            console,
            f"Capability recorded: {ref}",
            f"{len(result.steps)} steps, each with several verified ways to find its "
            "element. Inputs are parameters; no values or secrets are stored.",
        )
        await asyncio.sleep(3)
        await console.goto(f"http://127.0.0.1:{console_port}/catalog?c={CAPABILITY}")
        await console.wait_for_timeout(1000)
        await caption(
            console,
            "The capability, as a reviewer sees it",
            "Typed contract, the outcomes a caller can get back, and every recorded step "
            "with its locators ranked from most to least robust.",
        )
        await asyncio.sleep(4)
        await console.mouse.wheel(0, 900)
        await asyncio.sleep(4)
        await console.mouse.wheel(0, 900)
        await asyncio.sleep(3)
        await console.goto(f"http://127.0.0.1:{console_port}/operator")
        await console.wait_for_timeout(800)

        async def replay(
            title: str,
            body: str,
            member: str,
            *,
            target: str = bank_a,
            faults: dict[str, int] | None = None,
            pause: float = 3.5,
        ) -> None:
            await caption(console, title, body)
            await http.post(f"{target}/__faults", json=faults or {})
            await run_replay(settings, ref, {"member_id": member}, base_url=target, headless=True)
            await http.post(f"{target}/__faults", json={})
            await asyncio.sleep(pause)

        await replay(
            "2. Replay: a member the model never saw",
            "Deterministic replay with typed inputs and outputs. No model in the loop.",
            "48213",
        )
        await replay(
            "3. A business answer, not a crash",
            "Member 99999 does not exist: the caller gets the outcome member_not_found.",
            "99999",
        )
        await replay(
            "4. Runtime trouble is handled deliberately",
            "The core host times out twice (HCX-503). Replay recognises the state, backs off, "
            "retries and completes.",
            "12345",
            faults={"unavailable": 2},
        )

        await caption(
            console,
            "5. A person takes over the live session",
            "An employee record needs a supervisor override. Automation pauses; the operator "
            "takes control of the same browser, enters the override and hands it back.",
        )
        handoff = asyncio.create_task(
            run_replay(settings, ref, {"member_id": "20417"}, base_url=bank_a, headless=True)
        )
        try:
            await asyncio.wait_for(human_override(console), timeout=120)
        except BaseException:
            handoff.cancel()
            raise
        await asyncio.wait_for(handoff, timeout=120)
        await asyncio.sleep(4)

        await replay(
            "6. The same capability at another credit union",
            "A second tenant runs the same product with relabelled screens. Replay still "
            "succeeds and reports which steps needed a fallback locator (drift).",
            "12345",
            target=bank_b,
            pause=4,
        )

        await caption(
            console,
            "7. Drift becomes a reviewed overlay",
            "The fallback steps are re-derived on Bayside's own screens and proposed as a "
            "tenant overlay. Once approved, the same capability replays there with no drift.",
            tag=STRETCH,
        )
        _, overlay_path = await propose_overlay(
            settings, ref, {"member_id": "12345"}, tenant="bayside"
        )
        if overlay_path is not None:
            TenantStore(settings.catalog_dir).approve_overlay(
                "bayside", CapabilityStore(settings.catalog_dir).load(ref), "operator.jane"
            )
        await console.goto(f"http://127.0.0.1:{console_port}/catalog?c={CAPABILITY}")
        await console.wait_for_timeout(800)
        await caption(
            console,
            "7. Drift becomes a reviewed overlay",
            "The catalog now lists Bayside's approved overlay: only the drifting steps are "
            "specialised; the shared capability is untouched.",
            tag=STRETCH,
        )
        await asyncio.sleep(4.5)
        await console.goto(f"http://127.0.0.1:{console_port}/operator")
        await console.wait_for_timeout(800)
        await caption(
            console,
            "7. Drift becomes a reviewed overlay",
            "Replaying at Bayside with the approved overlay: no fallback locators needed.",
            tag=STRETCH,
        )
        await run_replay(settings, ref, {"member_id": "12345"}, tenant="bayside", headless=True)
        await asyncio.sleep(4)

        await caption(
            console,
            "8. A redesign breaks a step: one bounded repair",
            "Coastal's new menu removed the recorded link. Replay asks the model once to "
            "re-find that single element, verifies it, and flags the fix for review.",
            tag=STRETCH,
        )
        await run_replay(
            settings,
            ref,
            {"member_id": "12345"},
            tenant="coastal",
            headless=True,
            assist=decider if decider_kind != "offline" else None,
        )
        await asyncio.sleep(4)

        dashboard = REPO / "evidence" / "index.html"
        if dashboard.exists():
            await console.goto(dashboard.as_uri())
            await console.wait_for_timeout(800)
            await caption(
                console,
                "Evidence for every run",
                "Green succeeded, amber recovered, blue business outcome, teal model-assisted, "
                "violet a person was involved, red failed. Full logs and screenshots are in "
                "/evidence.",
            )
            await asyncio.sleep(3)
            await console.mouse.wheel(0, 700)
            await asyncio.sleep(4)

        video = console.video
        await ctx.close()
        await browser.close()
        if video is None:
            raise RuntimeError("no video recorded")
        webm = Path(await video.path())

    server.should_exit = True
    await server_task
    out_webm = REPO / "evidence" / "demo.webm"
    shutil.copy(webm, out_webm)
    return out_webm


def to_mp4(webm: Path) -> Path | None:
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
    except ImportError:
        print("imageio-ffmpeg not installed; kept WebM only")
        return None
    mp4 = webm.with_suffix(".mp4")
    subprocess.run(  # noqa: S603 - fixed argv
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "28",
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
    )
    return mp4


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decider", choices=["claude", "claude-code", "offline"], default="claude-code"
    )
    args = parser.parse_args()
    configure_logging("WARNING")
    webm = asyncio.run(asyncio.wait_for(main(args.decider), timeout=1500))
    mp4 = to_mp4(webm)
    print(f"wrote {mp4 or webm}")

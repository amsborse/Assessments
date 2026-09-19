"""Playwright implementation of the Surface protocol for modern and legacy web apps.

Perception: an accessibility-style snapshot built by an injected script in *every frame*
(framesets, iframes), so the agent never depends on ids, test ids, or CSS. Targeting: each
element the agent acts on is described by several strategies (role+name, label, form-field
name, table cell, text, CSS path); each is verified to resolve uniquely to that same element
before it is recorded. Replay resolves strategies in robustness order and reports when a
fallback had to be used (a drift signal).
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Dialog,
    Frame,
    Locator,
    Page,
    Route,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from assessments.capability.schema import (
    Condition,
    CssStrategy,
    DialogShown,
    FieldNameStrategy,
    HttpStatus,
    LabelStrategy,
    RoleStrategy,
    Scope,
    ScreenTitle,
    Strategy,
    TableCellStrategy,
    Target,
    TargetPresent,
    TextStrategy,
    TextVisible,
)
from assessments.capability.values import render, templatize
from assessments.safety import Policy
from assessments.session.control import HumanAction, SessionControl
from assessments.surfaces.base import ElementInfo, Observation, Resolution, TargetNotFound

logger = logging.getLogger(__name__)

CU_JS = (Path(__file__).parent / "cu.js").read_text(encoding="utf-8")
MAX_SNAPSHOT_CHARS = 12_000
EventSink = Callable[[str, dict[str, Any]], None]


class PlaywrightWebSurface:
    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        *,
        policy: Policy,
        control: SessionControl,
        redact_labels: list[str],
        on_event: EventSink,
        sensitive_values: set[str] | None = None,
    ) -> None:
        self.context = context
        self.page = page
        self.policy = policy
        self.control = control
        self.redact_labels = redact_labels
        self.sensitive_values = sensitive_values if sensitive_values is not None else set()
        self._on_event = on_event
        self._ref_frames: dict[str, Frame] = {}
        self._dialogs: list[str] = []
        self._doc_status: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    async def create(
        cls,
        context: BrowserContext,
        *,
        policy: Policy,
        control: SessionControl,
        redact_labels: list[str],
        on_event: EventSink,
        sensitive_values: set[str] | None = None,
    ) -> "PlaywrightWebSurface":
        await context.add_init_script(script=CU_JS)
        page = await context.new_page()
        surface = cls(
            context,
            page,
            policy=policy,
            control=control,
            redact_labels=redact_labels,
            on_event=on_event,
            sensitive_values=sensitive_values,
        )
        await context.route("**/*", surface._enforce_allowlist)
        await context.expose_binding("__cuHumanEvent", surface._on_page_event)
        page.on("dialog", surface._on_dialog)
        page.on("response", surface._on_response)
        return surface

    # ---------------------------------------------------------------- event plumbing
    async def _enforce_allowlist(self, route: Route) -> None:
        url = route.request.url
        if self.policy.host_allowed(url):
            await route.continue_()
            return
        self._on_event("policy_blocked_request", {"host": urlsplit(url).hostname})
        await route.abort("blockedbyclient")

    def _on_dialog(self, dialog: Dialog) -> None:
        # Conservative default: dismiss (cancel). Accepting a confirm() is a decision.
        self._dialogs.append(dialog.message)
        self._on_event(
            "dialog", {"type": dialog.type, "message": dialog.message, "handled": "dismissed"}
        )
        task = asyncio.ensure_future(dialog.dismiss())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_response(self, response: Any) -> None:
        if response.request.resource_type == "document":
            self._doc_status[_frame_key(response.frame)] = response.status

    def _on_page_event(self, source: dict[str, Any], event: dict[str, Any]) -> None:
        iv = self.control.intervention
        if iv is None or iv.claimed_by is None or self.control.controller != "human":
            return  # automation's own actions, or no one in control
        self.control.record_human_action(
            HumanAction(
                operator=iv.claimed_by,
                source="page",
                kind=str(event.get("kind")),
                detail=_summarize_human_event(event),
            )
        )

    # ---------------------------------------------------------------- frames
    @staticmethod
    def _children(frame: Frame) -> list[Frame]:
        # Playwright keeps detached frames in child_frames after the parent re-navigates
        # (e.g. re-login reloads the frameset); they must never be matched.
        return [f for f in frame.child_frames if not f.is_detached()]

    def _frames(self) -> list[Frame]:
        ordered: list[Frame] = []

        def walk(frame: Frame) -> None:
            ordered.append(frame)
            for child in self._children(frame):
                walk(child)

        walk(self.page.main_frame)
        return ordered

    def _scope_of(self, frame: Frame) -> Scope:
        names: list[str] = []
        while frame.parent_frame is not None:
            siblings = self._children(frame.parent_frame)
            index = siblings.index(frame) if frame in siblings else 0
            names.insert(0, frame.name or f"#{index}")
            frame = frame.parent_frame
        return Scope(frames=names)

    def _frame_for(self, scope: Scope) -> Frame | None:
        frame = self.page.main_frame
        for name in scope.frames:
            children = self._children(frame)
            match = next((f for f in children if f.name == name), None)
            if match is None and name.startswith("#") and name[1:].isdigit():
                idx = int(name[1:])
                match = children[idx] if idx < len(children) else None
            if match is None:
                return None
            frame = match
        return frame

    # ---------------------------------------------------------------- perception
    async def open(self, url: str) -> None:
        self.control.assert_automation()
        await self.page.goto(url, wait_until="load")

    async def observe(self, *, screenshot: bool = True) -> Observation:
        await self.settle()
        self._ref_frames.clear()
        elements: dict[str, ElementInfo] = {}
        sections: list[str] = [f"Top window: {urlsplit(self.page.url).path}"]
        signature: list[str] = []
        next_ref = 1
        for frame in self._frames():
            try:
                snap = await frame.evaluate(
                    "([n, l, v]) => window.__cu ? window.__cu.snapshot(n, l, v) : null",
                    [next_ref, self.redact_labels, sorted(self.sensitive_values)],
                )
            except PlaywrightError:
                continue  # frame navigated mid-snapshot; next observation will catch it
            if not snap:
                continue
            scope = self._scope_of(frame)
            next_ref = snap["next"]
            for el in snap["elements"]:
                self._ref_frames[el["ref"]] = frame
                elements[el["ref"]] = ElementInfo(el["ref"], el["role"], el["name"], scope)
            if frame.parent_frame is None and not snap["lines"]:
                continue
            label = "/".join(scope.frames) or "top"
            path = urlsplit(frame.url).path
            sections.append(f'== frame "{label}" {path} title="{snap["title"]}"')
            sections.extend(snap["lines"])
            signature.append(f"{label}:{snap['title']}")
        text = "\n".join(sections)
        if len(text) > MAX_SNAPSHOT_CHARS:
            text = text[:MAX_SNAPSHOT_CHARS] + "\n…(truncated)"
        png = await self.screenshot(redacted=True) if screenshot else None
        return Observation(
            text=text,
            elements=elements,
            signature="|".join(signature),
            url=self.page.url,
            screenshot_png=png,
            dialogs=list(self._dialogs),
        )

    async def screenshot(self, *, redacted: bool = True) -> bytes:
        frames = self._frames()
        if redacted:
            await self._each_frame(
                frames,
                "a => { window.__cu.applyRedaction(a[0], a[1]); window.__cu.setMasking(true) }",
                [self.redact_labels, sorted(self.sensitive_values)],
            )
        try:
            return await self.page.screenshot(type="jpeg", quality=70)
        finally:
            if redacted:
                await self._each_frame(frames, "() => window.__cu.setMasking(false)", None)

    async def _each_frame(self, frames: list[Frame], js: str, arg: Any) -> None:
        for frame in frames:
            try:
                await frame.evaluate(f"(a) => window.__cu && ({js})(a)", arg)
            except PlaywrightError:
                continue

    # ---------------------------------------------------------------- recording: ref → Target
    async def target_for(self, ref: str, examples: dict[str, str]) -> Target:
        frame = self._ref_frames.get(ref)
        if frame is None:
            raise KeyError(f"unknown element ref {ref}")
        desc: dict[str, Any] | None = await frame.evaluate("r => window.__cu.describe(r)", ref)
        if desc is None:
            raise KeyError(f"element {ref} is no longer on the page")
        candidates: list[Strategy] = []
        if desc.get("interactive") and desc.get("name") and desc.get("role"):
            candidates.append(RoleStrategy(role=desc["role"], name=desc["name"]))
        if desc.get("label"):
            candidates.append(LabelStrategy(label=desc["label"]))
        if desc.get("field_name"):
            candidates.append(FieldNameStrategy(name=desc["field_name"]))
        if desc.get("table_cell"):
            candidates.append(TableCellStrategy(**desc["table_cell"]))
        if desc.get("text"):
            element = (
                "link"
                if desc["role"] == "link"
                else "button"
                if desc["role"] == "button"
                else "any"
            )
            candidates.append(TextStrategy(text=desc["text"], element=element))
        candidates.append(CssStrategy(selector=desc["css"]))

        verified: list[Strategy] = []
        for strategy in candidates:
            locator, count = await self._locate(frame, strategy, {})
            if count == 1 and locator is not None and await _has_ref(locator, ref):
                verified.append(_templatize_strategy(strategy, examples))
        if not verified:
            raise TargetNotFound(Target(description=ref, strategies=candidates), ["none unique"])
        name = desc.get("name") or desc.get("label") or desc.get("text") or desc["tag"]
        what = desc.get("role") or desc["tag"]
        return Target(
            description=templatize(f'{what} "{name}"', examples),
            scope=self._scope_of(frame),
            strategies=verified,
        )

    async def target_for_read(
        self, frame_label: str, locate: Strategy, examples: dict[str, str]
    ) -> Target:
        """Build a verified target for a value the agent wants to read (non-interactive text)."""
        scope = Scope(frames=[] if frame_label in {"", "top"} else frame_label.split("/"))
        frame = self._frame_for(scope)
        if frame is None:
            raise KeyError(f"frame '{frame_label}' not found")
        locator, count = await self._locate(frame, locate, {})
        if count != 1 or locator is None:
            raise TargetNotFound(
                Target(description=frame_label, strategies=[locate]), [f"{locate.kind}={count}"]
            )
        strategies: list[Strategy] = [_templatize_strategy(locate, examples)]
        desc = await locator.evaluate("e => window.__cu.describeElement(e)")
        if desc and desc.get("css"):
            strategies.append(CssStrategy(selector=desc["css"]))
        match locate:
            case TableCellStrategy(row_key=row, column=col):
                what = f"'{col}' of row '{row}'"
            case LabelStrategy(label=label):
                what = f"value labelled '{label}'"
            case _:
                what = f"value located by {locate.kind}"
        return Target(description=templatize(what, examples), scope=scope, strategies=strategies)

    async def frame_titles(self) -> dict[str, str]:
        titles: dict[str, str] = {}
        for frame in self._frames():
            try:
                titles["/".join(self._scope_of(frame).frames)] = (await frame.title()).strip()
            except PlaywrightError:
                continue
        return titles

    # ---------------------------------------------------------------- resolution
    async def _locate(
        self, frame: Frame, strategy: Strategy, params: dict[str, str]
    ) -> tuple[Locator | None, int]:
        try:
            match strategy:
                case RoleStrategy(role=role, name=name):
                    loc = frame.get_by_role(role, name=render(name, params), exact=True)  # type: ignore[arg-type]
                case FieldNameStrategy(name=name):
                    loc = frame.locator(f'[name="{_css_str(render(name, params))}"]')
                case CssStrategy(selector=selector):
                    loc = frame.locator(render(selector, params))
                case LabelStrategy() | TableCellStrategy() | TextStrategy():
                    token = uuid.uuid4().hex[:8]
                    payload = strategy.model_dump()
                    payload = {
                        k: render(v, params) if isinstance(v, str) else v
                        for k, v in payload.items()
                    }
                    count = await frame.evaluate(
                        "([s, t]) => window.__cu.resolve(s, t)", [payload, token]
                    )
                    loc = frame.locator(f'[data-cu-match="{token}"]')
                    return (loc, int(count))
            loc = loc.filter(visible=True)
            return loc, await loc.count()
        except PlaywrightError:
            return None, 0

    async def _resolve(
        self, target: Target, params: dict[str, str]
    ) -> tuple[Resolution, Locator | None]:
        frame = self._frame_for(target.scope)
        if frame is None:
            return Resolution(0, None, -1, ["frame not present"]), None
        tried: list[str] = []
        for rank, strategy in enumerate(target.strategies):
            locator, count = await self._locate(frame, strategy, params)
            tried.append(f"{strategy.kind}={count}")
            if count == 1 and locator is not None:
                return Resolution(1, strategy, rank, tried), locator
        return Resolution(0, None, -1, tried), None

    async def resolve(self, target: Target, params: dict[str, str]) -> Resolution:
        resolution, _ = await self._resolve(target, params)
        return resolution

    async def _require(self, target: Target, params: dict[str, str]) -> tuple[Resolution, Locator]:
        resolution, locator = await self._resolve(target, params)
        if locator is None:
            raise TargetNotFound(target, resolution.tried)
        return resolution, locator

    # ---------------------------------------------------------------- actions (automation)
    async def click(self, target: Target, params: dict[str, str]) -> Resolution:
        self.control.assert_automation()
        resolution, locator = await self._require(target, params)
        await locator.click(timeout=5_000)
        return resolution

    async def fill(self, target: Target, params: dict[str, str], value: str) -> Resolution:
        self.control.assert_automation()
        resolution, locator = await self._require(target, params)
        await locator.fill(value, timeout=5_000)
        return resolution

    async def select(self, target: Target, params: dict[str, str], option: str) -> Resolution:
        self.control.assert_automation()
        resolution, locator = await self._require(target, params)
        await locator.select_option(label=option, timeout=5_000)
        return resolution

    async def press(self, key: str) -> None:
        self.control.assert_automation()
        await self.page.keyboard.press(key)

    async def read(self, target: Target, params: dict[str, str]) -> str:
        _, locator = await self._require(target, params)
        tag = await locator.evaluate("e => e.tagName")
        if tag in {"INPUT", "TEXTAREA", "SELECT"}:
            return await locator.input_value()
        return await locator.inner_text()

    # ---------------------------------------------------------------- conditions
    async def check(self, condition: Condition, params: dict[str, str]) -> bool:
        match condition:
            case TextVisible(text=text, regex=regex, scope=scope):
                frames = [f for f in [self._frame_for(scope)] if f] if scope else self._frames()
                needle = render(text, params)
                for frame in frames:
                    try:
                        body = await frame.evaluate(
                            "() => window.__cu ? window.__cu.pageText() : ''"
                        )
                    except PlaywrightError:
                        continue
                    if re.search(needle, body) if regex else needle in body:
                        return True
                return False
            case TargetPresent(target=target):
                return (await self.resolve(target, params)).count == 1
            case ScreenTitle(title=title, scope=scope):
                title_frame = self._frame_for(scope)
                if title_frame is None:
                    return False
                try:
                    return (await title_frame.title()).strip() == render(title, params).strip()
                except PlaywrightError:
                    return False
            case DialogShown(message_regex=pattern):
                return any(re.search(pattern, m) for m in self._dialogs)
            case HttpStatus(min_status=min_status):
                return any(s >= min_status for s in self._doc_status.values())
        return False

    async def settle(self) -> None:
        await asyncio.sleep(0.15)  # let a click-initiated navigation start
        for frame in self._frames():
            try:
                await frame.wait_for_load_state("domcontentloaded", timeout=5_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue

    def current_url(self) -> str:
        return self.page.url

    def drain_dialogs(self) -> list[str]:
        dialogs, self._dialogs = self._dialogs, []
        return dialogs

    def reset_status(self) -> None:
        self._doc_status.clear()

    @property
    def dialogs(self) -> list[str]:
        return list(self._dialogs)

    async def reload_frames_with(self, text: str) -> int:
        """Reload every frame currently showing `text` (transient error pages)."""
        reloaded = 0
        for frame in self._frames():
            try:
                body = await frame.evaluate("() => window.__cu ? window.__cu.pageText() : ''")
                if text in body:
                    await frame.evaluate("() => location.reload()")
                    reloaded += 1
            except PlaywrightError:
                continue
        return reloaded

    # ---------------------------------------------------------------- human control (console)
    async def human_click(self, operator: str, x: float, y: float) -> None:
        self.control.assert_human(operator)
        self._record_console(operator, "click", {"x": round(x), "y": round(y)})
        await self.page.mouse.click(x, y)

    async def human_type(self, operator: str, text: str) -> None:
        self.control.assert_human(operator)
        self._record_console(operator, "type", {"length": len(text)})  # never the text itself
        await self.page.keyboard.type(text)

    async def human_press(self, operator: str, key: str) -> None:
        self.control.assert_human(operator)
        self._record_console(operator, "press", {"key": key})
        await self.page.keyboard.press(key)

    def _record_console(self, operator: str, kind: str, detail: dict[str, Any]) -> None:
        self.control.record_human_action(
            HumanAction(operator=operator, source="console", kind=kind, detail=detail)
        )


def _frame_key(frame: Frame) -> str:
    return frame.name or "top"


def _css_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _has_ref(locator: Locator, ref: str) -> bool:
    try:
        return bool(await locator.evaluate("(e, r) => e.getAttribute('data-cu-ref') === r", ref))
    except PlaywrightError:
        return False


def _templatize_strategy(strategy: Strategy, examples: dict[str, str]) -> Strategy:
    data = {
        k: templatize(v, examples) if isinstance(v, str) and k != "kind" else v
        for k, v in strategy.model_dump().items()
    }
    return type(strategy).model_validate(data)


def _summarize_human_event(event: dict[str, Any]) -> dict[str, Any]:
    element = event.get("element") or {}
    return {
        "role": element.get("role"),
        "name": element.get("name") or element.get("label") or element.get("text"),
        "field_name": element.get("field_name"),
        "length": event.get("length"),
        "option": event.get("option"),
    }

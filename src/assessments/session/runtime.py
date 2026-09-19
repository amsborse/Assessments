"""Live sessions: one browser + surface + control + recorder, registered so the operator console
(served from the same process) can see and take over exactly the session automation is using.
"""

from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from assessments.runs import RunRecorder
from assessments.safety import Policy
from assessments.session.control import SessionControl
from assessments.surfaces.web.playwright_surface import PlaywrightWebSurface


@dataclass
class LiveSession:
    id: str
    kind: str
    label: str
    control: SessionControl
    surface: PlaywrightWebSurface
    recorder: RunRecorder
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FinishedSession:
    """A recently ended session, kept briefly so the console can show how it ended."""

    id: str
    kind: str
    label: str
    started_at: datetime
    ended_at: datetime
    status: str | None
    events: list[dict[str, Any]]


class SessionRegistry:
    def __init__(self, keep_finished: int = 12) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self.finished: deque[FinishedSession] = deque(maxlen=keep_finished)

    def finish(self, session: LiveSession) -> None:
        from assessments.session.activity import run_status

        events = list(session.recorder.recent)
        self.finished.appendleft(
            FinishedSession(
                session.id,
                session.kind,
                session.label,
                session.started_at,
                datetime.now(UTC),
                run_status(events),
                events,
            )
        )
        self.remove(session.id)

    def get_finished(self, session_id: str) -> FinishedSession | None:
        return next((f for f in self.finished if f.id == session_id), None)

    def add(self, session: LiveSession) -> None:
        self._sessions[session.id] = session

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def get(self, session_id: str) -> LiveSession | None:
        return self._sessions.get(session_id)

    def all(self) -> list[LiveSession]:
        return list(self._sessions.values())

    def by_intervention(self, intervention_id: str) -> LiveSession | None:
        for s in self._sessions.values():
            iv = s.control.intervention
            if iv is not None and iv.id == intervention_id:
                return s
        return None


REGISTRY = SessionRegistry()


@asynccontextmanager
async def live_session(
    kind: str,
    label: str,
    *,
    runs_dir: Path,
    policy: Policy,
    redact_labels: list[str],
    headless: bool,
    registry: SessionRegistry = REGISTRY,
) -> AsyncIterator[LiveSession]:
    recorder = RunRecorder(runs_dir, kind)
    control = SessionControl(recorder.run_id, on_event=lambda t, d: recorder.event(t, **d))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        surface = await PlaywrightWebSurface.create(
            context,
            policy=policy,
            control=control,
            redact_labels=redact_labels,
            on_event=lambda t, d: recorder.event(t, **d),
            sensitive_values=recorder.sensitive,
        )
        session = LiveSession(recorder.run_id, kind, label, control, surface, recorder)
        registry.add(session)
        try:
            yield session
        finally:
            control.end()
            registry.finish(session)
            await browser.close()
            recorder.close()

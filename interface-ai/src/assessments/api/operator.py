"""Operator console API: see interventions, take control of the live session, hand it back.

Identity is a plain `operator` field here (mocked); in production this sits behind SSO and the
claim is bound to the authenticated user. The console binds to localhost by default.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from assessments.errors import AppError, NotFoundError
from assessments.session.activity import progress, summarize
from assessments.session.control import ControlError, Resolution, ResolutionAction
from assessments.session.runtime import REGISTRY, FinishedSession, LiveSession

router = APIRouter()
_CONSOLE_HTML = (Path(__file__).parent / "operator.html").read_text(encoding="utf-8")


class ControlConflict(AppError):
    status_code = 409
    code = "control_conflict"


class ClaimRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=64)


class ReleaseRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=64)
    action: ResolutionAction
    note: str = Field(default="", max_length=500)


class ClickRequest(BaseModel):
    operator: str
    x: float = Field(ge=0, le=4000)
    y: float = Field(ge=0, le=4000)


class TypeRequest(BaseModel):
    operator: str
    text: str = Field(max_length=500)


class PressRequest(BaseModel):
    operator: str
    key: str = Field(
        pattern=r"^(Enter|Tab|Escape|Backspace|ArrowUp|ArrowDown|ArrowLeft|ArrowRight)$"
    )


def _session(session_id: str) -> LiveSession:
    session = REGISTRY.get(session_id)
    if session is None:
        raise NotFoundError(f"no live session {session_id}")
    return session


def _view(s: LiveSession) -> dict[str, Any]:
    iv = s.control.intervention
    return {
        "id": s.id,
        "kind": s.kind,
        "label": s.label,
        "controller": s.control.controller,
        "started_at": s.started_at,
        "intervention": iv.model_dump(mode="json") if iv else None,
        "resolved_interventions": len(s.control.history),
        "progress": progress(list(s.recorder.recent)),
        "finished": False,
        "status": None,
    }


def _finished_view(f: FinishedSession) -> dict[str, Any]:
    return {
        "id": f.id,
        "kind": f.kind,
        "label": f.label,
        "controller": "ended",
        "started_at": f.started_at,
        "ended_at": f.ended_at,
        "intervention": None,
        "progress": progress(f.events),
        "finished": True,
        "status": f.status,
    }


@router.get("/operator", response_class=HTMLResponse, include_in_schema=False)
async def console() -> HTMLResponse:
    return HTMLResponse(_CONSOLE_HTML)


@router.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """Live sessions, then recently finished ones (newest first)."""
    return [_view(s) for s in REGISTRY.all()] + [_finished_view(f) for f in REGISTRY.finished]


@router.get("/api/sessions/{session_id}/activity")
async def activity(session_id: str, after: int = 0) -> list[dict[str, Any]]:
    """What the run has been doing, in plain language (from redacted events)."""
    live = REGISTRY.get(session_id)
    finished = REGISTRY.get_finished(session_id) if live is None else None
    if live is None and finished is None:
        raise NotFoundError(f"no session {session_id}")
    events = list(live.recorder.recent) if live else finished.events  # type: ignore[union-attr]
    items = (summarize(e) for e in events if int(e.get("seq", 0)) > after)
    return [i for i in items if i is not None]


@router.get("/api/sessions/{session_id}/screen")
async def screen(session_id: str, operator: str = "") -> Response:
    """Live view. Redacted unless the requester currently holds control of the session."""
    session = _session(session_id)
    iv = session.control.intervention
    in_control = bool(operator) and iv is not None and iv.claimed_by == operator
    jpeg = await session.surface.screenshot(redacted=not in_control)
    return Response(jpeg, media_type="image/jpeg", headers={"cache-control": "no-store"})


@router.post("/api/interventions/{intervention_id}/claim")
async def claim(intervention_id: str, body: ClaimRequest) -> dict[str, Any]:
    session = REGISTRY.by_intervention(intervention_id)
    if session is None:
        raise NotFoundError(f"no open intervention {intervention_id}")
    try:
        session.control.claim(intervention_id, body.operator)
    except ControlError as exc:
        raise ControlConflict(str(exc)) from exc
    return _view(session)


@router.post("/api/interventions/{intervention_id}/release")
async def release(intervention_id: str, body: ReleaseRequest) -> dict[str, Any]:
    session = REGISTRY.by_intervention(intervention_id)
    if session is None:
        raise NotFoundError(f"no open intervention {intervention_id}")
    try:
        iv = session.control.release(
            intervention_id, Resolution(action=body.action, operator=body.operator, note=body.note)
        )
    except ControlError as exc:
        raise ControlConflict(str(exc)) from exc
    return {"intervention": iv.model_dump(mode="json"), "session": _view(session)}


@router.post("/api/sessions/{session_id}/click")
async def click(session_id: str, body: ClickRequest) -> dict[str, str]:
    await _act(_session(session_id), lambda s: s.surface.human_click(body.operator, body.x, body.y))
    return {"status": "ok"}


@router.post("/api/sessions/{session_id}/type")
async def type_text(session_id: str, body: TypeRequest) -> dict[str, str]:
    await _act(_session(session_id), lambda s: s.surface.human_type(body.operator, body.text))
    return {"status": "ok"}


@router.post("/api/sessions/{session_id}/press")
async def press(session_id: str, body: PressRequest) -> dict[str, str]:
    await _act(_session(session_id), lambda s: s.surface.human_press(body.operator, body.key))
    return {"status": "ok"}


async def _act(session: LiveSession, fn: Any) -> None:
    try:
        await fn(session)
    except ControlError as exc:
        raise ControlConflict(str(exc)) from exc

"""Plain-language activity feed for the operator console, from (already redacted) run events.

Each item has a `tone` the console maps to colour: `info` (agent/engine doing its job),
`success`, `recovered`, `business`, `human`, `danger`, `muted`.
"""

from typing import Any

_OUTCOME_TONE = {
    "succeeded": "success",
    "business_outcome": "business",
    "failed": "danger",
    "rejected": "muted",
    "aborted": "muted",
}


def _target(event: dict[str, Any]) -> str:
    target = event.get("target")
    if isinstance(target, dict):
        return str(target.get("description", ""))
    return str(target or "")


def summarize(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map one recorder event to a feed item, or None if it is not worth showing."""
    kind = event.get("type")
    text: str
    tone = "info"
    match kind:
        case "run_started":
            text = f"Started {event.get('kind', 'run')}" + (
                f": {event['goal']}" if event.get("goal") else f" of {event.get('capability', '')}"
            )
        case "decision":
            action = event.get("action") or {}
            if not action:
                text, tone = f"Model response rejected: {event.get('error', '')}", "danger"
            else:
                what = (
                    action.get("secret")
                    or action.get("value")
                    or action.get("option")
                    or action.get("output")
                    or ""
                )
                text = (
                    f"Agent chose {action.get('tool')}{f' {what}' if what else ''}: "
                    f"{action.get('reason', '')}"
                )
        case "action":
            text = f"Did {event.get('action')} on {_target(event) or event.get('output', '')}"
        case "action_failed":
            text, tone = f"Action failed: {event.get('error', '')}", "danger"
        case "step_passed":
            text = f"Step {event.get('step')} checkpoint passed" + (
                f" (via {event['strategy']})" if event.get("strategy") else ""
            )
        case "locator_degraded":
            text, tone = (
                f"Drift: step {event.get('step_id')} found only via {event.get('used')}",
                "recovered",
            )
        case "state_detected":
            state_kind = event.get("kind")
            tone = {
                "business_outcome": "business",
                "recoverable": "recovered",
                "escalate": "human",
                "failure": "danger",
            }.get(str(state_kind), "info")
            text = f"Detected {event.get('state')} ({state_kind}) → {event.get('response')}"
        case "restart":
            text, tone = "Restarting the flow from the beginning", "recovered"
        case "policy" if event.get("verdict") != "allow":
            text = f"Policy: {event.get('verdict')} ({event.get('reason')})"
            tone = "human" if event.get("verdict") == "require_approval" else "danger"
        case "dialog":
            text, tone = f"Dialog dismissed: {event.get('message', '')}", "recovered"
        case "intervention_raised":
            text, tone = f"Asked a person for help: {event.get('reason', '')}", "human"
        case "intervention_claimed":
            text, tone = f"{event.get('operator')} took control", "human"
        case "human_action":
            text, tone = f"{event.get('operator')} {_human(event)}", "human"
        case "intervention_resolved":
            res = event.get("resolution") or {}
            text = f"{res.get('operator')} handed control back ({res.get('action')})"
            tone = "human"
        case "run_finished":
            status = str(event.get("status", ""))
            outcome = (
                (event.get("outcome") or {}).get("code")
                if isinstance(event.get("outcome"), dict)
                else None
            )
            error = (
                (event.get("error") or {}).get("code")
                if isinstance(event.get("error"), dict)
                else None
            )
            text = f"Finished: {status.replace('_', ' ')}" + (
                f" ({outcome or error})" if outcome or error else ""
            )
            tone = _OUTCOME_TONE.get(status, "info")
        case _:
            return None
    return {"seq": event.get("seq"), "ts": event.get("ts"), "text": text, "tone": tone}


def _human(event: dict[str, Any]) -> str:
    d = event.get("detail") or {}
    kind, name = event.get("kind"), d.get("name")
    if event.get("source") == "console":
        match kind:
            case "click":
                return "clicked in the live screen"
            case "type":
                return f"typed {d.get('length', 0)} characters"
            case "press":
                return f"pressed {d.get('key')}"
    match kind:
        case "fill":
            return f"filled “{name or d.get('field_name') or 'a field'}”"
        case "select":
            return f"chose {d.get('option')} in “{name}”"
        case "click" if name:
            return f"clicked {d.get('role') or ''} “{name}”".replace("  ", " ")
    return str(kind)


def run_status(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "run_finished":
            return str(event.get("status"))
    return None


def progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Where a run is: its planned steps (replay) and which are done / current."""
    plan: list[dict[str, Any]] = []
    done: set[str] = set()
    current: str | None = None
    decisions = 0
    for event in events:
        kind = event.get("type")
        if kind == "run_started" and event.get("plan"):
            plan, done, current = list(event["plan"]), set(), None
        elif kind == "restart":
            done, current = set(), None
        elif kind == "step_started":
            current = str(event.get("step"))
        elif kind in {"step_passed", "step_skipped"}:
            done.add(str(event.get("step")))
        elif kind == "decision":
            decisions += 1
    return {"plan": plan, "done": sorted(done), "current": current, "decisions": decisions}

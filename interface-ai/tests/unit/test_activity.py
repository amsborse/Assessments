import pytest

from assessments.session.activity import run_status, summarize


@pytest.mark.parametrize(
    ("event", "text", "tone"),
    [
        (
            {
                "type": "decision",
                "action": {"tool": "fill", "value": "{{member_id}}", "reason": "enter the member"},
            },
            "Agent chose fill {{member_id}}: enter the member",
            "info",
        ),
        (
            {
                "type": "state_detected",
                "state": "member_not_found",
                "kind": "business_outcome",
                "response": "return_outcome",
            },
            "Detected member_not_found (business_outcome) → return_outcome",
            "business",
        ),
        (
            {
                "type": "human_action",
                "operator": "jane",
                "source": "page",
                "kind": "fill",
                "detail": {"name": "Supervisor ID:", "length": 5},
            },
            "jane filled “Supervisor ID:”",
            "human",
        ),
        (
            {
                "type": "human_action",
                "operator": "jane",
                "source": "console",
                "kind": "type",
                "detail": {"length": 4},
            },
            "jane typed 4 characters",
            "human",
        ),
        (
            {"type": "run_finished", "status": "failed", "error": {"code": "checkpoint_failed"}},
            "Finished: failed (checkpoint_failed)",
            "danger",
        ),
    ],
)
def test_summarize_turns_events_into_plain_language(
    event: dict[str, object], text: str, tone: str
) -> None:
    item = summarize({"seq": 1, "ts": "t", **event})

    assert item is not None
    assert (item["text"], item["tone"]) == (text, tone)


def test_routine_events_are_left_out() -> None:
    assert summarize({"type": "observation", "step": 1}) is None
    assert summarize({"type": "policy", "verdict": "allow"}) is None


def test_run_status_is_the_last_finish() -> None:
    assert (
        run_status([{"type": "run_started"}, {"type": "run_finished", "status": "succeeded"}])
        == "succeeded"
    )
    assert run_status([{"type": "run_started"}]) is None

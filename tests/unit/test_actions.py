import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from assessments.agent.actions import Extract, Fill, parse_tool_call, tool_definitions
from assessments.agent.decider import AnthropicDecider, DecisionContext
from assessments.surfaces.base import Observation


def test_tool_schemas_are_self_contained_and_hide_the_discriminator() -> None:
    tools = {t["name"]: t for t in tool_definitions()}

    assert set(tools) >= {"click", "fill", "fill_secret", "extract", "request_help", "finish"}
    for tool in tools.values():
        blob = json.dumps(tool["input_schema"])
        assert "$ref" not in blob
        assert "$defs" not in blob
        assert "tool" not in tool["input_schema"]["properties"]
        assert "reason" in tool["input_schema"]["required"]
    locate = tools["extract"]["input_schema"]["properties"]["locate"]
    assert locate["properties"]["kind"]["enum"] == ["table_cell", "label"]


def test_parse_tool_call_validates_input() -> None:
    action = parse_tool_call(
        "extract",
        {
            "output": "bal",
            "frame": "main",
            "reason": "r",
            "locate": {"kind": "table_cell", "row_key": "Savings", "column": "Balance"},
        },
    )

    assert isinstance(action, Extract)
    assert action.locate.row_key == "Savings"
    with pytest.raises(ValidationError):
        parse_tool_call("fill", {"ref": "e1", "reason": "r"})  # missing value
    with pytest.raises(ValidationError):
        parse_tool_call("click", {"ref": "e1", "reason": "r", "extra": 1})


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _decider(response: Any) -> tuple[AnthropicDecider, _FakeMessages]:
    decider = AnthropicDecider("claude-opus-5", api_key="test-key", fallbacks=False)
    fake = _FakeMessages(response)
    decider._client = SimpleNamespace(messages=fake)  # type: ignore[assignment]
    return decider, fake


def _ctx() -> DecisionContext:
    obs = Observation(
        text='- textbox "Member #:" [e5]',
        elements={},
        signature="s",
        url="u",
        screenshot_png=b"\xff\xd8jpeg",
    )
    return DecisionContext(
        goal="look up {{member_id}}",
        inputs={"member_id": "Member number"},
        outputs={},
        secrets=["operator_password"],
        history=[],
        feedback=[],
        observation=obs,
        step=1,
        max_steps=10,
    )


def _response(*blocks: Any, stop_reason: str = "tool_use") -> Any:
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        model="claude-opus-5",
        usage=usage,
        _request_id="req_1",
    )


async def test_decider_sends_one_stateless_request_and_parses_the_tool_call() -> None:
    call = SimpleNamespace(
        type="tool_use",
        name="fill",
        input={"ref": "e5", "value": "{{member_id}}", "reason": "type id"},
    )
    decider, fake = _decider(_response(call))

    decision = await decider.decide(_ctx())

    assert isinstance(decision.action, Fill)
    assert decision.raw["request_id"] == "req_1"
    [request] = fake.calls
    content = request["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "text"]
    assert "12345" not in content[1]["text"]  # the model only ever sees placeholders
    assert request["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


async def test_decider_reports_missing_tool_call_and_refusal_as_errors() -> None:
    text = SimpleNamespace(type="text", text="I think I should click search")
    no_tool, _ = _decider(_response(text, stop_reason="end_turn"))
    refused, _ = _decider(_response(stop_reason="refusal"))

    assert (await no_tool.decide(_ctx())).error is not None
    decision = await refused.decide(_ctx())
    assert decision.action is None
    assert decision.error == "model declined the request"


def test_claude_code_reply_is_validated_like_a_tool_call() -> None:
    from assessments.agent.claude_code import RESPONSE_SCHEMA, parse_cli_reply

    ok = parse_cli_reply(
        {
            "is_error": False,
            "session_id": "s",
            "modelUsage": {"claude-opus-5": {}},
            "usage": {"input_tokens": 3, "output_tokens": 4},
            "structured_output": {"tool": "click", "input": {"ref": "e3", "reason": "open"}},
        },
        "claude-opus-5",
    )
    bad = parse_cli_reply(
        {"is_error": False, "structured_output": {"tool": "click", "input": {}}}, "m"
    )
    err = parse_cli_reply({"is_error": True, "result": "Not logged in"}, "m")

    assert ok.action is not None
    assert ok.action.tool == "click"
    assert ok.raw["model"] == "claude-opus-5"
    assert bad.action is None
    assert "invalid action input" in (bad.error or "")
    assert err.action is None
    assert "Not logged in" in (err.error or "")
    assert set(RESPONSE_SCHEMA["properties"]["tool"]["enum"]) >= {"click", "extract", "finish"}

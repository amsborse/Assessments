"""Decider backed by the Claude Code CLI (`claude -p`), for accounts without API keys.

Each decision is one non-interactive `claude -p` call, authenticated by the local Claude Code
login (e.g. a Claude Pro/Max subscription). The call is isolated: no tools, no settings,
CLAUDE.md or memory (empty working directory, `--setting-sources ""`, custom system prompt), no
saved session. The reply is constrained by a JSON schema and validated exactly like an API tool
call, so the rest of the loop is identical to `AnthropicDecider`.

Difference from the API decider: the prompt carries the text snapshot but not the screenshot
(the CLI has no image input without enabling file tools). The snapshot is an accessibility-style
description of every frame, which is what actions are grounded in anyway.
"""

import asyncio
import json
import shutil
import tempfile
from typing import Any

from pydantic import ValidationError

from assessments.agent.actions import TOOL_DOCS, parse_tool_call, tool_definitions
from assessments.agent.decider import SYSTEM_PROMPT, Decision, DecisionContext, render_task

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": list(TOOL_DOCS)},
        "input": {"type": "object", "description": "Arguments for the chosen tool."},
    },
    "required": ["tool", "input"],
    "additionalProperties": False,
}


def _system_prompt() -> str:
    tools = "\n".join(
        f"- {t['name']}: {t['description']}\n  input schema: {json.dumps(t['input_schema'])}"
        for t in tool_definitions()
    )
    return (
        SYSTEM_PROMPT.replace(
            "Act only through the provided tools, one tool call per turn.",
            "Answer with exactly one action as {tool, input}.",
        )
        + "\nAvailable actions (use `tool` = the name and `input` matching its schema):\n"
        + tools
    )


class ClaudeCodeDecider:
    def __init__(
        self, model: str, *, executable: str | None = None, timeout_s: float = 300
    ) -> None:
        exe = executable or shutil.which("claude")
        if exe is None:
            raise RuntimeError(
                "Claude Code CLI not found on PATH (install it and run `claude` once to log in)"
            )
        self.executable = exe
        self.model_name = f"{model} (via Claude Code CLI)"
        self._model = model
        self._timeout_s = timeout_s
        self._system = _system_prompt()
        self._workdir = tempfile.mkdtemp(prefix="cu-decider-")  # no CLAUDE.md / project settings

    async def decide(self, ctx: DecisionContext) -> Decision:
        args = [
            self.executable,
            "-p",
            "--model",
            self._model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(RESPONSE_SCHEMA),
            "--system-prompt",
            self._system,
            "--tools",
            "",
            "--setting-sources",
            "",
            "--no-session-persistence",
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=self._workdir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(render_task(ctx).encode("utf-8")), timeout=self._timeout_s
            )
        except TimeoutError:
            proc.kill()
            return Decision(None, {}, f"Claude Code CLI timed out after {self._timeout_s:.0f}s")
        try:
            reply = json.loads(out.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            detail = (err or out).decode("utf-8", errors="replace").strip()[:300]
            return Decision(None, {"exit": proc.returncode}, f"Claude Code CLI failed: {detail}")

        return parse_cli_reply(reply, self._model)


def parse_cli_reply(reply: dict[str, Any], model: str) -> Decision:
    """Turn `claude -p --output-format json` output into a validated Decision."""
    usage = reply.get("usage") or {}
    raw: dict[str, Any] = {
        "via": "claude-code-cli",
        "session_id": reply.get("session_id"),
        "model": next(iter(reply.get("modelUsage") or {}), model),
        "stop_reason": reply.get("stop_reason"),
        "duration_ms": reply.get("duration_ms"),
        "usage": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
        },
    }
    if reply.get("is_error"):
        return Decision(None, raw, f"Claude Code CLI error: {str(reply.get('result'))[:300]}")
    choice = reply.get("structured_output")
    if not isinstance(choice, dict) or "tool" not in choice:
        return Decision(
            None, raw, f"no structured action in reply: {str(reply.get('result'))[:200]}"
        )
    raw["tool"] = {"name": choice["tool"], "input": choice.get("input")}
    try:
        return Decision(parse_tool_call(choice["tool"], dict(choice.get("input") or {})), raw)
    except (ValidationError, TypeError) as exc:
        return Decision(None, raw, f"invalid action input: {str(exc)[:300]}")

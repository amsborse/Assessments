"""The discovery agent's action space: one tool per action, validated with Pydantic.

Every action carries a `reason`, which is logged as the "why" for each step. Element-level
actions reference `ref`s from the current snapshot; the surface converts a ref into a verified,
multi-strategy Target at record time, so the model never writes selectors.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(description="One sentence: why this action moves toward the goal.")


class Click(_Action):
    tool: Literal["click"] = "click"
    ref: str = Field(description="Element ref from the snapshot, e.g. 'e12'.")


class Fill(_Action):
    tool: Literal["fill"] = "fill"
    ref: str
    value: str = Field(
        description="Text to type. Use {{input_name}} placeholders for task inputs, "
        "never the raw value."
    )


class FillSecret(_Action):
    tool: Literal["fill_secret"] = "fill_secret"
    ref: str
    secret: str = Field(description="Name of a credential from the task's secret list.")


class Select(_Action):
    tool: Literal["select"] = "select"
    ref: str
    option: str = Field(description="Visible option text, or an {{input_name}} placeholder.")


class Press(_Action):
    tool: Literal["press"] = "press"
    key: str = Field(description="Tab, Escape or an arrow key. Submit forms by clicking buttons.")


class Locate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["table_cell", "label"]
    row_key: str | None = Field(default=None, description="table_cell: text identifying the row.")
    column: str | None = Field(default=None, description="table_cell: column header text.")
    label: str | None = Field(default=None, description="label: label text next to the value.")


class Extract(_Action):
    tool: Literal["extract"] = "extract"
    output: str = Field(description="Name of a declared task output.")
    frame: str = Field(description="Frame label from the snapshot header, e.g. 'main'.")
    locate: Locate


class Wait(_Action):
    tool: Literal["wait"] = "wait"
    seconds: float = Field(ge=0.5, le=10)


class RequestHelp(_Action):
    tool: Literal["request_help"] = "request_help"


class Finish(_Action):
    tool: Literal["finish"] = "finish"
    summary: str = Field(description="What was accomplished; do not repeat sensitive values.")


AgentAction = Annotated[
    Click | Fill | FillSecret | Select | Press | Extract | Wait | RequestHelp | Finish,
    Field(discriminator="tool"),
]
ACTION_ADAPTER: TypeAdapter[AgentAction] = TypeAdapter(AgentAction)

TOOL_DOCS: dict[str, tuple[type[_Action], str]] = {
    "click": (Click, "Click a link, button, or control identified by its snapshot ref."),
    "fill": (Fill, "Replace the contents of a text field."),
    "fill_secret": (FillSecret, "Type a named credential into a field without seeing its value."),
    "select": (Select, "Choose an option in a dropdown."),
    "press": (Press, "Press a navigation key."),
    "extract": (Extract, "Read a declared output value from the screen by its semantic location."),
    "wait": (Wait, "Wait for a slow page, then observe again."),
    "request_help": (
        RequestHelp,
        "Ask a human operator to take over when you are blocked or unsure it is safe to continue.",
    ),
    "finish": (Finish, "End the run once the goal is met and all declared outputs are extracted."),
}


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Replace {"$ref": "#/$defs/X"} with the definition, so tool schemas are self-contained."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def tool_definitions() -> list[dict[str, Any]]:
    tools = []
    for name, (model, doc) in TOOL_DOCS.items():
        raw = model.model_json_schema()
        schema: dict[str, Any] = _inline_refs(raw, raw.get("$defs", {}))
        schema["properties"].pop("tool", None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r != "tool"]
        tools.append({"name": name, "description": doc, "input_schema": schema})
    return tools


def parse_tool_call(name: str, arguments: dict[str, Any]) -> AgentAction:
    return ACTION_ADAPTER.validate_python({**arguments, "tool": name})

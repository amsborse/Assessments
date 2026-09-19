"""MCP server: capabilities as tools for any MCP-capable AI agent.

    uv run assessments mcp            # stdio transport (e.g. add to an MCP client config)

Tools:
- `list_capabilities` — the catalog: each capability's contract (typed inputs as JSON Schema,
  outputs, business outcomes, side effects, review status, certification confidence).
- `invoke_capability` — deterministic replay by name with typed params; returns the structured
  ReplayResult (status first: succeeded / business_outcome / rejected / failed / aborted).

The same governance as every other entry point applies: drafts need `attended=true`,
irreversible steps need an approved capability plus `allow_irreversible=true`, and the policy
and redaction layers are unchanged. An agent calling this never sees a selector or a screen.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

from assessments.capability.certification import load_report
from assessments.capability.profile import effective_states, load_profile
from assessments.capability.schema import Capability, OutcomeKind, ReviewStatus, ValueType
from assessments.capability.store import CapabilityNotFound, CapabilityStore
from assessments.config import Settings
from assessments.service import run_replay

_JSON_TYPES = {
    ValueType.STRING: "string",
    ValueType.INTEGER: "integer",
    ValueType.DECIMAL: "string",
    ValueType.MONEY: "string",
    ValueType.DATE: "string",
    ValueType.BOOLEAN: "boolean",
    ValueType.ENUM: "string",
}


def input_schema(cap: Capability) -> dict[str, Any]:
    """JSON Schema for a capability's params, so agents can build calls without guessing."""
    props: dict[str, Any] = {}
    for name, spec in cap.inputs.items():
        prop: dict[str, Any] = {"type": _JSON_TYPES[spec.type], "description": spec.description}
        if spec.pattern:
            prop["pattern"] = spec.pattern
        if spec.enum:
            prop["enum"] = spec.enum
        if spec.type is ValueType.MONEY:
            prop["description"] += " (decimal, e.g. 25.00)"
        props[name] = prop
    required = [n for n, s in cap.inputs.items() if s.required]
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def describe(settings: Settings, cap: Capability) -> dict[str, Any]:
    states = effective_states(load_profile(settings.catalog_dir, cap.app.profile), cap)
    report = load_report(settings.catalog_dir, cap)
    return {
        "name": cap.id,
        "version": cap.version,
        "title": cap.title,
        "description": cap.description,
        "input_schema": input_schema(cap),
        "outputs": {
            k: {"type": v.type, "description": v.description} for k, v in cap.outputs.items()
        },
        "business_outcomes": {
            s.id: s.description for s in states if s.kind is OutcomeKind.BUSINESS
        },
        "side_effects": cap.side_effects,
        "review": cap.review.status,
        "confidence": report.confidence if report else None,
    }


def build_server(settings: Settings) -> MCPServer:
    server = MCPServer(
        name="legacy-banking-capabilities",
        instructions=(
            "Deterministic capabilities for a legacy credit-union teller system. Call "
            "list_capabilities first, then invoke_capability with params matching input_schema. "
            "Branch on result.status: 'business_outcome' is a valid answer (e.g. "
            "member_not_found), not an error; do not retry it."
        ),
    )
    store = CapabilityStore(settings.catalog_dir)

    @server.tool(description="List available capabilities and their typed contracts.")
    async def list_capabilities() -> list[dict[str, Any]]:
        return [describe(settings, c) for c in store.list()]

    @server.tool(
        description=(
            "Run a capability by name with typed params (deterministic replay, no model). "
            "Drafts require attended=true. Irreversible capabilities also need "
            "allow_irreversible=true and an approved review."
        )
    )
    async def invoke_capability(
        name: str,
        params: dict[str, Any],
        tenant: str | None = None,
        attended: bool = False,
        allow_irreversible: bool = False,
    ) -> dict[str, Any]:
        try:
            cap = store.load(name)
        except CapabilityNotFound:
            return {
                "status": "rejected",
                "error": {"code": "unknown_capability", "message": f"no capability {name}"},
            }
        if cap.review.status is not ReviewStatus.APPROVED and not attended:
            return {
                "status": "rejected",
                "error": {
                    "code": "capability_not_approved",
                    "message": f"{cap.ref} is {cap.review.status}; "
                    "pass attended=true or approve it",
                },
            }
        result = await run_replay(
            settings,
            cap.ref,
            params,
            tenant=tenant,
            headless=settings.browser_headless,
            allow_irreversible=allow_irreversible,
        )
        return result.model_dump(mode="json")

    return server

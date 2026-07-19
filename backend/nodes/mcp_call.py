"""`mcp_call` node: connects to a configured MCP server (a local stdio
subprocess) and invokes one of its tools -- the "real external systems"
connectivity node from ARCHITECTURE.md §2.1 (spec-003).

SECURITY: every tool call requires explicit approval by default
(require_approval=True) via a blocking terminal prompt -- spec-003 §3/§7's
MVP decision not to yet distinguish read vs. write tool calls.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.mcp import client as mcp_client
from backend.mcp.client import McpConnectionError, coerce_value, find_tool
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class McpCallConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    tool_name: str
    credential_ref: str | None = None
    require_approval: bool = True


def _resolve_mcp_slots(
    node: NodeSpec,
) -> tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None:
    """Per-instance schema resolved via a real (read-only) call to the
    configured server -- mirrors `code`'s resolve_slots contract exactly:
    returns None on any discovery failure (server unreachable, tool not
    found), which validation treats as "unresolvable, skip this node." The
    concrete error then surfaces at execution time -- spec-003 §6 permits
    either "a clear validation or execution error."
    """
    try:
        config = McpCallConfig.model_validate(node.config)
    except Exception:
        return None
    try:
        tools = mcp_client.list_tools(config.command, config.args)
    except McpConnectionError:
        return None
    tool = find_tool(tools, config.tool_name)
    if tool is None:
        return None
    inputs = [InputSlotSpec(name, TEXT) for name in tool.param_names]
    outputs = [OutputSlotSpec("result", TEXT)]
    return inputs, outputs


def _default_terminal_approval(tool_name: str, arguments: dict[str, Any]) -> bool:
    print(f"\n[mcp_call] About to call tool '{tool_name}' with arguments:")
    print(json.dumps(arguments, indent=2))
    response = input("Proceed? [y/N]: ").strip().lower()
    return response == "y"


@register_node(
    "mcp_call",
    inputs=[],
    outputs=[],
    config_model=McpCallConfig,
    category="connectivity",
    resolve_slots=_resolve_mcp_slots,
)
def execute_mcp_call(ctx: ExecutionContext) -> NodeResult:
    config = McpCallConfig.model_validate(ctx.node.config)

    try:
        tools = mcp_client.list_tools(config.command, config.args)
        tool = find_tool(tools, config.tool_name)
        if tool is None:
            available = ", ".join(t.name for t in tools) or "(none)"
            raise NodeExecutionError(
                f"MCP tool '{config.tool_name}' not found on server; available tools: {available}"
            )
        arguments = {
            name: coerce_value(ctx.inputs[name], tool.param_json_types.get(name, "string"))
            for name in tool.param_names
        }
    except NodeExecutionError:
        raise
    except Exception as e:
        raise NodeExecutionError(f"mcp_call failed to resolve tool: {e}") from e

    if config.require_approval:
        approve = ctx.resources.get("approval_prompt", _default_terminal_approval)
        if not approve(config.tool_name, arguments):
            raise NodeExecutionError(
                f"Tool call to '{config.tool_name}' was declined by the approval gate"
            )

    # credential_ref is a key into ctx.resources (test/override seam, same
    # pattern as llm_client); if absent, the subprocess still inherits the
    # full os.environ (see backend/mcp/client.py), so a credential already
    # exported in the invoking shell reaches the server with no wiring here.
    env: dict[str, str] = {}
    if config.credential_ref:
        credential = ctx.resources.get(config.credential_ref)
        if credential is not None:
            env[config.credential_ref.upper()] = str(credential)

    try:
        result_text = mcp_client.call_tool(
            config.command, config.args, config.tool_name, arguments, env=env or None
        )
    except Exception as e:
        raise NodeExecutionError(f"mcp_call failed: {e}") from e

    return NodeResult(outputs={"result": result_text}, side_effect=True)

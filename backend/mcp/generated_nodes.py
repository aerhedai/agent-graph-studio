"""spec-019: dynamically generates one registered node type per tool
discovered on a saved `mcp_server` connection -- the "written over a
generic node" mechanism. One shared execute closure per generated type
(parameterized by which (connection_name, tool_name) it's bound to) does
the real work; what's generated per tool is a real, typed, palette-visible
`NodeDefinition`, not a manually-configured generic node.

Callers (backend/api/app.py): `generate_node_types_for_connection` on
connection create and on explicit "refresh capabilities"; `unregister_for_connection`
on connection delete; both are called once per saved `mcp_server` connection
at backend startup so the palette is correct immediately after a restart
(mirrors SPEC-015's `_reactivate_persisted_graphs` pattern).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from backend.connections.mcp_server_connection import McpServerConnectionConfig, transport_config
from backend.connections.store import get_connection
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.mcp.client import McpToolInfo, coerce_value, default_terminal_approval
from backend.mcp.transport import call_tool, list_tools
from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec, default_registry
from backend.schema.types import TEXT


class _EmptyConfig(BaseModel):
    """Generated nodes take no per-instance config -- the tool and
    connection they're bound to are baked into the registration itself, not
    user-editable per node."""


# Tracks what was generated per connection, so a refresh/delete can remove
# exactly those types without prefix-scanning the whole registry. Rebuilt
# fresh on process start (see regenerate_all_on_startup) -- an in-memory
# tracking dict, same "rebuilt on startup, not itself persisted" shape as
# backend/triggers/registry.py's active-trigger cache.
_generated_types_by_connection: dict[str, list[str]] = {}


def type_name_for(connection_name: str, tool_name: str) -> str:
    # Namespaced by connection name so two different mcp_server connections
    # can each have a same-named tool with no collision.
    return f"mcp__{connection_name}__{tool_name}"


def _make_execute(connection_name: str, tool: McpToolInfo, path: Path | None) -> Callable[[ExecutionContext], NodeResult]:
    def execute(ctx: ExecutionContext) -> NodeResult:
        profile = get_connection(connection_name, path=path)
        if profile is None:
            raise NodeExecutionError(f"mcp_server connection '{connection_name}' no longer exists")
        config = McpServerConnectionConfig.model_validate(profile.config)
        server_config = transport_config(config)

        arguments = {
            name: coerce_value(ctx.inputs[name], tool.param_json_types.get(name, "string"))
            for name in tool.param_names
            if name in ctx.inputs
        }

        # spec-019 §4: trust is a property of the connection, not the node
        # -- an untrusted mcp_server connection's generated nodes still gate
        # on the same interactive approval mcp_call always requires;
        # trusted=true is the deliberate, explicit opt-out.
        if not config.trusted:
            approve = ctx.resources.get("approval_prompt", default_terminal_approval)
            if not approve(tool.name, arguments):
                raise NodeExecutionError(
                    f"Tool call to '{tool.name}' was declined by the approval gate"
                )

        try:
            result_text = call_tool(server_config, tool.name, arguments)
        except Exception as e:
            raise NodeExecutionError(f"{connection_name}.{tool.name} failed: {e}") from e

        return NodeResult(outputs={"result": result_text}, side_effect=True)

    return execute


def unregister_for_connection(connection_name: str, registry: NodeRegistry = default_registry) -> None:
    for type_name in _generated_types_by_connection.pop(connection_name, []):
        registry.unregister(type_name)


def generate_node_types_for_connection(
    connection_name: str,
    path: Path | None = None,
    registry: NodeRegistry = default_registry,
) -> list[str]:
    """Discovers `connection_name`'s tools (live) and registers one node
    type per tool. Idempotent and safe to call again as a refresh --
    discovery runs *before* the connection's prior generated set is torn
    down, so a failed refresh (server unreachable) leaves the previously-
    working node set intact rather than wiping it out."""
    profile = get_connection(connection_name, path=path)
    if profile is None:
        raise ValueError(f"Connection '{connection_name}' does not exist")
    if profile.type != "mcp_server":
        raise ValueError(f"Connection '{connection_name}' is not an mcp_server connection")

    config = McpServerConnectionConfig.model_validate(profile.config)
    server_config = transport_config(config)
    tools = list_tools(server_config)  # raises McpConnectionError before anything is torn down

    unregister_for_connection(connection_name, registry=registry)

    created: list[str] = []
    for tool in tools:
        type_name = type_name_for(connection_name, tool.name)
        inputs = [
            InputSlotSpec(name, TEXT, required=name in tool.required_names) for name in tool.param_names
        ]
        outputs = [OutputSlotSpec("result", TEXT)]
        registry.register(
            NodeDefinition(
                type_name=type_name,
                inputs=inputs,
                outputs=outputs,
                config_model=_EmptyConfig,
                execute=_make_execute(connection_name, tool, path),
                category="apps",
                integration=connection_name,
                capability_group=None,
            )
        )
        created.append(type_name)

    _generated_types_by_connection[connection_name] = created
    return created


def regenerate_all_on_startup(path: Path | None = None, registry: NodeRegistry = default_registry) -> None:
    """spec-019: every saved `mcp_server` connection gets its generated node
    set rebuilt once at backend startup, mirroring SPEC-015's
    `_reactivate_persisted_graphs` -- so the palette is correct immediately
    after a restart, not only after each connection happens to be manually
    refreshed. A single connection's discovery failure (server unreachable
    at boot) is logged and skipped, not fatal to startup or to any other
    connection's regeneration -- same "one broken thing can't block
    everything else" precedent as trigger reactivation."""
    import logging

    from backend.connections.store import list_connections

    logger = logging.getLogger(__name__)
    for profile in list_connections(path=path):
        if profile.type != "mcp_server":
            continue
        try:
            generate_node_types_for_connection(profile.name, path=path, registry=registry)
        except Exception:
            logger.exception(
                "Failed to regenerate node types for mcp_server connection '%s' on startup", profile.name
            )

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

spec-021: `owner_user_id` -- the id of the user who owns the underlying
`mcp_server` connection (`None` for a global connection, unchanged
pre-spec-021 behavior). Threaded through so a private connection's generated
node type name can't collide with a different user's own same-named private
connection (the node type registry itself is process-global, shared by
every user), and so execution resolves the *exact* owner's stored
credentials -- never another user's same-named connection. This is the
private-graph half of spec-021's per-user resolution; a shared graph's
"the running user's own mapped connection, not the graph author's" behavior
is layered on top of this in a later phase, not built here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from backend.connections.mcp_server_connection import McpServerConnectionConfig, transport_config
from backend.connections.store import get_connection
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.mcp import oauth_flow
from backend.mcp.client import McpToolInfo, coerce_value, default_terminal_approval
from backend.mcp.transport import McpServerConfig, call_tool, list_tools
from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec, default_registry
from backend.schema.types import TEXT


def _server_config_for(
    config: McpServerConnectionConfig, connection_name: str, owner_user_id: str | None
) -> McpServerConfig:
    """spec-021: attaches a real, currently-valid per-user OAuth access
    token as a Bearer header when this connection requires OAuth --
    everywhere `transport_config(config)` used to be called directly for
    an actual discovery/tool-call (not for schema/config purposes alone).
    Raises oauth_flow.McpOAuthError (unwrapped) if there's no owner or no
    stored token yet -- callers during discovery/generation let this
    propagate as-is; `_make_execute`'s execute() wraps it as a
    NodeExecutionError, the one context where that's the right shape."""
    base = transport_config(config)
    if not config.requires_oauth:
        return base
    if owner_user_id is None:
        raise oauth_flow.McpOAuthError(
            f"mcp_server connection '{connection_name}' requires OAuth but has no owning user"
        )
    token = oauth_flow.get_valid_access_token(owner_user_id, connection_name)
    return McpServerConfig(
        transport=base.transport,
        command=base.command,
        args=base.args,
        url=base.url,
        headers={**base.headers, "Authorization": f"Bearer {token}"},
    )


class _EmptyConfig(BaseModel):
    """Generated nodes take no per-instance config -- the tool and
    connection they're bound to are baked into the registration itself, not
    user-editable per node."""


# Tracks what was generated per (owner_user_id, connection_name), so a
# refresh/delete can remove exactly those types without prefix-scanning the
# whole registry. Rebuilt fresh on process start (see
# regenerate_all_on_startup) -- an in-memory tracking dict, same
# "rebuilt on startup, not itself persisted" shape as
# backend/triggers/registry.py's active-trigger cache.
_generated_types_by_connection: dict[tuple[str | None, str], list[str]] = {}


def type_name_for(connection_name: str, tool_name: str, owner_user_id: str | None = None) -> str:
    """Namespaced by connection name so two different mcp_server
    connections can each have a same-named tool with no collision --
    unchanged shape for a global connection (`owner_user_id=None`), so
    every pre-spec-021 graph's node-type references keep resolving exactly
    as before. A private connection's type name also carries its owner, so
    two different users' own same-named connections (e.g. both "my-gmail")
    can never collide in this process-global registry."""
    if owner_user_id is None:
        return f"mcp__{connection_name}__{tool_name}"
    return f"mcp__u_{owner_user_id}__{connection_name}__{tool_name}"


def _make_execute(
    connection_name: str, tool: McpToolInfo, path: Path | None, owner_user_id: str | None
) -> Callable[[ExecutionContext], NodeResult]:
    def execute(ctx: ExecutionContext) -> NodeResult:
        # spec-021: a private graph (or the author running their own
        # shared graph) resolves the connection's exact owner, unchanged.
        # A *different* user running a shared graph resolves their own
        # mapped connection instead -- running_user_id/slot_mappings come
        # from POST /runs via the same opaque `resources` bag
        # created_by/run_by/approval_prompt already use (SPEC-006 §4's
        # "engine and node bodies stay unaware of connection mechanics",
        # applied one level deeper).
        running_user_id = ctx.resources.get("running_user_id")
        resolve_name, resolve_owner = connection_name, owner_user_id
        if running_user_id is not None and running_user_id != owner_user_id:
            slot_mappings: dict[str, str] = ctx.resources.get("slot_mappings") or {}
            mapped_name = slot_mappings.get(connection_name)
            if mapped_name is not None:
                resolve_name, resolve_owner = mapped_name, running_user_id

        profile = get_connection(resolve_name, user_id=resolve_owner, path=path)
        if profile is None:
            raise NodeExecutionError(f"mcp_server connection '{resolve_name}' no longer exists")
        config = McpServerConnectionConfig.model_validate(profile.config)
        # spec-023: for a *global* connection (profile.user_id is None),
        # the OAuth token to attach is always the actual running user's own
        # -- any signed-in user independently connects their own account to
        # the same global profile (SPEC-021's per-caller token storage
        # already supports this; slot_mappings above is a different concern
        # -- substituting to a *different* connection entirely for a shared
        # graph -- and doesn't apply here since there's only one profile).
        # A private connection's resolve_owner is untouched (still the
        # exact owner resolved above).
        token_user_id = running_user_id if profile.user_id is None else resolve_owner
        try:
            server_config = _server_config_for(config, resolve_name, token_user_id)
        except oauth_flow.McpOAuthError as e:
            raise NodeExecutionError(str(e)) from e

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
            raise NodeExecutionError(f"{resolve_name}.{tool.name} failed: {e}") from e

        return NodeResult(outputs={"result": result_text}, side_effect=True)

    return execute


def unregister_for_connection(
    connection_name: str, owner_user_id: str | None = None, registry: NodeRegistry = default_registry
) -> None:
    for type_name in _generated_types_by_connection.pop((owner_user_id, connection_name), []):
        registry.unregister(type_name)


def generate_node_types_for_connection(
    connection_name: str,
    owner_user_id: str | None = None,
    discovery_user_id: str | None = None,
    path: Path | None = None,
    registry: NodeRegistry = default_registry,
) -> list[str]:
    """Discovers `connection_name`'s tools (live) and registers one node
    type per tool. Idempotent and safe to call again as a refresh --
    discovery runs *before* the connection's prior generated set is torn
    down, so a failed refresh (server unreachable) leaves the previously-
    working node set intact rather than wiping it out.

    spec-023: `owner_user_id` controls type *naming* (unchanged -- None for
    a global connection, the real owner for a private one) and is entirely
    separate from `discovery_user_id`, whose *token* (if the connection
    requires OAuth) is used to actually call tools/list. These are the same
    value for a private connection (its owner is the only one who'd ever
    discover it) but must differ for a global one -- there's no single
    "owner" token to use, only whichever real user is triggering discovery
    right now (the OAuth callback's own connecting user, or whoever hit
    refresh-capabilities/promote-to-global) has one. Defaults to
    owner_user_id when not given, so every pre-spec-023 caller (private
    connections only) is unaffected."""
    profile = get_connection(connection_name, user_id=owner_user_id, path=path)
    if profile is None:
        raise ValueError(f"Connection '{connection_name}' does not exist")
    if profile.type != "mcp_server":
        raise ValueError(f"Connection '{connection_name}' is not an mcp_server connection")

    if discovery_user_id is None:
        discovery_user_id = owner_user_id
    config = McpServerConnectionConfig.model_validate(profile.config)
    server_config = _server_config_for(config, connection_name, discovery_user_id)
    tools = list_tools(server_config)  # raises McpConnectionError before anything is torn down

    unregister_for_connection(connection_name, owner_user_id=owner_user_id, registry=registry)

    created: list[str] = []
    for tool in tools:
        type_name = type_name_for(connection_name, tool.name, owner_user_id)
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
                execute=_make_execute(connection_name, tool, path, owner_user_id),
                category="apps",
                integration=connection_name,
                capability_group=None,
            )
        )
        created.append(type_name)

    _generated_types_by_connection[(owner_user_id, connection_name)] = created
    return created


def regenerate_all_on_startup(path: Path | None = None, registry: NodeRegistry = default_registry) -> None:
    """spec-019: every saved `mcp_server` connection gets its generated node
    set rebuilt once at backend startup, mirroring SPEC-015's
    `_reactivate_persisted_graphs` -- so the palette is correct immediately
    after a restart, not only after each connection happens to be manually
    refreshed. A single connection's discovery failure (server unreachable
    at boot) is logged and skipped, not fatal to startup or to any other
    connection's regeneration -- same "one broken thing can't block
    everything else" precedent as trigger reactivation.

    spec-021: iterates `list_connections_unscoped`, not the caller-scoped
    `list_connections` -- startup has no caller, and every user's own
    mcp_server connections need their node types rebuilt, not just global
    ones."""
    import logging

    from backend.connections.store import list_connections_unscoped

    logger = logging.getLogger(__name__)
    for profile in list_connections_unscoped(path=path):
        if profile.type != "mcp_server":
            continue
        try:
            generate_node_types_for_connection(
                profile.name, owner_user_id=profile.user_id, path=path, registry=registry
            )
        except Exception:
            logger.exception(
                "Failed to regenerate node types for mcp_server connection '%s' (owner=%s) on startup",
                profile.name,
                profile.user_id,
            )

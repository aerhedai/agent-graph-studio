"""The `mcp_server` connection type (SPEC-019) -- a named, saved, testable
profile for an MCP server, stdio or remote (ADR-009). This is what makes an
MCP server a first-class, reusable configuration for the first time: before
this, `mcp_call` required retyping `command`/`args` on every single node
instance, with no shared, credential-store-integrated configuration.

`trusted` governs whether nodes *dynamically generated* from this connection
(backend/mcp/generated_nodes.py) skip the interactive approval gate --
that's a property of the connection, not of `mcp_call` itself, which is
untouched and always approval-gated by default (ADR-004).

spec-021: `requires_oauth`/`oauth_client_id`/`oauth_client_secret` support a
remote server that implements MCP's own standardized OAuth 2.1
authorization spec (Gmail's official MCP server, or any other spec-
conformant server) -- see backend/mcp/oauth_flow.py. `requires_oauth` is
discovered, not user-set, the first time this connection is tested/created.
`oauth_client_id`/`_secret` are populated either by dynamic client
registration (if the server's discovery advertises support for it) or
supplied up front for servers that require a pre-registered client (Gmail's
official server does not support dynamic registration, confirmed during
this spec's Phase 0)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.connections.base import ConnectionTestResult, register_connection_type
from backend.mcp import oauth_flow
from backend.mcp.client import McpConnectionError
from backend.mcp.transport import McpServerConfig, list_tools


class McpServerConnectionConfig(BaseModel):
    transport: Literal["stdio", "remote"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    trusted: bool = False
    requires_oauth: bool = False
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scope: str | None = None
    """spec-021: space-separated OAuth scopes to request -- explicit and
    user-set, since a provider's own discovery may advertise broader scopes
    (Protected Resource Metadata's `scopes_supported`) than what's actually
    configured on that operator's OAuth consent screen (e.g. Gmail's PRM
    advertises `mail.google.com`/`gmail.modify`, broader than the
    `gmail.readonly`+`gmail.compose` this project's own setup docs tell an
    operator to add) -- requesting an unapproved scope would otherwise fail
    at Google's own consent screen. Falls back to the discovered
    scopes_supported only if left unset."""

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "McpServerConnectionConfig":
        if self.transport == "stdio" and not self.command:
            raise ValueError("transport='stdio' requires 'command'")
        if self.transport == "remote" and not self.url:
            raise ValueError("transport='remote' requires 'url'")
        return self


def transport_config(config: McpServerConnectionConfig) -> McpServerConfig:
    """Shared with backend/mcp/generated_nodes.py -- both need the same
    validated-config-to-transport-config mapping, not two copies of it."""
    return McpServerConfig(
        transport=config.transport,
        command=config.command,
        args=config.args,
        url=config.url,
        headers=config.headers,
    )


def build_client(config: McpServerConnectionConfig) -> McpServerConnectionConfig:
    # There's no single "client object" the way Ollama/Anthropic have --
    # generated nodes and test_connection both need this connection's raw,
    # validated config to build an McpServerConfig at call time.
    return config


def test_connection(config: McpServerConnectionConfig) -> ConnectionTestResult:
    # spec-021: a generic connection test has no user context (it's called
    # via the plain `Callable[[ConfigModel], ConnectionTestResult]`
    # registry signature, shared by every connection type) -- it can
    # confirm *whether* a remote server requires OAuth, but never lists a
    # specific user's real tools through it. That live check happens via
    # generate_node_types_for_connection (backend/mcp/generated_nodes.py),
    # called from app.py routes that do have a real user_id.
    if config.requires_oauth:
        return ConnectionTestResult(
            success=True,
            message="This server requires OAuth login, already configured -- connect it "
            "(Settings -> Connections) to see and use its real tools.",
        )
    if config.transport == "remote":
        try:
            oauth_flow.discover_oauth_server(config.url)
        except oauth_flow.McpOAuthError:
            pass
        else:
            return ConnectionTestResult(
                success=True,
                message="This server requires OAuth login -- save this connection, then connect it "
                "(Settings -> Connections) before its tools become available.",
            )

    try:
        tools = list_tools(transport_config(config))
    except McpConnectionError as e:
        return ConnectionTestResult(success=False, message=str(e))
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"MCP server connection test failed: {e}")

    names = ", ".join(t.name for t in tools) or "(none)"
    return ConnectionTestResult(
        success=True,
        message=f"Connected. {len(tools)} tool(s) available: {names}",
    )


register_connection_type(
    "mcp_server",
    category="local",
    config_model=McpServerConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
)

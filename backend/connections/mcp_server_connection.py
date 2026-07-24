"""The `mcp_server` connection type (SPEC-019) -- a named, saved, testable
profile for an MCP server, stdio or remote (ADR-009). This is what makes an
MCP server a first-class, reusable configuration for the first time: before
this, `mcp_call` required retyping `command`/`args` on every single node
instance, with no shared, credential-store-integrated configuration.

`trusted` governs whether nodes *dynamically generated* from this connection
(backend/mcp/generated_nodes.py) skip the interactive approval gate --
that's a property of the connection, not of `mcp_call` itself, which is
untouched and always approval-gated by default (ADR-004)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.connections.base import ConnectionTestResult, register_connection_type
from backend.mcp.client import McpConnectionError
from backend.mcp.transport import McpServerConfig, list_tools


class McpServerConnectionConfig(BaseModel):
    transport: Literal["stdio", "remote"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    trusted: bool = False

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

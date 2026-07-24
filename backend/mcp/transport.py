"""Shared, transport-agnostic entry points for discovering and calling tools
on an MCP server -- callers (dynamic node generation, `mcp_server`
connection's test_connection) go through `list_tools`/`call_tool` here
without knowing or caring whether the underlying server is reached over
stdio or the remote (Streamable HTTP) transport (ADR-009).

`backend/mcp/client.py` (stdio) and `backend/mcp/remote_client.py` (remote)
each implement the same shape independently -- this module is the one place
that picks between them, keyed by `McpServerConfig.transport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.mcp.client import McpToolInfo
from backend.mcp.client import call_tool as _stdio_call_tool
from backend.mcp.client import list_tools as _stdio_list_tools
from backend.mcp.remote_client import call_tool as _remote_call_tool
from backend.mcp.remote_client import list_tools as _remote_list_tools


@dataclass(frozen=True)
class McpServerConfig:
    """Unifies a stdio or remote server's connection details into one shape
    dynamic node generation and `mcp_server_connection.py` pass around,
    instead of branching on transport at every call site."""

    transport: Literal["stdio", "remote"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def list_tools(config: McpServerConfig) -> list[McpToolInfo]:
    if config.transport == "stdio":
        return _stdio_list_tools(config.command, config.args)
    return _remote_list_tools(config.url, config.headers or None)


def call_tool(config: McpServerConfig, tool_name: str, arguments: dict) -> str:
    if config.transport == "stdio":
        return _stdio_call_tool(config.command, config.args, tool_name, arguments)
    return _remote_call_tool(config.url, tool_name, arguments, config.headers or None)

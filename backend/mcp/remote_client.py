"""Synchronous wrapper around the async `mcp` SDK's Streamable HTTP (remote)
transport -- mirrors `backend/mcp/client.py`'s stdio implementation exactly
(same sync-over-async pattern, same discovery-failure caching, same
`McpToolInfo`/`McpConnectionError` types, reused rather than duplicated),
differing only in how the session is opened (ADR-009).
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.mcp.client import (
    DISCOVERY_TIMEOUT_SECONDS,
    McpConnectionError,
    McpToolInfo,
    content_to_text,
)

CALL_TIMEOUT_SECONDS = 60

# Cached at module (process) level, keyed by (url, sorted headers items),
# INCLUDING failures -- same rationale as client.py's _tool_list_cache: avoid
# re-hitting an unreachable remote endpoint on every independent
# check_required_inputs/check_type_mismatches call site within one
# validate_graph() pass. A fresh process starts with an empty cache.
_tool_list_cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[McpToolInfo] | Exception] = {}


def _cache_key(url: str, headers: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    return url, tuple(sorted((headers or {}).items()))


async def _list_tools_async(url: str, headers: dict[str, str] | None) -> list[McpToolInfo]:
    async with streamablehttp_client(url, headers=headers) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()

    infos = []
    for tool in response.tools:
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required = frozenset(schema.get("required", []))
        param_names = list(properties.keys())
        param_json_types = {name: properties[name].get("type", "string") for name in param_names}
        infos.append(
            McpToolInfo(
                name=tool.name,
                param_names=param_names,
                param_json_types=param_json_types,
                required_names=required,
            )
        )
    return infos


def list_tools(url: str, headers: dict[str, str] | None = None) -> list[McpToolInfo]:
    key = _cache_key(url, headers)
    if key in _tool_list_cache:
        cached = _tool_list_cache[key]
        if isinstance(cached, Exception):
            raise cached
        return cached

    try:
        result = asyncio.run(
            asyncio.wait_for(_list_tools_async(url, headers), timeout=DISCOVERY_TIMEOUT_SECONDS)
        )
    except Exception as e:
        wrapped = McpConnectionError(f"Failed to discover tools from remote MCP server ({url}): {e}")
        _tool_list_cache[key] = wrapped
        raise wrapped from e

    _tool_list_cache[key] = result
    return result


async def _call_tool_async(
    url: str, tool_name: str, arguments: dict[str, Any], headers: dict[str, str] | None
) -> str:
    async with streamablehttp_client(url, headers=headers) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    if result.isError:
        raise McpConnectionError(
            f"MCP tool '{tool_name}' returned an error: {content_to_text(result.content)}"
        )
    return content_to_text(result.content)


def call_tool(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> str:
    """Never cached -- a fresh call every time, same rationale as
    client.py's call_tool (memoizing a potentially side-effecting call would
    be wrong)."""
    try:
        return asyncio.run(
            asyncio.wait_for(
                _call_tool_async(url, tool_name, arguments, headers),
                timeout=CALL_TIMEOUT_SECONDS,
            )
        )
    except McpConnectionError:
        raise
    except Exception as e:
        raise McpConnectionError(f"Remote MCP tool call to '{tool_name}' failed: {e}") from e

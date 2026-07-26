"""Synchronous wrapper around the async `mcp` SDK's Streamable HTTP (remote)
transport -- mirrors `backend/mcp/client.py`'s stdio implementation exactly
(same sync-over-async pattern, same discovery-failure caching, same
`McpToolInfo`/`McpConnectionError` types, reused rather than duplicated),
differing only in how the session is opened (ADR-009).

spec-026: `ClientSession.initialize()` (the `mcp` SDK's own implementation)
automatically sends a `notifications/initialized` message right after the
initialize response, through the same internal path as every other message
-- but that specific message loses its `Mcp-Session-Id` header, a
documented bug reproduced across multiple independent MCP client
implementations, not unique to this project (the official MCP Inspector
has the identical gap -- github.com/modelcontextprotocol/inspector#905).
Most servers tolerate this; a subset that strictly enforce the session
header for every request (confirmed live against a Supergateway-fronted
container this spec added) reject it outright, which poisons the whole
connection before our own code ever gets to call `list_tools`/`call_tool`.
Verified deterministic (100% reproducible, not a timing race -- a delay
between initialize and the next call doesn't help) via direct raw-HTTP
protocol testing, which also confirmed the fix: manually resending that
notification WITH the session header attached works every time.

`_manual_streamable_http_request` below is that manual fallback --
sidesteps `ClientSession`/`streamablehttp_client` entirely for one narrow
case, going straight to httpx so the session header can be attached to
every message ourselves. It's only ever reached when the normal
`ClientSession`-based path (`_list_tools_async`/`_call_tool_async`,
unchanged, still primary) fails with exactly this signature -- every
already-proven server (Gmail, Context7, kpidepot.com, etc.) keeps using the
standard path untouched."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.mcp.client import (
    DISCOVERY_TIMEOUT_SECONDS,
    McpConnectionError,
    McpToolInfo,
    content_to_text,
)

CALL_TIMEOUT_SECONDS = 60


def _parse_streamable_http_body(text: str) -> dict[str, Any]:
    """The Streamable HTTP transport's response body is SSE-framed
    (`event: message\\ndata: {...}`), not plain JSON -- extracts the JSON
    payload from the `data:` line. Falls back to parsing the whole body as
    JSON for a server that responds with a bare JSON body instead."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    return json.loads(text)


async def _manual_streamable_http_request(
    url: str, headers: dict[str, str] | None, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """See module docstring -- a from-scratch initialize -> notifications/
    initialized -> `method` sequence with the session header attached to
    every request, used only as a fallback when the standard `ClientSession`
    path fails."""
    request_headers = {
        **(headers or {}),
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
        init_response = await client.post(
            url,
            headers=request_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-graph-studio", "version": "1.0"},
                },
            },
        )
        init_response.raise_for_status()
        session_headers = dict(request_headers)
        session_id = init_response.headers.get("mcp-session-id")
        if session_id:
            session_headers["Mcp-Session-Id"] = session_id

        notify_response = await client.post(
            url, headers=session_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        notify_response.raise_for_status()

        call_response = await client.post(
            url,
            headers=session_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
        )
        call_response.raise_for_status()

    result = _parse_streamable_http_body(call_response.text)
    if "error" in result:
        raise McpConnectionError(f"{method} failed: {result['error']}")
    return result["result"]

# Cached at module (process) level, keyed by (url, sorted headers items),
# INCLUDING failures -- same rationale as client.py's _tool_list_cache: avoid
# re-hitting an unreachable remote endpoint on every independent
# check_required_inputs/check_type_mismatches call site within one
# validate_graph() pass. A fresh process starts with an empty cache.
_tool_list_cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[McpToolInfo] | Exception] = {}


def _cache_key(url: str, headers: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    return url, tuple(sorted((headers or {}).items()))


def _tools_from_raw_list(raw_tools: list[dict[str, Any]]) -> list[McpToolInfo]:
    infos = []
    for tool in raw_tools:
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties", {})
        required = frozenset(schema.get("required", []))
        param_names = list(properties.keys())
        param_json_types = {name: properties[name].get("type", "string") for name in param_names}
        infos.append(
            McpToolInfo(
                name=tool["name"],
                param_names=param_names,
                param_json_types=param_json_types,
                required_names=required,
            )
        )
    return infos


async def _list_tools_async(url: str, headers: dict[str, str] | None) -> list[McpToolInfo]:
    try:
        async with streamablehttp_client(url, headers=headers) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
        raw_tools = [tool.model_dump(by_alias=True, exclude_none=True) for tool in response.tools]
    except Exception:
        # See module docstring -- some servers strictly reject the SDK's
        # own automatic post-initialize notification for lacking a session
        # header; fall back to a manual sequence that attaches it
        # ourselves. Every server that doesn't hit this never reaches here.
        result = await _manual_streamable_http_request(url, headers, "tools/list", {})
        raw_tools = result["tools"]
    return _tools_from_raw_list(raw_tools)


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


def _raw_content_to_text(content: list[dict[str, Any]]) -> str:
    """Dict-based counterpart of client.py's content_to_text -- the manual
    fallback's result comes straight from parsed JSON (plain dicts), not
    SDK content objects."""
    text_blocks = [block["text"] for block in content if block.get("type") == "text"]
    if not text_blocks:
        raise McpConnectionError(
            "MCP tool result had no text content (non-text content is not supported for MVP)"
        )
    return "\n".join(text_blocks)


async def _call_tool_async(
    url: str, tool_name: str, arguments: dict[str, Any], headers: dict[str, str] | None
) -> str:
    try:
        async with streamablehttp_client(url, headers=headers) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        if result.isError:
            raise McpConnectionError(
                f"MCP tool '{tool_name}' returned an error: {content_to_text(result.content)}"
            )
        return content_to_text(result.content)
    except McpConnectionError:
        raise
    except Exception:
        # See module docstring on _list_tools_async's identical fallback.
        result = await _manual_streamable_http_request(
            url, headers, "tools/call", {"name": tool_name, "arguments": arguments}
        )
        if result.get("isError"):
            raise McpConnectionError(
                f"MCP tool '{tool_name}' returned an error: {_raw_content_to_text(result['content'])}"
            )
        return _raw_content_to_text(result["content"])


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

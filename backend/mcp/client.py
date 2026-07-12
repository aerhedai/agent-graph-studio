"""Synchronous wrapper around the async `mcp` SDK's stdio transport.

The engine is entirely synchronous; every entry point here drives the async
SDK via `asyncio.run(...)` internally, spawning the configured server as a
subprocess for the duration of one discovery or one tool call.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DISCOVERY_TIMEOUT_SECONDS = 30
CALL_TIMEOUT_SECONDS = 60


class McpConnectionError(RuntimeError):
    """The server subprocess couldn't be launched, didn't complete the
    handshake/respond in time, the requested tool doesn't exist, or the
    tool call itself reported an error."""


@dataclass(frozen=True)
class McpToolInfo:
    name: str
    param_names: list[str]
    param_json_types: dict[str, str]


# Cached at module (process) level, keyed by (command, args), INCLUDING
# failures. check_required_inputs/check_type_mismatches in validation/rules.py
# each independently resolve a node's schema (once per node, again per
# incident edge) -- without caching failures too, an unreachable server would
# be re-spawned (and re-timeout) on every one of those call sites within a
# single validate_graph() call, and a flaky failure could even produce
# inconsistent results across them. A fresh process (e.g. a new CLI
# invocation) always starts with an empty cache, so this still satisfies
# spec-003 §7's "re-fetch every validation" -- it just avoids redundant
# spawns *within* one validation. Known limitation: a hypothetical long-lived
# server process would need explicit invalidation; not a concern for the
# one-shot CLI this targets.
_tool_list_cache: dict[tuple[str, tuple[str, ...]], list[McpToolInfo] | Exception] = {}


def _build_env(env: dict[str, str] | None) -> dict[str, str]:
    return {**os.environ, **(env or {})}


def find_tool(tools: list[McpToolInfo], tool_name: str) -> McpToolInfo | None:
    return next((t for t in tools if t.name == tool_name), None)


def coerce_value(value: str, json_type: str) -> Any:
    """Our graph values are always TEXT; coerce to the tool's declared JSON
    type before calling it."""
    if json_type == "integer":
        return int(value)
    if json_type == "number":
        return float(value)
    if json_type == "boolean":
        return value.strip().lower() in ("true", "1", "yes")
    if json_type in ("object", "array"):
        return json.loads(value)
    return value


def content_to_text(content: list[Any]) -> str:
    """Join a CallToolResult's text content blocks into one string. Raises
    if there's no text content -- images/binary aren't representable in our
    TEXT-only slot type system for MVP."""
    text_blocks = [block.text for block in content if getattr(block, "type", None) == "text"]
    if not text_blocks:
        raise McpConnectionError(
            "MCP tool result had no text content (non-text content is not supported for MVP)"
        )
    return "\n".join(text_blocks)


async def _list_tools_async(
    command: str, args: list[str], env: dict[str, str] | None
) -> list[McpToolInfo]:
    params = StdioServerParameters(command=command, args=args, env=_build_env(env))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()

    infos = []
    for tool in response.tools:
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        # Only the tool's own required properties become graph ports -- our
        # engine's input-gathering loop doesn't honor InputSlotSpec.required
        # =False (a node executes only once every declared slot is wired),
        # so exposing optional properties as slots would force graph authors
        # to wire them anyway. Optional properties use the server's own
        # defaults for MVP. Discovered via live testing against the real
        # filesystem server (read_text_file's optional tail/head params).
        param_names = [name for name in properties if name in required]
        param_json_types = {name: properties[name].get("type", "string") for name in param_names}
        infos.append(
            McpToolInfo(name=tool.name, param_names=param_names, param_json_types=param_json_types)
        )
    return infos


def list_tools(command: str, args: list[str], env: dict[str, str] | None = None) -> list[McpToolInfo]:
    key = (command, tuple(args))
    if key in _tool_list_cache:
        cached = _tool_list_cache[key]
        if isinstance(cached, Exception):
            raise cached
        return cached

    try:
        result = asyncio.run(
            asyncio.wait_for(_list_tools_async(command, args, env), timeout=DISCOVERY_TIMEOUT_SECONDS)
        )
    except Exception as e:
        wrapped = McpConnectionError(
            f"Failed to discover tools from MCP server ({command} {' '.join(args)}): {e}"
        )
        _tool_list_cache[key] = wrapped
        raise wrapped from e

    _tool_list_cache[key] = result
    return result


async def _call_tool_async(
    command: str,
    args: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    env: dict[str, str] | None,
) -> str:
    params = StdioServerParameters(command=command, args=args, env=_build_env(env))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    if result.isError:
        raise McpConnectionError(
            f"MCP tool '{tool_name}' returned an error: {content_to_text(result.content)}"
        )
    return content_to_text(result.content)


def call_tool(
    command: str,
    args: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    env: dict[str, str] | None = None,
) -> str:
    """Actually invokes the tool. Never cached -- a fresh subprocess spawn
    every call, since memoizing a potentially side-effecting call would be
    wrong."""
    try:
        return asyncio.run(
            asyncio.wait_for(
                _call_tool_async(command, args, tool_name, arguments, env),
                timeout=CALL_TIMEOUT_SECONDS,
            )
        )
    except McpConnectionError:
        raise
    except Exception as e:
        raise McpConnectionError(f"MCP tool call to '{tool_name}' failed: {e}") from e

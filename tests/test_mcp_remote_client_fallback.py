"""spec-026: the manual streamable-HTTP fallback in backend/mcp/remote_client.py
-- some real servers (confirmed live against a Supergateway-fronted stdio
bridge this spec added) strictly reject the `mcp` SDK's own automatic
post-initialize notification for lacking a session header, a documented
bug reproduced across multiple independent MCP client implementations (see
the module's own docstring). `_manual_streamable_http_request` sidesteps
that by managing the session header itself; these tests cover its pure
parsing/sequencing logic directly (mocking httpx, not the real SDK
machinery, since the primary `ClientSession` path is unchanged and already
exercised by every other real mcp_server test in this project)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.mcp.client import McpConnectionError
from backend.mcp.remote_client import (
    _manual_streamable_http_request,
    _parse_streamable_http_body,
    _raw_content_to_text,
    _tools_from_raw_list,
)


def test_parse_streamable_http_body_extracts_sse_data_line():
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n'
    assert _parse_streamable_http_body(body) == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}


def test_parse_streamable_http_body_falls_back_to_bare_json():
    body = '{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}'
    assert _parse_streamable_http_body(body) == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}


def test_raw_content_to_text_joins_text_blocks_only():
    content = [{"type": "text", "text": "hello"}, {"type": "image", "data": "..."}, {"type": "text", "text": "world"}]
    assert _raw_content_to_text(content) == "hello\nworld"


def test_raw_content_to_text_raises_when_no_text_content():
    with pytest.raises(McpConnectionError):
        _raw_content_to_text([{"type": "image", "data": "..."}])


def test_tools_from_raw_list_extracts_schema_shape():
    raw = [
        {
            "name": "fetch",
            "inputSchema": {
                "properties": {"url": {"type": "string"}, "max_length": {"type": "integer"}},
                "required": ["url"],
            },
        }
    ]
    infos = _tools_from_raw_list(raw)
    assert len(infos) == 1
    assert infos[0].name == "fetch"
    assert infos[0].param_names == ["url", "max_length"]
    assert infos[0].param_json_types == {"url": "string", "max_length": "integer"}
    assert infos[0].required_names == frozenset({"url"})


def test_manual_request_attaches_session_header_to_every_call_after_initialize():
    """The exact bug this fallback exists for: the session header must be
    attached to the notifications/initialized call and the real method
    call, not just held onto after initialize -- confirmed live that
    omitting it there is what a strict server rejects."""
    init_response = MagicMock(headers={"mcp-session-id": "sess-123"})
    init_response.raise_for_status = MagicMock()
    notify_response = MagicMock()
    notify_response.raise_for_status = MagicMock()
    call_response = MagicMock(text='data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"fetch"}]}}')
    call_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[init_response, notify_response, call_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.mcp.remote_client.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_manual_streamable_http_request("https://example.com/mcp", {}, "tools/list", {}))

    assert result == {"tools": [{"name": "fetch"}]}
    calls = mock_client.post.call_args_list
    assert len(calls) == 3
    # initialize: no session header yet (none exists before the first response).
    assert "Mcp-Session-Id" not in calls[0].kwargs["headers"]
    # notifications/initialized and the real call: session header attached --
    # this is the exact thing the buggy SDK path fails to do.
    assert calls[1].kwargs["headers"]["Mcp-Session-Id"] == "sess-123"
    assert calls[1].kwargs["json"]["method"] == "notifications/initialized"
    assert calls[2].kwargs["headers"]["Mcp-Session-Id"] == "sess-123"
    assert calls[2].kwargs["json"]["method"] == "tools/list"


def test_manual_request_raises_mcp_connection_error_on_jsonrpc_error():
    init_response = MagicMock(headers={"mcp-session-id": "sess-1"})
    init_response.raise_for_status = MagicMock()
    notify_response = MagicMock()
    notify_response.raise_for_status = MagicMock()
    call_response = MagicMock(text='data: {"jsonrpc":"2.0","id":2,"error":{"code":-32000,"message":"boom"}}')
    call_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[init_response, notify_response, call_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.mcp.remote_client.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(McpConnectionError, match="boom"):
            asyncio.run(_manual_streamable_http_request("https://example.com/mcp", {}, "tools/list", {}))

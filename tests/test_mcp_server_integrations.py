"""spec-019: the `mcp_server` connection type and dynamic node generation.

Mocked/monkeypatched for the automated suite (matching test_mcp_call_node.py's
existing convention -- no real subprocess spawned here); a real, live,
non-mocked run against the actual `@modelcontextprotocol/server-filesystem`
server and a real API round trip were both separately demonstrated during
implementation (see the spec's implementation notes), not just asserted."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.connections.mcp_server_connection import McpServerConnectionConfig
from backend.connections.store import add_connection
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.mcp.client import McpConnectionError, McpToolInfo
from backend.mcp.generated_nodes import (
    generate_node_types_for_connection,
    regenerate_all_on_startup,
    type_name_for,
    unregister_for_connection,
)
from backend.registry.base import NodeRegistry

SEND_TOOL = McpToolInfo(
    name="send_message",
    param_names=["channel", "text"],
    param_json_types={"channel": "string", "text": "string"},
    required_names=frozenset({"channel", "text"}),
)
GET_INFO_TOOL = McpToolInfo(
    name="get_info",
    param_names=[],
    param_json_types={},
    required_names=frozenset(),
)


def _add_mcp_server_connection(name: str = "test-mcp-server", trusted: bool = False) -> None:
    add_connection(
        name,
        "mcp_server",
        {"transport": "stdio", "command": "fake-server", "args": [], "trusted": trusted},
    )


# --- McpServerConnectionConfig ----------------------------------------------


def test_stdio_transport_requires_command():
    with pytest.raises(ValidationError):
        McpServerConnectionConfig(transport="stdio", command="")


def test_remote_transport_requires_url():
    with pytest.raises(ValidationError):
        McpServerConnectionConfig(transport="remote", url="")


def test_stdio_transport_with_command_is_valid():
    config = McpServerConnectionConfig(transport="stdio", command="npx", args=["-y", "some-server"])
    assert config.command == "npx"


def test_remote_transport_with_url_is_valid():
    config = McpServerConnectionConfig(transport="remote", url="https://example.com/mcp")
    assert config.url == "https://example.com/mcp"


def test_trusted_defaults_to_false():
    config = McpServerConnectionConfig(transport="stdio", command="npx")
    assert config.trusted is False


# --- generate_node_types_for_connection -------------------------------------


def test_generate_node_types_registers_one_type_per_tool(monkeypatch):
    _add_mcp_server_connection()
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL, GET_INFO_TOOL])

    registry = NodeRegistry()
    created = generate_node_types_for_connection("test-mcp-server", registry=registry)

    assert set(created) == {
        type_name_for("test-mcp-server", "send_message"),
        type_name_for("test-mcp-server", "get_info"),
    }
    send_def = registry.get(type_name_for("test-mcp-server", "send_message"))
    assert send_def.category == "apps"
    assert send_def.integration == "test-mcp-server"
    assert send_def.capability_group is None
    assert {i.name for i in send_def.inputs} == {"channel", "text"}
    assert all(i.required for i in send_def.inputs)
    assert [o.name for o in send_def.outputs] == ["result"]


def test_generate_node_types_optional_param_marked_not_required(monkeypatch):
    _add_mcp_server_connection()
    tool = McpToolInfo(
        name="search",
        param_names=["query", "limit"],
        param_json_types={"query": "string", "limit": "integer"},
        required_names=frozenset({"query"}),
    )
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [tool])

    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)

    definition = registry.get(type_name_for("test-mcp-server", "search"))
    required_by_name = {i.name: i.required for i in definition.inputs}
    assert required_by_name == {"query": True, "limit": False}


def test_generate_node_types_unknown_connection_raises():
    registry = NodeRegistry()
    with pytest.raises(ValueError, match="does not exist"):
        generate_node_types_for_connection("nonexistent", registry=registry)


def test_generate_node_types_wrong_connection_type_raises():
    add_connection("not-mcp", "telegram", {"bot_token": "x"})
    registry = NodeRegistry()
    with pytest.raises(ValueError, match="not an mcp_server connection"):
        generate_node_types_for_connection("not-mcp", registry=registry)


def test_refresh_is_idempotent_and_replaces_prior_set(monkeypatch):
    _add_mcp_server_connection()
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)

    # Server's tool set changed -- send_message is gone, a new tool appears.
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [GET_INFO_TOOL])
    created = generate_node_types_for_connection("test-mcp-server", registry=registry)

    assert created == [type_name_for("test-mcp-server", "get_info")]
    assert registry.get(type_name_for("test-mcp-server", "send_message")) is None
    assert registry.get(type_name_for("test-mcp-server", "get_info")) is not None


def test_failed_refresh_preserves_previously_generated_set(monkeypatch):
    _add_mcp_server_connection()
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)

    def _fail(config):
        raise McpConnectionError("server unreachable")

    monkeypatch.setattr(generated_nodes_module, "list_tools", _fail)

    with pytest.raises(McpConnectionError):
        generate_node_types_for_connection("test-mcp-server", registry=registry)

    # The old, working set must still be intact -- a failed refresh must not
    # wipe out a previously-generated, still-valid node set.
    assert registry.get(type_name_for("test-mcp-server", "send_message")) is not None


def test_unregister_for_connection_removes_generated_types(monkeypatch):
    _add_mcp_server_connection()
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)

    unregister_for_connection("test-mcp-server", registry=registry)

    assert registry.get(type_name_for("test-mcp-server", "send_message")) is None


def test_unregister_for_connection_is_a_noop_when_nothing_generated():
    registry = NodeRegistry()
    unregister_for_connection("never-generated", registry=registry)  # must not raise


def test_regenerate_all_on_startup_rebuilds_every_saved_mcp_server_connection(monkeypatch):
    _add_mcp_server_connection("server-a")
    _add_mcp_server_connection("server-b")
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])

    registry = NodeRegistry()
    regenerate_all_on_startup(registry=registry)

    assert registry.get(type_name_for("server-a", "send_message")) is not None
    assert registry.get(type_name_for("server-b", "send_message")) is not None


def test_regenerate_all_on_startup_skips_non_mcp_server_connections(monkeypatch):
    add_connection("some-telegram", "telegram", {"bot_token": "x"})
    called = []
    monkeypatch.setattr(
        generated_nodes_module,
        "list_tools",
        lambda config: called.append(1) or [SEND_TOOL],
    )

    registry = NodeRegistry()
    regenerate_all_on_startup(registry=registry)

    assert called == []


def test_regenerate_all_on_startup_one_failure_does_not_block_others(monkeypatch):
    _add_mcp_server_connection("broken-server")
    _add_mcp_server_connection("healthy-server")

    # Both connections are indistinguishable to a lambda keyed only on
    # config, so patch per-call via a stateful counter instead.
    calls = {"n": 0}

    def _list_tools(config):
        calls["n"] += 1
        if calls["n"] == 1:
            raise McpConnectionError("unreachable")
        return [SEND_TOOL]

    monkeypatch.setattr(generated_nodes_module, "list_tools", _list_tools)

    registry = NodeRegistry()
    regenerate_all_on_startup(registry=registry)  # must not raise

    # At least one of the two connections' generation succeeded.
    generated_any = any(
        registry.get(type_name_for(name, "send_message")) is not None
        for name in ("broken-server", "healthy-server")
    )
    assert generated_any


# --- generated node execute(): trust-gated approval -------------------------


def test_generated_node_execute_requires_approval_when_untrusted(monkeypatch):
    _add_mcp_server_connection(trusted=False)
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(generated_nodes_module, "call_tool", lambda config, tool_name, args: "sent")

    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)
    definition = registry.get(type_name_for("test-mcp-server", "send_message"))

    declined_ctx = ExecutionContext(
        node=None,
        inputs={"channel": "general", "text": "hi"},
        resources={"approval_prompt": lambda tool_name, arguments: False},
    )
    with pytest.raises(NodeExecutionError):
        definition.execute(declined_ctx)

    approved_ctx = ExecutionContext(
        node=None,
        inputs={"channel": "general", "text": "hi"},
        resources={"approval_prompt": lambda tool_name, arguments: True},
    )
    result = definition.execute(approved_ctx)
    assert result.outputs == {"result": "sent"}
    assert result.side_effect is True


def test_generated_node_execute_skips_approval_when_trusted(monkeypatch):
    _add_mcp_server_connection(trusted=True)
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(generated_nodes_module, "call_tool", lambda config, tool_name, args: "sent")

    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)
    definition = registry.get(type_name_for("test-mcp-server", "send_message"))

    # No approval_prompt injected -- if this blocked on real input(), the
    # test would hang. Its return proves the trusted connection bypassed
    # the gate entirely, same convention as
    # test_execute_mcp_call_skips_approval_when_require_approval_false.
    ctx = ExecutionContext(node=None, inputs={"channel": "general", "text": "hi"}, resources={})
    result = definition.execute(ctx)
    assert result.outputs == {"result": "sent"}


def test_generated_node_execute_raises_if_connection_deleted(monkeypatch):
    _add_mcp_server_connection()
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    registry = NodeRegistry()
    generate_node_types_for_connection("test-mcp-server", registry=registry)
    definition = registry.get(type_name_for("test-mcp-server", "send_message"))

    from backend.connections.store import delete_connection

    delete_connection("test-mcp-server")

    ctx = ExecutionContext(node=None, inputs={"channel": "general", "text": "hi"}, resources={})
    with pytest.raises(NodeExecutionError, match="no longer exists"):
        definition.execute(ctx)


# --- API layer: POST /connections, refresh-capabilities, DELETE ------------

# spec-017: must match tests/conftest.py's TEST_API_KEY.
api_client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def test_create_mcp_server_connection_generates_node_types_visible_via_node_types(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL, GET_INFO_TOOL])

    response = api_client.post(
        "/connections",
        json={
            "name": "api-test-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake-server", "args": []},
        },
    )
    assert response.status_code == 201

    node_types = api_client.get("/node-types").json()
    generated = [t for t in node_types if t["integration"] == "api-test-server"]
    assert {t["type"] for t in generated} == {
        type_name_for("api-test-server", "send_message"),
        type_name_for("api-test-server", "get_info"),
    }
    assert all(t["category"] == "apps" for t in generated)
    assert all(t["capability_group"] is None for t in generated)


def test_create_mcp_server_connection_rolls_back_on_discovery_failure(monkeypatch):
    def _fail(config):
        raise McpConnectionError("server unreachable")

    monkeypatch.setattr(generated_nodes_module, "list_tools", _fail)

    response = api_client.post(
        "/connections",
        json={
            "name": "will-fail",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake-server", "args": []},
        },
    )
    assert response.status_code == 502

    # Rolled back -- not left behind as a half-configured saved connection.
    connections = api_client.get("/connections").json()
    assert not any(c["name"] == "will-fail" for c in connections)


def test_refresh_capabilities_endpoint_updates_generated_set(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    create = api_client.post(
        "/connections",
        json={
            "name": "refreshable-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake-server", "args": []},
        },
    )
    assert create.status_code == 201

    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [GET_INFO_TOOL])
    refresh = api_client.post("/connections/refreshable-server/refresh-capabilities")
    assert refresh.status_code == 200
    assert refresh.json()["generated_types"] == [type_name_for("refreshable-server", "get_info")]

    node_types = api_client.get("/node-types").json()
    generated = {t["type"] for t in node_types if t["integration"] == "refreshable-server"}
    assert generated == {type_name_for("refreshable-server", "get_info")}


def test_refresh_capabilities_endpoint_404_for_unknown_connection():
    response = api_client.post("/connections/does-not-exist/refresh-capabilities")
    assert response.status_code == 404


def test_refresh_capabilities_endpoint_422_for_non_mcp_server_connection():
    add_connection("a-telegram-conn", "telegram", {"bot_token": "x"})
    response = api_client.post("/connections/a-telegram-conn/refresh-capabilities")
    assert response.status_code == 422


def test_delete_mcp_server_connection_removes_generated_node_types(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    api_client.post(
        "/connections",
        json={
            "name": "deletable-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake-server", "args": []},
        },
    )

    response = api_client.delete("/connections/deletable-server")
    assert response.status_code == 204

    node_types = api_client.get("/node-types").json()
    assert not any(t["integration"] == "deletable-server" for t in node_types)


# --- real ASGI lifespan startup (regression: asyncio.run() from a running
# event loop) -----------------------------------------------------------

# Discovered live: entering `with TestClient(app)` actually runs
# `_lifespan`'s startup code on uvicorn's own event loop -- unlike calling
# `regenerate_all_on_startup()` directly (every test above), which runs on
# whatever thread pytest itself is on and never hits this. A saved
# mcp_server connection's discovery call internally does its own
# asyncio.run() (backend/mcp/client.py), which raises outright when called
# from code already running on an event loop -- caught and logged by
# regenerate_all_on_startup's own try/except, so this bug didn't crash
# startup, it just silently skipped generating that connection's nodes.
# Fixed by dispatching through asyncio.to_thread in app.py's _lifespan.


def test_real_asgi_lifespan_startup_regenerates_saved_mcp_server_connections(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    add_connection(
        "lifespan-regen-server",
        "mcp_server",
        {"transport": "stdio", "command": "fake-server", "args": []},
    )
    try:
        with TestClient(app, headers={"Authorization": "Bearer test-api-key"}) as lifespan_client:
            node_types = lifespan_client.get("/node-types").json()
            generated = [t for t in node_types if t["integration"] == "lifespan-regen-server"]
            assert len(generated) == 1
            assert generated[0]["type"] == "mcp__lifespan-regen-server__send_message"
    finally:
        generated_nodes_module.unregister_for_connection("lifespan-regen-server")

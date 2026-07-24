"""spec-019: telegram_messaging / telegram_chat_management -- the manifest
fallback's proving case. Mocked at backend.integrations.telegram.node_support's
call_telegram_api boundary (matches test_mcp_call_node.py's monkeypatch
convention); a real, live send_message to a real Telegram chat was
separately demonstrated during implementation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.integrations.telegram.node_support as node_support_module
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.telegram_chat_management import execute_telegram_chat_management
from backend.nodes.telegram_messaging import execute_telegram_messaging
from backend.registry.base import default_registry, effective_inputs, effective_outputs
from backend.schema.loader import parse_graph_json
from backend.schema.models import NodeSpec
from backend.validation.validator import validate_graph


def _node(type_name: str, config: dict) -> NodeSpec:
    return NodeSpec(id="n1", type=type_name, config=config)


# --- dynamic schema resolution -----------------------------------------


def test_send_message_resolves_required_and_optional_inputs():
    node = _node("telegram_messaging", {"bot_token_connection": "x", "action": "send_message"})
    definition = default_registry.get("telegram_messaging")
    inputs = effective_inputs(definition, node)
    required = {i.name for i in inputs if i.required}
    optional = {i.name for i in inputs if not i.required}
    assert required == {"chat_id", "text"}
    assert optional == {"parse_mode"}
    assert [o.name for o in effective_outputs(definition, node)] == ["result"]


def test_delete_message_has_no_optional_params():
    node = _node("telegram_messaging", {"bot_token_connection": "x", "action": "delete_message"})
    definition = default_registry.get("telegram_messaging")
    inputs = effective_inputs(definition, node)
    assert {i.name for i in inputs} == {"chat_id", "message_id"}
    assert all(i.required for i in inputs)


def test_get_chat_member_resolves_two_required_inputs():
    node = _node("telegram_chat_management", {"bot_token_connection": "x", "action": "get_chat_member"})
    definition = default_registry.get("telegram_chat_management")
    inputs = effective_inputs(definition, node)
    assert {i.name for i in inputs} == {"chat_id", "user_id"}
    assert all(i.required for i in inputs)


def test_resolve_returns_none_for_malformed_config():
    node = _node("telegram_messaging", {"bot_token_connection": "x", "action": "not_a_real_action"})
    definition = default_registry.get("telegram_messaging")
    assert effective_inputs(definition, node) is None
    assert effective_outputs(definition, node) is None


# --- execute_telegram_messaging / execute_telegram_chat_management -----


def test_execute_send_message_success(monkeypatch):
    monkeypatch.setattr(
        node_support_module,
        "call_telegram_api",
        lambda token, method, params: {"ok": True, "result": {"message_id": 42}},
    )
    ctx = ExecutionContext(
        node=_node("telegram_messaging", {"bot_token_connection": "my-bot", "action": "send_message"}),
        inputs={"chat_id": "123", "text": "hello"},
        resources={"connections": {"my-bot": "fake-token"}},
    )
    result = execute_telegram_messaging(ctx)
    assert json.loads(result.outputs["result"]) == {"message_id": 42}
    assert result.side_effect is True


def test_execute_send_message_passes_correct_method_and_params(monkeypatch):
    captured = {}

    def fake_call(token, method, params):
        captured["token"] = token
        captured["method"] = method
        captured["params"] = params
        return {"ok": True, "result": {}}

    monkeypatch.setattr(node_support_module, "call_telegram_api", fake_call)
    ctx = ExecutionContext(
        node=_node("telegram_messaging", {"bot_token_connection": "my-bot", "action": "send_message"}),
        inputs={"chat_id": "123", "text": "hello"},
        resources={"connections": {"my-bot": "the-real-token"}},
    )
    execute_telegram_messaging(ctx)
    assert captured["token"] == "the-real-token"
    assert captured["method"] == "sendMessage"
    assert captured["params"] == {"chat_id": "123", "text": "hello"}


def test_execute_omits_unwired_optional_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        node_support_module,
        "call_telegram_api",
        lambda token, method, params: captured.update(params) or {"ok": True, "result": {}},
    )
    ctx = ExecutionContext(
        node=_node("telegram_messaging", {"bot_token_connection": "my-bot", "action": "send_message"}),
        inputs={"chat_id": "123", "text": "hello"},  # parse_mode left unwired
        resources={"connections": {"my-bot": "tok"}},
    )
    execute_telegram_messaging(ctx)
    assert "parse_mode" not in captured


def test_execute_raises_when_connection_unresolved():
    ctx = ExecutionContext(
        node=_node("telegram_messaging", {"bot_token_connection": "missing", "action": "send_message"}),
        inputs={"chat_id": "123", "text": "hi"},
        resources={"connections": {}},
    )
    with pytest.raises(NodeExecutionError, match="unresolved"):
        execute_telegram_messaging(ctx)


def test_execute_wraps_telegram_api_failure(monkeypatch):
    def fail(token, method, params):
        raise RuntimeError("Telegram API 'sendMessage' rejected the request: chat not found")

    monkeypatch.setattr(node_support_module, "call_telegram_api", fail)
    ctx = ExecutionContext(
        node=_node("telegram_messaging", {"bot_token_connection": "my-bot", "action": "send_message"}),
        inputs={"chat_id": "123", "text": "hi"},
        resources={"connections": {"my-bot": "tok"}},
    )
    with pytest.raises(NodeExecutionError, match="chat not found"):
        execute_telegram_messaging(ctx)


def test_execute_get_chat_success(monkeypatch):
    monkeypatch.setattr(
        node_support_module,
        "call_telegram_api",
        lambda token, method, params: {"ok": True, "result": {"id": 123, "type": "private"}},
    )
    ctx = ExecutionContext(
        node=_node("telegram_chat_management", {"bot_token_connection": "my-bot", "action": "get_chat"}),
        inputs={"chat_id": "123"},
        resources={"connections": {"my-bot": "tok"}},
    )
    result = execute_telegram_chat_management(ctx)
    assert json.loads(result.outputs["result"]) == {"id": 123, "type": "private"}


# --- registration metadata ----------------------------------------------


def test_telegram_messaging_registered_under_apps_category_with_integration_metadata():
    definition = default_registry.get("telegram_messaging")
    assert definition.category == "apps"
    assert definition.integration == "telegram"
    assert definition.capability_group == "Messaging"


def test_telegram_chat_management_registered_under_apps_category_with_integration_metadata():
    definition = default_registry.get("telegram_chat_management")
    assert definition.category == "apps"
    assert definition.integration == "telegram"
    assert definition.capability_group == "Chat management"


def test_telegram_adapter_gains_integration_metadata_but_stays_a_trigger():
    definition = default_registry.get("telegram_adapter")
    assert definition.category == "triggers"  # unchanged, per spec-019 design decisions
    assert definition.integration == "telegram"
    assert definition.capability_group == "Listening"


# --- regression: existing telegram_adapter graphs unaffected ------------


def test_delivery_support_agent_example_graph_still_validates():
    from backend.connections.store import add_connection

    # The graph's `model` node references this connection by name --
    # check_missing_connections needs it to actually exist in the
    # (isolated, per-test) store, same as any other graph-validation test.
    add_connection("my-pc-ollama-canvas", "ollama", {"host": "localhost", "port": 11434})

    graph_path = Path(__file__).parent.parent / "examples" / "delivery_support_agent.json"
    graph = parse_graph_json(graph_path.read_text())
    # Must validate cleanly with zero changes -- telegram_messaging/
    # telegram_chat_management are additive, new node types don't affect an
    # existing graph that never references them.
    validate_graph(graph)

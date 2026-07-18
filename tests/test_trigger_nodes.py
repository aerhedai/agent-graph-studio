from __future__ import annotations

import json

from backend.connections.store import add_connection
from backend.execution.engine import run_graph
from backend.execution.types import ExecutionContext
from backend.nodes.schedule_trigger import execute_schedule_trigger
from backend.nodes.webhook_trigger import execute_webhook_trigger
from backend.registry.base import default_registry
from backend.schema.models import EdgeEndpoint, EdgeSpec, GraphSpec, NodeSpec


def test_schedule_trigger_registered_zero_input_one_text_output():
    definition = default_registry.get("schedule_trigger")
    assert definition is not None
    assert definition.inputs == []
    assert [s.name for s in definition.outputs] == ["fired_at"]
    assert definition.result_slot is None


def test_webhook_trigger_registered_as_cluster_root_with_trigger_adapter_slot():
    """spec-012: webhook_trigger's own static outputs are empty -- its real
    ports mirror whichever trigger_adapter sub-node is connected
    (resolve_slots_from_sub_node), not a fixed list."""
    definition = default_registry.get("webhook_trigger")
    assert definition is not None
    assert definition.inputs == []
    assert definition.outputs == []
    assert definition.result_slot is None
    assert definition.sub_node_slots is not None
    assert definition.sub_node_slots["trigger_adapter"].cardinality == "one"
    assert definition.sub_node_slots["trigger_adapter"].accepts_role == "trigger_adapter"
    assert definition.resolve_slots_from_sub_node == "trigger_adapter"


def test_generic_adapter_registered_with_trigger_adapter_role():
    definition = default_registry.get("generic_adapter")
    assert definition is not None
    assert definition.sub_node_role == "trigger_adapter"
    assert [s.name for s in definition.outputs] == ["payload"]


def test_telegram_adapter_registered_with_trigger_adapter_role():
    definition = default_registry.get("telegram_adapter")
    assert definition is not None
    assert definition.sub_node_role == "trigger_adapter"
    assert [s.name for s in definition.outputs] == ["message_text", "sender_id", "chat_id"]


def test_schedule_trigger_execute_produces_iso_timestamp():
    node = NodeSpec(id="n1", type="schedule_trigger", config={"cron": "*/5 * * * *"})
    ctx = ExecutionContext(node=node, inputs={})

    result = execute_schedule_trigger(ctx)

    # Just needs to parse as a real ISO 8601 UTC timestamp.
    from datetime import datetime

    datetime.fromisoformat(result.outputs["fired_at"])


def _webhook_ctx(node: NodeSpec, adapter_node: NodeSpec, trigger_payloads: dict | None = None) -> ExecutionContext:
    """spec-012: webhook_trigger delegates to its connected trigger_adapter
    sub-node via ctx.resources["sub_nodes"] + nodes_by_id -- the same
    generic mechanism agent's model/memory/tools slots use, mirroring
    exactly what engine.py's run_graph() itself populates from real
    sub_node edges."""
    resources: dict = {
        "nodes_by_id": {adapter_node.id: adapter_node},
        "sub_nodes": {(node.id, "trigger_adapter"): [adapter_node.id]},
    }
    if trigger_payloads is not None:
        resources["trigger_payloads"] = trigger_payloads
    return ExecutionContext(node=node, inputs={}, resources=resources)


def test_webhook_trigger_execute_returns_injected_payload_as_json_string():
    node = NodeSpec(id="n1", type="webhook_trigger", config={})
    adapter_node = NodeSpec(id="adapter_1", type="generic_adapter", config={})
    ctx = _webhook_ctx(node, adapter_node, trigger_payloads={"n1": {"hello": "world"}})

    result = execute_webhook_trigger(ctx)

    assert json.loads(result.outputs["payload"]) == {"hello": "world"}


def test_webhook_trigger_execute_defaults_to_empty_object_when_no_payload_injected():
    """Manual (non-webhook) invocation must not error just because there's no
    real request body -- this is what lets manual POST /runs and trigger-fired
    runs coexist cleanly (spec-009 §6)."""
    node = NodeSpec(id="n1", type="webhook_trigger", config={})
    adapter_node = NodeSpec(id="adapter_1", type="generic_adapter", config={})
    ctx = _webhook_ctx(node, adapter_node)

    result = execute_webhook_trigger(ctx)

    assert json.loads(result.outputs["payload"]) == {}


def test_webhook_trigger_with_telegram_adapter_parses_real_shaped_payload():
    """spec-012 §6: a webhook_trigger with a telegram_adapter correctly
    parses a realistically-shaped Telegram webhook payload."""
    node = NodeSpec(id="n1", type="webhook_trigger", config={})
    adapter_node = NodeSpec(
        id="adapter_1", type="telegram_adapter", config={"bot_token_connection": "my-telegram-bot"}
    )
    telegram_payload = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 987654321, "is_bot": False, "first_name": "Ada"},
            "chat": {"id": 555111222, "type": "private"},
            "date": 1710000000,
            "text": "Hello bot!",
        },
    }
    ctx = _webhook_ctx(node, adapter_node, trigger_payloads={"n1": telegram_payload})

    result = execute_webhook_trigger(ctx)

    assert result.outputs == {
        "message_text": "Hello bot!",
        "sender_id": "987654321",
        "chat_id": "555111222",
    }


def _code_node(node_id: str, param_name: str, body: str) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type="code",
        config={"function_source": f"def run({param_name}):\n    return {body}"},
    )


def test_schedule_trigger_runs_through_the_engine_into_a_code_node():
    graph = GraphSpec(
        version="0.1",
        nodes=[
            NodeSpec(id="trigger", type="schedule_trigger", config={"cron": "*/5 * * * *"}),
            _code_node("echo", "ts", "f'fired at {ts}'"),
        ],
        edges=[
            EdgeSpec(
                **{"from": EdgeEndpoint(node="trigger", slot="fired_at")},
                to=EdgeEndpoint(node="echo", slot="ts"),
            )
        ],
    )

    run_result = run_graph(graph)

    echo_trace = next(t for t in run_result.trace if t.node_id == "echo")
    assert echo_trace.error is None
    assert echo_trace.outputs["result"].startswith("fired at ")


def test_webhook_trigger_runs_through_the_engine_into_a_code_node_with_injected_payload():
    graph = GraphSpec(
        version="0.1",
        nodes=[
            NodeSpec(id="trigger", type="webhook_trigger", config={}),
            NodeSpec(id="adapter", type="generic_adapter", config={}),
            _code_node("parse", "raw", "__import__('json').loads(raw)['name']"),
        ],
        edges=[
            EdgeSpec(kind="sub_node", slot="trigger_adapter", **{"from": EdgeEndpoint(node="adapter")}, to=EdgeEndpoint(node="trigger")),
            EdgeSpec(
                **{"from": EdgeEndpoint(node="trigger", slot="payload")},
                to=EdgeEndpoint(node="parse", slot="raw"),
            ),
        ],
    )

    run_result = run_graph(
        graph, resources={"trigger_payloads": {"trigger": {"name": "spec-009"}}}
    )

    parse_trace = next(t for t in run_result.trace if t.node_id == "parse")
    assert parse_trace.error is None
    assert parse_trace.outputs == {"result": "spec-009"}


def test_webhook_trigger_with_telegram_adapter_runs_through_the_engine():
    add_connection("my-telegram-bot", "telegram", {"bot_token": "fake-token"})
    graph = GraphSpec(
        version="0.1",
        nodes=[
            NodeSpec(id="trigger", type="webhook_trigger", config={}),
            NodeSpec(
                id="adapter",
                type="telegram_adapter",
                config={"bot_token_connection": "my-telegram-bot"},
            ),
            _code_node("greet", "who", "f'hello, {who}'"),
        ],
        edges=[
            EdgeSpec(kind="sub_node", slot="trigger_adapter", **{"from": EdgeEndpoint(node="adapter")}, to=EdgeEndpoint(node="trigger")),
            EdgeSpec(
                **{"from": EdgeEndpoint(node="trigger", slot="message_text")},
                to=EdgeEndpoint(node="greet", slot="who"),
            ),
        ],
    )

    telegram_payload = {
        "message": {
            "from": {"id": 1}, "chat": {"id": 2}, "text": "Ada",
        }
    }
    run_result = run_graph(graph, resources={"trigger_payloads": {"trigger": telegram_payload}})

    greet_trace = next(t for t in run_result.trace if t.node_id == "greet")
    assert greet_trace.error is None
    assert greet_trace.outputs == {"result": "hello, Ada"}

from __future__ import annotations

import json

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


def test_webhook_trigger_registered_zero_input_one_text_output():
    definition = default_registry.get("webhook_trigger")
    assert definition is not None
    assert definition.inputs == []
    assert [s.name for s in definition.outputs] == ["payload"]
    assert definition.result_slot is None


def test_schedule_trigger_execute_produces_iso_timestamp():
    node = NodeSpec(id="n1", type="schedule_trigger", config={"cron": "*/5 * * * *"})
    ctx = ExecutionContext(node=node, inputs={})

    result = execute_schedule_trigger(ctx)

    # Just needs to parse as a real ISO 8601 UTC timestamp.
    from datetime import datetime

    datetime.fromisoformat(result.outputs["fired_at"])


def test_webhook_trigger_execute_returns_injected_payload_as_json_string():
    node = NodeSpec(id="n1", type="webhook_trigger", config={})
    ctx = ExecutionContext(
        node=node,
        inputs={},
        resources={"trigger_payloads": {"n1": {"hello": "world"}}},
    )

    result = execute_webhook_trigger(ctx)

    assert json.loads(result.outputs["payload"]) == {"hello": "world"}


def test_webhook_trigger_execute_defaults_to_empty_object_when_no_payload_injected():
    """Manual (non-webhook) invocation must not error just because there's no
    real request body -- this is what lets manual POST /runs and trigger-fired
    runs coexist cleanly (spec-009 §6)."""
    node = NodeSpec(id="n1", type="webhook_trigger", config={})
    ctx = ExecutionContext(node=node, inputs={})

    result = execute_webhook_trigger(ctx)

    assert json.loads(result.outputs["payload"]) == {}


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
            _code_node("parse", "raw", "__import__('json').loads(raw)['name']"),
        ],
        edges=[
            EdgeSpec(
                **{"from": EdgeEndpoint(node="trigger", slot="payload")},
                to=EdgeEndpoint(node="parse", slot="raw"),
            )
        ],
    )

    run_result = run_graph(
        graph, resources={"trigger_payloads": {"trigger": {"name": "spec-009"}}}
    )

    parse_trace = next(t for t in run_result.trace if t.node_id == "parse")
    assert parse_trace.error is None
    assert parse_trace.outputs == {"result": "spec-009"}

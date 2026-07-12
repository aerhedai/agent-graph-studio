from __future__ import annotations

import json

import pytest

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.loop import execute_loop
from backend.schema.models import NodeSpec

INCREMENT_SUB_GRAPH = json.loads(
    """
    {
      "version": "0.1",
      "nodes": [
        {"id": "entry", "type": "text_input", "config": {"value": ""}},
        {"id": "step", "type": "code", "config": {"function_source": "def add_bang(text):\\n    return text + '!'\\n"}},
        {"id": "out", "type": "text_output", "config": {}}
      ],
      "edges": [
        {"from": {"node": "entry", "slot": "text"}, "to": {"node": "step", "slot": "text"}},
        {"from": {"node": "step", "slot": "result"}, "to": {"node": "out", "slot": "text"}}
      ]
    }
    """
)

CONDITIONAL_SUB_GRAPH = json.loads(
    """
    {
      "version": "0.1",
      "nodes": [
        {"id": "entry", "type": "text_input", "config": {"value": ""}},
        {"id": "cond", "type": "conditional_branch", "config": {"condition": "contains('STOP')"}},
        {"id": "out_true", "type": "text_output", "config": {}},
        {"id": "out_false", "type": "text_output", "config": {}}
      ],
      "edges": [
        {"from": {"node": "entry", "slot": "text"}, "to": {"node": "cond", "slot": "value"}},
        {"from": {"node": "cond", "slot": "true_branch"}, "to": {"node": "out_true", "slot": "text"}},
        {"from": {"node": "cond", "slot": "false_branch"}, "to": {"node": "out_false", "slot": "text"}}
      ]
    }
    """
)


def _node(config: dict) -> NodeSpec:
    return NodeSpec(id="loop1", type="loop", config=config)


def test_loop_runs_until_max_iterations():
    ctx = ExecutionContext(
        node=_node({"sub_graph": INCREMENT_SUB_GRAPH, "max_iterations": 3}),
        inputs={"value": "a"},
    )

    result = execute_loop(ctx)

    assert result.outputs == {"value": "a!!!"}
    assert result.child_traces is not None
    assert len(result.child_traces) == 3
    for iteration_trace in result.child_traces:
        assert len(iteration_trace) == 3  # entry, step, out


def test_loop_stops_early_on_stop_condition_slot():
    ctx = ExecutionContext(
        node=_node(
            {
                "sub_graph": CONDITIONAL_SUB_GRAPH,
                "max_iterations": 5,
                "stop_condition_slot": "true_branch",
            }
        ),
        inputs={"value": "please STOP here"},
    )

    result = execute_loop(ctx)

    assert result.outputs == {"value": "please STOP here"}
    assert len(result.child_traces) == 1  # stopped after iteration 1, not all 5


def test_loop_does_not_stop_early_when_condition_never_fires():
    ctx = ExecutionContext(
        node=_node(
            {
                "sub_graph": CONDITIONAL_SUB_GRAPH,
                "max_iterations": 2,
                "stop_condition_slot": "true_branch",
            }
        ),
        inputs={"value": "no match here"},
    )

    result = execute_loop(ctx)

    assert len(result.child_traces) == 2  # ran the full max_iterations


def test_loop_rejects_invalid_sub_graph():
    cyclic_sub_graph = {
        "version": "0.1",
        "nodes": [
            {"id": "a", "type": "conditional_branch", "config": {"condition": "contains('x')"}},
            {"id": "b", "type": "conditional_branch", "config": {"condition": "contains('x')"}},
        ],
        "edges": [
            {"from": {"node": "a", "slot": "true_branch"}, "to": {"node": "b", "slot": "value"}},
            {"from": {"node": "b", "slot": "true_branch"}, "to": {"node": "a", "slot": "value"}},
        ],
    }
    ctx = ExecutionContext(
        node=_node({"sub_graph": cyclic_sub_graph, "max_iterations": 3}),
        inputs={"value": "a"},
    )

    with pytest.raises(NodeExecutionError):
        execute_loop(ctx)


def test_loop_rejects_sub_graph_with_no_entry_node():
    sub_graph = {
        "version": "0.1",
        "nodes": [{"id": "out", "type": "text_output", "config": {}}],
        "edges": [],
    }
    ctx = ExecutionContext(
        node=_node({"sub_graph": sub_graph, "max_iterations": 3}),
        inputs={"value": "a"},
    )

    with pytest.raises(NodeExecutionError):
        execute_loop(ctx)


def test_loop_rejects_sub_graph_with_multiple_entry_nodes():
    sub_graph = {
        "version": "0.1",
        "nodes": [
            {"id": "entry1", "type": "text_input", "config": {"value": ""}},
            {"id": "entry2", "type": "text_input", "config": {"value": ""}},
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "entry1", "slot": "text"}, "to": {"node": "out", "slot": "text"}},
        ],
    }
    ctx = ExecutionContext(
        node=_node({"sub_graph": sub_graph, "max_iterations": 3}),
        inputs={"value": "a"},
    )

    with pytest.raises(NodeExecutionError):
        execute_loop(ctx)


def test_loop_rejects_sub_graph_with_multiple_result_nodes():
    sub_graph = {
        "version": "0.1",
        "nodes": [
            {"id": "entry", "type": "text_input", "config": {"value": ""}},
            {"id": "out1", "type": "text_output", "config": {}},
            {"id": "out2", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "entry", "slot": "text"}, "to": {"node": "out1", "slot": "text"}},
            {"from": {"node": "entry", "slot": "text"}, "to": {"node": "out2", "slot": "text"}},
        ],
    }
    ctx = ExecutionContext(
        node=_node({"sub_graph": sub_graph, "max_iterations": 3}),
        inputs={"value": "a"},
    )

    with pytest.raises(NodeExecutionError):
        execute_loop(ctx)

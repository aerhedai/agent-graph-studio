from __future__ import annotations

from backend.execution.types import ExecutionContext
from backend.nodes.fan_out import _resolve_fanout_slots, execute_fan_out
from backend.schema.models import NodeSpec


def _node(worker_count: int) -> NodeSpec:
    return NodeSpec(id="n1", type="fan_out", config={"worker_count": worker_count})


def test_resolve_fanout_slots_produces_n_branch_outputs():
    resolved = _resolve_fanout_slots(_node(3))
    assert resolved is not None
    inputs, outputs = resolved
    assert [s.name for s in inputs] == ["value"]
    assert [s.name for s in outputs] == ["branch_1", "branch_2", "branch_3"]


def test_resolve_fanout_slots_returns_none_on_malformed_config():
    node = NodeSpec(id="n1", type="fan_out", config={})
    assert _resolve_fanout_slots(node) is None


def test_execute_fan_out_copies_value_to_all_branches():
    ctx = ExecutionContext(node=_node(3), inputs={"value": "hello"})

    result = execute_fan_out(ctx)

    assert result.outputs == {"branch_1": "hello", "branch_2": "hello", "branch_3": "hello"}


def test_execute_fan_out_single_worker():
    ctx = ExecutionContext(node=_node(1), inputs={"value": "x"})
    result = execute_fan_out(ctx)
    assert result.outputs == {"branch_1": "x"}

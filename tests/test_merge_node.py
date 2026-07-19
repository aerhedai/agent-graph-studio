from __future__ import annotations

import json

from backend.execution.types import ExecutionContext
from backend.nodes.merge import _resolve_merge_slots, execute_merge
from backend.schema.models import NodeSpec


def _node(expected_input_count: int) -> NodeSpec:
    return NodeSpec(id="n1", type="merge", config={"expected_input_count": expected_input_count})


def test_resolve_merge_slots_produces_n_inputs():
    resolved = _resolve_merge_slots(_node(3))
    assert resolved is not None
    inputs, outputs = resolved
    assert [s.name for s in inputs] == ["input_1", "input_2", "input_3"]
    assert [s.name for s in outputs] == ["result"]


def test_resolve_merge_slots_returns_none_on_malformed_config():
    # expected_input_count now has a default (2), so an *empty* config is
    # valid -- a genuinely malformed value (fails Field(gt=0)) is what
    # should still return None here.
    node = NodeSpec(id="n1", type="merge", config={"expected_input_count": -1})
    assert _resolve_merge_slots(node) is None


def test_resolve_merge_slots_uses_default_expected_input_count_when_omitted():
    node = NodeSpec(id="n1", type="merge", config={})
    resolved = _resolve_merge_slots(node)
    assert resolved is not None
    inputs, _ = resolved
    assert [s.name for s in inputs] == ["input_1", "input_2"]


def test_execute_merge_combines_in_index_order():
    ctx = ExecutionContext(
        node=_node(3),
        inputs={"input_1": "a", "input_2": "b", "input_3": "c"},
    )

    result = execute_merge(ctx)

    assert json.loads(result.outputs["result"]) == ["a", "b", "c"]


def test_execute_merge_preserves_index_order_regardless_of_dict_insertion_order():
    # ctx.inputs is a plain dict; execute_merge must read by slot NAME
    # (input_1..input_N), not by whatever order the dict happens to iterate.
    ctx = ExecutionContext(
        node=_node(3),
        inputs={"input_3": "c", "input_1": "a", "input_2": "b"},
    )

    result = execute_merge(ctx)

    assert json.loads(result.outputs["result"]) == ["a", "b", "c"]

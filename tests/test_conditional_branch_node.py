from __future__ import annotations

import pytest

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.conditional_branch import execute_conditional_branch
from backend.schema.models import NodeSpec


def _ctx(condition: str, value: str) -> ExecutionContext:
    node = NodeSpec(id="n1", type="conditional_branch", config={"condition": condition})
    return ExecutionContext(node=node, inputs={"value": value}, llm_client=None)


def test_contains_true_branch_fires():
    result = execute_conditional_branch(_ctx("contains('yes')", "yes indeed"))
    assert result.outputs == {"true_branch": "yes indeed"}


def test_contains_false_branch_fires():
    result = execute_conditional_branch(_ctx("contains('yes')", "no way"))
    assert result.outputs == {"false_branch": "no way"}


def test_equals_condition():
    assert execute_conditional_branch(_ctx("equals('exact')", "exact")).outputs == {
        "true_branch": "exact"
    }
    assert execute_conditional_branch(_ctx("equals('exact')", "close")).outputs == {
        "false_branch": "close"
    }


def test_unparseable_condition_raises():
    with pytest.raises(NodeExecutionError):
        execute_conditional_branch(_ctx("not a valid expr", "value"))


def test_unknown_condition_function_raises():
    with pytest.raises(NodeExecutionError):
        execute_conditional_branch(_ctx("unknown_fn('x')", "value"))

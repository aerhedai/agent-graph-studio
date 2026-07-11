from __future__ import annotations

from backend.execution.types import ExecutionContext
from backend.nodes.uppercase_text import execute_uppercase_text
from backend.registry.base import default_registry
from backend.schema.models import NodeSpec


def test_registered_with_text_in_text_out_schema():
    definition = default_registry.get("uppercase_text")
    assert definition is not None
    assert [s.name for s in definition.inputs] == ["text"]
    assert [s.name for s in definition.outputs] == ["text"]
    assert definition.result_slot is None


def test_uppercases_input_text():
    node = NodeSpec(id="n1", type="uppercase_text", config={})
    ctx = ExecutionContext(node=node, inputs={"text": "hello world"})

    result = execute_uppercase_text(ctx)

    assert result.outputs == {"text": "HELLO WORLD"}

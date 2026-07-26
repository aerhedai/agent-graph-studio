"""spec-025: a literal value in NodeSpec.input_values satisfies an input
slot exactly like an incoming edge would -- the prerequisite for dynamic
option loading on a generated MCP node's parameters, which are edge-only
InputSlotSpecs with no other way to receive a value at all. Mirrors
tests/test_engine_optional_inputs.py's own registry/pattern exactly."""

from __future__ import annotations

from pydantic import BaseModel

from backend.execution.engine import run_graph
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec
from backend.schema.loader import parse_graph_json
from backend.schema.types import TEXT
from backend.validation.validator import validate_graph


class _EmptyConfig(BaseModel):
    pass


def _root_execute(ctx: ExecutionContext) -> NodeResult:
    return NodeResult(outputs={"text": "from-edge"})


def _greeter_execute(ctx: ExecutionContext) -> NodeResult:
    title = ctx.inputs.get("title", "friend")
    return NodeResult(outputs={"greeting": f"hello, {title} {ctx.inputs['name']}"})


def _build_registry() -> NodeRegistry:
    registry = NodeRegistry()
    registry.register(
        NodeDefinition(
            type_name="root",
            inputs=[],
            outputs=[OutputSlotSpec("text", TEXT)],
            config_model=_EmptyConfig,
            execute=_root_execute,
            category="core",
        )
    )
    registry.register(
        NodeDefinition(
            type_name="greeter",
            inputs=[
                InputSlotSpec("name", TEXT, required=True),
                InputSlotSpec("title", TEXT, required=False),
            ],
            outputs=[OutputSlotSpec("greeting", TEXT)],
            config_model=_EmptyConfig,
            execute=_greeter_execute,
            category="core",
        )
    )
    return registry


def test_required_input_satisfied_purely_by_literal_value_runs():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "greeter", "type": "greeter", "config": {}, "input_values": {"name": "rohan"}}
          ],
          "edges": []
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.error is None
    assert greeter_record.inputs == {"name": "rohan"}
    assert greeter_record.outputs == {"greeting": "hello, friend rohan"}


def test_edge_takes_precedence_over_literal_value_for_the_same_slot():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "root", "config": {}},
            {"id": "greeter", "type": "greeter", "config": {}, "input_values": {"name": "literal-value"}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "greeter", "slot": "name"}}
          ]
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.inputs == {"name": "from-edge"}


def test_literal_value_also_works_for_an_optional_slot():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "greeter", "type": "greeter", "config": {}, "input_values": {"name": "rohan", "title": "sir"}}
          ],
          "edges": []
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.outputs == {"greeting": "hello, sir rohan"}


def test_validation_treats_literal_valued_required_slot_as_satisfied():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "greeter", "type": "greeter", "config": {}, "input_values": {"name": "rohan"}}
          ],
          "edges": []
        }
        """
    )

    validate_graph(graph, registry=registry)  # should not raise


def test_validation_still_rejects_a_required_slot_with_neither_edge_nor_literal():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "greeter", "type": "greeter", "config": {}}
          ],
          "edges": []
        }
        """
    )

    import pytest

    from backend.validation.errors import GraphValidationError

    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph, registry=registry)
    rules = {i.rule for i in exc_info.value.issues}
    assert "missing_required_input" in rules

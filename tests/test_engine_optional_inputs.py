from __future__ import annotations

from pydantic import BaseModel

from backend.execution.engine import run_graph
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec
from backend.schema.loader import parse_graph_json
from backend.schema.types import TEXT


class _EmptyConfig(BaseModel):
    pass


def _root_execute(ctx: ExecutionContext) -> NodeResult:
    return NodeResult(outputs={"text": "hi"})


def _root_no_output_execute(ctx: ExecutionContext) -> NodeResult:
    # Deliberately fires no outputs at all -- mirrors conditional_branch's
    # own "unfired branch slot" precedent, so a downstream optional input
    # wired to this node's output can never actually receive a value.
    return NodeResult(outputs={})


def _greeter_execute(ctx: ExecutionContext) -> NodeResult:
    # Reads the optional slot via .get(), never direct subscript -- exactly
    # the contract gather_inputs's optional-slot handling requires of node
    # bodies (backend/execution/engine.py).
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
            type_name="root_no_output",
            inputs=[],
            outputs=[OutputSlotSpec("text", TEXT)],
            config_model=_EmptyConfig,
            execute=_root_no_output_execute,
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


def test_unwired_optional_input_does_not_block_scheduling():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "root", "config": {}},
            {"id": "greeter", "type": "greeter", "config": {}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "greeter", "slot": "name"}}
          ]
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.error is None
    assert "title" not in greeter_record.inputs
    assert greeter_record.outputs == {"greeting": "hello, friend hi"}


def test_wired_optional_input_is_used_when_provided():
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "root", "config": {}},
            {"id": "greeter", "type": "greeter", "config": {}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "greeter", "slot": "name"}},
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "greeter", "slot": "title"}}
          ]
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.inputs == {"name": "hi", "title": "hi"}
    assert greeter_record.outputs == {"greeting": "hello, hi hi"}


def test_optional_input_whose_upstream_never_fires_is_omitted_not_blocked():
    # greeter's "title" is wired to a real edge, but that upstream node
    # never produces the "text" output at all -- gather_inputs's optional
    # branch must treat this as "proceed without it", not "blocked"
    # (required-slot semantics, unchanged, would skip the whole node here).
    registry = _build_registry()
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "silent_root", "type": "root_no_output", "config": {}},
            {"id": "name_source", "type": "root", "config": {}},
            {"id": "greeter", "type": "greeter", "config": {}}
          ],
          "edges": [
            {"from": {"node": "name_source", "slot": "text"}, "to": {"node": "greeter", "slot": "name"}},
            {"from": {"node": "silent_root", "slot": "text"}, "to": {"node": "greeter", "slot": "title"}}
          ]
        }
        """
    )

    result = run_graph(graph, registry=registry)

    greeter_record = next(r for r in result.trace if r.node_id == "greeter")
    assert greeter_record.error is None
    assert "title" not in greeter_record.inputs
    assert greeter_record.outputs == {"greeting": "hello, friend hi"}

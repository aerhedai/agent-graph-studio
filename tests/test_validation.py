from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec
from backend.schema.loader import parse_graph_json
from backend.schema.types import SlotType, SlotTypeSpec
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph
from conftest import load_fixture_text


def _load(name: str):
    return parse_graph_json(load_fixture_text(name))


def test_valid_linear_graph_passes(registered_test_connection):
    graph = _load("valid_linear.json")
    validate_graph(graph)  # should not raise


def test_valid_branching_graph_passes():
    graph = _load("valid_branching.json")
    validate_graph(graph)  # should not raise


def test_missing_required_input_rejected():
    graph = _load("missing_input.json")
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    rules = {i.rule for i in exc_info.value.issues}
    assert "missing_required_input" in rules


def test_cyclic_graph_rejected():
    graph = _load("cyclic.json")
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    rules = {i.rule for i in exc_info.value.issues}
    assert "cycle" in rules


def test_unregistered_node_type_rejected():
    graph = _load("unregistered_type.json")
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    rules = {i.rule for i in exc_info.value.issues}
    assert "unregistered_type" in rules


def test_invalid_config_rejected():
    graph = _load("valid_linear.json")
    graph.nodes[1].config = {"model": "claude-opus-4-8", "max_tokens": "not-an-int"}
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    rules = {i.rule for i in exc_info.value.issues}
    assert "invalid_config" in rules


def test_multiple_errors_aggregated():
    # missing_input.json already has an unconnected required input (llm_call.prompt);
    # add a second, independent problem (invalid config) to prove issues aggregate
    # rather than fail-fast on the first one found.
    graph = _load("missing_input.json")
    graph.nodes[0].config = {"model": "claude-opus-4-8", "max_tokens": "not-an-int"}
    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)
    rules = {i.rule for i in exc_info.value.issues}
    assert "missing_required_input" in rules
    assert "invalid_config" in rules
    assert len(exc_info.value.issues) >= 2


def test_type_mismatch_rejected(fresh_registry: NodeRegistry):
    text_out = SlotTypeSpec(base=SlotType.TEXT)
    json_out = SlotTypeSpec(base=SlotType.JSON)

    class EmptyConfig(BaseModel):
        pass

    fresh_registry.register(
        NodeDefinition(
            type_name="produces_json",
            inputs=[],
            outputs=[OutputSlotSpec("out", json_out)],
            config_model=EmptyConfig,
            execute=lambda ctx: None,
        )
    )
    fresh_registry.register(
        NodeDefinition(
            type_name="expects_text",
            inputs=[InputSlotSpec("in", text_out)],
            outputs=[],
            config_model=EmptyConfig,
            execute=lambda ctx: None,
        )
    )

    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "n1", "type": "produces_json", "config": {}},
            {"id": "n2", "type": "expects_text", "config": {}}
          ],
          "edges": [
            {"from": {"node": "n1", "slot": "out"}, "to": {"node": "n2", "slot": "in"}}
          ]
        }
        """
    )

    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph, registry=fresh_registry)
    rules = {i.rule for i in exc_info.value.issues}
    assert "type_mismatch" in rules


def test_no_false_positive_type_mismatch_on_real_mvp_graphs(registered_test_connection):
    # All 4 MVP node types are text-only; real graphs should never trip
    # the type-mismatch rule.
    for fixture in ("valid_linear.json", "valid_branching.json"):
        graph = _load(fixture)
        validate_graph(graph)  # should not raise


def test_code_node_dynamic_schema_passes_validation():
    graph = _load("code_node.json")
    validate_graph(graph)  # should not raise


def test_code_node_malformed_source_rejected_once_not_double_reported():
    # A malformed function_source can't be resolved into an input schema by
    # resolve_slots (it returns None), which must make check_required_inputs
    # and check_type_mismatches skip the node rather than pile on a second,
    # less-actionable issue on top of the real invalid_config one.
    graph = _load("code_node.json")
    graph.nodes[1].config["function_source"] = "not python("

    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)

    rules = [i.rule for i in exc_info.value.issues]
    assert rules.count("invalid_config") == 1
    assert "missing_required_input" not in rules
    assert "type_mismatch" not in rules


def test_connection_swap_needs_no_schema_redesign():
    # spec-006 §5: the same graph JSON format supports two llm_call nodes
    # referencing different named connections (one anthropic-typed, one
    # ollama-typed under the hood), with no schema changes required between
    # them -- the node config only ever names a connection, never a provider.
    from backend.connections.store import add_connection

    add_connection("personal-anthropic", "anthropic", {"api_key": "unused-in-tests"})
    add_connection("my-pc-ollama", "ollama", {"host": "localhost", "port": 11434})

    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hi"}},
            {"id": "anthropic_call", "type": "llm_call",
             "config": {"connection": "personal-anthropic", "model": "claude-opus-4-8", "max_tokens": 50}},
            {"id": "ollama_call", "type": "llm_call",
             "config": {"connection": "my-pc-ollama", "model": "llama3.2", "max_tokens": 50}},
            {"id": "out1", "type": "text_output", "config": {}},
            {"id": "out2", "type": "text_output", "config": {}}
          ],
          "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "anthropic_call", "slot": "prompt"}},
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "ollama_call", "slot": "prompt"}},
            {"from": {"node": "anthropic_call", "slot": "response"}, "to": {"node": "out1", "slot": "text"}},
            {"from": {"node": "ollama_call", "slot": "response"}, "to": {"node": "out2", "slot": "text"}}
          ]
        }
        """
    )

    validate_graph(graph)  # should not raise


def test_missing_connection_reports_specific_missing_connection_rule():
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hi"}},
            {"id": "call", "type": "llm_call",
             "config": {"connection": "not-configured-anywhere", "model": "claude-opus-4-8", "max_tokens": 50}},
            {"id": "out", "type": "text_output", "config": {}}
          ],
          "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "call", "slot": "prompt"}},
            {"from": {"node": "call", "slot": "response"}, "to": {"node": "out", "slot": "text"}}
          ]
        }
        """
    )

    with pytest.raises(GraphValidationError) as exc_info:
        validate_graph(graph)

    issue = next(i for i in exc_info.value.issues if i.rule == "missing_connection")
    assert issue.node_id == "call"
    assert "not-configured-anywhere" in issue.message

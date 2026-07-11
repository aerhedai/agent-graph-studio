from __future__ import annotations

import pytest

from backend.schema.loader import GraphParseError, parse_graph_json
from conftest import load_fixture_text


def test_valid_linear_graph_parses():
    graph = parse_graph_json(load_fixture_text("valid_linear.json"))
    assert graph.version == "0.1"
    assert [n.id for n in graph.nodes] == ["n1", "n2", "n3"]
    assert graph.edges[0].from_.node == "n1"
    assert graph.edges[0].from_.slot == "text"
    assert graph.edges[0].to.node == "n2"
    assert graph.edges[0].to.slot == "prompt"


def test_malformed_json_syntax_rejected():
    with pytest.raises(GraphParseError):
        parse_graph_json("{not valid json")


def test_missing_required_field_rejected():
    with pytest.raises(GraphParseError):
        parse_graph_json(load_fixture_text("malformed.json"))


def test_duplicate_node_id_rejected():
    with pytest.raises(GraphParseError):
        parse_graph_json(load_fixture_text("duplicate_node_id.json"))

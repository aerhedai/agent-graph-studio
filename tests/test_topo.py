from __future__ import annotations

from backend.schema.models import EdgeEndpoint, EdgeSpec
from backend.schema.topo import kahn_order


def _edge(src: str, dst: str) -> EdgeSpec:
    return EdgeSpec(from_=EdgeEndpoint(node=src, slot="out"), to=EdgeEndpoint(node=dst, slot="in"))


def test_linear_order():
    order, remaining = kahn_order(["a", "b", "c"], [_edge("a", "b"), _edge("b", "c")])
    assert order == ["a", "b", "c"]
    assert remaining == []


def test_branching_order_respects_dependencies():
    # a -> b, a -> c, b -> d, c -> d
    order, remaining = kahn_order(
        ["a", "b", "c", "d"],
        [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")],
    )
    assert remaining == []
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_disconnected_nodes_included():
    order, remaining = kahn_order(["a", "b"], [])
    assert set(order) == {"a", "b"}
    assert remaining == []


def test_cycle_detected_returns_remaining_nodes():
    order, remaining = kahn_order(["a", "b"], [_edge("a", "b"), _edge("b", "a")])
    assert order == []
    assert set(remaining) == {"a", "b"}


def test_partial_cycle_returns_only_cyclic_remainder():
    # a -> b -> c -> b (b,c cyclic; a is fine)
    order, remaining = kahn_order(
        ["a", "b", "c"], [_edge("a", "b"), _edge("b", "c"), _edge("c", "b")]
    )
    assert order == ["a"]
    assert set(remaining) == {"b", "c"}

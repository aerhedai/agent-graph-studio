from __future__ import annotations

import time

from pydantic import BaseModel

from backend.execution.engine import run_graph
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, NodeDefinition, NodeRegistry, OutputSlotSpec
from backend.schema.loader import parse_graph_json
from backend.schema.types import TEXT

DELAY_SECONDS = 0.3


class _EmptyConfig(BaseModel):
    pass


def _root_execute(ctx: ExecutionContext) -> NodeResult:
    return NodeResult(outputs={"text": "seed"})


def _slow_echo_execute(ctx: ExecutionContext) -> NodeResult:
    time.sleep(DELAY_SECONDS)
    return NodeResult(outputs={"text": ctx.inputs["text"]})


def _build_registry() -> NodeRegistry:
    registry = NodeRegistry()
    registry.register(
        NodeDefinition(
            type_name="root",
            inputs=[],
            outputs=[OutputSlotSpec("text", TEXT)],
            config_model=_EmptyConfig,
            execute=_root_execute,
        )
    )
    registry.register(
        NodeDefinition(
            type_name="slow_echo",
            inputs=[InputSlotSpec("text", TEXT)],
            outputs=[OutputSlotSpec("text", TEXT)],
            config_model=_EmptyConfig,
            execute=_slow_echo_execute,
        )
    )
    return registry


def test_independent_branches_run_concurrently_not_sequentially():
    # Two slow_echo nodes share only a root -- no dependency on each other.
    # Sequential execution would take ~2x DELAY_SECONDS; concurrent should
    # take ~1x plus scheduling overhead. This is the fast, deterministic,
    # CI-friendly proof of spec-004's core concurrency claim -- the live,
    # non-mocked demonstration is separate and shown directly to the user.
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "root", "config": {}},
            {"id": "a", "type": "slow_echo", "config": {}},
            {"id": "b", "type": "slow_echo", "config": {}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "a", "slot": "text"}},
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "b", "slot": "text"}}
          ]
        }
        """
    )
    registry = _build_registry()

    started = time.perf_counter()
    run_result = run_graph(graph, registry=registry)
    elapsed = time.perf_counter() - started

    assert len(run_result.trace) == 3
    # Comfortably below 2x DELAY_SECONDS (sequential) and above 1x (can't
    # finish faster than one node's own delay); generous buffer for CI jitter.
    assert elapsed < DELAY_SECONDS * 1.7


def test_dependent_chain_still_runs_sequentially():
    # A three-node chain (each depends on the previous) must NOT parallelize
    # -- the scheduler should only run independent nodes concurrently, never
    # nodes with a real data dependency between them.
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "root", "config": {}},
            {"id": "a", "type": "slow_echo", "config": {}},
            {"id": "b", "type": "slow_echo", "config": {}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "a", "slot": "text"}},
            {"from": {"node": "a", "slot": "text"}, "to": {"node": "b", "slot": "text"}}
          ]
        }
        """
    )
    registry = _build_registry()

    started = time.perf_counter()
    run_graph(graph, registry=registry)
    elapsed = time.perf_counter() - started

    assert elapsed > DELAY_SECONDS * 1.8

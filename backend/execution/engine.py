from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.execution.trace import RunResult, TokenCost, TraceRecord
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import NodeRegistry, default_registry
from backend.schema.models import GraphSpec
from backend.schema.topo import kahn_order
from backend.validation.validator import validate_graph


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_graph(
    graph: GraphSpec,
    registry: NodeRegistry = default_registry,
    resources: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Execute a validated graph per spec §6.

    `resources` is an opaque, caller-populated bag passed unchanged to every
    node's ExecutionContext -- the engine has no knowledge of what any node
    type needs (e.g. an LLM client) or constructs anything on a node type's
    behalf; each node's execute() resolves its own dependencies.

    Any node output slot the node's execute() doesn't return is treated as
    "did not fire" -- this single generic rule handles both
    conditional_branch's branch-pruning (only the fired branch's slot is
    returned) and failure propagation (a failed node returns no outputs at
    all), so downstream nodes are skipped by the same code path in either
    case, with zero node-type-specific branching in the engine.
    """
    validate_graph(graph, registry)

    resources = resources or {}

    node_ids = [n.id for n in graph.nodes]
    order, _ = kahn_order(node_ids, graph.edges)
    nodes_by_id = {n.id: n for n in graph.nodes}
    incoming_by_slot = {(e.to.node, e.to.slot): e for e in graph.edges}

    run_id = run_id or str(uuid4())
    available: dict[tuple[str, str], Any] = {}
    trace: list[TraceRecord] = []
    result: dict[str, Any] = {}

    for node_id in order:
        node = nodes_by_id[node_id]
        definition = registry.get(node.type)

        gathered_inputs: dict[str, Any] = {}
        skip = False
        for slot in definition.inputs:
            edge = incoming_by_slot.get((node_id, slot.name))
            key = (edge.from_.node, edge.from_.slot) if edge else None
            if key is None or key not in available:
                skip = True
                break
            gathered_inputs[slot.name] = available[key]
        if skip:
            continue

        started_at = _utcnow_iso()
        ctx = ExecutionContext(node=node, inputs=gathered_inputs, resources=resources)
        try:
            node_result: NodeResult = definition.execute(ctx)
            finished_at = _utcnow_iso()
            for out_slot, value in node_result.outputs.items():
                available[(node_id, out_slot)] = value
            trace.append(
                TraceRecord(
                    run_id=run_id,
                    node_id=node_id,
                    node_type=node.type,
                    started_at=started_at,
                    finished_at=finished_at,
                    inputs=gathered_inputs,
                    outputs=node_result.outputs,
                    token_cost=node_result.token_cost,
                    error=None,
                )
            )
            if definition.result_slot is not None:
                result[node_id] = gathered_inputs[definition.result_slot]
        except Exception as e:
            finished_at = _utcnow_iso()
            trace.append(
                TraceRecord(
                    run_id=run_id,
                    node_id=node_id,
                    node_type=node.type,
                    started_at=started_at,
                    finished_at=finished_at,
                    inputs=gathered_inputs,
                    outputs={},
                    token_cost=TokenCost(),
                    error=str(e),
                )
            )

    return RunResult(result=result, trace=trace)

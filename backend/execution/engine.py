from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from backend.execution.trace import RunResult, TokenCost, TraceRecord
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import NodeDefinition, NodeRegistry, default_registry, effective_inputs
from backend.schema.models import GraphSpec, NodeSpec
from backend.validation.validator import validate_graph


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _execute_node(
    node: NodeSpec,
    definition: NodeDefinition,
    gathered_inputs: dict[str, Any],
    resources: dict[str, Any],
    run_id: str,
) -> tuple[str, NodeResult | None, TraceRecord]:
    """Run one node's (synchronous) execute() body in a worker thread and
    build its trace record -- the direct async equivalent of the try/except
    block the old sequential engine ran inline. `asyncio.to_thread` is what
    actually delivers concurrency for I/O-bound node bodies (blocking HTTP/
    subprocess calls release the GIL while waiting), even though every node
    body itself stays a plain synchronous function -- no node type needed to
    change for this spec.
    """
    started_at = _utcnow_iso()
    ctx = ExecutionContext(node=node, inputs=gathered_inputs, resources=resources)
    try:
        node_result: NodeResult = await asyncio.to_thread(definition.execute, ctx)
        finished_at = _utcnow_iso()
        record = TraceRecord(
            run_id=run_id,
            node_id=node.id,
            node_type=node.type,
            started_at=started_at,
            finished_at=finished_at,
            inputs=gathered_inputs,
            outputs=node_result.outputs,
            token_cost=node_result.token_cost,
            side_effect=node_result.side_effect,
            child_traces=node_result.child_traces,
            error=None,
        )
        return node.id, node_result, record
    except Exception as e:
        finished_at = _utcnow_iso()
        record = TraceRecord(
            run_id=run_id,
            node_id=node.id,
            node_type=node.type,
            started_at=started_at,
            finished_at=finished_at,
            inputs=gathered_inputs,
            outputs={},
            token_cost=TokenCost(),
            side_effect=False,
            child_traces=None,
            error=str(e),
        )
        return node.id, None, record


async def _run_graph_async(
    graph: GraphSpec,
    registry: NodeRegistry,
    resources: dict[str, Any],
    run_id: str,
    on_round_start: Callable[[list[str]], None] | None = None,
    on_trace_record: Callable[[TraceRecord], None] | None = None,
) -> RunResult:
    """Layered/wavefront concurrent scheduler (spec-004).

    Each round: sort every still-pending node into `ready` (all required
    input slots resolved), `blocked` (an input can never arrive -- missing
    edge, or its upstream already finished without producing that slot;
    permanently skipped, exactly like the old engine's one-shot check, just
    now able to fire on any round instead of only once), or left pending
    ("waiting" -- some upstream hasn't finished yet, re-checked next round).
    The whole `ready` bucket runs concurrently via asyncio.gather -- this is
    the actual concurrency; fan_out's N branches land in the same bucket
    together and run at the same time with zero fan_out-specific code here.

    `pending` is a list, not a set: Python sets don't guarantee stable
    iteration order, and round composition (which nodes land in the same
    ready bucket, and in what order they're handed to gather) must be
    deterministic -- gather() returns results in submission order regardless
    of actual completion timing, so appending to `trace` in that order keeps
    every graph that never has more than one node ready per round (i.e.
    every graph from SPEC-001 through SPEC-003) producing byte-identical
    trace order to the old strictly-sequential engine.

    `finished` is tracked explicitly (not inferred from `available`) because
    "upstream hasn't run yet" and "upstream ran but didn't fire this slot"
    must be distinguishable mid-run now -- they were never ambiguous when
    everything ran once, in one fixed pass.

    `on_round_start`/`on_trace_record` (spec-005) are optional observers for
    callers that want incremental progress (the API layer's live per-node
    status polling) rather than only the final RunResult. `on_round_start`
    fires once per round with that round's ready node ids, right before they
    run -- the "running" signal. `on_trace_record` fires once per node
    immediately after its record is built -- the "success"/"error" signal.
    Both None by default; every other caller (CLI, `loop`'s recursive call,
    all pre-spec-005 tests) is unaffected. Caveat: nodes within the same
    concurrent round still transition together as a batch, since they only
    become individually available once `asyncio.gather` returns for the
    whole round -- not a gap in the callback, just inherent to running a
    round as one `gather` call.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
    incoming_by_slot = {(e.to.node, e.to.slot): e for e in graph.edges}

    available: dict[tuple[str, str], Any] = {}
    trace: list[TraceRecord] = []
    result: dict[str, Any] = {}

    pending: list[str] = [n.id for n in graph.nodes]
    finished: set[str] = set()

    def gather_inputs(node_id: str) -> tuple[str, dict[str, Any] | None]:
        node = nodes_by_id[node_id]
        definition = registry.get(node.type)
        gathered: dict[str, Any] = {}
        for slot in effective_inputs(definition, node) or []:
            edge = incoming_by_slot.get((node_id, slot.name))
            if edge is None:
                return "blocked", None
            key = (edge.from_.node, edge.from_.slot)
            if key in available:
                gathered[slot.name] = available[key]
            elif edge.from_.node in finished:
                return "blocked", None
            else:
                return "waiting", None
        return "ready", gathered

    while pending:
        ready: list[tuple[str, dict[str, Any]]] = []
        for node_id in list(pending):
            status, gathered = gather_inputs(node_id)
            if status == "blocked":
                pending.remove(node_id)
                finished.add(node_id)
            elif status == "ready":
                ready.append((node_id, gathered))
            # "waiting": stays in pending, re-checked next round

        if not ready:
            # Nothing left can ever become ready. Unreachable for a validated
            # (cycle-free) graph -- a safety net, not a real code path.
            break

        if on_round_start is not None:
            on_round_start([node_id for node_id, _ in ready])

        round_results = await asyncio.gather(
            *(
                _execute_node(nodes_by_id[node_id], registry.get(nodes_by_id[node_id].type), gathered, resources, run_id)
                for node_id, gathered in ready
            )
        )

        for node_id, node_result, record in round_results:
            pending.remove(node_id)
            finished.add(node_id)
            trace.append(record)
            if on_trace_record is not None:
                on_trace_record(record)
            if node_result is not None:
                definition = registry.get(nodes_by_id[node_id].type)
                for out_slot, value in node_result.outputs.items():
                    available[(node_id, out_slot)] = value
                if definition.result_slot is not None:
                    result[node_id] = record.inputs[definition.result_slot]

    return RunResult(result=result, trace=trace)


def run_graph(
    graph: GraphSpec,
    registry: NodeRegistry = default_registry,
    resources: dict[str, Any] | None = None,
    run_id: str | None = None,
    on_round_start: Callable[[list[str]], None] | None = None,
    on_trace_record: Callable[[TraceRecord], None] | None = None,
) -> RunResult:
    """Execute a validated graph, running independent branches concurrently
    (spec-004). Synchronous, unchanged public signature -- internally a thin
    wrapper around the async scheduler in `_run_graph_async`, so every
    existing call site (CLI, tests, and `loop`'s own recursive sub-graph
    invocation) needs no changes.

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

    Calling this from within an already-running event loop (e.g. a future
    async API server) would raise, since asyncio.run() requires there be no
    running loop on the current thread -- not a concern for the synchronous
    CLI or for `loop`'s recursive call, which runs inside an
    asyncio.to_thread worker with no loop of its own.
    """
    validate_graph(graph, registry)
    resources = resources or {}
    run_id = run_id or str(uuid4())
    return asyncio.run(
        _run_graph_async(graph, registry, resources, run_id, on_round_start, on_trace_record)
    )

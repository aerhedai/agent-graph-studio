"""`loop` node: wraps a sub-graph and re-invokes it, re-injecting its own
output as the next iteration's input, until `stop_condition_slot` fires or
`max_iterations` is hit (ADR-002's loop-as-subgraph design, spec-004).

From the outer graph's perspective this is a true DAG node: exactly one
input, one output. Internally it recursively drives the same `run_graph()`
every other graph run goes through.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.execution.errors import NodeExecutionError
from backend.execution.trace import TraceRecord
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.models import GraphSpec
from backend.schema.types import TEXT
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph


class LoopConfig(BaseModel):
    sub_graph: GraphSpec
    max_iterations: int = Field(gt=0)
    stop_condition_slot: str | None = None


def _find_entry_node_id(sub_graph: GraphSpec) -> str:
    """The sub-graph's entry point: the one text_input node whose config
    value gets overwritten with the current loop value each iteration.
    text_input is the only zero-input node type, so it's the natural,
    unambiguous convention for "where does the loop's value get seeded" --
    this type check lives here, in the loop node's own domain knowledge,
    not in the engine (same category as `code` knowing about ast.FunctionDef
    or `mcp_call` knowing about JSON Schema)."""
    entry_nodes = [n for n in sub_graph.nodes if n.type == "text_input"]
    if len(entry_nodes) != 1:
        raise NodeExecutionError(
            "loop sub_graph must contain exactly one text_input node as its "
            f"entry point, found {len(entry_nodes)}"
        )
    return entry_nodes[0].id


@register_node(
    "loop",
    inputs=[InputSlotSpec("value", TEXT)],
    outputs=[OutputSlotSpec("value", TEXT)],
    config_model=LoopConfig,
    category="core",
)
def execute_loop(ctx: ExecutionContext) -> NodeResult:
    # Local import: no actual import cycle exists (execution.engine never
    # imports backend.nodes), but this keeps the recursive engine<->loop-node
    # dependency contained to the one place that needs it rather than a
    # module-level import shared by every node type in this package.
    from backend.execution.engine import run_graph

    config = LoopConfig.model_validate(ctx.node.config)

    try:
        validate_graph(config.sub_graph)
    except GraphValidationError as e:
        raise NodeExecutionError(f"loop sub_graph is invalid: {e}") from e

    entry_node_id = _find_entry_node_id(config.sub_graph)

    value = ctx.inputs["value"]
    child_traces: list[list[TraceRecord]] = []

    for _ in range(config.max_iterations):
        iteration_graph = config.sub_graph.model_copy(deep=True)
        for node in iteration_graph.nodes:
            if node.id == entry_node_id:
                node.config["value"] = value

        try:
            sub_result = run_graph(iteration_graph, resources=ctx.resources)
        except Exception as e:
            raise NodeExecutionError(f"loop iteration failed: {e}") from e

        child_traces.append(sub_result.trace)

        if len(sub_result.result) != 1:
            raise NodeExecutionError(
                "loop sub_graph must produce exactly one result value, got "
                f"{len(sub_result.result)}"
            )
        value = next(iter(sub_result.result.values()))

        if config.stop_condition_slot is not None:
            fired = any(
                config.stop_condition_slot in record.outputs for record in sub_result.trace
            )
            if fired:
                break

    return NodeResult(outputs={"value": value}, child_traces=child_traces)

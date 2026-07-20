"""`agent` node: wraps a model connection with memory (conversation window)
and tools (other nodes it can call) and runs a genuine reasoning loop -- the
model itself decides which tools to call, in what order, and when it's
done (spec-008).

Tool calls are the one deliberate, disclosed exception to "every node's
inputs come from graph edges" -- see ADR-008. A tool node is invoked
directly via its own NodeDefinition.execute(), with the model-supplied
arguments as inputs, bypassing edge-based gathering entirely for that call.

spec-012: `model`/`memory`/`tools` are no longer inline config -- they're
sub-node slots, wired via `sub_node` edges and resolved here through
`ctx.resources["sub_nodes"]` (the same generic mechanism `nodes_by_id`
already provides, extended in engine.py's `run_graph()`). `AgentConfig`
shrinks to just `max_iterations`. Tool-calling itself (direct execute()
invocation, ADR-008) is completely unchanged -- only how tool node ids are
discovered changed, from a config list to sub_node edges.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.connections.base import ToolDefinition, default_connection_registry
from backend.execution.errors import NodeExecutionError
from backend.execution.trace import TokenCost, TraceRecord
from backend.execution.types import ExecutionContext, NodeResult
from backend.nodes.memory import MemoryConfig
from backend.nodes.model import ModelConfig
from backend.registry.base import InputSlotSpec, OutputSlotSpec, SubNodeSlotSpec, effective_inputs
from backend.registry.base import default_registry as default_node_registry
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class AgentConfig(BaseModel):
    max_iterations: int = Field(gt=0, default=10)


def _utcnow_iso() -> str:
    # Mirrors backend/execution/engine.py's private _utcnow_iso exactly --
    # small, deliberate duplication rather than exporting an engine
    # internal for one caller.
    return datetime.now(timezone.utc).isoformat()


def _apply_memory_window(
    messages: list[dict[str, Any]], max_messages: int
) -> list[dict[str, Any]]:
    """Last-N-messages window (spec-008 §3), scoped to this one call only
    -- a pure function so it's directly unit-testable without a real model."""
    return messages[-max_messages:] if len(messages) > max_messages else messages


def _notify_sub_node_activity(
    resources: dict[str, Any], parent_node_id: str, sub_node_id: str, active: bool
) -> None:
    """Live per-call progress signal for a sub-node an agent invokes
    directly (the model connection, or a tool via ADR-008's bypass) --
    invisible to the engine's own `running_node_ids` since none of this
    happens through the scheduler. A resources-bag callback (same
    established pattern as `on_round_start`/`on_trace_record`, ADR-003),
    not a new `run_graph()` parameter, since this happens *inside* one
    top-level node's own execute() body, not something the scheduler
    itself orchestrates -- keeps this a zero-engine-change addition. A
    no-op when nothing is listening (every pre-existing test/caller that
    doesn't set this resource key)."""
    callback = resources.get("on_sub_node_activity")
    if callback is not None:
        callback(parent_node_id, sub_node_id, active)


def _tool_definition(node: NodeSpec) -> ToolDefinition:
    """Derives a tool's parameter schema from its referenced node's actual
    resolved input schema (resolve_slots or static .inputs, SPEC-002's
    effective_inputs -- reused unchanged) -- derived, not separately
    authored, so a tool's schema and its callability never drift apart
    (spec-008 §5). Every slot in this project is TEXT-typed today, so every
    parameter is a plain JSON-schema string."""
    definition = default_node_registry.get(node.type)
    if definition is None:
        raise NodeExecutionError(f"agent tool '{node.id}' has unregistered type '{node.type}'")
    inputs = effective_inputs(definition, node)
    if inputs is None:
        raise NodeExecutionError(f"agent tool '{node.id}' has an unresolvable input schema")
    properties = {slot.name: {"type": "string"} for slot in inputs}
    required = [slot.name for slot in inputs if slot.required]
    return ToolDefinition(
        name=node.id,
        description=f"Executes the '{node.id}' node (type: {node.type}).",
        parameters={"type": "object", "properties": properties, "required": required},
    )


def _run_tool(
    call_name: str,
    call_arguments: dict[str, Any],
    tool_node: NodeSpec,
    resources: dict[str, Any],
) -> tuple[TraceRecord, str]:
    """Directly invokes the tool node's own execute() with the model-
    supplied arguments as inputs -- the ADR-008 exception in action.
    Returns the resulting trace record plus the text to feed back to the
    model. Never raises: a failure (bad arguments or the tool's own
    internal error) becomes an error-carrying trace record and an error
    message fed back for the model to see and retry, per spec-008's
    resolved "self-correct" decision -- it does not abort the agent's loop.
    """
    definition = default_node_registry.get(tool_node.type)
    started_at = _utcnow_iso()
    run_id = str(uuid4())
    try:
        if definition is None:
            raise NodeExecutionError(f"agent tool '{tool_node.id}' has unregistered type '{tool_node.type}'")
        tool_ctx = ExecutionContext(node=tool_node, inputs=call_arguments, resources=resources)
        tool_result: NodeResult = definition.execute(tool_ctx)
    except Exception as e:
        finished_at = _utcnow_iso()
        error_text = str(e)
        record = TraceRecord(
            run_id=run_id,
            node_id=tool_node.id,
            node_type=tool_node.type,
            started_at=started_at,
            finished_at=finished_at,
            inputs=call_arguments,
            outputs={},
            error=error_text,
        )
        return record, f"Error: {error_text}"

    finished_at = _utcnow_iso()
    record = TraceRecord(
        run_id=run_id,
        node_id=tool_node.id,
        node_type=tool_node.type,
        started_at=started_at,
        finished_at=finished_at,
        inputs=call_arguments,
        outputs=tool_result.outputs,
        token_cost=tool_result.token_cost,
        side_effect=tool_result.side_effect,
        error=None,
    )
    return record, json.dumps(tool_result.outputs)


@register_node(
    "agent",
    inputs=[InputSlotSpec("task", TEXT)],
    outputs=[OutputSlotSpec("answer", TEXT)],
    config_model=AgentConfig,
    category="ai",
    sub_node_slots={
        "model": SubNodeSlotSpec(cardinality="one", accepts_role="model"),
        "memory": SubNodeSlotSpec(cardinality="zero_or_one", accepts_role="memory"),
        "tools": SubNodeSlotSpec(cardinality="zero_or_one", accepts_role="tool_group"),
    },
)
def execute_agent(ctx: ExecutionContext) -> NodeResult:
    config = AgentConfig.model_validate(ctx.node.config)
    nodes_by_id: dict[str, NodeSpec] = ctx.resources.get("nodes_by_id", {})
    sub_nodes: dict[tuple[str, str], list[str]] = ctx.resources.get("sub_nodes", {})

    model_ids = sub_nodes.get((ctx.node.id, "model"), [])
    if len(model_ids) != 1:
        # Defensive only -- validate_graph()'s check_sub_node_edges already
        # guarantees exactly one `model` sub-node before a run ever starts,
        # the same "should have been resolved before this run started"
        # precedent used throughout this codebase (e.g. llm_call, connection
        # resolution).
        raise NodeExecutionError(
            f"agent '{ctx.node.id}' has {len(model_ids)} connected 'model' sub-nodes, expected exactly 1"
        )
    model_node = nodes_by_id[model_ids[0]]
    model_config = ModelConfig.model_validate(model_node.config)

    memory_ids = sub_nodes.get((ctx.node.id, "memory"), [])
    memory_config = MemoryConfig.model_validate(nodes_by_id[memory_ids[0]].config) if memory_ids else None

    # spec-014 (relaxed): agent.tools resolves to zero or one `tool_group`,
    # not tool nodes directly -- an agent with no tools connected simply
    # reasons over model+memory alone, tool_definitions ends up empty. One
    # added level of indirection through the exact same `sub_nodes`
    # resource (keyed by (root_id, slot) for every sub_node edge in the
    # graph, not just top-level roots), so reading through the group needs
    # no engine changes, just one more lookup.
    tool_group_ids = sub_nodes.get((ctx.node.id, "tools"), [])
    if len(tool_group_ids) > 1:
        # Defensive only -- validate_graph()'s check_sub_node_edges already
        # guarantees at most one connected 'tool_group' sub-node before a
        # run ever starts, same precedent as model_ids above.
        raise NodeExecutionError(
            f"agent '{ctx.node.id}' has {len(tool_group_ids)} connected 'tool_group' "
            "sub-nodes, expected at most 1"
        )
    tool_ids = sub_nodes.get((tool_group_ids[0], "tools"), []) if tool_group_ids else []

    profile = ctx.resources.get("connection_profiles", {}).get(model_config.connection)
    if profile is None:
        raise NodeExecutionError(
            f"No resolved connection profile for '{model_config.connection}' -- "
            "it should have been resolved before this run started"
        )
    connection_definition = default_connection_registry.get(profile.type)
    if connection_definition is None or connection_definition.complete_with_tools is None:
        raise NodeExecutionError(
            f"Connection '{model_config.connection}' (type '{profile.type}') does not support "
            "tool-calling, required by the 'agent' node type"
        )
    connection_config = connection_definition.config_model.model_validate(profile.config)

    tool_definitions: list[ToolDefinition] = []
    for tool_id in tool_ids:
        tool_node = nodes_by_id.get(tool_id)
        if tool_node is None:
            raise NodeExecutionError(f"agent tool reference '{tool_id}' does not exist in the graph")
        tool_definitions.append(_tool_definition(tool_node))

    messages: list[dict[str, Any]] = [{"role": "user", "content": ctx.inputs["task"]}]
    child_traces: list[list[TraceRecord]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    for _ in range(config.max_iterations):
        windowed = (
            _apply_memory_window(messages, memory_config.max_messages)
            if memory_config is not None
            else messages
        )
        # memory has no external call of its own -- it's a synchronous
        # window applied above -- but it genuinely shapes this exact model
        # call (windowed vs. the raw `messages`), so "in use" means "for as
        # long as it's feeding the current call", the same window as the
        # model itself, not a separate call boundary.
        _notify_sub_node_activity(ctx.resources, ctx.node.id, model_node.id, True)
        if memory_ids:
            _notify_sub_node_activity(ctx.resources, ctx.node.id, memory_ids[0], True)
        try:
            response = connection_definition.complete_with_tools(
                connection_config,
                model=model_config.model,
                system_prompt=model_config.system_prompt,
                messages=windowed,
                tools=tool_definitions,
                max_tokens=model_config.max_tokens,
            )
        except Exception as e:
            raise NodeExecutionError(f"agent model call failed: {e}") from e
        finally:
            _notify_sub_node_activity(ctx.resources, ctx.node.id, model_node.id, False)
            if memory_ids:
                _notify_sub_node_activity(ctx.resources, ctx.node.id, memory_ids[0], False)

        total_input_tokens += response.input_tokens
        total_output_tokens += response.output_tokens

        if not response.tool_calls:
            return NodeResult(
                outputs={"answer": response.text or ""},
                child_traces=child_traces or None,
                token_cost=TokenCost(input_tokens=total_input_tokens, output_tokens=total_output_tokens),
            )

        messages.append(
            {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls
                ],
            }
        )

        # spec-014's tool_group card gets its own live signal too (mostly
        # for the collapsed view, which has no per-tool rows to light up)
        # -- active for this whole batch of tool calls, not per-call, since
        # a collapsed group can't show which specific tool is running.
        if tool_group_ids:
            _notify_sub_node_activity(ctx.resources, ctx.node.id, tool_group_ids[0], True)
        try:
            for call in response.tool_calls:
                tool_node = nodes_by_id.get(call.name) if call.name in tool_ids else None
                if tool_node is None:
                    error_text = f"Unknown tool '{call.name}'. Available tools: {', '.join(tool_ids)}"
                    started_at = _utcnow_iso()
                    record = TraceRecord(
                        run_id=str(uuid4()),
                        node_id=call.name,
                        node_type="unknown",
                        started_at=started_at,
                        finished_at=_utcnow_iso(),
                        inputs=call.arguments,
                        outputs={},
                        error=error_text,
                    )
                    child_traces.append([record])
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": error_text}
                    )
                    continue

                _notify_sub_node_activity(ctx.resources, ctx.node.id, tool_node.id, True)
                try:
                    record, tool_text = _run_tool(call.name, call.arguments, tool_node, ctx.resources)
                finally:
                    _notify_sub_node_activity(ctx.resources, ctx.node.id, tool_node.id, False)
                child_traces.append([record])
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": tool_text}
                )
        finally:
            if tool_group_ids:
                _notify_sub_node_activity(ctx.resources, ctx.node.id, tool_group_ids[0], False)

    raise NodeExecutionError(
        f"agent exceeded max_iterations ({config.max_iterations}) without producing a final answer"
    )

"""`agent` node: wraps a model connection with memory (conversation window)
and tools (other nodes it can call) and runs a genuine reasoning loop -- the
model itself decides which tools to call, in what order, and when it's
done (spec-008).

Tool calls are the one deliberate, disclosed exception to "every node's
inputs come from graph edges" -- see ADR-008. A tool node is invoked
directly via its own NodeDefinition.execute(), with the model-supplied
arguments as inputs, bypassing edge-based gathering entirely for that call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.connections.base import ToolDefinition, default_connection_registry
from backend.execution.errors import NodeExecutionError
from backend.execution.trace import TokenCost, TraceRecord
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec, effective_inputs
from backend.registry.base import default_registry as default_node_registry
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class AgentMemoryConfig(BaseModel):
    type: Literal["window"] = "window"
    """v1 supports only a simple in-memory window, scoped to a single run
    (spec-008 §3). A future "summary" type is explicitly deferred."""
    max_messages: int = Field(gt=0)


class AgentConfig(BaseModel):
    connection: str
    model: str
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    memory: AgentMemoryConfig
    max_iterations: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    """Not in spec §5's literal config sketch -- added because every real
    completion call needs a bound, matching llm_call's existing required
    field exactly."""


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
)
def execute_agent(ctx: ExecutionContext) -> NodeResult:
    config = AgentConfig.model_validate(ctx.node.config)

    profile = ctx.resources.get("connection_profiles", {}).get(config.connection)
    if profile is None:
        raise NodeExecutionError(
            f"No resolved connection profile for '{config.connection}' -- "
            "it should have been resolved before this run started"
        )
    connection_definition = default_connection_registry.get(profile.type)
    if connection_definition is None or connection_definition.complete_with_tools is None:
        raise NodeExecutionError(
            f"Connection '{config.connection}' (type '{profile.type}') does not support "
            "tool-calling, required by the 'agent' node type"
        )
    connection_config = connection_definition.config_model.model_validate(profile.config)

    nodes_by_id: dict[str, NodeSpec] = ctx.resources.get("nodes_by_id", {})
    tool_definitions: list[ToolDefinition] = []
    for tool_id in config.tools:
        tool_node = nodes_by_id.get(tool_id)
        if tool_node is None:
            raise NodeExecutionError(f"agent tool reference '{tool_id}' does not exist in the graph")
        tool_definitions.append(_tool_definition(tool_node))

    messages: list[dict[str, Any]] = [{"role": "user", "content": ctx.inputs["task"]}]
    child_traces: list[list[TraceRecord]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    for _ in range(config.max_iterations):
        windowed = _apply_memory_window(messages, config.memory.max_messages)
        try:
            response = connection_definition.complete_with_tools(
                connection_config,
                model=config.model,
                system_prompt=config.system_prompt,
                messages=windowed,
                tools=tool_definitions,
                max_tokens=config.max_tokens,
            )
        except Exception as e:
            raise NodeExecutionError(f"agent model call failed: {e}") from e

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

        for call in response.tool_calls:
            tool_node = nodes_by_id.get(call.name) if call.name in config.tools else None
            if tool_node is None:
                error_text = f"Unknown tool '{call.name}'. Available tools: {', '.join(config.tools)}"
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

            record, tool_text = _run_tool(call.name, call.arguments, tool_node, ctx.resources)
            child_traces.append([record])
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": tool_text}
            )

    raise NodeExecutionError(
        f"agent exceeded max_iterations ({config.max_iterations}) without producing a final answer"
    )

from __future__ import annotations

from typing import Any, Callable

import pytest
from pydantic import BaseModel

from backend.connections.base import (
    ConnectionTestResult,
    ToolCallRequest,
    ToolCallResponse,
    register_connection_type,
)
from backend.connections.store import ConnectionProfile
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.agent import _apply_memory_window, execute_agent
from backend.schema.models import NodeSpec

# --- a fake connection type, registered once for this whole test module --
# execute_agent looks up complete_with_tools via the real
# default_connection_registry (module-level, process-wide singleton, same
# as the node registry) -- so fake test types are registered once here
# under unique names, and each test controls behavior by reassigning
# `_current_impl` rather than re-registering (ConnectionRegistry.register()
# raises on a duplicate type_name).


class _FakeConnectionConfig(BaseModel):
    pass


_current_impl: Callable[..., ToolCallResponse] | None = None


def _dispatch(config: _FakeConnectionConfig, **kwargs: Any) -> ToolCallResponse:
    assert _current_impl is not None, "test forgot to set _current_impl"
    return _current_impl(config, **kwargs)


register_connection_type(
    "fake-tool-calling",
    category="local",
    config_model=_FakeConnectionConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    complete_with_tools=_dispatch,
)

register_connection_type(
    "fake-no-tool-support",
    category="local",
    config_model=_FakeConnectionConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    # complete_with_tools intentionally omitted (None) -- the capability
    # check under test.
)


@pytest.fixture(autouse=True)
def _reset_fake_impl():
    global _current_impl
    _current_impl = None
    yield
    _current_impl = None


# --- spec-012: model/memory/tools are sub-node edges now, not inline agent
# config -- these helpers build the separate `model`/`memory` node specs
# and thread everything through `resources["sub_nodes"]`
# ((root_id, slot_name) -> [connected sub-node ids]), mirroring exactly what
# engine.py's run_graph() itself populates from real `sub_node` edges.


def _agent_node(max_iterations: int = 10) -> NodeSpec:
    return NodeSpec(id="agent_1", type="agent", config={"max_iterations": max_iterations})


def _model_node(connection: str = "fake-conn", node_id: str = "model_1") -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type="model",
        config={"connection": connection, "model": "test-model", "system_prompt": "", "max_tokens": 100},
    )


def _memory_node(max_messages: int = 20, node_id: str = "memory_1") -> NodeSpec:
    return NodeSpec(id=node_id, type="memory", config={"type": "window", "max_messages": max_messages})


def _multiply_tool_node(node_id: str = "multiply_tool") -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type="code",
        config={"function_source": "def multiply(a, b):\n    return str(int(a) * int(b))\n"},
    )


def _ctx(
    node: NodeSpec,
    task: str,
    tool_nodes: list[NodeSpec],
    connection_type: str = "fake-tool-calling",
    model_node: NodeSpec | None = None,
    memory_node: NodeSpec | None = None,
    omit_connection_profile: bool = False,
    include_tool_group: bool = True,
) -> ExecutionContext:
    model_node = model_node if model_node is not None else _model_node()
    # spec-014: agent.tools resolves to zero or one `tool_group` sub-node
    # (cardinality="zero_or_one"), not tool nodes directly -- mirrors the
    # real shape every production graph now uses. include_tool_group=False
    # exercises the "agent has no tools at all" case directly (no "tools"
    # sub_nodes entry whatsoever, not merely an empty tool_group).
    all_nodes = [model_node, *tool_nodes]
    sub_nodes: dict[tuple[str, str], list[str]] = {(node.id, "model"): [model_node.id]}
    if include_tool_group:
        tool_group_node = NodeSpec(id="tool_group_1", type="tool_group", config={})
        all_nodes.append(tool_group_node)
        sub_nodes[(node.id, "tools")] = [tool_group_node.id]
        if tool_nodes:
            sub_nodes[(tool_group_node.id, "tools")] = [n.id for n in tool_nodes]
    if memory_node is not None:
        all_nodes.append(memory_node)
        sub_nodes[(node.id, "memory")] = [memory_node.id]

    connection_profiles = {}
    if not omit_connection_profile:
        connection_profiles[model_node.config["connection"]] = ConnectionProfile(
            name=model_node.config["connection"], type=connection_type, config={}
        )

    return ExecutionContext(
        node=node,
        inputs={"task": task},
        resources={
            "connection_profiles": connection_profiles,
            "nodes_by_id": {n.id: n for n in all_nodes},
            "sub_nodes": sub_nodes,
        },
    )


# --- memory window (pure helper) ------------------------------------------


def test_apply_memory_window_keeps_last_n():
    messages = [{"role": "user", "content": str(i)} for i in range(10)]
    windowed = _apply_memory_window(messages, 3)
    assert [m["content"] for m in windowed] == ["7", "8", "9"]


def test_apply_memory_window_noop_when_under_limit():
    messages = [{"role": "user", "content": "hi"}]
    assert _apply_memory_window(messages, 10) == messages


# --- core reasoning loop ----------------------------------------------------


def test_agent_calls_tool_and_incorporates_result():
    global _current_impl
    calls = []

    def impl(config, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call_1", name="multiply_tool", arguments={"a": "6", "b": "7"})],
            )
        return ToolCallResponse(text="The answer is 42.", tool_calls=[])

    _current_impl = impl

    node = _agent_node()
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "What is 6 times 7?", [tool_node])

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "The answer is 42."}
    assert result.child_traces is not None
    assert len(result.child_traces) == 1
    tool_record = result.child_traces[0][0]
    assert tool_record.node_id == "multiply_tool"
    assert tool_record.outputs == {"result": "42"}
    assert tool_record.error is None


def test_agent_direct_answer_no_tool_call():
    global _current_impl
    _current_impl = lambda config, **kwargs: ToolCallResponse(text="Paris", tool_calls=[])

    node = _agent_node()
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "What is the capital of France?", [tool_node])

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "Paris"}
    assert result.child_traces is None


def test_agent_runs_with_no_tool_group_connected_at_all():
    # tools is now cardinality="zero_or_one" (relaxed from "one") -- an
    # agent with no tool_group sub-node connected at all (not even an
    # empty one) must still run fine, simply with no tools available to
    # the model.
    global _current_impl
    _current_impl = lambda config, **kwargs: ToolCallResponse(text="4", tool_calls=[])

    node = _agent_node()
    ctx = _ctx(node, "What is 2 plus 2?", [], include_tool_group=False)

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "4"}
    assert result.child_traces is None


def test_max_iterations_stops_a_never_ending_tool_loop():
    global _current_impl
    call_count = 0

    def impl(config, **kwargs):
        nonlocal call_count
        call_count += 1
        return ToolCallResponse(
            text=None,
            tool_calls=[ToolCallRequest(id=f"call_{call_count}", name="multiply_tool", arguments={"a": "1", "b": "1"})],
        )

    _current_impl = impl

    node = _agent_node(max_iterations=3)
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "keep going forever", [tool_node])

    with pytest.raises(NodeExecutionError, match="max_iterations"):
        execute_agent(ctx)

    assert call_count == 3


def test_memory_window_truncates_within_a_single_run():
    global _current_impl
    call_count = 0
    observed_message_lengths = []

    def impl(config, **kwargs):
        nonlocal call_count
        call_count += 1
        observed_message_lengths.append(len(kwargs["messages"]))
        if call_count < 5:
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id=f"call_{call_count}", name="multiply_tool", arguments={"a": "1", "b": "1"})],
            )
        return ToolCallResponse(text="done", tool_calls=[])

    _current_impl = impl

    node = _agent_node(max_iterations=10)
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "start", [tool_node], memory_node=_memory_node(max_messages=3))

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "done"}
    # Every call after the window fills must never see more than max_messages.
    assert all(n <= 3 for n in observed_message_lengths[1:])
    assert max(observed_message_lengths) <= 3


def test_no_memory_sub_node_connected_keeps_full_history():
    """spec-012 §4: `memory` is a zero-or-one sub-node slot -- with none
    connected, no windowing happens at all (the sensible default)."""
    global _current_impl
    call_count = 0
    observed_message_lengths = []

    def impl(config, **kwargs):
        nonlocal call_count
        call_count += 1
        observed_message_lengths.append(len(kwargs["messages"]))
        if call_count < 5:
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id=f"call_{call_count}", name="multiply_tool", arguments={"a": "1", "b": "1"})],
            )
        return ToolCallResponse(text="done", tool_calls=[])

    _current_impl = impl

    node = _agent_node(max_iterations=10)
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "start", [tool_node])  # no memory_node passed

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "done"}
    # Unbounded: message count grows every call, never truncated.
    assert observed_message_lengths == sorted(observed_message_lengths)
    assert observed_message_lengths[-1] > 3


def test_malformed_tool_arguments_are_fed_back_and_model_self_corrects():
    global _current_impl
    call_count = 0

    def impl(config, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Missing required "b" -- code node will raise a KeyError,
            # wrapped as NodeExecutionError by code.py itself.
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call_1", name="multiply_tool", arguments={"a": "5"})],
            )
        if call_count == 2:
            assert "Error" in kwargs["messages"][-1]["content"]
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call_2", name="multiply_tool", arguments={"a": "5", "b": "3"})],
            )
        return ToolCallResponse(text="15", tool_calls=[])

    _current_impl = impl

    node = _agent_node(max_iterations=5)
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "5 times 3", [tool_node])

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "15"}
    assert result.child_traces is not None
    assert len(result.child_traces) == 2
    assert result.child_traces[0][0].error is not None
    assert result.child_traces[1][0].error is None


def test_hallucinated_tool_name_is_fed_back_and_model_self_corrects():
    global _current_impl
    call_count = 0

    def impl(config, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call_1", name="not_a_real_tool", arguments={})],
            )
        if call_count == 2:
            assert "Unknown tool" in kwargs["messages"][-1]["content"]
            return ToolCallResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call_2", name="multiply_tool", arguments={"a": "2", "b": "2"})],
            )
        return ToolCallResponse(text="4", tool_calls=[])

    _current_impl = impl

    node = _agent_node(max_iterations=5)
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "2 times 2", [tool_node])

    result = execute_agent(ctx)

    assert result.outputs == {"answer": "4"}
    assert result.child_traces[0][0].node_id == "not_a_real_tool"
    assert result.child_traces[0][0].error is not None


def test_connection_type_without_tool_calling_support_raises_clear_error():
    node = _agent_node()
    ctx = _ctx(node, "hello", [], connection_type="fake-no-tool-support")

    with pytest.raises(NodeExecutionError, match="does not support"):
        execute_agent(ctx)


def test_missing_connection_profile_raises_clear_error():
    node = _agent_node()
    ctx = _ctx(node, "hi", [], omit_connection_profile=True)

    with pytest.raises(NodeExecutionError):
        execute_agent(ctx)


def test_no_model_sub_node_connected_raises_clear_error():
    """Defensive-only path: validate_graph()'s check_sub_node_edges already
    guarantees exactly one connected `model` sub-node before a real run
    ever reaches execute_agent (cardinality="one") -- this exercises that
    defensive guard directly, the same "should have been resolved before
    this run started" precedent used throughout this codebase."""
    node = _agent_node()
    ctx = ExecutionContext(
        node=node,
        inputs={"task": "hi"},
        resources={"connection_profiles": {}, "nodes_by_id": {}, "sub_nodes": {}},
    )

    with pytest.raises(NodeExecutionError, match="expected exactly 1"):
        execute_agent(ctx)


def test_tool_schema_derivation_matches_referenced_code_node_params():
    global _current_impl
    captured_tools = []

    def impl(config, **kwargs):
        captured_tools.extend(kwargs["tools"])
        return ToolCallResponse(text="done", tool_calls=[])

    _current_impl = impl

    node = _agent_node()
    tool_node = _multiply_tool_node()
    ctx = _ctx(node, "hi", [tool_node])

    execute_agent(ctx)

    assert len(captured_tools) == 1
    tool_def = captured_tools[0]
    assert tool_def.name == "multiply_tool"
    assert set(tool_def.parameters["properties"].keys()) == {"a", "b"}
    assert set(tool_def.parameters["required"]) == {"a", "b"}

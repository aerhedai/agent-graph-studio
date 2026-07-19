from __future__ import annotations

import pytest

import backend.mcp.client as mcp_client_module
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.mcp.client import McpConnectionError, McpToolInfo
from backend.nodes.mcp_call import _resolve_mcp_slots, execute_mcp_call
from backend.schema.models import NodeSpec


def _node(config: dict) -> NodeSpec:
    return NodeSpec(id="n1", type="mcp_call", config=config)


def _base_config(**overrides) -> dict:
    config = {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"],
        "tool_name": "read_text_file",
    }
    config.update(overrides)
    return config


READ_TOOL = McpToolInfo(
    name="read_text_file",
    param_names=["path"],
    param_json_types={"path": "string"},
    required_names=frozenset({"path"}),
)

# Mirrors the real filesystem server's read_text_file tool, whose "tail"
# param is optional in its own JSON Schema (discovered via live testing --
# see mcp/client.py's _list_tools_async docstring).
READ_TOOL_WITH_OPTIONAL_PARAM = McpToolInfo(
    name="read_text_file",
    param_names=["path", "tail"],
    param_json_types={"path": "string", "tail": "integer"},
    required_names=frozenset({"path"}),
)


# --- _resolve_mcp_slots ---------------------------------------------------


def test_resolve_mcp_slots_success(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])

    resolved = _resolve_mcp_slots(_node(_base_config()))

    assert resolved is not None
    inputs, outputs = resolved
    assert [s.name for s in inputs] == ["path"]
    assert [s.name for s in outputs] == ["result"]


def test_resolve_mcp_slots_returns_none_on_connection_error(monkeypatch):
    def raise_connection_error(command, args):
        raise McpConnectionError("server unreachable")

    monkeypatch.setattr(mcp_client_module, "list_tools", raise_connection_error)

    assert _resolve_mcp_slots(_node(_base_config())) is None


def test_resolve_mcp_slots_returns_none_when_tool_not_found(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])

    node = _node(_base_config(tool_name="does_not_exist"))
    assert _resolve_mcp_slots(node) is None


def test_resolve_mcp_slots_returns_none_on_malformed_config():
    node = _node({"command": "npx"})  # missing required tool_name
    assert _resolve_mcp_slots(node) is None


def test_resolve_mcp_slots_marks_optional_tool_param_as_not_required(monkeypatch):
    # spec-014 follow-up: an MCP tool's optional properties are now exposed
    # as real (non-required) slots instead of being hidden from the graph
    # entirely -- the engine honors InputSlotSpec.required=False now.
    monkeypatch.setattr(
        mcp_client_module, "list_tools", lambda command, args: [READ_TOOL_WITH_OPTIONAL_PARAM]
    )

    resolved = _resolve_mcp_slots(_node(_base_config()))

    assert resolved is not None
    inputs, _ = resolved
    by_name = {s.name: s for s in inputs}
    assert by_name["path"].required is True
    assert by_name["tail"].required is False


# --- execute_mcp_call ------------------------------------------------------


def test_execute_mcp_call_success_with_approval(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])
    monkeypatch.setattr(
        mcp_client_module,
        "call_tool",
        lambda command, args, tool_name, arguments, env=None: "hello from a test file",
    )

    ctx = ExecutionContext(
        node=_node(_base_config()),
        inputs={"path": "/tmp/sandbox/sample.txt"},
        resources={"approval_prompt": lambda tool_name, arguments: True},
    )

    result = execute_mcp_call(ctx)

    assert result.outputs == {"result": "hello from a test file"}
    assert result.side_effect is True


def test_execute_mcp_call_declined_raises_and_does_not_call_tool(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])

    called = []
    monkeypatch.setattr(
        mcp_client_module,
        "call_tool",
        lambda *a, **kw: called.append(1) or "should not happen",
    )

    ctx = ExecutionContext(
        node=_node(_base_config()),
        inputs={"path": "/tmp/sandbox/sample.txt"},
        resources={"approval_prompt": lambda tool_name, arguments: False},
    )

    with pytest.raises(NodeExecutionError):
        execute_mcp_call(ctx)

    assert called == []


def test_execute_mcp_call_skips_approval_when_require_approval_false(monkeypatch):
    # No approval_prompt injected -- if this actually blocked on input(), the
    # test would hang, so this also proves require_approval=False bypasses
    # the gate entirely.
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])
    monkeypatch.setattr(
        mcp_client_module,
        "call_tool",
        lambda command, args, tool_name, arguments, env=None: "ok",
    )

    ctx = ExecutionContext(
        node=_node(_base_config(require_approval=False)),
        inputs={"path": "/tmp/sandbox/sample.txt"},
        resources={},
    )

    result = execute_mcp_call(ctx)
    assert result.outputs == {"result": "ok"}


def test_execute_mcp_call_unknown_tool_raises(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])

    ctx = ExecutionContext(
        node=_node(_base_config(tool_name="does_not_exist", require_approval=False)),
        inputs={},
        resources={},
    )

    with pytest.raises(NodeExecutionError):
        execute_mcp_call(ctx)


def test_execute_mcp_call_wraps_connection_failure(monkeypatch):
    def raise_connection_error(command, args):
        raise McpConnectionError("server unreachable")

    monkeypatch.setattr(mcp_client_module, "list_tools", raise_connection_error)

    ctx = ExecutionContext(
        node=_node(_base_config(require_approval=False)), inputs={}, resources={}
    )

    with pytest.raises(NodeExecutionError):
        execute_mcp_call(ctx)


def test_execute_mcp_call_injects_credential_as_uppercased_env_var(monkeypatch):
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [READ_TOOL])

    captured_env = {}

    def fake_call_tool(command, args, tool_name, arguments, env=None):
        captured_env.update(env or {})
        return "ok"

    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)

    ctx = ExecutionContext(
        node=_node(_base_config(credential_ref="api_key", require_approval=False)),
        inputs={"path": "/tmp/sandbox/sample.txt"},
        resources={"api_key": "secret123"},
    )

    execute_mcp_call(ctx)

    assert captured_env == {"API_KEY": "secret123"}


def test_execute_mcp_call_omits_unwired_optional_param_from_arguments(monkeypatch):
    monkeypatch.setattr(
        mcp_client_module, "list_tools", lambda command, args: [READ_TOOL_WITH_OPTIONAL_PARAM]
    )

    captured_arguments = {}

    def fake_call_tool(command, args, tool_name, arguments, env=None):
        captured_arguments.update(arguments)
        return "ok"

    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)

    ctx = ExecutionContext(
        # "tail" deliberately absent from inputs -- as if the engine left an
        # unwired optional slot out of gathered_inputs.
        node=_node(_base_config(require_approval=False)),
        inputs={"path": "/tmp/sandbox/sample.txt"},
        resources={},
    )

    execute_mcp_call(ctx)

    assert captured_arguments == {"path": "/tmp/sandbox/sample.txt"}


def test_execute_mcp_call_coerces_input_types(monkeypatch):
    tool = McpToolInfo(
        name="search",
        param_names=["path", "limit"],
        param_json_types={"path": "string", "limit": "integer"},
    )
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [tool])

    captured_arguments = {}

    def fake_call_tool(command, args, tool_name, arguments, env=None):
        captured_arguments.update(arguments)
        return "ok"

    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)

    ctx = ExecutionContext(
        node=_node(_base_config(tool_name="search", require_approval=False)),
        inputs={"path": "/tmp/sandbox", "limit": "5"},
        resources={},
    )

    execute_mcp_call(ctx)

    assert captured_arguments == {"path": "/tmp/sandbox", "limit": 5}

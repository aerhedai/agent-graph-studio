from __future__ import annotations

import backend.mcp.client as mcp_client_module
from backend.execution.engine import run_graph
from backend.llm.client import LLMResponse
from backend.mcp.client import McpToolInfo
from backend.schema.loader import parse_graph_json
from conftest import load_fixture_text
from fakes import FakeLLMClient


def _load(name: str):
    return parse_graph_json(load_fixture_text(name))


def test_linear_graph_executes_and_returns_llm_response():
    graph = _load("valid_linear.json")
    client = FakeLLMClient(response=LLMResponse(text="mocked reply", input_tokens=12, output_tokens=8))

    run_result = run_graph(graph, resources={"llm_client": client})

    assert run_result.result == {"n3": "mocked reply"}
    llm_trace = next(t for t in run_result.trace if t.node_id == "n2")
    assert llm_trace.error is None
    assert llm_trace.outputs == {"response": "mocked reply"}
    assert llm_trace.token_cost.input_tokens == 12
    assert llm_trace.token_cost.output_tokens == 8
    assert client.calls[0]["prompt"] == "hello"


def test_branching_graph_fires_true_branch_only():
    graph = _load("valid_branching.json")  # text_input value = "yes", condition contains('yes')

    run_result = run_graph(graph)

    traced_ids = {t.node_id for t in run_result.trace}
    assert "n3" in traced_ids  # true-branch text_output executed
    assert "n4" not in traced_ids  # false-branch text_output did not execute
    assert run_result.result == {"n3": "yes"}


def test_branching_graph_fires_false_branch_only():
    graph = _load("valid_branching.json")
    graph.nodes[0].config["value"] = "no"

    run_result = run_graph(graph)

    traced_ids = {t.node_id for t in run_result.trace}
    assert "n4" in traced_ids
    assert "n3" not in traced_ids
    assert run_result.result == {"n4": "no"}


def test_every_node_execution_produces_complete_trace_record():
    graph = _load("valid_linear.json")
    client = FakeLLMClient(response=LLMResponse(text="hi", input_tokens=1, output_tokens=2))

    run_result = run_graph(graph, resources={"llm_client": client})

    assert len(run_result.trace) == 3
    for record in run_result.trace:
        assert record.run_id
        assert record.node_id
        assert record.node_type
        assert record.started_at
        assert record.finished_at
        assert record.error is None

    llm_trace = next(t for t in run_result.trace if t.node_type == "llm_call")
    assert llm_trace.token_cost.input_tokens == 1
    assert llm_trace.token_cost.output_tokens == 2


def test_uppercase_text_node_runs_through_the_engine_untouched():
    # No llm_client/resources needed -- proves a plain text-in/text-out node
    # type plugs into run_graph() with zero engine changes.
    graph = _load("uppercase.json")

    run_result = run_graph(graph)

    assert run_result.result == {"n3": "HELLO WORLD"}


def test_code_node_dynamic_schema_runs_through_the_engine():
    # code node's input port ("text") is resolved per-instance from its own
    # function_source, not from a fixed schema -- proves the generic
    # resolve_slots mechanism works end to end through the real engine.
    graph = _load("code_node.json")

    run_result = run_graph(graph)

    assert run_result.result == {"n3": "HELLO!"}


def test_mcp_call_node_runs_through_the_engine(monkeypatch):
    # mcp_call's "path" port is resolved per-instance from the (mocked)
    # server's real tool schema -- proves resolve_slots + the side_effect
    # trace field work end to end through the real engine, with zero
    # engine.py node-type-specific branching.
    tool = McpToolInfo(
        name="read_text_file", param_names=["path"], param_json_types={"path": "string"}
    )
    monkeypatch.setattr(mcp_client_module, "list_tools", lambda command, args: [tool])
    monkeypatch.setattr(
        mcp_client_module,
        "call_tool",
        lambda command, args, tool_name, arguments, env=None: "hello from a test file",
    )

    graph = _load("mcp_call.json")

    run_result = run_graph(graph)

    assert run_result.result == {"n3": "hello from a test file"}
    mcp_trace = next(t for t in run_result.trace if t.node_id == "n2")
    assert mcp_trace.side_effect is True
    assert mcp_trace.error is None

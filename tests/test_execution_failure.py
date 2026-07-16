from __future__ import annotations

from backend.execution.engine import run_graph
from backend.llm.client import LLMResponse
from backend.schema.loader import parse_graph_json
from conftest import load_fixture_text
from fakes import FakeLLMClient, FailingLLMClient


def _load(name: str):
    return parse_graph_json(load_fixture_text(name))


def test_node_failure_captured_in_trace_and_downstream_skipped(registered_test_connection):
    graph = _load("valid_linear.json")
    client = FailingLLMClient(RuntimeError("simulated failure"))

    run_result = run_graph(graph, resources={"connections": {registered_test_connection: client}})

    llm_trace = next(t for t in run_result.trace if t.node_id == "n2")
    assert llm_trace.error is not None
    assert "simulated failure" in llm_trace.error

    traced_ids = {t.node_id for t in run_result.trace}
    assert "n3" not in traced_ids  # downstream text_output did not execute
    assert run_result.result == {}


def test_independent_branch_continues_after_sibling_failure(registered_test_connection):
    graph = parse_graph_json(
        """
        {
          "version": "0.1",
          "nodes": [
            {"id": "root", "type": "text_input", "config": {"value": "hi"}},
            {"id": "fail_call", "type": "llm_call", "config": {"connection": "test-connection", "model": "model-fail", "max_tokens": 10}},
            {"id": "fail_out", "type": "text_output", "config": {}},
            {"id": "ok_call", "type": "llm_call", "config": {"connection": "test-connection", "model": "model-ok", "max_tokens": 10}},
            {"id": "ok_out", "type": "text_output", "config": {}}
          ],
          "edges": [
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "fail_call", "slot": "prompt"}},
            {"from": {"node": "fail_call", "slot": "response"}, "to": {"node": "fail_out", "slot": "text"}},
            {"from": {"node": "root", "slot": "text"}, "to": {"node": "ok_call", "slot": "prompt"}},
            {"from": {"node": "ok_call", "slot": "response"}, "to": {"node": "ok_out", "slot": "text"}}
          ]
        }
        """
    )

    def on_complete(*, model, system_prompt, prompt, max_tokens):
        if model == "model-fail":
            raise RuntimeError("simulated failure")
        return LLMResponse(text="ok reply", input_tokens=1, output_tokens=1)

    client = FakeLLMClient(on_complete=on_complete)

    run_result = run_graph(graph, resources={"connections": {registered_test_connection: client}})

    fail_trace = next(t for t in run_result.trace if t.node_id == "fail_call")
    assert fail_trace.error is not None

    traced_ids = {t.node_id for t in run_result.trace}
    assert "fail_out" not in traced_ids  # downstream of the failed node skipped
    assert "ok_out" in traced_ids  # independent sibling branch still executed

    assert run_result.result == {"ok_out": "ok reply"}

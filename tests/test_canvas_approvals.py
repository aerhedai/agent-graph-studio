"""spec-019 (addendum): approval-gated tool calls answered from the canvas
instead of a terminal input() prompt -- backend/execution/approvals.py,
wired into backend/api/runs.py::execute_run and exposed via
GET /runs/{run_id}'s pending_approvals field plus
POST /runs/{run_id}/approvals/{approval_id}.
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api import runs as runs_module
from backend.api.app import app
from backend.connections.resolver import resolve_connection_profiles, resolve_connections
from backend.execution import approvals
from backend.mcp.client import McpToolInfo
from backend.schema.loader import parse_graph_json

# spec-017: must match tests/conftest.py's TEST_API_KEY.
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _start_run_in_background(graph_dict: dict) -> str:
    """Starts a real run exactly like POST /runs does internally
    (backend/api/app.py::submit_run), but bypasses actually going through
    TestClient for *submission* specifically: Starlette's TestClient runs
    BackgroundTasks synchronously as part of the request/response cycle, so
    `client.post("/runs", ...)` would itself block until the whole run --
    including any approval it blocks on -- finishes, deadlocking against
    the later call that's supposed to resolve that same approval. This is
    a TestClient-only quirk (a real deployed server's HTTP response returns
    immediately, exactly as documented) -- GET/POST calls that don't carry
    a BackgroundTask (polling, resolving an approval) behave normally
    through the same TestClient and are used as such below."""
    import json as _json

    graph = parse_graph_json(_json.dumps(graph_dict))
    resolved_connections = resolve_connections(graph)
    resolved_profiles = resolve_connection_profiles(graph)
    run_id = str(uuid4())
    runs_module.create_run(run_id, graph_id=None, trigger_source="manual")
    thread = threading.Thread(
        target=runs_module.execute_run,
        args=(run_id, graph, {"connections": resolved_connections, "connection_profiles": resolved_profiles}),
        daemon=True,
    )
    thread.start()
    return run_id

SEND_TOOL = McpToolInfo(
    name="send_message",
    param_names=["text"],
    param_json_types={"text": "string"},
    required_names=frozenset({"text"}),
)


# --- backend/execution/approvals.py, direct unit tests ----------------------


def test_request_approval_blocks_until_resolved_then_returns_decision():
    results = []

    def waiter():
        results.append(approvals.request_approval("run-1", "some_tool", {"a": "b"}))

    thread = threading.Thread(target=waiter)
    thread.start()

    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-1")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    assert approval_id is not None

    assert approvals.resolve_approval(approval_id, True) is True
    thread.join(timeout=2)
    assert results == [True]


def test_request_approval_returns_false_decision_when_declined():
    results = []

    def waiter():
        results.append(approvals.request_approval("run-2", "some_tool", {}))

    thread = threading.Thread(target=waiter)
    thread.start()
    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-2")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    approvals.resolve_approval(approval_id, False)
    thread.join(timeout=2)
    assert results == [False]


def test_resolve_approval_returns_false_for_unknown_id():
    assert approvals.resolve_approval("does-not-exist", True) is False


def test_list_pending_for_run_only_returns_that_runs_approvals():
    thread = threading.Thread(target=lambda: approvals.request_approval("run-a", "t", {}))
    thread.start()
    deadline = time.time() + 2
    while time.time() < deadline and not approvals.list_pending_for_run("run-a"):
        time.sleep(0.02)

    assert approvals.list_pending_for_run("run-b") == []
    assert len(approvals.list_pending_for_run("run-a")) == 1

    for p in approvals.list_pending_for_run("run-a"):
        approvals.resolve_approval(p.approval_id, True)
    thread.join(timeout=2)


# --- "remember for this run" scoping ----------------------------------------


def test_remembered_decision_answers_immediately_without_a_pending_approval():
    # First call: resolve normally, with remember=True.
    results = []
    thread = threading.Thread(
        target=lambda: results.append(approvals.request_approval("run-remember-1", "some_tool", {}))
    )
    thread.start()
    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-remember-1")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    approvals.resolve_approval(approval_id, True, remember=True)
    thread.join(timeout=2)
    assert results == [True]

    # Second call, same run + tool name: must return immediately, with no
    # pending approval ever created (not just resolved fast).
    second_result = approvals.request_approval("run-remember-1", "some_tool", {"different": "args"})
    assert second_result is True
    assert approvals.list_pending_for_run("run-remember-1") == []

    approvals.clear_remembered_for_run("run-remember-1")


def test_remembered_decline_also_auto_resolves_subsequent_calls():
    thread = threading.Thread(
        target=lambda: approvals.request_approval("run-remember-2", "risky_tool", {})
    )
    thread.start()
    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-remember-2")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    approvals.resolve_approval(approval_id, False, remember=True)
    thread.join(timeout=2)

    assert approvals.request_approval("run-remember-2", "risky_tool", {}) is False
    approvals.clear_remembered_for_run("run-remember-2")


def test_remember_is_scoped_to_one_run_not_shared_across_runs():
    thread = threading.Thread(
        target=lambda: approvals.request_approval("run-remember-3a", "shared_tool_name", {})
    )
    thread.start()
    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-remember-3a")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    approvals.resolve_approval(approval_id, True, remember=True)
    thread.join(timeout=2)

    # A different run_id, same tool name -- must NOT be auto-answered by
    # run-remember-3a's remembered decision; must genuinely block again.
    results = []
    thread2 = threading.Thread(
        target=lambda: results.append(approvals.request_approval("run-remember-3b", "shared_tool_name", {}))
    )
    thread2.start()
    deadline = time.time() + 2
    approval_id_2 = None
    while time.time() < deadline and approval_id_2 is None:
        pending = approvals.list_pending_for_run("run-remember-3b")
        if pending:
            approval_id_2 = pending[0].approval_id
        else:
            time.sleep(0.02)
    assert approval_id_2 is not None  # genuinely blocked, not auto-answered
    approvals.resolve_approval(approval_id_2, True)
    thread2.join(timeout=2)

    approvals.clear_remembered_for_run("run-remember-3a")


def test_clear_remembered_for_run_removes_only_that_runs_entries():
    thread_a = threading.Thread(
        target=lambda: approvals.request_approval("run-clear-a", "tool_x", {})
    )
    thread_b = threading.Thread(
        target=lambda: approvals.request_approval("run-clear-b", "tool_x", {})
    )
    thread_a.start()
    thread_b.start()
    deadline = time.time() + 2
    while time.time() < deadline and (
        not approvals.list_pending_for_run("run-clear-a") or not approvals.list_pending_for_run("run-clear-b")
    ):
        time.sleep(0.02)
    approvals.resolve_approval(approvals.list_pending_for_run("run-clear-a")[0].approval_id, True, remember=True)
    approvals.resolve_approval(approvals.list_pending_for_run("run-clear-b")[0].approval_id, True, remember=True)
    thread_a.join(timeout=2)
    thread_b.join(timeout=2)

    approvals.clear_remembered_for_run("run-clear-a")

    # run-clear-a's memory is gone -- a fresh call genuinely blocks again.
    results = []
    thread_a2 = threading.Thread(
        target=lambda: results.append(approvals.request_approval("run-clear-a", "tool_x", {}))
    )
    thread_a2.start()
    deadline = time.time() + 2
    approval_id = None
    while time.time() < deadline and approval_id is None:
        pending = approvals.list_pending_for_run("run-clear-a")
        if pending:
            approval_id = pending[0].approval_id
        else:
            time.sleep(0.02)
    assert approval_id is not None
    approvals.resolve_approval(approval_id, True)
    thread_a2.join(timeout=2)

    # run-clear-b's memory must be untouched.
    assert approvals.request_approval("run-clear-b", "tool_x", {}) is True
    approvals.clear_remembered_for_run("run-clear-b")


# --- API integration: full run -> pending -> resolve -> completes ----------


def _wait_until(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"condition never became true, last value: {last}")


def test_untrusted_generated_node_blocks_run_until_canvas_approval(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(
        generated_nodes_module, "call_tool", lambda config, tool_name, args: "message sent"
    )

    create = client.post(
        "/connections",
        json={
            "name": "approval-demo-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake", "args": []},
        },
    )
    assert create.status_code == 201
    tool_type = "mcp__approval-demo-server__send_message"

    # This test targets the generated node directly (no agent/tool-calling
    # involved, which would need a real LLM) -- that's what exercises the
    # approval gate under test; text_input -> tool -> text_output.
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hello"}},
            {"id": "tool_1", "type": tool_type, "config": {}},
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "tool_1", "slot": "text"}},
            {"from": {"node": "tool_1", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }

    run_id = _start_run_in_background(graph)

    body = _wait_until(
        lambda: (lambda r: r if r.get("pending_approvals") else None)(client.get(f"/runs/{run_id}").json())
    )
    assert len(body["pending_approvals"]) == 1
    pending = body["pending_approvals"][0]
    assert pending["tool_name"] == "send_message"
    assert pending["arguments"] == {"text": "hello"}
    assert body["status"] == "running"

    resolve = client.post(f"/runs/{run_id}/approvals/{pending['approval_id']}", json={"approved": True})
    assert resolve.status_code == 200

    final = _wait_until(lambda: (lambda r: r if r["status"] != "running" else None)(client.get(f"/runs/{run_id}").json()))
    assert final["status"] == "completed"
    assert final["result"] == {"out": "message sent"}


def test_declining_approval_fails_the_node_but_the_run_still_completes(monkeypatch):
    """Matches this engine's established failure semantics (ADR-005): a
    node's own failure never crashes the whole run -- it's recorded on
    that node's trace, and its downstream consumers are silently skipped
    (never scheduled, since their required input never arrives). The run
    itself still reports "completed", just with an incomplete result."""
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(generated_nodes_module, "call_tool", lambda config, tool_name, args: "unused")

    client.post(
        "/connections",
        json={
            "name": "decline-demo-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake", "args": []},
        },
    )
    tool_type = "mcp__decline-demo-server__send_message"
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hello"}},
            {"id": "tool_1", "type": tool_type, "config": {}},
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "tool_1", "slot": "text"}},
            {"from": {"node": "tool_1", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }
    run_id = _start_run_in_background(graph)

    body = _wait_until(
        lambda: (lambda r: r if r.get("pending_approvals") else None)(client.get(f"/runs/{run_id}").json())
    )
    approval_id = body["pending_approvals"][0]["approval_id"]
    client.post(f"/runs/{run_id}/approvals/{approval_id}", json={"approved": False})

    final = _wait_until(lambda: (lambda r: r if r["status"] != "running" else None)(client.get(f"/runs/{run_id}").json()))
    assert final["status"] == "completed"
    assert final["result"] == {}  # "out" never ran -- tool_1 failed, blocking its one consumer
    tool_trace = next(t for t in final["trace"] if t["node_id"] == "tool_1")
    assert tool_trace["error"] is not None
    assert "declined" in tool_trace["error"].lower()


def test_resolving_unknown_approval_id_returns_404():
    response = client.post("/runs/some-run/approvals/does-not-exist", json={"approved": True})
    assert response.status_code == 404


def test_resolve_endpoint_remember_flag_is_forwarded_and_takes_effect(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(generated_nodes_module, "call_tool", lambda config, tool_name, args: "sent")

    client.post(
        "/connections",
        json={
            "name": "remember-endpoint-demo-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake", "args": []},
        },
    )
    tool_type = "mcp__remember-endpoint-demo-server__send_message"
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hello"}},
            {"id": "tool_1", "type": tool_type, "config": {}},
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "tool_1", "slot": "text"}},
            {"from": {"node": "tool_1", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }
    run_id = _start_run_in_background(graph)
    body = _wait_until(
        lambda: (lambda r: r if r.get("pending_approvals") else None)(client.get(f"/runs/{run_id}").json())
    )
    approval_id = body["pending_approvals"][0]["approval_id"]

    resolve = client.post(
        f"/runs/{run_id}/approvals/{approval_id}", json={"approved": True, "remember": True}
    )
    assert resolve.status_code == 200

    # The endpoint's remember=True must have reached approvals.py's actual
    # remembered-decisions store immediately -- checked *before* the run
    # finishes, since execute_run's own completion cleanup
    # (clear_remembered_for_run) deliberately wipes this once the run is
    # done (this scoping is a feature -- see approvals.py's own tests --
    # not something to race against here).
    assert approvals.request_approval(run_id, "send_message", {"text": "anything"}) is True
    assert approvals.list_pending_for_run(run_id) == []

    _wait_until(lambda: (lambda r: r if r["status"] != "running" else None)(client.get(f"/runs/{run_id}").json()))


def test_trusted_generated_node_run_has_no_pending_approvals(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [SEND_TOOL])
    monkeypatch.setattr(generated_nodes_module, "call_tool", lambda config, tool_name, args: "sent instantly")

    client.post(
        "/connections",
        json={
            "name": "trusted-demo-server",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "fake", "args": [], "trusted": True},
        },
    )
    tool_type = "mcp__trusted-demo-server__send_message"
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "in", "type": "text_input", "config": {"value": "hello"}},
            {"id": "tool_1", "type": tool_type, "config": {}},
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "in", "slot": "text"}, "to": {"node": "tool_1", "slot": "text"}},
            {"from": {"node": "tool_1", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }
    run_id = _start_run_in_background(graph)
    final = _wait_until(lambda: (lambda r: r if r["status"] != "running" else None)(client.get(f"/runs/{run_id}").json()))
    assert final["status"] == "completed"
    assert final["result"] == {"out": "sent instantly"}

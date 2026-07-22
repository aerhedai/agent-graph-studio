from __future__ import annotations

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from backend.api import runs as runs_module
from backend.api.app import app
from backend.storage import runs_store
from backend.triggers import registry as trigger_registry
from backend.triggers import runner as trigger_runner

# spec-017: must match tests/conftest.py's TEST_API_KEY (the isolated_api_key
# fixture sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value).
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _linear_graph(value: str = "hello world") -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": value}},
            {"id": "n2", "type": "uppercase_text", "config": {}},
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "text"}},
            {"from": {"node": "n2", "slot": "text"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }


LOOP_SUB_GRAPH = {
    "version": "0.1",
    "nodes": [
        {"id": "entry", "type": "text_input", "config": {"value": ""}},
        {
            "id": "step",
            "type": "code",
            "config": {"function_source": "def add_bang(text):\n    return text + '!'\n"},
        },
        {"id": "out", "type": "text_output", "config": {}},
    ],
    "edges": [
        {"from": {"node": "entry", "slot": "text"}, "to": {"node": "step", "slot": "text"}},
        {"from": {"node": "step", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
    ],
}


def _loop_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": "a"}},
            {
                "id": "loop1",
                "type": "loop",
                "config": {"sub_graph": LOOP_SUB_GRAPH, "max_iterations": 2},
            },
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "loop1", "slot": "value"}},
            {"from": {"node": "loop1", "slot": "value"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }


def _schedule_graph(cron: str = "*/5 * * * *") -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "schedule_trigger", "config": {"cron": cron}},
            {
                "id": "echo",
                "type": "code",
                "config": {"function_source": "def run(ts):\n    return f'fired at {ts}'"},
            },
        ],
        "edges": [{"from": {"node": "trigger", "slot": "fired_at"}, "to": {"node": "echo", "slot": "ts"}}],
    }


def _webhook_graph() -> dict:
    # spec-012: webhook_trigger is a cluster root now -- a generic_adapter
    # sub-node (today's exact SPEC-009 passthrough behavior) must be wired
    # in via a sub_node edge for this to behave identically to before.
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
            {
                "id": "parse",
                "type": "code",
                "config": {
                    "function_source": "def run(raw):\n    return __import__('json').loads(raw)['name']"
                },
            },
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
            {"from": {"node": "trigger", "slot": "payload"}, "to": {"node": "parse", "slot": "raw"}},
        ],
    }


def _wait_for_run(run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    assert body is not None
    return body


def _deactivate_quietly(graph_id: str) -> None:
    client.post(f"/graphs/{graph_id}/deactivate")


def test_manual_run_is_persisted_and_retrievable_via_get_run_after_removed_from_memory():
    submit = client.post("/runs", json=_linear_graph())
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]

    live = _wait_for_run(run_id)
    assert live["status"] == "completed"

    # Simulate "the process restarted" -- the in-memory dict is gone, only
    # SQLite has this run_id now.
    del runs_module._runs[run_id]

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"] == {"n3": "HELLO WORLD"}
    assert len(body["trace"]) == 3
    assert body["running_node_ids"] == []


def test_manual_run_records_graph_id_when_provided():
    submit = client.post("/runs?graph_id=my-graph", json=_linear_graph())
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]
    _wait_for_run(run_id)

    row = runs_store.get_run_record(run_id)
    assert row is not None
    assert row.graph_id == "my-graph"
    assert row.trigger_source == "manual"


def test_manual_run_without_graph_id_stores_null():
    submit = client.post("/runs", json=_linear_graph())
    run_id = submit.json()["run_id"]
    _wait_for_run(run_id)

    row = runs_store.get_run_record(run_id)
    assert row is not None
    assert row.graph_id is None


def test_triggered_schedule_run_persisted_with_trigger_source_schedule():
    graph_id = "persist-sched-graph"
    try:
        client.post(f"/graphs/{graph_id}/activate", json=_schedule_graph())
        run_id = trigger_runner.fire(graph_id, "trigger")

        deadline = time.time() + 5
        row = None
        while time.time() < deadline:
            row = runs_store.get_run_record(run_id)
            if row is not None and row.status != "running":
                break
            time.sleep(0.05)

        assert row is not None
        assert row.status == "completed"
        assert row.graph_id == graph_id
        assert row.trigger_source == "schedule"
    finally:
        _deactivate_quietly(graph_id)


def test_triggered_webhook_run_persisted_with_trigger_source_webhook():
    graph_id = "persist-webhook-graph"
    try:
        activate = client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        endpoint = activate.json()["triggers"][0]["endpoint_or_schedule"]

        fire = client.post(endpoint, json={"name": "spec-010"})
        assert fire.status_code == 200
        run_id = fire.json()["run_id"]
        _wait_for_run(run_id)

        row = runs_store.get_run_record(run_id)
        assert row is not None
        assert row.status == "completed"
        assert row.graph_id == graph_id
        assert row.trigger_source == "webhook"
    finally:
        _deactivate_quietly(graph_id)


def test_get_runs_filters_by_graph_id_status_and_trigger_source_and_paginates():
    # Seed directly through the store layer -- this test is about the list
    # endpoint's SQL filtering/pagination, not about exercising every real
    # run path again (that's covered end-to-end by the tests above).
    seed = [
        ("r-1", "graph-a", "completed", "manual", "2026-01-01T00:00:00Z"),
        ("r-2", "graph-a", "failed", "manual", "2026-01-01T00:01:00Z"),
        ("r-3", "graph-a", "completed", "schedule", "2026-01-01T00:02:00Z"),
        ("r-4", "graph-b", "completed", "webhook", "2026-01-01T00:03:00Z"),
        ("r-5", "graph-b", "completed", "manual", "2026-01-01T00:04:00Z"),
    ]
    for run_id, graph_id, status, trigger_source, started_at in seed:
        runs_store.create_run_record(run_id, graph_id, trigger_source, started_at)
        runs_store.complete_run_record(run_id, status, started_at, json.dumps({"result": {}, "trace": []}), None)

    by_graph = client.get("/runs", params={"graph_id": "graph-a"}).json()
    assert by_graph["total"] == 3
    assert {r["run_id"] for r in by_graph["runs"]} == {"r-1", "r-2", "r-3"}

    by_status = client.get("/runs", params={"status": "failed"}).json()
    assert by_status["total"] == 1
    assert by_status["runs"][0]["run_id"] == "r-2"

    by_source = client.get("/runs", params={"trigger_source": "webhook"}).json()
    assert by_source["total"] == 1
    assert by_source["runs"][0]["run_id"] == "r-4"

    combined = client.get("/runs", params={"graph_id": "graph-a", "trigger_source": "manual"}).json()
    assert combined["total"] == 2
    assert {r["run_id"] for r in combined["runs"]} == {"r-1", "r-2"}

    page1 = client.get("/runs", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/runs", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 5
    assert page2["total"] == 5
    assert len(page1["runs"]) == 2
    assert len(page2["runs"]) == 2
    assert {r["run_id"] for r in page1["runs"]}.isdisjoint({r["run_id"] for r in page2["runs"]})

    # List summaries never include the full trace/result blob.
    assert "trace" not in by_graph["runs"][0]
    assert "result" not in by_graph["runs"][0]


def test_nested_child_traces_survive_persistence_for_loop_node():
    submit = client.post("/runs", json=_loop_graph())
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]
    live = _wait_for_run(run_id)
    assert live["status"] == "completed"

    loop_trace_live = next(t for t in live["trace"] if t["node_id"] == "loop1")
    assert loop_trace_live["child_traces"] is not None
    assert len(loop_trace_live["child_traces"]) == 2  # max_iterations

    # Force the SQLite fallback path, same as the restart-durability test.
    del runs_module._runs[run_id]
    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    loop_trace_persisted = next(t for t in body["trace"] if t["node_id"] == "loop1")
    assert loop_trace_persisted["child_traces"] == loop_trace_live["child_traces"]


def test_run_status_response_includes_empty_active_sub_node_ids_for_non_agent_graph():
    submit = client.post("/runs", json=_linear_graph())
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]

    live = _wait_for_run(run_id)
    assert live["active_sub_node_ids"] == []

    # Also present (and empty) on the SQLite-fallback path, same as
    # running_node_ids -- a historical run has nothing live to report.
    del runs_module._runs[run_id]
    response = client.get(f"/runs/{run_id}")
    assert response.json()["active_sub_node_ids"] == []


def test_on_sub_node_activity_ref_counts_a_sub_node_shared_across_two_concurrent_activations():
    """A sub-node id can be marked active by two independent callers at
    once (e.g. the same `model` wired into two agents executing
    concurrently within one run) -- a naive add/remove would let one
    "inactive" transition wipe an id the other caller is still using.
    Unit-tests `_make_on_sub_node_activity` directly against a bare
    RunRecord, without going through a real run."""
    record = runs_module.RunRecord(run_id="r-ref-count")
    on_activity = runs_module._make_on_sub_node_activity(record)

    on_activity("agent_1", "model_1", True)
    on_activity("agent_2", "model_1", True)  # shared model, second caller
    assert record.active_sub_node_ids == ["model_1"]

    on_activity("agent_1", "model_1", False)
    assert record.active_sub_node_ids == ["model_1"]  # still in use by agent_2

    on_activity("agent_2", "model_1", False)
    assert record.active_sub_node_ids == []

    # Distinct ids don't interfere with each other's counts.
    on_activity("agent_1", "model_1", True)
    on_activity("agent_1", "tool_1", True)
    assert set(record.active_sub_node_ids) == {"model_1", "tool_1"}
    on_activity("agent_1", "tool_1", False)
    assert record.active_sub_node_ids == ["model_1"]


def test_sqlite_write_failure_does_not_break_the_run(monkeypatch):
    # Patch _connect (the actual sqlite3 boundary), not create_run_record/
    # complete_run_record themselves -- this exercises the real try/except
    # inside those functions (spec-010 §6: "SQLite write failures... do not
    # crash or silently swallow a graph run's actual execution"), rather than
    # bypassing the very protection being tested.
    def _boom_connect(*args, **kwargs):
        raise sqlite3.OperationalError("simulated disk-full")

    monkeypatch.setattr(runs_store, "_connect", _boom_connect)

    submit = client.post("/runs", json=_linear_graph())
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]

    live = _wait_for_run(run_id)
    assert live["status"] == "completed"
    assert live["result"] == {"n3": "HELLO WORLD"}

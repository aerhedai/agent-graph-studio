from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.api.app import app

# spec-017: must match tests/conftest.py's TEST_API_KEY (the isolated_api_key
# fixture sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value).
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def test_node_types_lists_every_registered_type_with_zero_hardcoding():
    response = client.get("/node-types")
    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()}

    # The original 4 + everything added across SPEC-002/003/004.
    for expected in (
        "text_input",
        "llm_call",
        "conditional_branch",
        "text_output",
        "uppercase_text",
        "code",
        "mcp_call",
        "loop",
        "fan_out",
        "merge",
    ):
        assert expected in by_type, f"{expected} missing from GET /node-types"

    # Static-schema types: real inputs/outputs, not flagged dynamic.
    text_input = by_type["text_input"]
    assert text_input["dynamic_schema"] is False
    assert [s["name"] for s in text_input["outputs"]] == ["text"]
    assert "value" in text_input["config_schema"]["properties"]

    # Dynamic-schema types: flagged, empty until resolve-slots is called.
    for dynamic_type in ("code", "mcp_call", "fan_out", "merge"):
        entry = by_type[dynamic_type]
        assert entry["dynamic_schema"] is True
        assert entry["inputs"] == []
        assert entry["outputs"] == []
        assert entry["config_schema"]  # still always present


def test_resolve_slots_for_code_node_returns_param_ports():
    response = client.post(
        "/node-types/code/resolve-slots",
        json={"config": {"function_source": "def transform(text, prefix):\n    return prefix + text\n"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["inputs"]] == ["text", "prefix"]
    assert [s["name"] for s in body["outputs"]] == ["result"]


def test_resolve_slots_unknown_type_returns_404():
    response = client.post("/node-types/not_a_real_type/resolve-slots", json={"config": {}})
    assert response.status_code == 404


def test_resolve_slots_malformed_config_returns_422():
    response = client.post(
        "/node-types/code/resolve-slots",
        json={"config": {"function_source": "not python("}},
    )
    assert response.status_code == 422


def test_submit_and_poll_run_matches_direct_run_graph():
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": "hello world"}},
            {"id": "n2", "type": "uppercase_text", "config": {}},
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "text"}},
            {"from": {"node": "n2", "slot": "text"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }

    submit = client.post("/runs", json=graph)
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]
    assert submit.json()["status"] == "running"

    # TestClient runs BackgroundTasks to completion within the request/response
    # cycle itself (a well-known quirk, unlike a real deployed server where the
    # HTTP response returns immediately and the task continues separately) --
    # so a single poll already reflects the final state here. Genuine live
    # "running -> completed" polling against a real server is verified
    # separately, manually, against a real uvicorn process (Phase 1 checkpoint).
    deadline = time.time() + 5
    status_response = None
    while time.time() < deadline:
        status_response = client.get(f"/runs/{run_id}")
        assert status_response.status_code == 200
        if status_response.json()["status"] != "running":
            break
        time.sleep(0.05)

    assert status_response is not None
    body = status_response.json()
    assert body["status"] == "completed"
    assert body["result"] == {"n3": "HELLO WORLD"}
    assert len(body["trace"]) == 3
    assert body["running_node_ids"] == []


def test_submit_invalid_graph_returns_422_with_issues():
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "llm_call", "config": {"model": "x", "max_tokens": 10}},
            {"id": "n2", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "response"}, "to": {"node": "n2", "slot": "text"}}
        ],
    }

    response = client.post("/runs", json=graph)

    assert response.status_code == 422
    issues = response.json()["detail"]
    assert any(issue["rule"] == "missing_required_input" for issue in issues)


def test_get_unknown_run_id_returns_404():
    response = client.get("/runs/not-a-real-run-id")
    assert response.status_code == 404

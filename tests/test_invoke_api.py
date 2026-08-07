from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.auth import jwt as auth_jwt

# spec-017: must match tests/conftest.py's TEST_API_KEY (the isolated_api_key
# fixture sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value).
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _linear_graph(value: str = "hello", label: str | None = None, out_label: str | None = None) -> dict:
    input_config: dict = {"value": value}
    if label is not None:
        input_config["label"] = label
    output_config: dict = {}
    if out_label is not None:
        output_config["label"] = out_label
    return {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": input_config},
            {"id": "n2", "type": "uppercase_text", "config": {}},
            {"id": "n3", "type": "text_output", "config": output_config},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "text"}},
            {"from": {"node": "n2", "slot": "text"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }


def _slow_graph(sleep_seconds: float) -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "entry", "type": "text_input", "config": {"value": "go", "label": "input"}},
            {
                "id": "step",
                "type": "code",
                "config": {
                    "function_source": (
                        f"def slow(text):\n    import time\n    time.sleep({sleep_seconds})\n    return text\n"
                    )
                },
            },
            {"id": "out", "type": "text_output", "config": {"label": "output"}},
        ],
        "edges": [
            {"from": {"node": "entry", "slot": "text"}, "to": {"node": "step", "slot": "text"}},
            {"from": {"node": "step", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }


def _failing_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "entry", "type": "text_input", "config": {"value": "go"}},
            {
                "id": "step",
                "type": "code",
                "config": {"function_source": "def boom(text):\n    raise ValueError('kaboom')\n"},
            },
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "entry", "slot": "text"}, "to": {"node": "step", "slot": "text"}},
            {"from": {"node": "step", "slot": "result"}, "to": {"node": "out", "slot": "text"}},
        ],
    }


def _create_graph(spec: dict, name: str = "g") -> str:
    resp = client.post("/graphs", json={"name": name, "spec": spec})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def _create_key(graph_id: str, label: str = "ci key", timeout_seconds: int = 60) -> tuple[str, str]:
    resp = client.post(
        f"/graphs/{graph_id}/invoke-keys", json={"label": label, "timeout_seconds": timeout_seconds}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["key"]["key_id"], body["token"]


def _wait_for_run(run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/runs/{run_id}")
        if resp.json()["status"] != "running":
            return resp.json()
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish in time")


# --- contract derivation ----------------------------------------------------


def test_contract_uses_label_when_set_and_falls_back_to_node_id():
    graph_id = _create_graph(_linear_graph(label="customer_message", out_label="reply"))
    resp = client.get(f"/graphs/{graph_id}/contract")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inputs"] == [
        {"name": "customer_message", "node_id": "n1", "direction": "input", "required": False, "default": "hello"}
    ]
    assert body["outputs"] == [{"name": "reply", "node_id": "n3", "direction": "output", "required": False, "default": None}]


def test_contract_falls_back_to_node_id_when_no_label():
    graph_id = _create_graph(_linear_graph())
    resp = client.get(f"/graphs/{graph_id}/contract")
    body = resp.json()
    assert body["inputs"][0]["name"] == "n1"
    assert body["outputs"][0]["name"] == "n3"


def test_contract_reports_required_when_saved_default_is_empty():
    graph_id = _create_graph(_linear_graph(value=""))
    resp = client.get(f"/graphs/{graph_id}/contract")
    assert resp.json()["inputs"][0]["required"] is True


def test_contract_404s_for_unknown_graph():
    resp = client.get("/graphs/does-not-exist/contract")
    assert resp.status_code == 404


def test_contract_422s_on_duplicate_external_field_name():
    spec = _linear_graph(label="dup")
    spec["nodes"].append({"id": "n4", "type": "text_input", "config": {"value": "x", "label": "dup"}})
    graph_id = _create_graph(spec)
    resp = client.get(f"/graphs/{graph_id}/contract")
    assert resp.status_code == 422


# --- invoke: happy path + validation ----------------------------------------


def test_invoke_happy_path_returns_outputs_synchronously():
    graph_id = _create_graph(_linear_graph(value="hello", label="in", out_label="out"))
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "hello world"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outputs"] == {"out": "HELLO WORLD"}
    assert body["run_id"]


def test_invoke_uses_saved_default_when_optional_field_omitted():
    graph_id = _create_graph(_linear_graph(value="default value", label="in", out_label="out"))
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outputs"] == {"out": "DEFAULT VALUE"}


def test_invoke_override_does_not_mutate_the_persisted_graph():
    graph_id = _create_graph(_linear_graph(value="original", label="in", out_label="out"))
    client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "overridden"}})
    fetched = client.get(f"/graphs/{graph_id}").json()
    saved_value = next(n["config"]["value"] for n in fetched["spec"]["nodes"] if n["id"] == "n1")
    assert saved_value == "original"


def test_invoke_missing_required_field_is_422():
    graph_id = _create_graph(_linear_graph(value="", label="in"))
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 422
    assert "in" in resp.text


def test_invoke_unrecognized_field_is_422():
    graph_id = _create_graph(_linear_graph(label="in"))
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"not_a_real_field": "x"}})
    assert resp.status_code == 422
    assert "not_a_real_field" in resp.text


def test_invoke_404s_for_unknown_graph():
    resp = client.post("/graphs/does-not-exist/invoke", json={"inputs": {}})
    assert resp.status_code == 404


def test_invoke_failed_run_returns_500_with_run_id():
    graph_id = _create_graph(_failing_graph())
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 500
    assert resp.json()["detail"]["run_id"]


def test_invoke_output_is_null_when_text_output_node_never_ran():
    # conditional_branch prunes one side -- its text_output receives no
    # value at all, so the contract field must come back null, not 500.
    spec = {
        "version": "0.1",
        "nodes": [
            {"id": "cond", "type": "text_input", "config": {"value": "no match", "label": "flag"}},
            {"id": "branch", "type": "conditional_branch", "config": {"condition": "equals('yes')"}},
            {"id": "out_true", "type": "text_output", "config": {"label": "on_true"}},
        ],
        "edges": [
            {"from": {"node": "cond", "slot": "text"}, "to": {"node": "branch", "slot": "value"}},
            {"from": {"node": "branch", "slot": "true_branch"}, "to": {"node": "out_true", "slot": "text"}},
        ],
    }
    graph_id = _create_graph(spec)
    resp = client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outputs"] == {"on_true": None}


# --- invoke keys: CRUD + plaintext handling ---------------------------------


def test_invoke_key_created_and_never_leaks_hash_or_plaintext_on_list():
    graph_id = _create_graph(_linear_graph())
    key_id, token = _create_key(graph_id)
    assert token.startswith("agsk_")

    listed = client.get(f"/graphs/{graph_id}/invoke-keys").json()
    assert len(listed) == 1
    assert listed[0]["key_id"] == key_id
    assert "key_hash" not in listed[0]
    assert "token" not in listed[0]
    assert token not in str(listed)


def test_invoke_key_creation_rejects_out_of_range_timeout():
    graph_id = _create_graph(_linear_graph())
    resp = client.post(f"/graphs/{graph_id}/invoke-keys", json={"label": "bad", "timeout_seconds": 9999})
    assert resp.status_code == 422


def test_revoke_then_reuse_fails():
    graph_id = _create_graph(_linear_graph(label="in", out_label="out"))
    key_id, token = _create_key(graph_id)

    # works once, using the invoke key itself as the credential
    invoke_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    ok = invoke_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "hi"}})
    assert ok.status_code == 200, ok.text

    deleted = client.delete(f"/graphs/{graph_id}/invoke-keys/{key_id}")
    assert deleted.status_code == 204

    again = invoke_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "hi"}})
    assert again.status_code == 401


def test_revoke_nonexistent_key_is_404():
    graph_id = _create_graph(_linear_graph())
    resp = client.delete(f"/graphs/{graph_id}/invoke-keys/not-a-real-key")
    assert resp.status_code == 404


# --- auth carve-out: an invoke key works ONLY on /invoke and /contract, ----
# --- ONLY for the graph_id it was created for -------------------------------


def test_invoke_key_authenticates_invoke_and_contract_for_its_own_graph():
    graph_id = _create_graph(_linear_graph(label="in", out_label="out"))
    _, token = _create_key(graph_id)
    invoke_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    contract = invoke_client.get(f"/graphs/{graph_id}/contract")
    assert contract.status_code == 200

    result = invoke_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "hi"}})
    assert result.status_code == 200


def test_invoke_key_is_rejected_against_a_different_graphs_invoke_route():
    graph_a = _create_graph(_linear_graph(), name="a")
    graph_b = _create_graph(_linear_graph(), name="b")
    _, token = _create_key(graph_a)
    invoke_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    resp = invoke_client.post(f"/graphs/{graph_b}/invoke", json={"inputs": {}})
    assert resp.status_code == 401


def test_invoke_key_does_not_authenticate_unrelated_routes():
    graph_id = _create_graph(_linear_graph())
    _, token = _create_key(graph_id)
    invoke_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    assert invoke_client.get("/graphs").status_code == 401
    assert invoke_client.get("/connections").status_code == 401
    assert invoke_client.get("/runs").status_code == 401
    # not even key management for its own graph
    assert invoke_client.get(f"/graphs/{graph_id}/invoke-keys").status_code == 401


def test_wrong_token_on_invoke_route_is_401():
    graph_id = _create_graph(_linear_graph())
    invoke_client = TestClient(app, headers={"Authorization": "Bearer agsk_totally-made-up"})
    resp = invoke_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 401


def test_jwt_and_shared_key_still_work_on_invoke_and_contract_routes():
    graph_id = _create_graph(_linear_graph(label="in", out_label="out"))

    # shared key (default `client` fixture) -- already exercised by every
    # other test above; this test is the explicit JWT-tier regression check.
    token = auth_jwt.issue_token("user-1", "person@example.com", "member")
    jwt_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    contract = jwt_client.get(f"/graphs/{graph_id}/contract")
    assert contract.status_code == 200

    result = jwt_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {"in": "hi"}})
    assert result.status_code == 200


def test_no_credential_at_all_on_invoke_route_is_401():
    graph_id = _create_graph(_linear_graph())
    anon_client = TestClient(app)
    resp = anon_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 401


# --- timeout ------------------------------------------------------------


def test_invoke_timeout_returns_504_and_run_completes_in_the_background():
    graph_id = _create_graph(_slow_graph(sleep_seconds=1.5))
    _, token = _create_key(graph_id, timeout_seconds=1)
    invoke_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    resp = invoke_client.post(f"/graphs/{graph_id}/invoke", json={"inputs": {}})
    assert resp.status_code == 504
    run_id = resp.json()["detail"]["run_id"]
    assert run_id

    final = _wait_for_run(run_id, timeout=5.0)
    assert final["status"] == "completed"

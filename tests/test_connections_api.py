from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.connections.store import add_connection
from backend.llm.client import LLMResponse

client = TestClient(app)


def test_connection_types_lists_anthropic_and_ollama_with_zero_hardcoding():
    response = client.get("/connection-types")
    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()}

    assert by_type["anthropic"]["category"] == "cloud"
    assert "api_key" in by_type["anthropic"]["config_schema"]["properties"]
    assert by_type["anthropic"]["supports_model_listing"] is False

    assert by_type["ollama"]["category"] == "local"
    assert "host" in by_type["ollama"]["config_schema"]["properties"]
    assert "port" in by_type["ollama"]["config_schema"]["properties"]
    assert by_type["ollama"]["supports_model_listing"] is True
    assert by_type["ollama"]["supports_embedding"] is True
    assert by_type["anthropic"]["supports_embedding"] is False
    assert by_type["vector_store"]["category"] == "local"


def test_list_connection_models_returns_real_live_models(monkeypatch):
    import io
    import json as json_module

    import backend.connections.ollama_connection as ollama_connection_module

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=5):
        return _FakeResponse(
            json_module.dumps({"models": [{"name": "qwen2.5:14b"}, {"name": "devstral:24b"}]}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen)
    add_connection("models-conn", "ollama", {"host": "localhost", "port": 11434})

    response = client.get("/connections/models-conn/models")

    assert response.status_code == 200
    assert response.json() == ["qwen2.5:14b", "devstral:24b"]


def test_list_connection_models_unsupported_type_returns_422():
    add_connection("no-listing-conn", "anthropic", {"api_key": "sk-1"})

    response = client.get("/connections/no-listing-conn/models")

    assert response.status_code == 422


def test_list_connection_models_unknown_connection_returns_404():
    response = client.get("/connections/never-saved-for-models/models")
    assert response.status_code == 404


def test_list_connection_models_live_failure_returns_502(monkeypatch):
    import urllib.error

    import backend.connections.ollama_connection as ollama_connection_module

    def fake_urlopen_failure(url, timeout=5):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen_failure)
    add_connection("unreachable-models-conn", "ollama", {"host": "localhost", "port": 19999})

    response = client.get("/connections/unreachable-models-conn/models")

    assert response.status_code == 502


def test_list_connections_never_returns_config():
    add_connection("listed-conn", "anthropic", {"api_key": "sk-secret"})

    response = client.get("/connections")

    assert response.status_code == 200
    entries = response.json()
    assert {"name": "listed-conn", "type": "anthropic"} in entries
    assert all("config" not in entry for entry in entries)


def test_create_connection_persists_and_is_listed():
    response = client.post(
        "/connections",
        json={"name": "new-conn", "type": "ollama", "config": {"host": "localhost", "port": 11434}},
    )

    assert response.status_code == 201
    assert response.json() == {"name": "new-conn", "type": "ollama"}
    assert any(c["name"] == "new-conn" for c in client.get("/connections").json())


def test_create_connection_duplicate_name_returns_409():
    add_connection("already-there", "anthropic", {"api_key": "sk-1"})

    response = client.post(
        "/connections",
        json={"name": "already-there", "type": "anthropic", "config": {"api_key": "sk-2"}},
    )

    assert response.status_code == 409


def test_create_connection_unknown_type_returns_422():
    response = client.post(
        "/connections", json={"name": "bad-type-conn", "type": "not-a-real-type", "config": {}}
    )
    assert response.status_code == 422


def test_create_connection_invalid_config_returns_422():
    response = client.post(
        "/connections",
        json={"name": "bad-config-conn", "type": "anthropic", "config": {}},  # missing api_key
    )
    assert response.status_code == 422


def test_test_connection_pre_save_flow_reports_success(monkeypatch):
    from backend.llm import ollama_client as ollama_client_module

    class _StubOllamaLLMClient:
        def __init__(self, host):
            pass

        def complete(self, **kwargs) -> LLMResponse:
            return LLMResponse(text="ok", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(ollama_client_module, "OllamaLLMClient", _StubOllamaLLMClient)

    def fake_urlopen(url, timeout=5):
        raise AssertionError("real network call should not happen in this offline test")

    import backend.connections.ollama_connection as ollama_connection_module
    import json as json_module
    import io

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen_success(url, timeout=5):
        return _FakeResponse(json_module.dumps({"models": [{"name": "llama3"}]}).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen_success)

    response = client.post(
        "/connections/not-yet-saved/test",
        json={"type": "ollama", "config": {"host": "localhost", "port": 11434}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "llama3" in body["message"]
    # Not persisted by testing alone -- test-before-save never calls add_connection.
    assert not any(c["name"] == "not-yet-saved" for c in client.get("/connections").json())


def test_test_connection_pre_save_flow_reports_failure(monkeypatch):
    import backend.connections.ollama_connection as ollama_connection_module
    import urllib.error

    def fake_urlopen_failure(url, timeout=5):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen_failure)

    response = client.post(
        "/connections/unreachable/test",
        json={"type": "ollama", "config": {"host": "localhost", "port": 19999}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False


def test_test_connection_retest_saved_connection_by_name(monkeypatch):
    import backend.connections.ollama_connection as ollama_connection_module
    import json as json_module
    import io

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen_success(url, timeout=5):
        return _FakeResponse(json_module.dumps({"models": []}).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen_success)
    add_connection("saved-for-retest", "ollama", {"host": "localhost", "port": 11434})

    response = client.post("/connections/saved-for-retest/test", json={})

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_test_connection_unknown_saved_name_returns_404():
    response = client.post("/connections/never-saved/test", json={})
    assert response.status_code == 404


def test_delete_connection_returns_204_and_removes_it():
    add_connection("to-delete", "anthropic", {"api_key": "sk-1"})

    response = client.delete("/connections/to-delete")

    assert response.status_code == 204
    assert not any(c["name"] == "to-delete" for c in client.get("/connections").json())


def test_delete_unknown_connection_returns_404():
    response = client.delete("/connections/never-existed")
    assert response.status_code == 404


def test_clear_connection_vectors_removes_stored_chunks_without_deleting_connection(tmp_path):
    add_connection("vectors-conn", "vector_store", {"path": str(tmp_path / "store.db")})

    from backend.connections.vector_store_connection import VectorStoreClient

    VectorStoreClient(tmp_path / "store.db").add(["a chunk"], [[1.0, 0.0]], document_name=None)

    response = client.delete("/connections/vectors-conn/vectors")

    assert response.status_code == 204
    assert VectorStoreClient(tmp_path / "store.db").query([1.0, 0.0], top_k=5) == []
    # The connection profile itself is untouched.
    assert any(c["name"] == "vectors-conn" for c in client.get("/connections").json())


def test_clear_connection_vectors_unknown_connection_returns_404():
    response = client.delete("/connections/never-saved-for-vectors/vectors")
    assert response.status_code == 404


def test_clear_connection_vectors_wrong_type_returns_422():
    add_connection("not-a-vector-store", "anthropic", {"api_key": "sk-1"})

    response = client.delete("/connections/not-a-vector-store/vectors")

    assert response.status_code == 422


def test_submit_run_with_valid_connection_resolves_and_executes(monkeypatch):
    from backend.llm import anthropic_client as anthropic_client_module

    class _StubAnthropicLLMClient:
        def __init__(self, api_key=None):
            pass

        def complete(self, **kwargs) -> LLMResponse:
            return LLMResponse(text="api mocked reply", input_tokens=2, output_tokens=3)

    monkeypatch.setattr(anthropic_client_module, "AnthropicLLMClient", _StubAnthropicLLMClient)
    add_connection("run-conn", "anthropic", {"api_key": "sk-run"})

    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": "hello"}},
            {
                "id": "n2",
                "type": "llm_call",
                "config": {"connection": "run-conn", "model": "m", "max_tokens": 10},
            },
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "prompt"}},
            {"from": {"node": "n2", "slot": "response"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }

    submit = client.post("/runs", json=graph)
    assert submit.status_code == 202
    run_id = submit.json()["run_id"]

    status = client.get(f"/runs/{run_id}")
    assert status.json()["status"] == "completed"
    assert status.json()["result"] == {"n3": "api mocked reply"}


def test_submit_run_with_missing_connection_returns_422_missing_connection_rule():
    graph = {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": "hello"}},
            {
                "id": "n2",
                "type": "llm_call",
                "config": {"connection": "definitely-not-configured", "model": "m", "max_tokens": 10},
            },
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "prompt"}},
            {"from": {"node": "n2", "slot": "response"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }

    response = client.post("/runs", json=graph)

    assert response.status_code == 422
    issues = response.json()["detail"]
    assert any(
        issue["rule"] == "missing_connection" and "definitely-not-configured" in issue["message"]
        for issue in issues
    )

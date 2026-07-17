from __future__ import annotations

import json

import pytest

from backend.connections.base import ConnectionDefinition, ConnectionRegistry, ConnectionTestResult
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.resolver import resolve_connections
from backend.connections.store import add_connection, connections_path, delete_connection, get_connection, list_connections
from backend.schema.loader import parse_graph_json
from pydantic import BaseModel


# --- store CRUD -----------------------------------------------------------


def test_add_and_get_connection_round_trips():
    profile = add_connection("my-conn", "anthropic", {"api_key": "sk-test"})
    assert profile.name == "my-conn"
    assert profile.type == "anthropic"

    fetched = get_connection("my-conn")
    assert fetched is not None
    assert fetched.config == {"api_key": "sk-test"}


def test_add_duplicate_name_raises():
    add_connection("dup", "anthropic", {"api_key": "sk-test"})
    with pytest.raises(DuplicateConnectionError):
        add_connection("dup", "ollama", {"host": "localhost", "port": 11434})


def test_list_connections_never_needed_to_expose_config_at_this_layer():
    add_connection("a", "anthropic", {"api_key": "sk-a"})
    add_connection("b", "ollama", {"host": "localhost", "port": 11434})
    names = {c.name for c in list_connections()}
    assert names == {"a", "b"}


def test_delete_connection_removes_it_and_returns_true():
    add_connection("gone-soon", "anthropic", {"api_key": "sk-test"})
    assert delete_connection("gone-soon") is True
    assert get_connection("gone-soon") is None


def test_delete_unknown_connection_returns_false():
    assert delete_connection("never-existed") is False


def test_store_is_a_plain_json_file_never_inline_in_graph_json():
    add_connection("persisted", "anthropic", {"api_key": "sk-test"})
    path = connections_path()
    data = json.loads(path.read_text())
    assert data["connections"][0]["name"] == "persisted"


# --- ConnectionRegistry (mirrors NodeRegistry's own duplicate-type test) --


def test_connection_registry_rejects_duplicate_type_registration():
    registry = ConnectionRegistry()
    dummy_config = type("DummyConfig", (BaseModel,), {})
    definition = ConnectionDefinition(
        type_name="dummy",
        category="local",
        config_model=dummy_config,
        build_client=lambda config: object(),
        test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    )
    registry.register(definition)
    with pytest.raises(ValueError):
        registry.register(definition)


def test_default_connection_registry_has_anthropic_and_ollama():
    from backend.connections.base import default_connection_registry

    assert "anthropic" in default_connection_registry.all_types()
    assert "ollama" in default_connection_registry.all_types()
    assert default_connection_registry.get("anthropic").category == "cloud"
    assert default_connection_registry.get("ollama").category == "local"


def test_definition_without_list_models_defaults_to_none():
    dummy_config = type("DummyConfig2", (BaseModel,), {})
    definition = ConnectionDefinition(
        type_name="dummy2",
        category="cloud",
        config_model=dummy_config,
        build_client=lambda config: object(),
        test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    )
    assert definition.list_models is None


def test_ollama_list_models_returns_real_names(monkeypatch):
    import io
    import json as json_module

    import backend.connections.ollama_connection as ollama_connection_module

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=5):
        return _FakeResponse(json_module.dumps({"models": [{"name": "llama3"}]}).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen)

    config = ollama_connection_module.OllamaConnectionConfig(host="localhost", port=11434)
    assert ollama_connection_module.list_models(config) == ["llama3"]


def test_anthropic_connection_has_no_list_models():
    from backend.connections.base import default_connection_registry

    assert default_connection_registry.get("anthropic").list_models is None


# --- resolver ---------------------------------------------------------------


def test_resolve_connections_builds_a_real_client_per_referenced_name(monkeypatch):
    from backend.llm import anthropic_client as anthropic_client_module

    built_with = []

    class _StubAnthropicLLMClient:
        def __init__(self, api_key=None):
            built_with.append(api_key)

    monkeypatch.setattr(anthropic_client_module, "AnthropicLLMClient", _StubAnthropicLLMClient)
    add_connection("resolve-me", "anthropic", {"api_key": "sk-resolved"})

    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {"id": "in", "type": "text_input", "config": {"value": "hi"}},
                    {
                        "id": "call",
                        "type": "llm_call",
                        "config": {"connection": "resolve-me", "model": "m", "max_tokens": 10},
                    },
                    {"id": "out", "type": "text_output", "config": {}},
                ],
                "edges": [
                    {"from": {"node": "in", "slot": "text"}, "to": {"node": "call", "slot": "prompt"}},
                    {"from": {"node": "call", "slot": "response"}, "to": {"node": "out", "slot": "text"}},
                ],
            }
        )
    )

    resolved = resolve_connections(graph)

    assert built_with == ["sk-resolved"]
    assert isinstance(resolved["resolve-me"], _StubAnthropicLLMClient)


def test_resolve_connections_raises_clear_error_for_missing_connection():
    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {
                        "id": "call",
                        "type": "llm_call",
                        "config": {"connection": "does-not-exist", "model": "m", "max_tokens": 10},
                    },
                ],
                "edges": [],
            }
        )
    )

    with pytest.raises(ConnectionNotFoundError) as exc_info:
        resolve_connections(graph)

    assert "does-not-exist" in str(exc_info.value)


def test_resolve_connections_ignores_nodes_without_a_connection_field():
    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [{"id": "n1", "type": "text_input", "config": {"value": "hi"}}],
                "edges": [],
            }
        )
    )

    assert resolve_connections(graph) == {}

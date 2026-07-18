from __future__ import annotations

import json

import pytest

from backend.connections.base import (
    ConnectionDefinition,
    ConnectionRegistry,
    ConnectionTestResult,
    ToolCallRequest,
    ToolDefinition,
)
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.resolver import (
    connection_reference_names,
    resolve_connection_profiles,
    resolve_connections,
)
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


def test_resolve_connection_profiles_returns_raw_type_and_config():
    add_connection("agent-conn", "ollama", {"host": "1.2.3.4", "port": 11434})

    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {
                        "id": "a",
                        "type": "agent",
                        "config": {
                            "connection": "agent-conn",
                            "model": "m",
                            "tools": [],
                            "memory": {"max_messages": 5},
                            "max_iterations": 3,
                            "max_tokens": 50,
                        },
                    }
                ],
                "edges": [],
            }
        )
    )

    profiles = resolve_connection_profiles(graph)

    assert profiles["agent-conn"].type == "ollama"
    assert profiles["agent-conn"].config == {"host": "1.2.3.4", "port": 11434}


def test_resolve_connection_profiles_raises_clear_error_for_missing_connection():
    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {
                        "id": "a",
                        "type": "agent",
                        "config": {
                            "connection": "nope",
                            "model": "m",
                            "tools": [],
                            "memory": {"max_messages": 5},
                            "max_iterations": 3,
                            "max_tokens": 50,
                        },
                    }
                ],
                "edges": [],
            }
        )
    )

    with pytest.raises(ConnectionNotFoundError):
        resolve_connection_profiles(graph)


# --- complete_with_tools (spec-008) -----------------------------------------


def test_default_registry_ollama_supports_tool_calling_anthropic_not_yet_checked_here():
    from backend.connections.base import default_connection_registry

    assert default_connection_registry.get("ollama").complete_with_tools is not None
    assert default_connection_registry.get("anthropic").complete_with_tools is not None


def test_definition_without_complete_with_tools_defaults_to_none():
    dummy_config = type("DummyConfig3", (BaseModel,), {})
    definition = ConnectionDefinition(
        type_name="dummy3",
        category="cloud",
        config_model=dummy_config,
        build_client=lambda config: object(),
        test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    )
    assert definition.complete_with_tools is None


def test_ollama_complete_with_tools_returns_tool_calls_when_model_requests_one(monkeypatch):
    import io
    import json as json_module

    import backend.connections.ollama_connection as ollama_connection_module

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured_payload = {}

    def fake_urlopen(request, timeout=120):
        captured_payload.update(json_module.loads(request.data.decode("utf-8")))
        body = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "multiply", "arguments": {"a": "6", "b": "7"}}}],
            },
            "prompt_eval_count": 12,
            "eval_count": 5,
        }
        return _FakeResponse(json_module.dumps(body).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen)

    config = ollama_connection_module.OllamaConnectionConfig(host="localhost", port=11434)
    tool = ToolDefinition(
        name="multiply",
        description="multiplies",
        parameters={"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
    )

    response = ollama_connection_module.complete_with_tools(
        config,
        model="qwen2.5:14b",
        system_prompt="be helpful",
        messages=[{"role": "user", "content": "6 times 7"}],
        tools=[tool],
        max_tokens=100,
    )

    assert response.text is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0] == ToolCallRequest(id="call_1", name="multiply", arguments={"a": "6", "b": "7"})
    assert response.input_tokens == 12
    assert response.output_tokens == 5

    # request wire format: OpenAI-compatible function-wrapped tool defs,
    # system prompt prepended as a role="system" message.
    assert captured_payload["tools"][0]["function"]["name"] == "multiply"
    assert captured_payload["messages"][0] == {"role": "system", "content": "be helpful"}


def test_ollama_complete_with_tools_returns_direct_text_when_no_tool_needed(monkeypatch):
    import io
    import json as json_module

    import backend.connections.ollama_connection as ollama_connection_module

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=120):
        body = {"message": {"role": "assistant", "content": "Paris"}, "prompt_eval_count": 8, "eval_count": 2}
        return _FakeResponse(json_module.dumps(body).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen)

    config = ollama_connection_module.OllamaConnectionConfig(host="localhost", port=11434)
    response = ollama_connection_module.complete_with_tools(
        config,
        model="qwen2.5:14b",
        system_prompt="",
        messages=[{"role": "user", "content": "capital of France?"}],
        tools=[],
        max_tokens=100,
    )

    assert response.text == "Paris"
    assert response.tool_calls == []


def test_anthropic_complete_with_tools_returns_tool_calls_when_model_requests_one(monkeypatch):
    import anthropic as anthropic_module

    class _FakeBlock:
        def __init__(self, type_, **kwargs):
            self.type = type_
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeUsage:
        input_tokens = 20
        output_tokens = 6

    class _FakeResponse:
        content = [_FakeBlock("tool_use", id="toolu_1", name="multiply", input={"a": "6", "b": "7"})]
        usage = _FakeUsage()

    captured_kwargs = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic_module, "Anthropic", _FakeAnthropicClient)

    import backend.connections.anthropic_connection as anthropic_connection_module

    config = anthropic_connection_module.AnthropicConnectionConfig(api_key="sk-test")
    tool = ToolDefinition(
        name="multiply",
        description="multiplies",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    )

    response = anthropic_connection_module.complete_with_tools(
        config,
        model="claude-opus-4-8",
        system_prompt="be helpful",
        messages=[{"role": "user", "content": "6 times 7"}],
        tools=[tool],
        max_tokens=100,
    )

    assert response.text is None
    assert response.tool_calls == [ToolCallRequest(id="toolu_1", name="multiply", arguments={"a": "6", "b": "7"})]
    assert response.input_tokens == 20
    assert response.output_tokens == 6
    assert captured_kwargs["tools"][0]["name"] == "multiply"
    assert captured_kwargs["tools"][0]["input_schema"] == tool.parameters
    assert captured_kwargs["system"] == "be helpful"


def test_anthropic_complete_with_tools_translates_tool_result_messages(monkeypatch):
    import anthropic as anthropic_module

    class _FakeBlock:
        def __init__(self, type_, **kwargs):
            self.type = type_
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeUsage:
        input_tokens = 30
        output_tokens = 10

    class _FakeResponse:
        content = [_FakeBlock("text", text="The answer is 42.")]
        usage = _FakeUsage()

    captured_kwargs = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic_module, "Anthropic", _FakeAnthropicClient)

    import backend.connections.anthropic_connection as anthropic_connection_module

    config = anthropic_connection_module.AnthropicConnectionConfig(api_key="sk-test")
    messages = [
        {"role": "user", "content": "6 times 7"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "toolu_1", "name": "multiply", "arguments": {"a": "6", "b": "7"}}],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "name": "multiply", "content": "42"},
    ]

    response = anthropic_connection_module.complete_with_tools(
        config, model="claude-opus-4-8", system_prompt="", messages=messages, tools=[], max_tokens=100
    )

    assert response.text == "The answer is 42."
    wire_messages = captured_kwargs["messages"]
    assert wire_messages[1]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "multiply",
        "input": {"a": "6", "b": "7"},
    }
    assert wire_messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"}],
    }


# --- embed capability + connection_reference_names convention (spec-011) ----


def test_definition_without_embed_defaults_to_none():
    dummy_config = type("DummyConfig4", (BaseModel,), {})
    definition = ConnectionDefinition(
        type_name="dummy4",
        category="cloud",
        config_model=dummy_config,
        build_client=lambda config: object(),
        test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    )
    assert definition.embed is None


def test_default_connection_registry_has_vector_store_and_ollama_embed():
    from backend.connections.base import default_connection_registry

    assert "vector_store" in default_connection_registry.all_types()
    assert default_connection_registry.get("vector_store").category == "local"
    assert default_connection_registry.get("ollama").embed is not None
    assert default_connection_registry.get("anthropic").embed is None


def test_ollama_embed_returns_real_vector(monkeypatch):
    import io
    import json as json_module

    import backend.connections.ollama_connection as ollama_connection_module

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured_payload = {}

    def fake_urlopen(request, timeout=120):
        captured_payload.update(json_module.loads(request.data.decode("utf-8")))
        return _FakeResponse(json_module.dumps({"embedding": [0.1, 0.2, 0.3]}).encode("utf-8"))

    monkeypatch.setattr(ollama_connection_module.urllib.request, "urlopen", fake_urlopen)

    config = ollama_connection_module.OllamaConnectionConfig(host="localhost", port=11434)
    result = ollama_connection_module.embed(config, "nomic-embed-text", "hello world")

    assert result == [0.1, 0.2, 0.3]
    assert captured_payload == {"model": "nomic-embed-text", "prompt": "hello world"}


def test_connection_reference_names_matches_exact_and_suffix_keys():
    config = {
        "connection": "vs1",
        "embedding_model_connection": "emb1",
        "model": "not-a-connection-field",
        "other_field": 42,
    }
    assert set(connection_reference_names(config)) == {"vs1", "emb1"}


def test_connection_reference_names_ignores_non_string_values():
    assert connection_reference_names({"connection": 123, "reranker_connection": None}) == []


def test_resolve_connections_resolves_a_suffixed_connection_key():
    add_connection("emb-conn", "ollama", {"host": "localhost", "port": 11434})

    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "ingest_document",
                        "config": {
                            "connection": "vs-conn-not-registered-yet",
                            "embedding_model_connection": "emb-conn",
                            "embedding_model": "nomic-embed-text",
                            "chunk_size": 100,
                            "chunk_overlap": 10,
                        },
                    }
                ],
                "edges": [],
            }
        )
    )
    add_connection("vs-conn-not-registered-yet", "vector_store", {"path": "/tmp/does-not-matter.db"})

    resolved = resolve_connections(graph)
    assert set(resolved.keys()) == {"vs-conn-not-registered-yet", "emb-conn"}


def test_check_missing_connections_detects_a_missing_suffixed_connection_key():
    from backend.validation.rules import check_missing_connections

    graph = parse_graph_json(
        json.dumps(
            {
                "version": "0.1",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "ingest_document",
                        "config": {
                            "connection": "vs-missing",
                            "embedding_model_connection": "emb-missing",
                            "embedding_model": "nomic-embed-text",
                            "chunk_size": 100,
                            "chunk_overlap": 10,
                        },
                    }
                ],
                "edges": [],
            }
        )
    )

    issues = check_missing_connections(graph)

    missing_names = {i.message for i in issues}
    assert any("vs-missing" in m for m in missing_names)
    assert any("emb-missing" in m for m in missing_names)
    assert len(issues) == 2

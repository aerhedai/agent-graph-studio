from __future__ import annotations

import pytest

import backend.llm.anthropic_client as anthropic_client_module
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.llm.client import LLMResponse
from backend.nodes.llm_call import execute_llm_call
from backend.schema.models import NodeSpec
from fakes import FailingLLMClient, FakeLLMClient


def _node(config: dict) -> NodeSpec:
    return NodeSpec(id="n1", type="llm_call", config=config)


def test_llm_call_maps_config_and_returns_response():
    client = FakeLLMClient(response=LLMResponse(text="hi there", input_tokens=5, output_tokens=7))
    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "system_prompt": "be nice", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={"llm_client": client},
    )

    result = execute_llm_call(ctx)

    assert result.outputs == {"response": "hi there"}
    assert result.token_cost.input_tokens == 5
    assert result.token_cost.output_tokens == 7
    assert client.calls == [
        {"model": "claude-opus-4-8", "system_prompt": "be nice", "prompt": "hello", "max_tokens": 50}
    ]


def test_llm_call_wraps_client_exception_as_node_execution_error():
    client = FailingLLMClient(RuntimeError("boom"))
    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={"llm_client": client},
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)


def test_llm_call_constructs_default_anthropic_client_when_none_injected(monkeypatch):
    calls = []

    class _StubAnthropicLLMClient:
        def __init__(self) -> None:
            calls.append("constructed")

        def complete(self, **kwargs) -> LLMResponse:
            return LLMResponse(text="default client reply", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(anthropic_client_module, "AnthropicLLMClient", _StubAnthropicLLMClient)

    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={},
    )

    result = execute_llm_call(ctx)

    assert calls == ["constructed"]
    assert result.outputs == {"response": "default client reply"}


def test_llm_call_wraps_default_client_construction_failure_as_node_execution_error(monkeypatch):
    class _BrokenAnthropicLLMClient:
        def __init__(self) -> None:
            raise RuntimeError("no API key configured")

    monkeypatch.setattr(anthropic_client_module, "AnthropicLLMClient", _BrokenAnthropicLLMClient)

    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={},
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)


def test_llm_call_dispatches_to_ollama_provider_when_selected(monkeypatch):
    import backend.llm.ollama_client as ollama_client_module

    calls = []

    class _StubOllamaLLMClient:
        def __init__(self, host: str) -> None:
            calls.append(host)

        def complete(self, **kwargs) -> LLMResponse:
            return LLMResponse(text="ollama reply", input_tokens=2, output_tokens=3)

    monkeypatch.setattr(ollama_client_module, "OllamaLLMClient", _StubOllamaLLMClient)

    ctx = ExecutionContext(
        node=_node(
            {
                "provider": "ollama",
                "model": "llama3.2",
                "max_tokens": 50,
                "provider_options": {"host": "http://example.internal:11434"},
            }
        ),
        inputs={"prompt": "hello"},
        resources={},
    )

    result = execute_llm_call(ctx)

    assert calls == ["http://example.internal:11434"]
    assert result.outputs == {"response": "ollama reply"}


def test_llm_call_unknown_provider_raises_node_execution_error():
    ctx = ExecutionContext(
        node=_node({"provider": "not-a-real-provider", "model": "x", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={},
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)

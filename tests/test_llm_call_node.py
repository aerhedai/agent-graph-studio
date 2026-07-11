from __future__ import annotations

import pytest

import backend.llm.client as llm_client_module
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


def test_llm_call_constructs_default_client_when_none_injected(monkeypatch):
    calls = []

    class _StubAnthropicLLMClient:
        def __init__(self) -> None:
            calls.append("constructed")

        def complete(self, **kwargs) -> LLMResponse:
            return LLMResponse(text="default client reply", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(llm_client_module, "AnthropicLLMClient", _StubAnthropicLLMClient)

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

    monkeypatch.setattr(llm_client_module, "AnthropicLLMClient", _BrokenAnthropicLLMClient)

    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        resources={},
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)

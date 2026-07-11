from __future__ import annotations

import pytest

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
        llm_client=client,
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
        llm_client=client,
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)


def test_llm_call_raises_when_no_client_provided():
    ctx = ExecutionContext(
        node=_node({"model": "claude-opus-4-8", "max_tokens": 50}),
        inputs={"prompt": "hello"},
        llm_client=None,
    )

    with pytest.raises(NodeExecutionError):
        execute_llm_call(ctx)

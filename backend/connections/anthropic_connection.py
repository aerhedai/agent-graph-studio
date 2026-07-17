"""The `anthropic` connection type -- a "cloud" connection needing an API
key. Mirrors ollama_connection.py's shape exactly (spec-006 §4/§5)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.connections.base import (
    ConnectionTestResult,
    ToolCallRequest,
    ToolCallResponse,
    ToolDefinition,
    register_connection_type,
)

_TEST_MODEL = "claude-haiku-4-5-20251001"


class AnthropicConnectionConfig(BaseModel):
    api_key: str


def build_client(config: AnthropicConnectionConfig):
    # Module-qualified lookup at call time (not a top-of-file import) so
    # tests can monkeypatch backend.llm.anthropic_client.AnthropicLLMClient,
    # same precedent as the old backend/llm/providers.py.
    from backend.llm import anthropic_client

    return anthropic_client.AnthropicLLMClient(api_key=config.api_key)


def test_connection(config: AnthropicConnectionConfig) -> ConnectionTestResult:
    try:
        client = build_client(config)
        client.complete(model=_TEST_MODEL, system_prompt="", prompt="hi", max_tokens=1)
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"Anthropic connection failed: {e}")
    return ConnectionTestResult(success=True, message="Connected to the Anthropic API successfully.")


def _to_anthropic_message(message: dict[str, Any]) -> dict[str, Any]:
    """Translates agent.py's generic OpenAI-style message shape into
    Anthropic's content-block format. A generic role="tool" message (a tool
    result) becomes an Anthropic role="user" message with a tool_result
    content block -- Anthropic has no separate "tool" role."""
    role = message["role"]
    if role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
            ],
        }
    if role == "assistant" and message.get("tool_calls"):
        blocks: list[dict[str, Any]] = []
        if message.get("content"):
            blocks.append({"type": "text", "text": message["content"]})
        for call in message["tool_calls"]:
            blocks.append(
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]}
            )
        return {"role": "assistant", "content": blocks}
    return {"role": role, "content": message["content"]}


def complete_with_tools(
    config: AnthropicConnectionConfig,
    *,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[ToolDefinition],
    max_tokens: int,
) -> ToolCallResponse:
    """spec-008 §5: a tool-calling-capable completion call, via Anthropic's
    own tool-use API. Built against Anthropic's documented Messages API
    shape and unit-tested with mocks only -- no live Anthropic account is
    available to verify this against right now (Ollama is the spec's
    primary, live-verified target); this must not block that."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.api_key)

    wire_tools = [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [_to_anthropic_message(m) for m in messages],
        "tools": wire_tools,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    try:
        response = client.messages.create(**kwargs)
    except Exception as e:
        raise RuntimeError(f"Anthropic tool-calling request failed: {e}") from e

    tool_calls = [
        ToolCallRequest(id=block.id, name=block.name, arguments=block.input)
        for block in response.content
        if block.type == "tool_use"
    ]
    text_blocks = [block.text for block in response.content if block.type == "text"]

    return ToolCallResponse(
        text=None if tool_calls else "\n".join(text_blocks),
        tool_calls=tool_calls,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


register_connection_type(
    "anthropic",
    category="cloud",
    config_model=AnthropicConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
    complete_with_tools=complete_with_tools,
)

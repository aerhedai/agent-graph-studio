"""The `openai` connection type -- a "cloud" connection needing an API key.
Mirrors anthropic_connection.py's shape exactly, following the pattern
documented in backend/connections/__init__.py: a new connection type is
just a new module here plus one line added to that package's imports --
no changes needed to base.py, resolver.py, the API layer, or the frontend
connection picker."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from backend.connections.base import (
    ConnectionTestResult,
    ToolCallRequest,
    ToolCallResponse,
    ToolDefinition,
    register_connection_type,
)


class OpenAIConnectionConfig(BaseModel):
    api_key: str


def build_client(config: OpenAIConnectionConfig):
    # Module-qualified lookup at call time (not a top-of-file import) so
    # tests can monkeypatch backend.llm.openai_client.OpenAILLMClient, same
    # precedent as anthropic_connection.py's build_client.
    from backend.llm import openai_client

    return openai_client.OpenAILLMClient(api_key=config.api_key)


def _list_model_ids(config: OpenAIConnectionConfig) -> list[str]:
    """Shared by test_connection and list_models -- one real, free call to
    the models list endpoint, no completion tokens spent. Raises on
    failure; callers decide how to surface that."""
    import openai

    client = openai.OpenAI(api_key=config.api_key)
    response = client.models.list()
    return [m.id for m in response.data]


def test_connection(config: OpenAIConnectionConfig) -> ConnectionTestResult:
    try:
        _list_model_ids(config)
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"OpenAI connection failed: {e}")
    return ConnectionTestResult(success=True, message="Connected to the OpenAI API successfully.")


def list_models(config: OpenAIConnectionConfig) -> list[str]:
    return _list_model_ids(config)


def _to_openai_message(message: dict[str, Any]) -> dict[str, Any]:
    """Translates agent.py's generic message shape into OpenAI's Chat
    Completions shape. The two are already very close (agent.py's shape was
    itself modeled after OpenAI's, per ollama_connection.py's own docstring
    on being "OpenAI-compatible") -- the one real difference is that
    OpenAI's `tool_calls[].function.arguments` must be a JSON *string*,
    while agent.py's internal shape (and our own ToolCallRequest) keeps
    arguments as an already-parsed dict throughout."""
    role = message["role"]
    if role == "tool":
        return {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
    if role == "assistant" and message.get("tool_calls"):
        return {
            "role": "assistant",
            "content": message.get("content") or None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                }
                for call in message["tool_calls"]
            ],
        }
    return {"role": role, "content": message["content"]}


def complete_with_tools(
    config: OpenAIConnectionConfig,
    *,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[ToolDefinition],
    max_tokens: int,
) -> ToolCallResponse:
    """spec-008 §5-style tool-calling completion, via OpenAI's own
    tool-calling API. Unit-tested with mocks only -- no live OpenAI account
    is available to verify this against right now, same disclosed
    limitation anthropic_connection.py's own complete_with_tools already
    carries."""
    import openai

    client = openai.OpenAI(api_key=config.api_key)

    wire_messages: list[dict[str, Any]] = []
    if system_prompt:
        wire_messages.append({"role": "system", "content": system_prompt})
    wire_messages.extend(_to_openai_message(m) for m in messages)

    wire_tools = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=wire_messages,
            tools=wire_tools,
            max_tokens=max_tokens,
        )
    except Exception as e:
        raise RuntimeError(f"OpenAI tool-calling request failed: {e}") from e

    choice_message = response.choices[0].message
    raw_tool_calls = choice_message.tool_calls or []
    tool_calls = [
        ToolCallRequest(
            id=call.id,
            name=call.function.name,
            arguments=json.loads(call.function.arguments) if call.function.arguments else {},
        )
        for call in raw_tool_calls
    ]

    return ToolCallResponse(
        text=None if tool_calls else (choice_message.content or ""),
        tool_calls=tool_calls,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )


register_connection_type(
    "openai",
    category="cloud",
    config_model=OpenAIConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
    list_models=list_models,
    complete_with_tools=complete_with_tools,
)

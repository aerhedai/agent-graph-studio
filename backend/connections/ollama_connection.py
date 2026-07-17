"""The `ollama` connection type -- a "local" connection needing just a
host/port. Mirrors anthropic_connection.py's shape exactly (spec-006
§4/§5)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel

from backend.connections.base import (
    ConnectionTestResult,
    ToolCallRequest,
    ToolCallResponse,
    ToolDefinition,
    register_connection_type,
)


class OllamaConnectionConfig(BaseModel):
    host: str = "localhost"
    port: int = 11434


def _base_url(config: OllamaConnectionConfig) -> str:
    return f"http://{config.host}:{config.port}"


def _fetch_tags(config: OllamaConnectionConfig) -> list[str]:
    """Shared by test_connection and list_models -- one real call to
    /api/tags, no generation (and therefore no model-name guess) required.
    Raises on any failure; callers decide how to surface that."""
    url = f"{_base_url(config)}/api/tags"
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [m.get("name", "?") for m in data.get("models", [])]


def build_client(config: OllamaConnectionConfig):
    # Module-qualified lookup at call time -- same monkeypatchability
    # precedent as anthropic_connection.build_client.
    from backend.llm import ollama_client

    return ollama_client.OllamaLLMClient(host=_base_url(config))


def test_connection(config: OllamaConnectionConfig) -> ConnectionTestResult:
    try:
        model_names = _fetch_tags(config)
    except urllib.error.URLError as e:
        url = f"{_base_url(config)}/api/tags"
        return ConnectionTestResult(success=False, message=f"Could not reach Ollama at {url}: {e}")
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"Ollama connection test failed: {e}")

    return ConnectionTestResult(
        success=True,
        message=f"Connected to Ollama at {_base_url(config)}. Models available: {', '.join(model_names) or '(none pulled)'}",
    )


def list_models(config: OllamaConnectionConfig) -> list[str]:
    # spec-006 §9: real, live model names for the llm_call model-field
    # dropdown. Raises on failure -- the API layer (GET
    # /connections/{name}/models) turns that into a 502.
    return _fetch_tags(config)


def complete_with_tools(
    config: OllamaConnectionConfig,
    *,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[ToolDefinition],
    max_tokens: int,
) -> ToolCallResponse:
    """spec-008 §5: a tool-calling-capable completion call. Posts to
    /api/chat (not /api/generate, which has no tools support) -- Ollama's
    tool-calling wire format is OpenAI-compatible, confirmed live against a
    real server before implementation: tool definitions nest under
    `{"type": "function", "function": {name, description, parameters}}`,
    and a requested tool call's arguments arrive already parsed as a dict
    (not a JSON string) under `message.tool_calls[].function.arguments`.

    `temperature: 0` is deliberate, not an oversight: live-verifying this
    spec against qwen2.5:14b showed the model's default sampling
    temperature made it genuinely unreliable at actually populating
    `tool_calls` -- it would sometimes emit a plausible-looking tool call
    as plain text instead of the structured field, reproduced identically
    via raw curl with the exact same payload (so a model behavior, not a
    bug here). Forcing temperature 0 made it consistently reliable across
    repeated live attempts. Tool-calling's entire point is precise,
    structured output, so deterministic sampling is the right default for
    this call specifically -- unlike `llm_call`, which is free-form
    generation and rightly leaves sampling behavior alone.
    """
    wire_messages: list[dict[str, Any]] = []
    if system_prompt:
        wire_messages.append({"role": "system", "content": system_prompt})
    wire_messages.extend(messages)

    wire_tools = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]

    payload = {
        "model": model,
        "messages": wire_messages,
        "tools": wire_tools,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0},
    }
    request = urllib.request.Request(
        f"{_base_url(config)}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama tool-calling request to {_base_url(config)} failed: {e}") from e

    message = data.get("message", {})
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls = [
        ToolCallRequest(
            id=tc.get("id") or f"call_{i}",
            name=tc["function"]["name"],
            arguments=tc["function"]["arguments"],
        )
        for i, tc in enumerate(raw_tool_calls)
    ]

    return ToolCallResponse(
        text=None if tool_calls else message.get("content", ""),
        tool_calls=tool_calls,
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
    )


register_connection_type(
    "ollama",
    category="local",
    config_model=OllamaConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
    list_models=list_models,
    complete_with_tools=complete_with_tools,
)

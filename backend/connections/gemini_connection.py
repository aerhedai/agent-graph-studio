"""The `gemini` connection type -- a "cloud" connection needing an API key.
Mirrors anthropic_connection.py's shape exactly, following the pattern
documented in backend/connections/__init__.py: a new connection type is
just a new module here plus one line added to that package's imports --
no changes needed to base.py, resolver.py, the API layer, or the frontend
connection picker.

Gemini's tool-calling shape is a genuine third shape, distinct from both
Anthropic's and OpenAI's (which Ollama's own wire format already mirrors,
per ollama_connection.py's docstring) -- Gemini uses "user"/"model" roles
(not "assistant"), has no top-level "system" message (system prompt is a
separate `system_instruction` config field, same as the plain `complete()`
call). `types.FunctionCall`/`types.FunctionResponse` both carry their own
`id` field (confirmed by inspecting the installed `google-genai` SDK's
model fields directly, not assumed) -- threaded through via
`ToolCallRequest.id` exactly like Anthropic's `tool_use.id`/OpenAI's
`tool_calls[].id`, falling back to a synthesized `call_{i}` only when the
API genuinely doesn't supply one (the same synthesized-id precedent
ollama_connection.py establishes for a provider whose wire format doesn't
supply one at all).

Gemini's "thinking" models (confirmed live, not from docs, against a real
account: gemini-3.5-flash-lite) additionally require a `thought_signature`
-- an opaque token returned alongside a function-call part -- to be
replayed back on that exact same part when it's included in a later turn's
conversation history; omitting it is a real, live-confirmed 400
("Function call is missing a thought_signature in functionCall parts").
Carried via `ToolCallRequest.metadata["thought_signature"]` (see that
field's own docstring for why it isn't merged into `arguments` instead).
"""

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


class GeminiConnectionConfig(BaseModel):
    api_key: str


def build_client(config: GeminiConnectionConfig):
    # Module-qualified lookup at call time (not a top-of-file import) so
    # tests can monkeypatch backend.llm.gemini_client.GeminiLLMClient, same
    # precedent as anthropic_connection.py's build_client.
    from backend.llm import gemini_client

    return gemini_client.GeminiLLMClient(api_key=config.api_key)


def _list_model_names(config: GeminiConnectionConfig) -> list[str]:
    """Shared by test_connection and list_models -- one real, free call to
    the models list endpoint. Raises on failure; callers decide how to
    surface that. Strips the "models/" prefix Gemini's API returns so
    displayed names match the plain form used elsewhere (e.g.
    "gemini-2.0-flash", not "models/gemini-2.0-flash")."""
    from google import genai

    client = genai.Client(api_key=config.api_key)
    return [m.name.removeprefix("models/") for m in client.models.list()]


def test_connection(config: GeminiConnectionConfig) -> ConnectionTestResult:
    try:
        _list_model_names(config)
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"Gemini connection failed: {e}")
    return ConnectionTestResult(success=True, message="Connected to the Gemini API successfully.")


def list_models(config: GeminiConnectionConfig) -> list[str]:
    return _list_model_names(config)


def _to_gemini_content(message: dict[str, Any]):
    from google.genai import types

    role = message["role"]
    if role == "tool":
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=message.get("tool_call_id"),
                        name=message.get("name", ""),
                        response={"result": message["content"]},
                    )
                )
            ],
        )
    if role == "assistant" and message.get("tool_calls"):
        parts = []
        if message.get("content"):
            parts.append(types.Part(text=message["content"]))
        for call in message["tool_calls"]:
            thought_signature = (call.get("metadata") or {}).get("thought_signature")
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(id=call["id"], name=call["name"], args=call["arguments"]),
                    thought_signature=thought_signature,
                )
            )
        return types.Content(role="model", parts=parts)
    return types.Content(role="model" if role == "assistant" else "user", parts=[types.Part(text=message["content"])])


def complete_with_tools(
    config: GeminiConnectionConfig,
    *,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[ToolDefinition],
    max_tokens: int,
) -> ToolCallResponse:
    """spec-008 §5-style tool-calling completion, via Gemini's own
    function-calling API. The thought_signature/id handling below was added
    after a real, live failure against a real account (a genuine
    "Simple telegram assistant" graph using gemini-3.5-flash-lite) --
    unlike anthropic_connection.py's complete_with_tools, this one has now
    been exercised against a real API, just not by this session directly
    (no Gemini credentials available here; the fix is grounded in the
    real error message and the installed SDK's own field definitions)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.api_key)

    contents = [_to_gemini_content(m) for m in messages]
    function_declarations = [
        types.FunctionDeclaration(name=t.name, description=t.description, parameters=t.parameters)
        for t in tools
    ]
    gen_config = types.GenerateContentConfig(
        system_instruction=system_prompt or None,
        max_output_tokens=max_tokens,
        tools=[types.Tool(function_declarations=function_declarations)],
    )

    try:
        response = client.models.generate_content(model=model, contents=contents, config=gen_config)
    except Exception as e:
        raise RuntimeError(f"Gemini tool-calling request failed: {e}") from e

    candidate_parts = response.candidates[0].content.parts if response.candidates else []
    tool_calls = [
        ToolCallRequest(
            id=part.function_call.id or f"call_{i}",
            name=part.function_call.name,
            arguments=dict(part.function_call.args or {}),
            metadata={"thought_signature": part.thought_signature} if part.thought_signature else None,
        )
        for i, part in enumerate(candidate_parts)
        if getattr(part, "function_call", None) is not None
    ]

    usage = response.usage_metadata
    return ToolCallResponse(
        text=None if tool_calls else (response.text or ""),
        tool_calls=tool_calls,
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=(usage.candidates_token_count or 0) if usage else 0,
    )


register_connection_type(
    "gemini",
    category="cloud",
    config_model=GeminiConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
    list_models=list_models,
    complete_with_tools=complete_with_tools,
)

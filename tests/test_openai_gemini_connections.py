from __future__ import annotations

from backend.connections.base import ToolCallRequest, ToolDefinition, default_connection_registry


# --- registry ---------------------------------------------------------------


def test_default_registry_has_openai_and_gemini():
    openai_def = default_connection_registry.get("openai")
    gemini_def = default_connection_registry.get("gemini")
    assert openai_def is not None and openai_def.category == "cloud"
    assert gemini_def is not None and gemini_def.category == "cloud"
    assert openai_def.list_models is not None
    assert gemini_def.list_models is not None
    assert openai_def.complete_with_tools is not None
    assert gemini_def.complete_with_tools is not None


# --- OpenAI: list_models / test_connection ----------------------------------


def test_openai_list_models_returns_real_ids(monkeypatch):
    import openai as openai_module

    class _FakeModel:
        def __init__(self, id_):
            self.id = id_

    class _FakeModelsList:
        data = [_FakeModel("gpt-5"), _FakeModel("gpt-5-mini")]

    class _FakeModels:
        def list(self):
            return _FakeModelsList()

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    import backend.connections.openai_connection as openai_connection_module

    config = openai_connection_module.OpenAIConnectionConfig(api_key="sk-test")
    assert openai_connection_module.list_models(config) == ["gpt-5", "gpt-5-mini"]

    result = openai_connection_module.test_connection(config)
    assert result.success is True


def test_openai_test_connection_reports_failure_on_error(monkeypatch):
    import openai as openai_module

    class _FakeModels:
        def list(self):
            raise RuntimeError("invalid api key")

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    import backend.connections.openai_connection as openai_connection_module

    config = openai_connection_module.OpenAIConnectionConfig(api_key="sk-bad")
    result = openai_connection_module.test_connection(config)
    assert result.success is False
    assert "invalid api key" in result.message


# --- OpenAI: complete_with_tools --------------------------------------------


def test_openai_complete_with_tools_returns_tool_calls_when_model_requests_one(monkeypatch):
    import json

    import openai as openai_module

    class _FakeFunction:
        name = "multiply"
        arguments = json.dumps({"a": "6", "b": "7"})

    class _FakeToolCall:
        id = "call_1"
        function = _FakeFunction()

    class _FakeMessage:
        content = None
        tool_calls = [_FakeToolCall()]

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        prompt_tokens = 20
        completion_tokens = 6

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    captured_kwargs = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    import backend.connections.openai_connection as openai_connection_module

    config = openai_connection_module.OpenAIConnectionConfig(api_key="sk-test")
    tool = ToolDefinition(
        name="multiply",
        description="multiplies",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    )

    response = openai_connection_module.complete_with_tools(
        config,
        model="gpt-5",
        system_prompt="be helpful",
        messages=[{"role": "user", "content": "6 times 7"}],
        tools=[tool],
        max_tokens=100,
    )

    assert response.text is None
    assert response.tool_calls == [ToolCallRequest(id="call_1", name="multiply", arguments={"a": "6", "b": "7"})]
    assert response.input_tokens == 20
    assert response.output_tokens == 6
    assert captured_kwargs["tools"][0]["function"]["name"] == "multiply"
    assert captured_kwargs["messages"][0] == {"role": "system", "content": "be helpful"}


def test_openai_complete_with_tools_translates_tool_result_messages(monkeypatch):
    import openai as openai_module

    class _FakeMessage:
        content = "The answer is 42."
        tool_calls = None

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        prompt_tokens = 30
        completion_tokens = 10

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    captured_kwargs = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    import backend.connections.openai_connection as openai_connection_module

    config = openai_connection_module.OpenAIConnectionConfig(api_key="sk-test")
    messages = [
        {"role": "user", "content": "6 times 7"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "name": "multiply", "arguments": {"a": "6", "b": "7"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "multiply", "content": "42"},
    ]

    response = openai_connection_module.complete_with_tools(
        config, model="gpt-5", system_prompt="", messages=messages, tools=[], max_tokens=100
    )

    assert response.text == "The answer is 42."
    assert response.tool_calls == []
    wire_messages = captured_kwargs["messages"]
    assert wire_messages[1]["tool_calls"][0]["function"]["arguments"] == '{"a": "6", "b": "7"}'
    assert wire_messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


# --- Gemini: list_models / test_connection ----------------------------------


def test_gemini_list_models_strips_models_prefix(monkeypatch):
    from google import genai as genai_module

    class _FakeModel:
        def __init__(self, name):
            self.name = name

    class _FakeModels:
        def list(self):
            return [_FakeModel("models/gemini-2.0-flash"), _FakeModel("models/gemini-2.0-pro")]

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    import backend.connections.gemini_connection as gemini_connection_module

    config = gemini_connection_module.GeminiConnectionConfig(api_key="test-key")
    assert gemini_connection_module.list_models(config) == ["gemini-2.0-flash", "gemini-2.0-pro"]

    result = gemini_connection_module.test_connection(config)
    assert result.success is True


def test_gemini_test_connection_reports_failure_on_error(monkeypatch):
    from google import genai as genai_module

    class _FakeModels:
        def list(self):
            raise RuntimeError("invalid api key")

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    import backend.connections.gemini_connection as gemini_connection_module

    config = gemini_connection_module.GeminiConnectionConfig(api_key="bad-key")
    result = gemini_connection_module.test_connection(config)
    assert result.success is False
    assert "invalid api key" in result.message


# --- Gemini: complete_with_tools --------------------------------------------


def test_gemini_complete_with_tools_returns_tool_calls_when_model_requests_one(monkeypatch):
    from google import genai as genai_module

    class _FakeFunctionCall:
        id = None  # a real response doesn't always supply one -- fall back to a synthesized id
        name = "multiply"
        args = {"a": "6", "b": "7"}

    class _FakePart:
        function_call = _FakeFunctionCall()
        thought_signature = None

    class _FakeContent:
        parts = [_FakePart()]

    class _FakeCandidate:
        content = _FakeContent()

    class _FakeUsage:
        prompt_token_count = 20
        candidates_token_count = 6

    class _FakeResponse:
        candidates = [_FakeCandidate()]
        usage_metadata = _FakeUsage()
        text = None

    captured_kwargs = {}

    class _FakeModels:
        def generate_content(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    import backend.connections.gemini_connection as gemini_connection_module

    config = gemini_connection_module.GeminiConnectionConfig(api_key="test-key")
    tool = ToolDefinition(
        name="multiply",
        description="multiplies",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    )

    response = gemini_connection_module.complete_with_tools(
        config,
        model="gemini-2.0-flash",
        system_prompt="be helpful",
        messages=[{"role": "user", "content": "6 times 7"}],
        tools=[tool],
        max_tokens=100,
    )

    assert response.text is None
    assert response.tool_calls == [ToolCallRequest(id="call_0", name="multiply", arguments={"a": "6", "b": "7"})]
    assert response.input_tokens == 20
    assert response.output_tokens == 6
    assert captured_kwargs["model"] == "gemini-2.0-flash"


def test_gemini_complete_with_tools_captures_real_id_and_thought_signature(monkeypatch):
    """Regression test for a real, live-confirmed bug: Gemini's "thinking"
    models (e.g. gemini-3.5-flash-lite) reject a replayed function-call
    turn that's missing its thought_signature with a real 400 error. This
    proves complete_with_tools captures both the real id (when the API
    supplies one) and the thought_signature from the response, via
    ToolCallRequest.metadata."""
    from google import genai as genai_module

    class _FakeFunctionCall:
        id = "fc_real_id_123"
        name = "multiply"
        args = {"a": "6", "b": "7"}

    class _FakePart:
        function_call = _FakeFunctionCall()
        thought_signature = b"opaque-signature-bytes"

    class _FakeContent:
        parts = [_FakePart()]

    class _FakeCandidate:
        content = _FakeContent()

    class _FakeUsage:
        prompt_token_count = 20
        candidates_token_count = 6

    class _FakeResponse:
        candidates = [_FakeCandidate()]
        usage_metadata = _FakeUsage()
        text = None

    class _FakeModels:
        def generate_content(self, **kwargs):
            return _FakeResponse()

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    import backend.connections.gemini_connection as gemini_connection_module

    config = gemini_connection_module.GeminiConnectionConfig(api_key="test-key")
    response = gemini_connection_module.complete_with_tools(
        config,
        model="gemini-3.5-flash-lite",
        system_prompt="",
        messages=[{"role": "user", "content": "6 times 7"}],
        tools=[],
        max_tokens=100,
    )

    assert response.tool_calls == [
        ToolCallRequest(
            id="fc_real_id_123",
            name="multiply",
            arguments={"a": "6", "b": "7"},
            metadata={"thought_signature": b"opaque-signature-bytes"},
        )
    ]


def test_gemini_replays_thought_signature_and_id_on_the_next_turn(monkeypatch):
    """The other half of the round trip: when agent.py replays a prior
    assistant tool_calls message back (a real multi-turn tool loop), the
    captured id and thought_signature must both land on the reconstructed
    Part -- this is exactly what the real Gemini API rejected without."""
    import backend.connections.gemini_connection as gemini_connection_module

    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "fc_real_id_123",
                "name": "multiply",
                "arguments": {"a": "6", "b": "7"},
                "metadata": {"thought_signature": b"opaque-signature-bytes"},
            }
        ],
    }
    content = gemini_connection_module._to_gemini_content(message)
    part = content.parts[0]
    assert part.function_call.id == "fc_real_id_123"
    assert part.function_call.name == "multiply"
    assert part.thought_signature == b"opaque-signature-bytes"

    tool_result_message = {"role": "tool", "tool_call_id": "fc_real_id_123", "name": "multiply", "content": "42"}
    result_content = gemini_connection_module._to_gemini_content(tool_result_message)
    assert result_content.parts[0].function_response.id == "fc_real_id_123"


def test_gemini_complete_with_tools_translates_tool_result_messages(monkeypatch):
    from google import genai as genai_module

    class _FakePart:
        function_call = None

    class _FakeContent:
        parts = [_FakePart()]

    class _FakeCandidate:
        content = _FakeContent()

    class _FakeUsage:
        prompt_token_count = 30
        candidates_token_count = 10

    class _FakeResponse:
        candidates = [_FakeCandidate()]
        usage_metadata = _FakeUsage()
        text = "The answer is 42."

    captured_kwargs = {}

    class _FakeModels:
        def generate_content(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    import backend.connections.gemini_connection as gemini_connection_module

    config = gemini_connection_module.GeminiConnectionConfig(api_key="test-key")
    messages = [
        {"role": "user", "content": "6 times 7"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_0", "name": "multiply", "arguments": {"a": "6", "b": "7"}}],
        },
        {"role": "tool", "tool_call_id": "call_0", "name": "multiply", "content": "42"},
    ]

    response = gemini_connection_module.complete_with_tools(
        config, model="gemini-2.0-flash", system_prompt="", messages=messages, tools=[], max_tokens=100
    )

    assert response.text == "The answer is 42."
    assert response.tool_calls == []
    contents = captured_kwargs["contents"]
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "multiply"
    assert contents[2].role == "user"
    assert contents[2].parts[0].function_response.name == "multiply"
    assert contents[2].parts[0].function_response.response == {"result": "42"}

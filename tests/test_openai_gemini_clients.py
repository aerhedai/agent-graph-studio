from __future__ import annotations

from backend.llm.gemini_client import GeminiLLMClient
from backend.llm.openai_client import OpenAILLMClient


def test_openai_complete_success(monkeypatch):
    import openai as openai_module

    class _FakeMessage:
        content = "hi there"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        prompt_tokens = 4
        completion_tokens = 6

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    client = OpenAILLMClient(api_key="sk-test")
    result = client.complete(model="gpt-5", system_prompt="be nice", prompt="hello", max_tokens=64)

    assert result.text == "hi there"
    assert result.input_tokens == 4
    assert result.output_tokens == 6
    assert captured["messages"] == [{"role": "system", "content": "be nice"}, {"role": "user", "content": "hello"}]
    assert captured["max_tokens"] == 64


def test_openai_complete_omits_system_when_empty(monkeypatch):
    import openai as openai_module

    class _FakeMessage:
        content = "ok"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, api_key=None):
            self.chat = _FakeChat()

    monkeypatch.setattr(openai_module, "OpenAI", _FakeOpenAIClient)

    OpenAILLMClient(api_key="sk-test").complete(model="gpt-5", system_prompt="", prompt="hi", max_tokens=10)
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_gemini_complete_success(monkeypatch):
    from google import genai as genai_module

    class _FakeUsage:
        prompt_token_count = 4
        candidates_token_count = 6

    class _FakeResponse:
        text = "hi there"
        usage_metadata = _FakeUsage()

    captured = {}

    class _FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeGenaiClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai_module, "Client", _FakeGenaiClient)

    client = GeminiLLMClient(api_key="test-key")
    result = client.complete(model="gemini-2.0-flash", system_prompt="be nice", prompt="hello", max_tokens=64)

    assert result.text == "hi there"
    assert result.input_tokens == 4
    assert result.output_tokens == 6
    assert captured["model"] == "gemini-2.0-flash"
    assert captured["contents"] == "hello"
    assert captured["config"].system_instruction == "be nice"
    assert captured["config"].max_output_tokens == 64

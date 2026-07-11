from __future__ import annotations

import json
import urllib.error

import pytest

from backend.llm.ollama_client import OllamaLLMClient


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_complete_success(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"response": "hi there", "prompt_eval_count": 4, "eval_count": 6})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaLLMClient(host="http://localhost:11434")
    result = client.complete(
        model="llama3.2", system_prompt="be nice", prompt="hello", max_tokens=64
    )

    assert result.text == "hi there"
    assert result.input_tokens == 4
    assert result.output_tokens == 6
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["body"]["model"] == "llama3.2"
    assert captured["body"]["prompt"] == "hello"
    assert captured["body"]["system"] == "be nice"
    assert captured["body"]["options"]["num_predict"] == 64


def test_complete_omits_system_when_empty(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"response": "ok", "prompt_eval_count": 1, "eval_count": 1})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    OllamaLLMClient().complete(model="llama3.2", system_prompt="", prompt="hi", max_tokens=10)

    assert "system" not in captured["body"]


def test_complete_missing_usage_fields_default_to_zero(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeHTTPResponse({"response": "ok"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = OllamaLLMClient().complete(
        model="llama3.2", system_prompt="", prompt="hi", max_tokens=10
    )

    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_complete_wraps_connection_failure_as_runtime_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaLLMClient(host="http://localhost:11434")

    with pytest.raises(RuntimeError):
        client.complete(model="llama3.2", system_prompt="", prompt="hi", max_tokens=10)

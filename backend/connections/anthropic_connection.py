"""The `anthropic` connection type -- a "cloud" connection needing an API
key. Mirrors ollama_connection.py's shape exactly (spec-006 §4/§5)."""

from __future__ import annotations

from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type

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


register_connection_type(
    "anthropic",
    category="cloud",
    config_model=AnthropicConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
)

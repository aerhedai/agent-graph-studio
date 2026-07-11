from __future__ import annotations

from typing import Any, Callable

from backend.llm.client import LLMClient

ProviderFactory = Callable[[dict[str, Any]], LLMClient]

_PROVIDERS: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    if name in _PROVIDERS:
        raise ValueError(f"Duplicate LLM provider registration: {name}")
    _PROVIDERS[name] = factory


def build_client(provider: str, provider_options: dict[str, Any]) -> LLMClient:
    factory = _PROVIDERS.get(provider)
    if factory is None:
        raise ValueError(f"Unknown LLM provider: {provider!r}")
    return factory(provider_options)


def _build_anthropic(provider_options: dict[str, Any]) -> LLMClient:
    # Module-qualified lookup at call time (not a top-of-file import) so
    # tests can monkeypatch backend.llm.anthropic_client.AnthropicLLMClient.
    from backend.llm import anthropic_client

    return anthropic_client.AnthropicLLMClient()


def _build_ollama(provider_options: dict[str, Any]) -> LLMClient:
    from backend.llm import ollama_client

    host = provider_options.get("host", ollama_client.DEFAULT_HOST)
    return ollama_client.OllamaLLMClient(host=host)


register_provider("anthropic", _build_anthropic)
register_provider("ollama", _build_ollama)

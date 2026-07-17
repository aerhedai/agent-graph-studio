"""The `ollama` connection type -- a "local" connection needing just a
host/port. Mirrors anthropic_connection.py's shape exactly (spec-006
§4/§5)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type


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


register_connection_type(
    "ollama",
    category="local",
    config_model=OllamaConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
    list_models=list_models,
)

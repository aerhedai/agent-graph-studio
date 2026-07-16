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


def build_client(config: OllamaConnectionConfig):
    # Module-qualified lookup at call time -- same monkeypatchability
    # precedent as anthropic_connection.build_client.
    from backend.llm import ollama_client

    return ollama_client.OllamaLLMClient(host=_base_url(config))


def test_connection(config: OllamaConnectionConfig) -> ConnectionTestResult:
    # Lightweight real check: list locally-pulled models, no generation
    # (and therefore no model-name guess) required -- spec-006 §5's own
    # suggested check for Ollama.
    url = f"{_base_url(config)}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return ConnectionTestResult(success=False, message=f"Could not reach Ollama at {url}: {e}")
    except Exception as e:
        return ConnectionTestResult(success=False, message=f"Ollama connection test failed: {e}")

    model_names = [m.get("name", "?") for m in data.get("models", [])]
    return ConnectionTestResult(
        success=True,
        message=f"Connected to Ollama at {_base_url(config)}. Models available: {', '.join(model_names) or '(none pulled)'}",
    )


register_connection_type(
    "ollama",
    category="local",
    config_model=OllamaConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
)

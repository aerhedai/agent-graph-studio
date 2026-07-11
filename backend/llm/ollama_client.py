from __future__ import annotations

import json
import urllib.error
import urllib.request

from backend.llm.client import LLMResponse

DEFAULT_HOST = "http://localhost:11434"


class OllamaLLMClient:
    """Talks to a local Ollama instance via its REST API. Errors (connection
    refused, model not pulled, etc.) are surfaced as a plain RuntimeError --
    spec-002 §7 recommends generic error handling over a distinct "model not
    pulled" case for MVP."""

    def __init__(self, host: str = DEFAULT_HOST) -> None:
        self._host = host.rstrip("/")

    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system_prompt:
            payload["system"] = system_prompt

        request = urllib.request.Request(
            f"{self._host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request to {self._host} failed: {e}") from e

        return LLMResponse(
            text=data.get("response", ""),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

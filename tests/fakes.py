from __future__ import annotations

from typing import Callable

from backend.llm.client import LLMResponse


class FakeLLMClient:
    """Hand-written stand-in for LLMClient -- records every call it received
    and returns either a fixed response or whatever `on_complete` computes,
    so tests never need a real ANTHROPIC_API_KEY or network access."""

    def __init__(
        self,
        response: LLMResponse | None = None,
        on_complete: Callable[..., LLMResponse] | None = None,
    ) -> None:
        self._response = response
        self._on_complete = on_complete
        self.calls: list[dict] = []

    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "prompt": prompt,
                "max_tokens": max_tokens,
            }
        )
        if self._on_complete is not None:
            return self._on_complete(
                model=model, system_prompt=system_prompt, prompt=prompt, max_tokens=max_tokens
            )
        assert self._response is not None
        return self._response


class FailingLLMClient:
    """LLMClient stand-in that always raises, for testing failure propagation."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, **kwargs) -> LLMResponse:
        raise self._exc

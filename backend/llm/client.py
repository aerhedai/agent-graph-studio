from __future__ import annotations

from typing import NamedTuple, Protocol


class LLMResponse(NamedTuple):
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse: ...


class AnthropicLLMClient:
    """Talks to the real Claude API. Constructed lazily by the engine -- only
    when a graph actually contains an llm_call node and no client was
    injected -- so graphs without llm_call nodes never require ANTHROPIC_API_KEY."""

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic()

    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        response = self._client.messages.create(**kwargs)
        text = next((block.text for block in response.content if block.type == "text"), "")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

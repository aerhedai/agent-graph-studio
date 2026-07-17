from __future__ import annotations

from backend.llm.client import LLMResponse


class AnthropicLLMClient:
    """Talks to the real Claude API. Constructed by the `anthropic`
    connection type (backend/connections/anthropic_connection.py) -- only
    when a graph actually resolves an anthropic-typed connection -- so
    graphs that don't reference one (or tests injecting a fake) never
    require ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

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

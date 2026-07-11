from __future__ import annotations

from backend.llm.client import LLMResponse


class AnthropicLLMClient:
    """Talks to the real Claude API. Constructed lazily by the provider
    registry (backend/llm/providers.py) -- only when a node actually needs
    it and no client was injected via ExecutionContext.resources -- so
    graphs without an anthropic-provider llm_call node (or tests injecting
    a fake) never require ANTHROPIC_API_KEY."""

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

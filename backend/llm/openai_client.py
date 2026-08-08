from __future__ import annotations

from backend.llm.client import LLMResponse


class OpenAILLMClient:
    """Talks to the real OpenAI API. Constructed by the `openai` connection
    type (backend/connections/openai_connection.py) -- only when a graph
    actually resolves an openai-typed connection, mirroring
    AnthropicLLMClient's lazy-construction precedent exactly."""

    def __init__(self, api_key: str | None = None) -> None:
        import openai

        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        text = response.choices[0].message.content or ""
        return LLMResponse(
            text=text,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

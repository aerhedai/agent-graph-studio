from __future__ import annotations

from backend.llm.client import LLMResponse


class GeminiLLMClient:
    """Talks to the real Google Gemini API. Constructed by the `gemini`
    connection type (backend/connections/gemini_connection.py) -- only when
    a graph actually resolves a gemini-typed connection, mirroring
    AnthropicLLMClient's lazy-construction precedent exactly."""

    def __init__(self, api_key: str | None = None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def complete(
        self, *, model: str, system_prompt: str, prompt: str, max_tokens: int
    ) -> LLMResponse:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=max_tokens,
        )
        response = self._client.models.generate_content(model=model, contents=prompt, config=config)
        usage = response.usage_metadata
        return LLMResponse(
            text=response.text or "",
            input_tokens=usage.prompt_token_count or 0,
            output_tokens=usage.candidates_token_count or 0,
        )

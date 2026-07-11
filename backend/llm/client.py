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

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TokenCost(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TraceRecord(BaseModel):
    run_id: str
    node_id: str
    node_type: str
    started_at: str
    finished_at: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    token_cost: TokenCost = Field(default_factory=TokenCost)
    side_effect: bool = False
    error: str | None = None


class RunResult(BaseModel):
    result: dict[str, Any]
    trace: list[TraceRecord]

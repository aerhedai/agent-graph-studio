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
    child_traces: list[list["TraceRecord"]] | None = None
    """Nested full traces for node types that internally re-invoke run_graph
    (currently only `loop`: one inner list per iteration). None for every
    other node type. Not used by fan_out -- its branches are ordinary
    sibling nodes in the same top-level trace, not a separate sub-run; see
    spec-004 Implementation notes."""
    error: str | None = None


TraceRecord.model_rebuild()


class RunResult(BaseModel):
    result: dict[str, Any]
    trace: list[TraceRecord]

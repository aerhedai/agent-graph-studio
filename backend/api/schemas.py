from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.execution.trace import TraceRecord


class SlotInfo(BaseModel):
    name: str
    type: dict[str, Any]
    required: bool = True


class NodeTypeInfo(BaseModel):
    type: str
    config_schema: dict[str, Any]
    dynamic_schema: bool
    """True for node types whose actual ports depend on per-instance config
    (code, mcp_call, fan_out, merge -- SPEC-002's resolve_slots) rather than
    being fixed for the whole type. `inputs`/`outputs` are empty when this is
    true; call POST /node-types/{type}/resolve-slots with a real config to
    get the actual ports."""
    inputs: list[SlotInfo]
    outputs: list[SlotInfo]


class ResolveSlotsRequest(BaseModel):
    config: dict[str, Any] = {}


class ResolveSlotsResponse(BaseModel):
    inputs: list[SlotInfo]
    outputs: list[SlotInfo]


class RunSubmitResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str  # "running" | "completed" | "failed"
    graph_id: str | None = None
    trigger_source: str = "manual"
    """spec-010: which of manual/schedule/webhook started this run --
    populated for every run going forward; may be absent/defaulted for a
    run whose only surviving record predates this field (there are none in
    practice, since this ships atomically with the runs table itself)."""
    running_node_ids: list[str]
    trace: list[TraceRecord]
    result: dict[str, Any] | None
    error: str | None


class RunSummary(BaseModel):
    """One row of a GET /runs listing -- no trace/result, per spec-010 §5's
    "keep list responses light"; fetch GET /runs/{run_id} for the full
    record."""

    run_id: str
    graph_id: str | None
    status: str
    trigger_source: str
    started_at: str
    finished_at: str | None


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int
    limit: int
    offset: int


class ConnectionTypeInfo(BaseModel):
    type: str
    category: str  # "local" | "cloud"
    config_schema: dict[str, Any]
    supports_model_listing: bool
    """spec-006 §9: whether GET /connections/{name}/models is meaningful for
    connections of this type -- lets the frontend decide up front whether to
    render the llm_call model field as a dropdown, without trial-and-error."""
    supports_tool_calling: bool
    """spec-008 §5: whether this connection type can be used by an `agent`
    node. Computed from `complete_with_tools is not None`, same precedent
    as supports_model_listing -- no separate capability flag to drift out
    of sync with the actual callable."""


class ConnectionInfo(BaseModel):
    name: str
    type: str
    """Never includes `config` -- secrets (API keys, etc.) stay server-side
    only and are never returned over the API (spec-006 §5)."""


class CreateConnectionRequest(BaseModel):
    name: str
    type: str
    config: dict[str, Any] = {}


class TestConnectionRequest(BaseModel):
    type: str | None = None
    config: dict[str, Any] | None = None
    """When both are set, tests that type+config directly without requiring
    it to already be saved (the canvas's "Test Connection before Save"
    flow). When omitted, re-tests the already-saved connection by name."""


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


class TriggerInfo(BaseModel):
    node_id: str
    type: str  # "schedule_trigger" | "webhook_trigger"
    endpoint_or_schedule: str
    """The node's cron expression (schedule_trigger) or its derived webhook
    URL path (webhook_trigger) -- spec-009 §5."""


class ActivateGraphResponse(BaseModel):
    status: str
    triggers: list[TriggerInfo]


class ActiveGraphInfo(BaseModel):
    graph_id: str
    triggers: list[TriggerInfo]

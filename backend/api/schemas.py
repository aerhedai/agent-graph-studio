from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.execution.trace import TraceRecord


class SlotInfo(BaseModel):
    name: str
    type: dict[str, Any]
    required: bool = True


class SubNodeSlotInfo(BaseModel):
    cardinality: str  # "one" | "zero_or_one" | "many"
    accepts_role: str | None = None
    """The sub_node_role a connected sub-node's type must declare to be
    valid in this slot. None means any node type is accepted (e.g. the
    `tools` slot) -- spec-012 §4."""


class NodeTypeInfo(BaseModel):
    type: str
    config_schema: dict[str, Any]
    dynamic_schema: bool
    """True for node types whose actual ports depend on per-instance config
    (code, mcp_call, fan_out, merge -- SPEC-002's resolve_slots) rather than
    being fixed for the whole type, OR whose ports mirror a connected
    sub-node (webhook_trigger -- spec-012's resolve_slots_from_sub_node).
    `inputs`/`outputs` are empty when this is true; for config-based
    dynamism call POST /node-types/{type}/resolve-slots, for sub-node-
    mirrored dynamism the canvas resolves it client-side (the connected
    sub-node's own static outputs, already known from this same endpoint)."""
    inputs: list[SlotInfo]
    outputs: list[SlotInfo]
    sub_node_slots: dict[str, SubNodeSlotInfo] | None = None
    """spec-012 §4: this type's own declared sub-node slots, e.g. agent's
    model/memory/tools. None for non-root types."""
    sub_node_role: str | None = None
    """spec-012 §4: the role this type can fill in some root's slot (e.g.
    "model", "trigger_adapter"). None for ordinary/root types."""
    resolve_slots_from_sub_node: str | None = None
    """spec-012 §4: names the sub-node slot whose connected sub-node's own
    outputs this root's outputs mirror (e.g. webhook_trigger's
    "trigger_adapter"). None for every type whose outputs are fixed
    regardless of what's connected (e.g. agent, whose sub_node_slots are
    non-null but whose own `answer` output never changes). Exposed so the
    canvas can resolve a root's real ports client-side generically -- no
    slot name hardcoded in frontend code."""


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
    supports_embedding: bool
    """spec-011 §4: whether this connection type can be used as an
    `ingest_document`/`vector_search` node's embedding_model_connection.
    Computed from `embed is not None`, same precedent as
    supports_model_listing/supports_tool_calling."""


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

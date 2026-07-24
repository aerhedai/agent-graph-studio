from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.execution.trace import TraceRecord
from backend.schema.models import GraphSpec


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
    category: str
    """spec-013 §4/§5: which palette section this type belongs to (e.g.
    "triggers", "core", "ai", "data", "connectivity") -- drives the
    canvas's categorized/collapsible palette. The palette derives its
    section list from whatever categories are actually present here,
    never a hardcoded list on the frontend."""
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
    integration: str | None = None
    """spec-019 §4: which app/integration this type belongs to under the
    "apps" category -- "telegram" for a manifest-backed type, or an
    `mcp_server` connection's own name for a dynamically-generated type.
    None for every non-app node type."""
    capability_group: str | None = None
    """spec-019 §4: curated sub-grouping within `integration` (e.g.
    "Messaging"). None for dynamically-generated MCP nodes, which have no
    curated grouping -- the palette renders those as Apps -> connection ->
    tool instead of the 3-level Apps -> App -> capability_group shape."""


class ResolveSlotsRequest(BaseModel):
    config: dict[str, Any] = {}


class ResolveSlotsResponse(BaseModel):
    inputs: list[SlotInfo]
    outputs: list[SlotInfo]


class RunSubmitResponse(BaseModel):
    run_id: str
    status: str


class PendingApprovalInfo(BaseModel):
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]


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
    active_sub_node_ids: list[str]
    """Live per-call activity signal: a sub-node (an agent's connected
    `model`, or a tool invoked directly via ADR-008's bypass) currently
    mid-call. Invisible to `running_node_ids` since none of this happens
    through the engine's own scheduler -- see
    `backend.nodes.agent._notify_sub_node_activity`. Always empty for a
    historical/persisted run (same reasoning as `running_node_ids` above)."""
    pending_approvals: list[PendingApprovalInfo] = []
    """spec-019: any approval-gated tool call (mcp_call, or a
    dynamically-generated MCP node from an untrusted mcp_server connection)
    currently blocked waiting for a decision -- POST
    /runs/{run_id}/approvals/{approval_id} answers it. Always empty for a
    historical/persisted run (nothing can still be waiting on one) or for
    a run whose approval gates were all already resolved or never hit."""
    trace: list[TraceRecord]
    result: dict[str, Any] | None
    error: str | None
    run_by: str | None = None
    """spec-020: the user id who submitted this run, None for a
    schedule/webhook-triggered run or a shared-API-key caller."""


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


class ResolveApprovalRequest(BaseModel):
    approved: bool
    remember: bool = False
    """If true, this decision is remembered for (this run, this tool name)
    -- every subsequent call to the same tool within this run auto-resolves
    without asking again. Scoped to the run's lifetime, not persisted
    beyond it. Distinct from an mcp_server connection's `trusted` flag,
    which skips asking for that connection's nodes across every run."""


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


class RefreshCapabilitiesResponse(BaseModel):
    generated_types: list[str]
    """spec-019: the full, current set of node type names generated for
    this `mcp_server` connection after the refresh -- lets the canvas
    confirm what actually changed without a second GET /node-types round
    trip being strictly necessary (though it should still refetch the
    palette to render them)."""


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


# spec-015: saved graphs, giving GraphSpec a real server-side identity for
# the first time -- see backend/storage/graphs_store.py's module docstring.


class CreateGraphRequest(BaseModel):
    name: str
    spec: GraphSpec


class UpdateGraphRequest(BaseModel):
    name: str | None = None
    spec: GraphSpec | None = None


class GraphSummary(BaseModel):
    graph_id: str
    name: str
    is_active: bool
    updated_at: str


class GraphDetail(BaseModel):
    graph_id: str
    name: str
    spec: GraphSpec
    is_active: bool
    created_by: str | None = None
    """spec-020: the user id who created this graph, None for a
    pre-spec-020 graph or one created via the shared API key."""


# spec-018: the one app-level setting needed to auto-register external
# webhooks (Telegram) -- see backend/storage/settings_store.py.


class SettingsResponse(BaseModel):
    public_base_url: str | None


class UpdateSettingsRequest(BaseModel):
    public_base_url: str


class UpdateSettingsResponse(BaseModel):
    public_base_url: str
    warning: str | None = None


# --- spec-020: platform authentication --------------------------------


class MeResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str


class InviteRequest(BaseModel):
    email: str
    role: str = "member"


class InviteResponse(BaseModel):
    email: str
    role: str
    invited_by: str | None
    invited_at: str

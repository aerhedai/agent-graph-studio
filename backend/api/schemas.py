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
    running_node_ids: list[str]
    trace: list[TraceRecord]
    result: dict[str, Any] | None
    error: str | None


class ConnectionTypeInfo(BaseModel):
    type: str
    category: str  # "local" | "cloud"
    config_schema: dict[str, Any]
    supports_model_listing: bool
    """spec-006 §9: whether GET /connections/{name}/models is meaningful for
    connections of this type -- lets the frontend decide up front whether to
    render the llm_call model field as a dropdown, without trial-and-error."""


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

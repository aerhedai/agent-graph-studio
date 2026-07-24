"""FastAPI layer over the execution engine (spec-005).

Every route below is a plain `def`, never `async def` -- load-bearing, not a
style choice. `validate_graph()` (via POST /runs) and the resolve-slots logic
both transitively call `resolve_slots` for `mcp_call`, which internally does
its own `asyncio.run(...)` (backend/mcp/client.py). Calling that from a
coroutine already running on an event loop (which any `async def` route runs
on) raises "asyncio.run() cannot be called from a running event loop".
FastAPI/Starlette dispatches plain `def` routes through a worker thread
automatically (`run_in_threadpool`) -- the same "no event loop on this
thread" pattern already relied on for the `loop` node's recursive
`run_graph()` call. Every route here is plain `def` so this never has to be
reasoned about per-route.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

import backend.connections  # noqa: F401 -- import side effect registers every connection type
import backend.integrations  # noqa: F401 -- import side effect registers every integration's webhook-sync handler
import backend.nodes  # noqa: F401 -- import side effect registers every node type
from backend.api import runs
from backend.api.schemas import (
    ActivateGraphResponse,
    ActiveGraphInfo,
    ConnectionInfo,
    ConnectionTypeInfo,
    CreateConnectionRequest,
    CreateGraphRequest,
    GraphDetail,
    GraphSummary,
    NodeTypeInfo,
    PendingApprovalInfo,
    RefreshCapabilitiesResponse,
    ResolveApprovalRequest,
    ResolveSlotsRequest,
    ResolveSlotsResponse,
    RunListResponse,
    RunStatusResponse,
    RunSubmitResponse,
    RunSummary,
    SettingsResponse,
    SlotInfo,
    SubNodeSlotInfo,
    TestConnectionRequest,
    TestConnectionResponse,
    TriggerInfo,
    UpdateGraphRequest,
    UpdateSettingsRequest,
    UpdateSettingsResponse,
)
from backend.connections.base import default_connection_registry
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.resolver import resolve_connection_profiles, resolve_connections
from backend.connections.store import (
    add_connection,
    delete_connection,
    ensure_encryption_key_configured,
    get_connection,
    list_connections,
)
from backend.execution import approvals
from backend.mcp import generated_nodes
from backend.registry.base import default_registry, effective_inputs, effective_outputs
from backend.schema.models import GraphSpec, NodeSpec
from backend.storage import graphs_store, runs_store, settings_store
from backend.triggers import registry as trigger_registry
from backend.triggers import runner as trigger_runner
from backend.triggers import scheduler as trigger_scheduler
from backend.triggers import webhook_sync
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph

logger = logging.getLogger(__name__)


class MissingApiKeyError(RuntimeError):
    """spec-017: raised eagerly at API startup when AGENT_GRAPH_STUDIO_API_KEY
    isn't set -- refusing to start is the point, mirroring
    backend/connections/store.py's MissingEncryptionKeyError exactly."""

    def __init__(self) -> None:
        super().__init__(
            "AGENT_GRAPH_STUDIO_API_KEY is not set -- refusing to start without a real "
            "shared credential (see docs/DEPLOYMENT.md)."
        )


# spec-017: paths reachable with no credential at all -- schema/shape only
# (not data), or a health check for container orchestration (SPEC-016).
_AUTH_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _configured_api_key() -> str:
    key = os.environ.get("AGENT_GRAPH_STUDIO_API_KEY")
    if not key:
        raise MissingApiKeyError()
    return key


def ensure_api_key_configured() -> None:
    """Eager startup check, called from _lifespan -- see module docstring
    on MissingApiKeyError for why this can't be lazy."""
    _configured_api_key()


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    key: str | None = None,
) -> None:
    """spec-017: one global dependency (attached to the whole app, not
    per-route -- see `app = FastAPI(dependencies=...)` below), protecting
    every route including ones added dynamically later via
    `app.add_api_route` (the webhook routes -- verified by a real test, not
    assumed, since a router-level dependency silently not covering a
    dynamically-added route would be a real, dangerous gap).

    Two ways to present the one shared secret: `Authorization: Bearer
    <key>` (the canvas, normal case) or a `?key=<key>` query parameter (for
    external callers like Telegram that can't set a custom header on a
    webhook callback URL -- see docs/specs/017-production-hardening.md §6's
    resolved open question for the full reasoning and disclosed tradeoff).
    """
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return
    configured = _configured_api_key()
    supplied: str | None = None
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[len("Bearer ") :]
    elif key:
        supplied = key
    if supplied != configured:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _utcnow_iso() -> str:
    # Mirrors backend/api/runs.py's private _utcnow_iso exactly -- this
    # project's established small-duplication-over-shared-utils convention
    # (see backend/nodes/agent.py's identical helper for the same reasoning).
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Spec-017: eager, explicit checks first -- both secrets must be
    # genuinely configured before this process is allowed to start serving
    # anything, not just incidentally validated the first time some other
    # operation happens to need them.
    ensure_encryption_key_configured()
    ensure_api_key_configured()
    # Spec-015: re-arm every persisted is_active graph's triggers on real
    # process startup -- `_reactivate_persisted_graphs` is defined later in
    # this module; that's fine, its name is only resolved when this
    # coroutine actually runs (real ASGI startup / TestClient's `with`
    # context), by which point the whole module has finished executing.
    _reactivate_persisted_graphs()
    # spec-019: rebuild every saved mcp_server connection's generated node
    # set on startup too, mirroring the graph-reactivation precedent above
    # -- the palette must be correct immediately after a restart, not only
    # after each connection happens to be manually refreshed.
    #
    # Dispatched via asyncio.to_thread, NOT called directly -- unlike
    # _reactivate_persisted_graphs, this calls real MCP discovery
    # (backend/mcp/client.py's list_tools), which internally does its own
    # asyncio.run() (same sync-over-async pattern used everywhere else MCP
    # discovery happens). That fails outright when called directly from
    # this coroutine, since _lifespan already runs on uvicorn's own event
    # loop -- discovered live, restarting the backend after this feature
    # was added. Every other MCP-discovery call site (POST /connections,
    # POST /connections/{name}/refresh-capabilities) is a plain synchronous
    # route handler, dispatched through Starlette's own worker thread, so
    # it never hits this; startup is the one place this module runs
    # directly on the event loop thread.
    await asyncio.to_thread(generated_nodes.regenerate_all_on_startup)
    yield


# spec-017: a single shared credential required on every route (see
# require_api_key above) -- app-level `dependencies` attaches to the app's
# router itself, so routes added later via `app.add_api_route` (the
# dynamic webhook routes, SPEC-009) are covered too, not just the ones
# defined directly below. Local single-user tool origins remain permissive
# for CORS -- auth is the actual access control now, not CORS.
app = FastAPI(title="Agent Graph Studio API", lifespan=_lifespan, dependencies=[Depends(require_api_key)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated (see _AUTH_EXEMPT_PATHS) -- for container
    orchestration health checks (SPEC-016's Compose setup can use this)."""
    return {"status": "ok"}


def _slot_info_list(slots) -> list[SlotInfo]:
    return [
        SlotInfo(name=s.name, type=s.type.model_dump(), required=getattr(s, "required", True))
        for s in slots
    ]


@app.get("/node-types", response_model=list[NodeTypeInfo])
def list_node_types() -> list[NodeTypeInfo]:
    """The node palette's entire data source. `default_registry.all_types()`
    (backend/registry/base.py) is the *only* place any node type list is
    enumerated -- populated by @register_node(...) decorator side effects
    across backend/nodes/*.py at import time, plus (spec-019) runtime
    registrations from backend/mcp/generated_nodes.py for each saved
    `mcp_server` connection. No type name is hardcoded here or anywhere in
    the frontend; a new backend node type appears automatically either way.
    """
    infos: list[NodeTypeInfo] = []
    for type_name in default_registry.all_types():
        definition = default_registry.get(type_name)
        # spec-012: a root whose ports mirror a connected sub-node
        # (resolve_slots_from_sub_node, e.g. webhook_trigger) is dynamic in
        # the same "empty until resolved" sense as config-based dynamism --
        # just resolved by the canvas client-side from the connected
        # sub-node's own static outputs, not via POST /resolve-slots.
        is_dynamic = definition.resolve_slots is not None or definition.resolve_slots_from_sub_node is not None
        inputs = [] if is_dynamic else _slot_info_list(definition.inputs)
        outputs = [] if is_dynamic else _slot_info_list(definition.outputs)
        sub_node_slots = (
            {
                name: SubNodeSlotInfo(cardinality=spec.cardinality, accepts_role=spec.accepts_role)
                for name, spec in definition.sub_node_slots.items()
            }
            if definition.sub_node_slots is not None
            else None
        )
        infos.append(
            NodeTypeInfo(
                type=type_name,
                category=definition.category,
                config_schema=definition.config_model.model_json_schema(),
                dynamic_schema=is_dynamic,
                inputs=inputs,
                outputs=outputs,
                sub_node_slots=sub_node_slots,
                sub_node_role=definition.sub_node_role,
                resolve_slots_from_sub_node=definition.resolve_slots_from_sub_node,
                integration=definition.integration,
                capability_group=definition.capability_group,
            )
        )
    return infos


@app.post("/node-types/{type_name}/resolve-slots", response_model=ResolveSlotsResponse)
def resolve_node_slots(type_name: str, request: ResolveSlotsRequest) -> ResolveSlotsResponse:
    """Per-instance port resolution for dynamic-schema node types (code,
    mcp_call, fan_out, merge). Reuses the exact backend effective_inputs/
    effective_outputs logic (SPEC-002) against a throwaway probe NodeSpec --
    not a new resolution mechanism, just an HTTP-shaped way to call the
    existing one."""
    definition = default_registry.get(type_name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Unknown node type: {type_name!r}")

    probe_node = NodeSpec(id="_probe", type=type_name, config=request.config)
    inputs = effective_inputs(definition, probe_node)
    outputs = effective_outputs(definition, probe_node)
    if inputs is None or outputs is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve ports for '{type_name}' with the given config",
        )
    return ResolveSlotsResponse(inputs=_slot_info_list(inputs), outputs=_slot_info_list(outputs))


@app.post("/runs", response_model=RunSubmitResponse, status_code=202)
def submit_run(
    graph: GraphSpec, background_tasks: BackgroundTasks, graph_id: str | None = None
) -> RunSubmitResponse:
    """Validates synchronously (reusing the exact backend validate_graph(),
    zero duplicated logic) and returns immediately; the run itself executes
    in a background worker thread. Necessary given SPEC-004's loops could run
    for a while -- the HTTP request must not be held open for the duration.

    Spec-006: validate_graph() already includes the missing_connection rule,
    so a graph referencing an unconfigured connection is rejected here with
    the same 422/issues shape as any other validation failure -- no separate
    error path. Once validation passes, every referenced connection is
    resolved to a real client (backend/connections/resolver.py) and handed
    to the run as `resources={"connections": ...}`, exactly the same opaque
    resources bag mechanism the engine has supported since SPEC-002.

    `graph_id` (spec-010, optional query param): GraphSpec has no
    server-side identity anywhere in this codebase -- POST /runs takes a raw
    graph body, same as always. Unlike an activated graph (whose graph_id is
    a required part of its activation URL, spec-009), a manual run's
    graph_id is caller-chosen and optional; omitted, it's stored as null in
    the run history rather than invented. See docs/specs/010-run-persistence.md
    §8 for why this was resolved as an explicit param rather than assumed.
    """
    try:
        validate_graph(graph)
    except GraphValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[
                {"rule": issue.rule, "node_id": issue.node_id, "message": issue.message}
                for issue in e.issues
            ],
        ) from e

    try:
        resolved_connections = resolve_connections(graph)
        resolved_connection_profiles = resolve_connection_profiles(graph)
    except ConnectionNotFoundError as e:
        # Only reachable via a race (store changed between validate_graph()
        # and here) -- validate_graph()'s missing_connection rule already
        # covers the common case with the same friendly error shape.
        raise HTTPException(status_code=422, detail=str(e)) from e

    run_id = str(uuid4())
    runs.create_run(run_id, graph_id=graph_id, trigger_source="manual")
    background_tasks.add_task(
        runs.execute_run,
        run_id,
        graph,
        {"connections": resolved_connections, "connection_profiles": resolved_connection_profiles},
    )
    return RunSubmitResponse(run_id=run_id, status="running")


@app.get("/connection-types", response_model=list[ConnectionTypeInfo])
def list_connection_types() -> list[ConnectionTypeInfo]:
    """The connection picker's entire data source for type-appropriate
    fields and Local/Cloud tabs -- mirrors GET /node-types exactly.
    `default_connection_registry.all_types()` is the only place any
    connection type list is enumerated; nothing is hardcoded here or in the
    frontend. A necessary addition beyond spec-006 §5's literal endpoint
    list, same justification as SPEC-005's resolve-slots addition."""
    infos: list[ConnectionTypeInfo] = []
    for type_name in default_connection_registry.all_types():
        definition = default_connection_registry.get(type_name)
        infos.append(
            ConnectionTypeInfo(
                type=type_name,
                category=definition.category,
                config_schema=definition.config_model.model_json_schema(),
                supports_model_listing=definition.list_models is not None,
                supports_tool_calling=definition.complete_with_tools is not None,
                supports_embedding=definition.embed is not None,
            )
        )
    return infos


@app.get("/connections", response_model=list[ConnectionInfo])
def list_all_connections() -> list[ConnectionInfo]:
    return [ConnectionInfo(name=c.name, type=c.type) for c in list_connections()]


@app.get("/connections/{name}/models", response_model=list[str])
def list_connection_models(name: str) -> list[str]:
    """spec-006 §9: real, live models available on this connection's actual
    backend (e.g. Ollama's /api/tags), for the llm_call model-field dropdown.
    Only meaningful for connection types where
    ConnectionTypeInfo.supports_model_listing is true -- the frontend checks
    that first via GET /connection-types rather than trial-and-erroring this
    endpoint against every connection."""
    profile = get_connection(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")

    definition = default_connection_registry.get(profile.type)
    if definition is None or definition.list_models is None:
        raise HTTPException(
            status_code=422,
            detail=f"Connection type '{profile.type}' does not support model listing",
        )

    config = definition.config_model.model_validate(profile.config)
    try:
        return definition.list_models(config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list models: {e}") from e


@app.post("/connections", response_model=ConnectionInfo, status_code=201)
def create_connection(request: CreateConnectionRequest) -> ConnectionInfo:
    definition = default_connection_registry.get(request.type)
    if definition is None:
        raise HTTPException(status_code=422, detail=f"Unknown connection type: {request.type!r}")
    try:
        definition.config_model.model_validate(request.config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config for '{request.type}': {e}") from e

    try:
        profile = add_connection(request.name, request.type, request.config)
    except DuplicateConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # spec-019: an mcp_server connection's node types are generated once,
    # here, at creation -- not polled. A discovery failure rolls the
    # creation back rather than leaving a saved connection with zero
    # generated capabilities and no signal why (the same fail-closed
    # instinct as SPEC-018's activation rollback).
    if request.type == "mcp_server":
        try:
            generated_nodes.generate_node_types_for_connection(request.name)
        except Exception as e:
            delete_connection(request.name)
            raise HTTPException(
                status_code=502, detail=f"Connection saved config is valid, but tool discovery failed: {e}"
            ) from e

    return ConnectionInfo(name=profile.name, type=profile.type)


@app.post("/connections/{name}/test", response_model=TestConnectionResponse)
def test_connection_endpoint(name: str, request: TestConnectionRequest) -> TestConnectionResponse:
    """Tests a real, lightweight round-trip against the connection's actual
    backend. A failed connectivity check is an expected outcome (wrong
    host, server down, bad key), not a server error -- always a normal 200
    with success=False, never a non-2xx.

    If `request.type`/`request.config` are given, tests that configuration
    directly without requiring it to be saved yet (the canvas's "Test before
    Save" flow, spec-006 §3). Otherwise re-tests the already-saved
    connection named `name`."""
    if request.type is not None and request.config is not None:
        type_name, config_dict = request.type, request.config
    else:
        profile = get_connection(name)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
        type_name, config_dict = profile.type, profile.config

    definition = default_connection_registry.get(type_name)
    if definition is None:
        raise HTTPException(status_code=422, detail=f"Unknown connection type: {type_name!r}")

    try:
        config = definition.config_model.model_validate(config_dict)
    except Exception as e:
        return TestConnectionResponse(success=False, message=f"Invalid config: {e}")

    result = definition.test_connection(config)
    return TestConnectionResponse(success=result.success, message=result.message)


@app.delete("/connections/{name}", status_code=204)
def delete_connection_endpoint(name: str) -> None:
    if not delete_connection(name):
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    # spec-019: a no-op for any connection that never had generated node
    # types (every type except mcp_server) -- cheap and correct either way.
    generated_nodes.unregister_for_connection(name)


@app.post("/connections/{name}/refresh-capabilities", response_model=RefreshCapabilitiesResponse)
def refresh_capabilities(name: str) -> RefreshCapabilitiesResponse:
    """spec-019: re-runs live discovery for an `mcp_server` connection and
    updates its generated node set -- discovery is refreshed explicitly,
    never polled (see backend/mcp/generated_nodes.py's own docstring). A
    failed refresh leaves the previously-generated set intact (see
    generate_node_types_for_connection's ordering)."""
    profile = get_connection(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.type != "mcp_server":
        raise HTTPException(status_code=422, detail=f"Connection '{name}' is not an mcp_server connection")
    try:
        generated_types = generated_nodes.generate_node_types_for_connection(name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to refresh capabilities: {e}") from e
    return RefreshCapabilitiesResponse(generated_types=generated_types)


@app.delete("/connections/{name}/vectors", status_code=204)
def clear_connection_vectors(name: str) -> None:
    """spec-011 §7: clears a vector_store connection's stored chunks without
    deleting the connection profile itself -- avoids needing to delete and
    recreate an entire connection just to start over during testing."""
    profile = get_connection(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.type != "vector_store":
        raise HTTPException(
            status_code=422, detail=f"Connection '{name}' is not a vector_store connection"
        )
    definition = default_connection_registry.get(profile.type)
    config = definition.config_model.model_validate(profile.config)
    client = definition.build_client(config)
    client.clear()


# spec-018: the one app-level setting needed to auto-register external
# webhooks (Telegram's setWebhook/deleteWebhook) -- see
# backend/storage/settings_store.py's module docstring for why this is a
# separate plain (unencrypted) store, not a *_connection field or a
# per-graph setting.


@app.get("/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    return SettingsResponse(public_base_url=settings_store.get_public_base_url())


@app.put("/settings", response_model=UpdateSettingsResponse)
def update_settings(request: UpdateSettingsRequest) -> UpdateSettingsResponse:
    """Spec-018 §6's resolved open question: a lightweight, non-blocking
    reachability check against the new value's /health (SPEC-017) --
    surfaced as a warning, never a hard block, since a URL can be correct
    but momentarily unreachable (e.g. a tunnel not yet started)."""
    url = request.public_base_url.rstrip("/")
    settings_store.set_public_base_url(url)
    warning: str | None = None
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            if resp.status != 200:
                warning = f"{url}/health responded with status {resp.status}"
    except Exception as e:
        warning = f"Could not reach {url}/health: {e}"
    return UpdateSettingsResponse(public_base_url=url, warning=warning)


@app.get("/runs", response_model=RunListResponse)
def list_runs(
    graph_id: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RunListResponse:
    """spec-010: paginated run history, read exclusively from the durable
    SQLite store (backend/storage/runs_store.py) -- listing is inherently
    about history/browsing, not the live "still running" hot path that
    GET /runs/{run_id} optimizes for, so there's no in-memory fallback to
    reason about here. Summaries only (no trace/result) per spec §5."""
    rows, total = runs_store.list_run_records(
        graph_id=graph_id,
        status=status,
        trigger_source=trigger_source,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(
        runs=[
            RunSummary(
                run_id=r.run_id,
                graph_id=r.graph_id,
                status=r.status,
                trigger_source=r.trigger_source,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    """spec-010: falls back to the durable SQLite store when a run is no
    longer in the in-memory `_runs` dict (e.g. the API process was
    restarted since it ran) -- this is what makes a run's result queryable
    long after the process that ran it. The in-memory path stays primary
    (checked first) since it's the only place `running_node_ids` (live
    per-node progress) exists at all; a persisted-only record has none to
    report, since spec-010's write point is after run_graph returns, not
    during."""
    record = runs.get_run_snapshot(run_id)
    if record is not None:
        pending_approvals = [
            PendingApprovalInfo(approval_id=p.approval_id, tool_name=p.tool_name, arguments=p.arguments)
            for p in approvals.list_pending_for_run(run_id)
        ]
        return RunStatusResponse(
            run_id=record.run_id,
            status=record.status,
            graph_id=record.graph_id,
            trigger_source=record.trigger_source,
            running_node_ids=record.running_node_ids,
            active_sub_node_ids=record.active_sub_node_ids,
            pending_approvals=pending_approvals,
            trace=record.trace,
            result=record.result,
            error=record.error,
        )

    row = runs_store.get_run_record(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")

    result: dict[str, Any] | None = None
    trace: list[Any] = []
    if row.result_json is not None:
        parsed = json.loads(row.result_json)
        result = parsed.get("result")
        trace = parsed.get("trace", [])
    return RunStatusResponse(
        run_id=row.run_id,
        status=row.status,
        graph_id=row.graph_id,
        trigger_source=row.trigger_source,
        running_node_ids=[],
        active_sub_node_ids=[],
        pending_approvals=[],
        trace=trace,
        result=result,
        error=row.error,
    )


@app.post("/runs/{run_id}/approvals/{approval_id}")
def resolve_run_approval(run_id: str, approval_id: str, request: ResolveApprovalRequest) -> dict[str, str]:
    """spec-019: answers a pending approval-gated tool call from the canvas
    (backend/execution/approvals.py) -- unblocks the node's execute() call
    that's waiting on it. `run_id` isn't itself used to look up the
    approval (approval_id alone is already globally unique) but is part of
    the URL for symmetry with every other /runs/{run_id}/... route and so
    a client can't accidentally resolve an approval against the wrong run
    without it being visible in the URL."""
    if not approvals.resolve_approval(approval_id, request.approved, remember=request.remember):
        raise HTTPException(status_code=404, detail=f"Unknown or already-resolved approval_id: {approval_id!r}")
    return {"status": "resolved"}


# --- spec-009: trigger nodes (schedule + webhook) ---------------------------
#
# `graph_id` has no persisted identity anywhere else in this codebase (no
# `id` field on GraphSpec, no server-side "save a graph" concept -- the
# canvas's own save/load is a local file download/upload, per SPEC-005).
# Rather than invent a whole new /graphs CRUD resource the spec never asked
# for, POST /graphs/{graph_id}/activate carries the full GraphSpec as its
# own request body: `graph_id` is caller-chosen, and the graph is cached in
# `backend.triggers.registry` purely in-memory, for exactly as long as it's
# active -- consistent with this spec's own explicitly-accepted "no
# persistence across restarts" scope line (§3).


def _webhook_path(graph_id: str, node_id: str) -> str:
    return f"/webhooks/{graph_id}/{node_id}"


def _to_schema_trigger(t: trigger_registry.TriggerRecord) -> TriggerInfo:
    return TriggerInfo(node_id=t.node_id, type=t.type, endpoint_or_schedule=t.endpoint_or_schedule)


def _make_webhook_handler(graph_id: str, node_id: str):
    # Plain `def`, never `async def` -- same blanket policy as every other
    # route in this module (see module docstring): FastAPI/Starlette parses
    # `payload` before calling the handler regardless of sync/async, so this
    # needs no `await request.json()` to stay a plain sync callable.
    def webhook_handler(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, str]:
        try:
            run_id = trigger_runner.fire(
                graph_id, node_id, payload=payload, trigger_source="webhook"
            )
        except trigger_runner.GraphNotActiveError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"run_id": run_id}

    return webhook_handler


def _deactivate(graph_id: str) -> None:
    trigger_scheduler.remove_jobs_for_graph(graph_id)
    prefix = f"/webhooks/{graph_id}/"
    app.router.routes = [
        route for route in app.router.routes if not getattr(route, "path", "").startswith(prefix)
    ]
    trigger_registry.clear_active(graph_id)


# spec-018/019: auto-registering a trigger adapter's external webhook on
# Activate/Deactivate -- plain graph-edge traversal plus the generic
# integration-agnostic interface (backend/triggers/webhook_sync.py). No
# adapter type is named here; Telegram is just the first registered
# handler (backend/integrations/telegram/webhook_sync.py).


def _sync_webhooks_on_activate(
    graph_id: str,
    graph: GraphSpec,
    triggers: list[trigger_registry.TriggerRecord],
) -> None:
    """Called after _register_triggers succeeds. A failure here rolls back
    the whole activation (matching the existing invalid-cron-expression
    precedent) -- spec-018 §4's resolved decision: Activate must not report
    success while the actual external wiring silently didn't happen."""
    pairs = webhook_sync.adapter_pairs_for_graph(graph)
    if not pairs:
        return

    public_base_url = settings_store.get_public_base_url()
    if not public_base_url:
        raise HTTPException(
            status_code=422,
            detail="This graph has a trigger adapter that needs a registered webhook, but no "
            "public base URL is configured yet -- set one first (see Settings) before activating.",
        )

    try:
        resolved_connections = resolve_connections(graph)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    trigger_by_node_id = {t.node_id: t for t in triggers}
    for webhook_node, adapter_node in pairs:
        handler = webhook_sync.get_handler(adapter_node.type)
        # adapter_pairs_for_graph only returns pairs whose adapter type has
        # a registered handler, so this is always non-None here -- asserted
        # rather than silently trusted.
        assert handler is not None
        # The reported endpoint already carries `?key=...` (SPEC-017) --
        # the exact same URL the trigger chip shows, immediately usable.
        full_url = f"{public_base_url}{trigger_by_node_id[webhook_node.id].endpoint_or_schedule}"
        try:
            handler.sync_on_activate(webhook_node, adapter_node, full_url, resolved_connections)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e


def _sync_webhooks_on_deactivate(graph_id: str, graph: GraphSpec) -> None:
    """Best-effort, unlike activate's fail-closed behavior -- deactivation's
    primary job (removing the local route/registration) must still succeed
    even if the external API is briefly unreachable; a stray webhook the
    external service will 404 against on its next delivery attempt anyway
    is a smaller problem than a graph stuck unable to deactivate."""
    pairs = webhook_sync.adapter_pairs_for_graph(graph)
    if not pairs:
        return
    try:
        resolved_connections = resolve_connections(graph)
    except ConnectionNotFoundError:
        logger.exception("Could not resolve connections to deregister webhook(s) for graph_id=%s", graph_id)
        return
    for webhook_node, adapter_node in pairs:
        handler = webhook_sync.get_handler(adapter_node.type)
        assert handler is not None
        try:
            handler.sync_on_deactivate(webhook_node, adapter_node, resolved_connections)
        except RuntimeError:
            logger.exception(
                "Failed to deregister webhook for adapter '%s', graph_id=%s", adapter_node.id, graph_id
            )


def _register_triggers(graph_id: str, graph: GraphSpec) -> list[trigger_registry.TriggerRecord]:
    """The actual registration work (a cron job per `schedule_trigger`, a
    dynamic webhook route per `webhook_trigger`) -- spec-015 §4: extracted
    so both `activate_graph` (the HTTP endpoint) and the startup
    re-activation pass call the exact same code, never two copies to keep
    in sync. Raises HTTPException on an invalid cron expression, same as
    before this extraction; the startup caller wraps this in a broader
    try/except instead of relying on this raising HTTPException
    specifically (it's just a convenient exception type to reuse, not an
    HTTP-layer concept the startup path actually needs)."""
    triggers: list[trigger_registry.TriggerRecord] = []
    for node in graph.nodes:
        if node.type == "schedule_trigger":
            cron = node.config.get("cron", "")
            try:
                trigger_scheduler.add_schedule_job(
                    graph_id, node.id, cron, lambda gid=graph_id, nid=node.id: trigger_runner.fire(gid, nid)
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid cron expression for node '{node.id}': {e}",
                ) from e
            triggers.append(
                trigger_registry.TriggerRecord(
                    node_id=node.id, type="schedule_trigger", endpoint_or_schedule=cron
                )
            )
        elif node.type == "webhook_trigger":
            path = _webhook_path(graph_id, node.id)
            app.add_api_route(path, _make_webhook_handler(graph_id, node.id), methods=["POST"])
            # spec-017: the *reported* endpoint carries the API key as a
            # query param, ready to use directly in an external service's
            # webhook config (e.g. Telegram's setWebhook) -- but route
            # registration above uses the bare `path`, since a route
            # pattern isn't a URL and can't include a query string.
            display_path = f"{path}?key={_configured_api_key()}"
            triggers.append(
                trigger_registry.TriggerRecord(
                    node_id=node.id, type="webhook_trigger", endpoint_or_schedule=display_path
                )
            )
    return triggers


@app.post("/graphs/{graph_id}/activate", response_model=ActivateGraphResponse)
def activate_graph(graph_id: str, graph: GraphSpec) -> ActivateGraphResponse:
    """Registers a cron job per `schedule_trigger` node and a dynamic
    webhook route per `webhook_trigger` node. Validates first via the exact
    same validate_graph() every other entry point uses (422 with the same
    issues shape on failure). Re-activating an already-active graph_id
    replaces the prior registration outright rather than erroring --
    activation is idempotent from the caller's perspective.

    Spec-015: also persists is_active=true + the activated spec to
    `graphs_store`, upserting a row if `graph_id` was never explicitly
    saved via POST /graphs first (SPEC-009's original "graph_id is
    caller-chosen" contract, unchanged) -- this is what makes startup
    re-activation possible."""
    try:
        validate_graph(graph)
    except GraphValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[
                {"rule": issue.rule, "node_id": issue.node_id, "message": issue.message}
                for issue in e.issues
            ],
        ) from e

    if trigger_registry.get_active(graph_id) is not None:
        _deactivate(graph_id)

    try:
        triggers = _register_triggers(graph_id, graph)
        # spec-018/019: auto-registers a trigger adapter's external webhook,
        # if this graph has one with a registered sync handler -- a no-op
        # for every other graph. Failure here rolls back exactly like an
        # invalid cron expression does.
        _sync_webhooks_on_activate(graph_id, graph, triggers)
    except HTTPException:
        _deactivate(graph_id)  # don't leave a half-registered graph behind
        raise

    trigger_registry.set_active(graph_id, graph, triggers)
    graphs_store.set_active_state(graph_id, graph.model_dump_json(), is_active=True, updated_at=_utcnow_iso())
    return ActivateGraphResponse(status="active", triggers=[_to_schema_trigger(t) for t in triggers])


@app.post("/graphs/{graph_id}/deactivate")
def deactivate_graph(graph_id: str) -> dict[str, str]:
    active = trigger_registry.get_active(graph_id)
    if active is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' is not active")
    # spec-018/019: best-effort deregistration of a trigger adapter's
    # external webhook, if any -- see _sync_webhooks_on_deactivate's own
    # docstring for why this is deliberately not fatal to deactivation
    # itself, unlike activate.
    _sync_webhooks_on_deactivate(graph_id, active.graph)
    _deactivate(graph_id)
    graphs_store.set_is_active(graph_id, is_active=False, updated_at=_utcnow_iso())
    return {"status": "inactive"}


@app.get("/graphs/active", response_model=list[ActiveGraphInfo])
def list_active_graphs() -> list[ActiveGraphInfo]:
    return [
        ActiveGraphInfo(
            graph_id=g.graph_id, triggers=[_to_schema_trigger(t) for t in g.triggers]
        )
        for g in trigger_registry.list_active()
    ]


def _reactivate_persisted_graphs() -> None:
    """Spec-015 §4: the actual fix for triggers vanishing on a backend
    restart. Re-registers every graph flagged is_active=true in
    `graphs_store` via the exact same `_register_triggers` the /activate
    endpoint uses -- one broken persisted graph (e.g. its spec no longer
    validates against a since-changed node registry) must not prevent any
    other graph from re-activating, so each graph's re-activation is
    independently try/excepted and logged rather than one loop that could
    abort partway through."""
    for row in graphs_store.list_active_graphs():
        try:
            graph = GraphSpec.model_validate_json(row.spec_json)
            validate_graph(graph)
            triggers = _register_triggers(row.graph_id, graph)
            trigger_registry.set_active(row.graph_id, graph, triggers)
        except Exception:
            logger.exception("Failed to re-activate graph_id=%s on startup", row.graph_id)


# spec-015: saved graphs, giving GraphSpec a real server-side identity.
# Registered after GET /graphs/active (above) so Starlette's registration-
# order route matching tries the literal "/graphs/active" path first --
# GET /graphs/{graph_id} below would otherwise swallow it (graph_id="active").


@app.post("/graphs", response_model=GraphDetail, status_code=201)
def create_graph(request: CreateGraphRequest) -> GraphDetail:
    graph_id = str(uuid4())
    now = _utcnow_iso()
    row = graphs_store.create_graph(graph_id, request.name, request.spec.model_dump_json(), now)
    return GraphDetail(graph_id=row.graph_id, name=row.name, spec=request.spec, is_active=row.is_active)


@app.get("/graphs", response_model=list[GraphSummary])
def list_graphs() -> list[GraphSummary]:
    return [
        GraphSummary(graph_id=g.graph_id, name=g.name, is_active=g.is_active, updated_at=g.updated_at)
        for g in graphs_store.list_graphs()
    ]


@app.get("/graphs/{graph_id}", response_model=GraphDetail)
def get_graph(graph_id: str) -> GraphDetail:
    row = graphs_store.get_graph(graph_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return GraphDetail(
        graph_id=row.graph_id,
        name=row.name,
        spec=GraphSpec.model_validate_json(row.spec_json),
        is_active=row.is_active,
    )


@app.put("/graphs/{graph_id}", response_model=GraphDetail)
def update_graph(graph_id: str, request: UpdateGraphRequest) -> GraphDetail:
    spec_json = request.spec.model_dump_json() if request.spec is not None else None
    row = graphs_store.update_graph(graph_id, _utcnow_iso(), name=request.name, spec_json=spec_json)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return GraphDetail(
        graph_id=row.graph_id,
        name=row.name,
        spec=GraphSpec.model_validate_json(row.spec_json),
        is_active=row.is_active,
    )


@app.delete("/graphs/{graph_id}", status_code=204)
def delete_graph(graph_id: str) -> None:
    """Deactivates first if currently active (spec-015 §7's resolved open
    question) -- DELETE's usual "just make it gone" semantics, not a
    separate forced manual deactivate step first."""
    if trigger_registry.get_active(graph_id) is not None:
        _deactivate(graph_id)
    deleted = graphs_store.delete_graph(graph_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")

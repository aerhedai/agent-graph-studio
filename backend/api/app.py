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

import json
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import backend.connections  # noqa: F401 -- import side effect registers every connection type
import backend.nodes  # noqa: F401 -- import side effect registers every node type
from backend.api import runs
from backend.api.schemas import (
    ActivateGraphResponse,
    ActiveGraphInfo,
    ConnectionInfo,
    ConnectionTypeInfo,
    CreateConnectionRequest,
    NodeTypeInfo,
    ResolveSlotsRequest,
    ResolveSlotsResponse,
    RunListResponse,
    RunStatusResponse,
    RunSubmitResponse,
    RunSummary,
    SlotInfo,
    TestConnectionRequest,
    TestConnectionResponse,
    TriggerInfo,
)
from backend.connections.base import default_connection_registry
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.resolver import resolve_connection_profiles, resolve_connections
from backend.connections.store import add_connection, delete_connection, get_connection, list_connections
from backend.registry.base import default_registry, effective_inputs, effective_outputs
from backend.schema.models import GraphSpec, NodeSpec
from backend.storage import runs_store
from backend.triggers import registry as trigger_registry
from backend.triggers import runner as trigger_runner
from backend.triggers import scheduler as trigger_scheduler
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph

app = FastAPI(title="Agent Graph Studio API")

# Local, single-user tool -- no auth, permissive CORS for the Vite dev server
# (and any other local origin). Spec-005 §3 explicitly puts auth/multi-tenancy
# out of scope; revisit only if this is ever hosted for others.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _slot_info_list(slots) -> list[SlotInfo]:
    return [
        SlotInfo(name=s.name, type=s.type.model_dump(), required=getattr(s, "required", True))
        for s in slots
    ]


@app.get("/node-types", response_model=list[NodeTypeInfo])
def list_node_types() -> list[NodeTypeInfo]:
    """The node palette's entire data source. `default_registry.all_types()`
    (backend/registry/base.py) is the *only* place any node type list is
    enumerated -- populated purely by @register_node(...) decorator side
    effects across backend/nodes/*.py. No type name is hardcoded here or
    anywhere in the frontend; a new backend node type appears automatically.
    """
    infos: list[NodeTypeInfo] = []
    for type_name in default_registry.all_types():
        definition = default_registry.get(type_name)
        is_dynamic = definition.resolve_slots is not None
        inputs = [] if is_dynamic else _slot_info_list(definition.inputs)
        outputs = [] if is_dynamic else _slot_info_list(definition.outputs)
        infos.append(
            NodeTypeInfo(
                type=type_name,
                config_schema=definition.config_model.model_json_schema(),
                dynamic_schema=is_dynamic,
                inputs=inputs,
                outputs=outputs,
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
        return RunStatusResponse(
            run_id=record.run_id,
            status=record.status,
            graph_id=record.graph_id,
            trigger_source=record.trigger_source,
            running_node_ids=record.running_node_ids,
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
        trace=trace,
        result=result,
        error=row.error,
    )


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


@app.post("/graphs/{graph_id}/activate", response_model=ActivateGraphResponse)
def activate_graph(graph_id: str, graph: GraphSpec) -> ActivateGraphResponse:
    """Registers a cron job per `schedule_trigger` node and a dynamic
    webhook route per `webhook_trigger` node. Validates first via the exact
    same validate_graph() every other entry point uses (422 with the same
    issues shape on failure). Re-activating an already-active graph_id
    replaces the prior registration outright rather than erroring --
    activation is idempotent from the caller's perspective."""
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

    triggers: list[trigger_registry.TriggerRecord] = []
    try:
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
                triggers.append(
                    trigger_registry.TriggerRecord(
                        node_id=node.id, type="webhook_trigger", endpoint_or_schedule=path
                    )
                )
    except HTTPException:
        _deactivate(graph_id)  # don't leave a half-registered graph behind
        raise

    trigger_registry.set_active(graph_id, graph, triggers)
    return ActivateGraphResponse(status="active", triggers=[_to_schema_trigger(t) for t in triggers])


@app.post("/graphs/{graph_id}/deactivate")
def deactivate_graph(graph_id: str) -> dict[str, str]:
    if trigger_registry.get_active(graph_id) is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' is not active")
    _deactivate(graph_id)
    return {"status": "inactive"}


@app.get("/graphs/active", response_model=list[ActiveGraphInfo])
def list_active_graphs() -> list[ActiveGraphInfo]:
    return [
        ActiveGraphInfo(
            graph_id=g.graph_id, triggers=[_to_schema_trigger(t) for t in g.triggers]
        )
        for g in trigger_registry.list_active()
    ]

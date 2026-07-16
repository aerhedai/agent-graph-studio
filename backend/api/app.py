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

from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import backend.connections  # noqa: F401 -- import side effect registers every connection type
import backend.nodes  # noqa: F401 -- import side effect registers every node type
from backend.api import runs
from backend.api.schemas import (
    ConnectionInfo,
    ConnectionTypeInfo,
    CreateConnectionRequest,
    NodeTypeInfo,
    ResolveSlotsRequest,
    ResolveSlotsResponse,
    RunStatusResponse,
    RunSubmitResponse,
    SlotInfo,
    TestConnectionRequest,
    TestConnectionResponse,
)
from backend.connections.base import default_connection_registry
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.resolver import resolve_connections
from backend.connections.store import add_connection, delete_connection, get_connection, list_connections
from backend.registry.base import default_registry, effective_inputs, effective_outputs
from backend.schema.models import GraphSpec, NodeSpec
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
def submit_run(graph: GraphSpec, background_tasks: BackgroundTasks) -> RunSubmitResponse:
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
    except ConnectionNotFoundError as e:
        # Only reachable via a race (store changed between validate_graph()
        # and here) -- validate_graph()'s missing_connection rule already
        # covers the common case with the same friendly error shape.
        raise HTTPException(status_code=422, detail=str(e)) from e

    run_id = str(uuid4())
    runs.create_run(run_id)
    background_tasks.add_task(
        runs.execute_run, run_id, graph, {"connections": resolved_connections}
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
            )
        )
    return infos


@app.get("/connections", response_model=list[ConnectionInfo])
def list_all_connections() -> list[ConnectionInfo]:
    return [ConnectionInfo(name=c.name, type=c.type) for c in list_connections()]


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


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    record = runs.get_run_snapshot(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    return RunStatusResponse(
        run_id=record.run_id,
        status=record.status,
        running_node_ids=record.running_node_ids,
        trace=record.trace,
        result=record.result,
        error=record.error,
    )

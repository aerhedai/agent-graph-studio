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

import backend.nodes  # noqa: F401 -- import side effect registers every node type
from backend.api import runs
from backend.api.schemas import (
    NodeTypeInfo,
    ResolveSlotsRequest,
    ResolveSlotsResponse,
    RunStatusResponse,
    RunSubmitResponse,
    SlotInfo,
)
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

    run_id = str(uuid4())
    runs.create_run(run_id)
    background_tasks.add_task(runs.execute_run, run_id, graph)
    return RunSubmitResponse(run_id=run_id, status="running")


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

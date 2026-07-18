"""In-memory run store for the API layer (spec-005), now write-through to
durable SQLite storage (spec-010).

A run's lifecycle: `create_run` (on POST /runs, main thread) -> `execute_run`
(dispatched via FastAPI's BackgroundTasks, which runs plain sync callables
through Starlette's `run_in_threadpool` -- a genuine worker thread with no
event loop of its own, the same "safe to call run_graph()'s own internal
asyncio.run()" pattern already validated for the `loop` node's recursive
call) -> polled via `get_run_snapshot` (main thread, GET /runs/{run_id}).

The module-level `_runs` dict is mutated from two threads (the worker thread
running a given execution, and the event-loop thread serving GET requests).
No explicit lock: CPython's GIL makes the individual dict/list operations
here atomic enough for MVP correctness (one writer thread per run_id at a
time, one reader) -- `get_run_snapshot` takes a shallow copy specifically to
avoid handing back a record that's still being mutated mid-response-encode.

spec-010: `create_run`/`execute_run` also write through to
`backend.storage.runs_store` (SQLite) -- once on start (status="running")
and once on completion (final status/result/error). This is what makes a
run's result queryable via GET /runs/{run_id} long after this in-memory
dict has been wiped by a process restart, and what GET /runs (list) reads
exclusively. The in-memory dict remains the primary source for a run that's
still in progress (has real running_node_ids); persistence write failures
are caught and logged inside runs_store itself, never raised here, so they
can never break the run's actual execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.execution.engine import run_graph
from backend.execution.trace import TraceRecord
from backend.schema.models import GraphSpec
from backend.storage import runs_store


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    run_id: str
    status: str = "running"  # "running" | "completed" | "failed"
    graph_id: str | None = None
    trigger_source: str = "manual"
    started_at: str = ""
    finished_at: str | None = None
    running_node_ids: list[str] = field(default_factory=list)
    trace: list[TraceRecord] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


_runs: dict[str, RunRecord] = {}


def create_run(run_id: str, graph_id: str | None = None, trigger_source: str = "manual") -> RunRecord:
    started_at = _utcnow_iso()
    record = RunRecord(
        run_id=run_id,
        graph_id=graph_id,
        trigger_source=trigger_source,
        started_at=started_at,
    )
    _runs[run_id] = record
    runs_store.create_run_record(run_id, graph_id, trigger_source, started_at)
    return record


def get_run_snapshot(run_id: str) -> RunRecord | None:
    record = _runs.get(run_id)
    if record is None:
        return None
    return RunRecord(
        run_id=record.run_id,
        status=record.status,
        graph_id=record.graph_id,
        trigger_source=record.trigger_source,
        started_at=record.started_at,
        finished_at=record.finished_at,
        running_node_ids=list(record.running_node_ids),
        trace=list(record.trace),
        result=record.result,
        error=record.error,
    )


def execute_run(run_id: str, graph: GraphSpec, resources: dict[str, Any] | None = None) -> None:
    """The actual background task body -- called by Starlette in a worker
    thread. Wires run_graph()'s progress callbacks (spec-005) into the
    in-memory record so GET /runs/{run_id} can report live status without
    waiting for the whole run to finish. `resources` (spec-006) carries the
    already-resolved named-connection clients, built by the caller (POST
    /runs) before this task was dispatched."""
    record = _runs[run_id]

    def on_round_start(node_ids: list[str]) -> None:
        record.running_node_ids = list(node_ids)

    def on_trace_record(trace_record: TraceRecord) -> None:
        if trace_record.node_id in record.running_node_ids:
            record.running_node_ids.remove(trace_record.node_id)
        record.trace.append(trace_record)

    try:
        result = run_graph(
            graph,
            resources=resources,
            on_round_start=on_round_start,
            on_trace_record=on_trace_record,
        )
        record.result = result.result
        record.status = "completed"
        result_json = result.model_dump_json()
        error = None
    except Exception as e:
        record.status = "failed"
        record.error = str(e)
        result_json = None
        error = str(e)
    finally:
        record.running_node_ids = []
        record.finished_at = _utcnow_iso()
        runs_store.complete_run_record(
            run_id, record.status, record.finished_at, result_json, error
        )

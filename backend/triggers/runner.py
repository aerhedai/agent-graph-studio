"""Fires one activated graph, on behalf of either a real APScheduler cron
tick or a real webhook POST (spec-009 §4). Deliberately thin: reuses
`backend.api.runs.create_run`/`execute_run` as-is (the exact same in-memory
run store `GET /runs/{run_id}` already polls) rather than building a second,
parallel run-tracking mechanism -- a trigger-fired run is inspectable
exactly like a manually-submitted one.

Runs on a plain background `threading.Thread`, not `asyncio.to_thread` or
FastAPI's `BackgroundTasks` -- neither is available here: a scheduler tick
has no owning request/event loop at all, and a dynamically-added webhook
route needs the exact same firing path a schedule tick uses, so both go
through this one function rather than two divergent ones.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from backend.api import runs
from backend.connections.resolver import resolve_connection_profiles, resolve_connections
from backend.triggers.registry import get_active


class GraphNotActiveError(Exception):
    def __init__(self, graph_id: str) -> None:
        super().__init__(f"Graph '{graph_id}' is not currently active")
        self.graph_id = graph_id


def fire(graph_id: str, node_id: str, payload: dict[str, Any] | None = None) -> str:
    """Starts a real run in a background thread and returns its run_id
    immediately -- same "don't hold the caller open for the run's duration"
    shape as `POST /runs` (spec-005 §4), whether the caller is a webhook
    HTTP handler or a scheduler tick with no HTTP request behind it at all."""
    active = get_active(graph_id)
    if active is None:
        raise GraphNotActiveError(graph_id)

    graph = active.graph
    resolved_connections = resolve_connections(graph)
    resolved_connection_profiles = resolve_connection_profiles(graph)
    resources: dict[str, Any] = {
        "connections": resolved_connections,
        "connection_profiles": resolved_connection_profiles,
    }
    if payload is not None:
        resources["trigger_payloads"] = {node_id: payload}

    run_id = str(uuid4())
    runs.create_run(run_id)
    thread = threading.Thread(
        target=runs.execute_run, args=(run_id, graph, resources), daemon=True
    )
    thread.start()
    return run_id

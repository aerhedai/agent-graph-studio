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


def fire(
    graph_id: str,
    node_id: str,
    payload: dict[str, Any] | None = None,
    trigger_source: str = "schedule",
) -> str:
    """Starts a real run in a background thread and returns its run_id
    immediately -- same "don't hold the caller open for the run's duration"
    shape as `POST /runs` (spec-005 §4), whether the caller is a webhook
    HTTP handler or a scheduler tick with no HTTP request behind it at all.

    `trigger_source` (spec-010) defaults to "schedule" since that's the
    original/primary caller shape (a cron tick has no other signal to carry
    its own type); the webhook handler (backend/api/app.py) passes
    trigger_source="webhook" explicitly at its own call site."""
    active = get_active(graph_id)
    if active is None:
        raise GraphNotActiveError(graph_id)

    graph = active.graph
    # Bug fix: previously called with no user_id at all, which can only
    # ever see *global* connections -- any trigger-fired graph referencing
    # a private connection (the default for anything created through the
    # normal UI, spec-021) would raise ConnectionNotFoundError here,
    # uncaught, surfacing as a 500 to the external caller (e.g. Telegram
    # reporting "Wrong response from the webhook: 500 Internal Server
    # Error" and queuing pending updates indefinitely). `active.created_by`
    # is the same owner identity activation/re-activation already resolve
    # connections with -- see ActiveGraph's own docstring.
    resolved_connections = resolve_connections(graph, user_id=active.created_by)
    resolved_connection_profiles = resolve_connection_profiles(graph, user_id=active.created_by)
    resources: dict[str, Any] = {
        "connections": resolved_connections,
        "connection_profiles": resolved_connection_profiles,
        # Same fix, one level deeper: a dynamically-generated MCP node
        # backed by a *global* connection resolves whose OAuth token to use
        # via resources["running_user_id"] (backend/mcp/generated_nodes.py's
        # _make_execute) -- left unset, it would try to attach no one's
        # token at all. A trigger fire always represents the graph owner's
        # own automated execution (there's no other live caller identity to
        # use), so this is the same `active.created_by` used above.
        "running_user_id": active.created_by,
    }
    if payload is not None:
        resources["trigger_payloads"] = {node_id: payload}

    run_id = str(uuid4())
    runs.create_run(run_id, graph_id=graph_id, trigger_source=trigger_source)
    thread = threading.Thread(
        target=runs.execute_run, args=(run_id, graph, resources), daemon=True
    )
    thread.start()
    return run_id

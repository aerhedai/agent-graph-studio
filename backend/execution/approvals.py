"""In-memory pending-approval registry -- lets a node's approval gate
(`mcp_call`, spec-019's dynamically-generated MCP nodes) be answered from
the canvas UI instead of blocking on a terminal `input()` prompt.

The approval-gate contract (`ctx.resources.get("approval_prompt",
default_terminal_approval)`, ADR-004) is unchanged -- this is a second
implementation of that same `(tool_name, arguments) -> bool` callable,
injected by `backend/api/runs.py::execute_run` for every run started
through the API (manual or trigger-fired), so a node's own code never
needs to know or care which approval mechanism is answering it. The CLI
keeps using `default_terminal_approval` unchanged; it has no canvas to
show a pending approval in.

Two independent scopes for "don't ask me again", requested directly after
real use surfaced the gap between them:
  - `mcp_server_connection.py`'s `trusted` flag: never ask, for any node
    generated from that connection, ever (a property of the connection).
  - This module's "remember" option: ask once, then don't ask again for
    the *same tool name*, for the rest of *this run only* (a property of
    one run, cleared when it finishes) -- for an untrusted connection
    where full-time trust isn't wanted, but a chatty agent calling the
    same tool repeatedly within one run genuinely doesn't need re-asking
    every time.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingApproval:
    approval_id: str
    run_id: str
    tool_name: str
    arguments: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    decision: bool | None = None


_pending: dict[str, PendingApproval] = {}
# (run_id, tool_name) -> the remembered decision, set only when a caller
# resolves an approval with remember=True. Checked before ever creating a
# new PendingApproval, so a remembered call never blocks or shows a banner
# at all -- not merely "auto-resolved fast."
_remembered: dict[tuple[str, str], bool] = {}
_lock = threading.Lock()


def request_approval(run_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
    """Blocks the calling thread -- a node's execute(), running inside
    asyncio.to_thread -- until resolve_approval() is called for this
    request, UNLESS this exact (run_id, tool_name) was already remembered
    earlier in this same run, in which case it returns immediately with no
    pending approval ever created."""
    with _lock:
        if (run_id, tool_name) in _remembered:
            return _remembered[(run_id, tool_name)]

    approval_id = str(uuid.uuid4())
    pending = PendingApproval(approval_id=approval_id, run_id=run_id, tool_name=tool_name, arguments=arguments)
    with _lock:
        _pending[approval_id] = pending
    try:
        pending.event.wait()
        return bool(pending.decision)
    finally:
        with _lock:
            _pending.pop(approval_id, None)


def list_pending_for_run(run_id: str) -> list[PendingApproval]:
    with _lock:
        return [p for p in _pending.values() if p.run_id == run_id]


def resolve_approval(approval_id: str, approved: bool, remember: bool = False) -> bool:
    """Returns False if approval_id is unknown (already resolved, or never
    existed) -- the caller (the API endpoint) turns that into a 404.
    `remember=True` also stores this decision for (run_id, tool_name), so
    every subsequent call to that same tool within this run auto-resolves
    without asking again -- approve-and-remember and decline-and-remember
    both work symmetrically."""
    with _lock:
        pending = _pending.get(approval_id)
    if pending is None:
        return False
    pending.decision = approved
    if remember:
        with _lock:
            _remembered[(pending.run_id, pending.tool_name)] = approved
    pending.event.set()
    return True


def clear_remembered_for_run(run_id: str) -> None:
    """Called once a run finishes (backend/api/runs.py::execute_run) --
    remembered decisions are scoped to one run's lifetime, not persisted
    beyond it."""
    with _lock:
        for key in [k for k in _remembered if k[0] == run_id]:
            _remembered.pop(key, None)

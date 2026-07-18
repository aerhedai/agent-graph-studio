"""In-memory registry of currently-active graphs (spec-009 §4).

Deliberately not persisted anywhere -- a server restart wipes this dict,
the scheduler's jobs, and the dynamically-added webhook routes together,
exactly matching the spec's explicitly-accepted "no persistence across
restarts" limitation (§3). Mirrors the shape of the connection store
(backend/connections/store.py) in spirit, but in-memory only: there is no
separate "save a graph" concept in this spec (see the implementation note
in docs/specs/009-trigger-nodes.md's activation endpoint) -- a graph only
exists here for as long as it's active, keyed by whatever `graph_id` the
caller chose when activating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.schema.models import GraphSpec


@dataclass
class TriggerRecord:
    """Domain-side trigger metadata -- kept separate from
    `backend.api.schemas.TriggerInfo` (identical fields) so this module has
    no dependency on the API layer; `backend/api/app.py` converts between
    the two at the boundary, same direction of dependency as everywhere
    else in this codebase (api -> domain, never the reverse)."""

    node_id: str
    type: str  # "schedule_trigger" | "webhook_trigger"
    endpoint_or_schedule: str


@dataclass
class ActiveGraph:
    graph_id: str
    graph: GraphSpec
    triggers: list[TriggerRecord] = field(default_factory=list)


_active: dict[str, ActiveGraph] = {}


def set_active(graph_id: str, graph: GraphSpec, triggers: list[TriggerRecord]) -> ActiveGraph:
    record = ActiveGraph(graph_id=graph_id, graph=graph, triggers=triggers)
    _active[graph_id] = record
    return record


def get_active(graph_id: str) -> ActiveGraph | None:
    return _active.get(graph_id)


def clear_active(graph_id: str) -> ActiveGraph | None:
    return _active.pop(graph_id, None)


def list_active() -> list[ActiveGraph]:
    return list(_active.values())

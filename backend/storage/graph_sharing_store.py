"""spec-021: a graph marked `sharing="shared"` declares named connection
slots (author-facing, explicit -- your confirmed choice over auto-inferring
from `*_connection` config fields, since auto-inference is ambiguous the
moment a user has more than one connection of a given type). A different
user running that graph maps each slot to one of their *own* connections,
once; the mapping is remembered and reused silently on every subsequent run.

Two tables, one store -- same "related concerns share one file" precedent
as backend/storage/users_store.py (users + invited_emails).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_connection_slots (
    graph_id TEXT NOT NULL,
    slot_name TEXT NOT NULL,
    connection_type TEXT NOT NULL,
    PRIMARY KEY (graph_id, slot_name)
);
CREATE TABLE IF NOT EXISTS user_slot_mappings (
    user_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    slot_name TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    PRIMARY KEY (user_id, graph_id, slot_name)
)
"""


@dataclass(frozen=True)
class SlotRow:
    graph_id: str
    slot_name: str
    connection_type: str


@dataclass(frozen=True)
class MappingRow:
    user_id: str
    graph_id: str
    slot_name: str
    connection_name: str


def graph_sharing_db_path() -> Path:
    override = os.environ.get("AGENT_GRAPH_STUDIO_GRAPH_SHARING_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "graph_sharing.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or graph_sharing_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.executescript(_SCHEMA)
    return conn


def set_slots(graph_id: str, slots: list[tuple[str, str]], path: Path | None = None) -> None:
    """Replaces the full declared slot set for `graph_id` -- called whenever
    a shared graph is saved/updated with its author's current slot
    declarations, not incrementally patched. `slots` is a list of
    (slot_name, connection_type)."""
    with _connect(path) as conn:
        conn.execute("DELETE FROM graph_connection_slots WHERE graph_id = ?", (graph_id,))
        conn.executemany(
            "INSERT INTO graph_connection_slots (graph_id, slot_name, connection_type) VALUES (?, ?, ?)",
            [(graph_id, slot_name, connection_type) for slot_name, connection_type in slots],
        )


def list_slots(graph_id: str, path: Path | None = None) -> list[SlotRow]:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT graph_id, slot_name, connection_type FROM graph_connection_slots WHERE graph_id = ?",
            (graph_id,),
        ).fetchall()
    return [SlotRow(**dict(r)) for r in rows]


def clear_slots(graph_id: str, path: Path | None = None) -> None:
    """Called when a graph is switched back to private -- its declared
    slots (and any per-user mappings against them) become meaningless."""
    with _connect(path) as conn:
        conn.execute("DELETE FROM graph_connection_slots WHERE graph_id = ?", (graph_id,))
        conn.execute("DELETE FROM user_slot_mappings WHERE graph_id = ?", (graph_id,))


def set_mapping(user_id: str, graph_id: str, slot_name: str, connection_name: str, path: Path | None = None) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO user_slot_mappings (user_id, graph_id, slot_name, connection_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, graph_id, slot_name) DO UPDATE SET connection_name = excluded.connection_name
            """,
            (user_id, graph_id, slot_name, connection_name),
        )


def get_mapping(user_id: str, graph_id: str, slot_name: str, path: Path | None = None) -> str | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT connection_name FROM user_slot_mappings WHERE user_id = ? AND graph_id = ? AND slot_name = ?",
            (user_id, graph_id, slot_name),
        ).fetchone()
    return row[0] if row is not None else None


def get_mappings_for_user(user_id: str, graph_id: str, path: Path | None = None) -> dict[str, str]:
    """slot_name -> this user's own mapped connection_name, for every slot
    they've already mapped on this graph -- the exact shape execution-time
    resolution needs (see backend/mcp/generated_nodes.py's `_make_execute`
    and backend/connections/resolver.py's `resolve_connections`, both
    threaded a `slot_mappings` dict via the run's `resources` bag)."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT slot_name, connection_name FROM user_slot_mappings WHERE user_id = ? AND graph_id = ?",
            (user_id, graph_id),
        ).fetchall()
    return {slot_name: connection_name for slot_name, connection_name in rows}

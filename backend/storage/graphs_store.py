"""Durable, local SQLite store for saved graphs and their activation state
(spec-015 §4/§5).

Mirrors `backend/storage/runs_store.py`'s override-path pattern (env var for
test isolation, real path under ~/.agent-graph-studio by default) and its
per-call short-lived connection approach -- writers can come from the
FastAPI request-handling path (create/update/delete) and, on startup, a
single re-activation pass, so there is no long-lived shared connection to
worry about across threads.

Two different error-handling postures live in this one module, matching
spec-015 §4's own resolved reasoning:
- `create_graph`/`update_graph`/`get_graph`/`list_graphs`/`delete_graph` let
  `sqlite3.Error` propagate normally. A failed explicit save/load/delete is
  the primary result the caller needs correctly reported -- silently
  swallowing here would be a real bug, not a durability nicety (unlike a
  run, there is no in-memory record that's already the authoritative source
  of truth for a saved graph).
- `set_active_state`/`set_is_active` (called from inside
  `POST /graphs/{id}/activate|deactivate`) swallow-and-log instead, exactly
  like `runs_store`'s reasoning: activation's primary job is registering
  routes/cron jobs, and that must not be blocked by a persistence hiccup.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graphs (
    graph_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


@dataclass
class GraphRow:
    """Full persisted record, including the complete spec -- what
    GET /graphs/{id} and startup re-activation both need."""

    graph_id: str
    name: str
    spec_json: str
    is_active: bool
    created_at: str
    updated_at: str
    created_by: str | None = None
    """spec-020: the user id who created this graph, None for a graph
    created before this spec (or via a shared-API-key/system call with no
    human initiator)."""


@dataclass
class GraphSummaryRow:
    """Lightweight listing row -- no spec_json, per SPEC-010's "keep list
    responses light" precedent applied here too."""

    graph_id: str
    name: str
    is_active: bool
    updated_at: str


def graphs_db_path() -> Path:
    """The real store location, overridable via an env var purely for test
    isolation -- same override pattern as backend/storage/runs_store.py's
    AGENT_GRAPH_STUDIO_RUNS_DB_PATH."""
    override = os.environ.get("AGENT_GRAPH_STUDIO_GRAPHS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "graphs.db"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """spec-020: this project's first schema change to an already-existing
    table -- CREATE TABLE IF NOT EXISTS (below) does nothing for a table
    that already exists without the new column. Deliberately minimal (no
    migration framework); tolerates "duplicate column" so this is safe to
    call on every boot, not just the first one after upgrading."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or graphs_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.execute(_SCHEMA)
    _add_column_if_missing(conn, "graphs", "created_by", "TEXT")
    return conn


def _row_to_graph(row: sqlite3.Row) -> GraphRow:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    return GraphRow(**d)


def create_graph(
    graph_id: str,
    name: str,
    spec_json: str,
    created_at: str,
    created_by: str | None = None,
    path: Path | None = None,
) -> GraphRow:
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO graphs (graph_id, name, spec_json, is_active, created_at, updated_at, created_by) "
            "VALUES (?, ?, ?, 0, ?, ?, ?)",
            (graph_id, name, spec_json, created_at, created_at, created_by),
        )
    return GraphRow(
        graph_id=graph_id,
        name=name,
        spec_json=spec_json,
        is_active=False,
        created_at=created_at,
        updated_at=created_at,
        created_by=created_by,
    )


def get_graph(graph_id: str, path: Path | None = None) -> GraphRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT graph_id, name, spec_json, is_active, created_at, updated_at, created_by "
            "FROM graphs WHERE graph_id = ?",
            (graph_id,),
        ).fetchone()
    return _row_to_graph(row) if row is not None else None


def list_graphs(path: Path | None = None) -> list[GraphSummaryRow]:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT graph_id, name, is_active, updated_at FROM graphs ORDER BY updated_at DESC",
        ).fetchall()
    return [GraphSummaryRow(**{**dict(r), "is_active": bool(r["is_active"])}) for r in rows]


def update_graph(
    graph_id: str,
    updated_at: str,
    name: str | None = None,
    spec_json: str | None = None,
    path: Path | None = None,
) -> GraphRow | None:
    """Partial update -- only fields actually provided are overwritten.
    Returns the updated row, or None if graph_id doesn't exist."""
    existing = get_graph(graph_id, path=path)
    if existing is None:
        return None
    new_name = name if name is not None else existing.name
    new_spec_json = spec_json if spec_json is not None else existing.spec_json
    with _connect(path) as conn:
        conn.execute(
            "UPDATE graphs SET name = ?, spec_json = ?, updated_at = ? WHERE graph_id = ?",
            (new_name, new_spec_json, updated_at, graph_id),
        )
    return GraphRow(
        graph_id=graph_id,
        name=new_name,
        spec_json=new_spec_json,
        is_active=existing.is_active,
        created_at=existing.created_at,
        updated_at=updated_at,
        created_by=existing.created_by,
    )


def delete_graph(graph_id: str, path: Path | None = None) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute("DELETE FROM graphs WHERE graph_id = ?", (graph_id,))
    return cursor.rowcount > 0


def list_active_graphs(path: Path | None = None) -> list[GraphRow]:
    """Every graph currently flagged active -- what startup re-activation
    iterates over. Full rows (spec_json included), unlike list_graphs()."""
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT graph_id, name, spec_json, is_active, created_at, updated_at, created_by "
            "FROM graphs WHERE is_active = 1",
        ).fetchall()
    return [_row_to_graph(r) for r in rows]


def set_active_state(
    graph_id: str,
    spec_json: str,
    is_active: bool,
    updated_at: str,
    path: Path | None = None,
) -> None:
    """Upsert: called from POST /graphs/{id}/activate, which must keep
    working on an arbitrary caller-chosen id that was never explicitly
    saved first (SPEC-009's existing contract, unchanged) -- so this
    creates the row (name defaulting to graph_id) if it doesn't exist yet,
    or updates spec_json/is_active/updated_at if it does, preserving
    whatever name was already set. Swallows and logs failures -- activation
    itself must not be blocked by a persistence hiccup (see module
    docstring)."""
    try:
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO graphs (graph_id, name, spec_json, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(graph_id) DO UPDATE SET "
                "spec_json = excluded.spec_json, is_active = excluded.is_active, "
                "updated_at = excluded.updated_at",
                (graph_id, graph_id, spec_json, int(is_active), updated_at, updated_at),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist active state for graph_id=%s", graph_id)


def set_is_active(graph_id: str, is_active: bool, updated_at: str, path: Path | None = None) -> None:
    """Narrower than set_active_state -- only flips the flag, leaving
    spec_json untouched. Used by deactivate, which has no need to re-read
    or re-serialize the spec just to flip one column. Swallows and logs,
    same reasoning as set_active_state."""
    try:
        with _connect(path) as conn:
            conn.execute(
                "UPDATE graphs SET is_active = ?, updated_at = ? WHERE graph_id = ?",
                (int(is_active), updated_at, graph_id),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist is_active=%s for graph_id=%s", is_active, graph_id)

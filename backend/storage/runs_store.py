"""Durable, local SQLite store for run history (spec-010 §4/§5).

Mirrors `backend/connections/store.py`'s override-path pattern (env var for
test isolation, real path under ~/.agent-graph-studio by default) but uses
SQLite instead of a flat JSON file, since this data needs filtering and
pagination (GET /runs) that a single JSON blob can't do efficiently.

Every write function swallows `sqlite3.Error` after logging it -- a disk-full
or locked-file failure must never break the graph run itself (spec-010 §6);
the run still completes and returns its real result to the caller regardless
of whether persistence succeeded. Read functions propagate normally, since
they're called directly from API routes with their own error handling.

Each call opens and closes its own short-lived connection rather than
sharing one across threads: writers come from both the FastAPI background
worker-thread pool (`backend/api/runs.py`) and `backend/triggers/runner.py`'s
raw `threading.Thread`, and `sqlite3.Connection` objects aren't safe to share
across threads by default. A `timeout=5.0` busy-timeout tolerates brief lock
contention between, e.g., a scheduler tick and a manual run writing at
nearly the same moment, rather than failing immediately.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    graph_id TEXT,
    status TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    error TEXT
)
"""


@dataclass
class RunRow:
    """Full persisted record, including the complete result/trace blob --
    what GET /runs/{run_id} needs once a run has fallen out of the in-memory
    store (process restart, or simply evicted)."""

    run_id: str
    graph_id: str | None
    status: str
    trigger_source: str
    started_at: str
    finished_at: str | None
    result_json: str | None
    error: str | None


@dataclass
class RunRowSummary:
    """Lightweight listing row -- no result_json, per spec-010 §5's "keep
    list responses light" (a full trace blob per row would make GET /runs
    unnecessarily heavy for a simple browse/filter view)."""

    run_id: str
    graph_id: str | None
    status: str
    trigger_source: str
    started_at: str
    finished_at: str | None


def runs_db_path() -> Path:
    """The real store location, overridable via an env var purely for test
    isolation (tests must never touch the actual user's home directory) --
    same override pattern as backend/connections/store.py's
    AGENT_GRAPH_STUDIO_CONNECTIONS_PATH."""
    override = os.environ.get("AGENT_GRAPH_STUDIO_RUNS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "runs.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or runs_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.execute(_SCHEMA)
    return conn


def create_run_record(
    run_id: str,
    graph_id: str | None,
    trigger_source: str,
    started_at: str,
    path: Path | None = None,
) -> None:
    """Inserts the initial "running" row. Failure here is logged, not
    raised -- the caller (backend/api/runs.py::create_run) must be able to
    proceed with the actual run regardless of whether this succeeded."""
    try:
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, graph_id, status, trigger_source, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, graph_id, "running", trigger_source, started_at),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist run start for run_id=%s", run_id)


def complete_run_record(
    run_id: str,
    status: str,
    finished_at: str,
    result_json: str | None,
    error: str | None,
    path: Path | None = None,
) -> None:
    """Updates the row with the final status/result/error. Failure here is
    logged, not raised -- same reasoning as create_run_record."""
    try:
        with _connect(path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?, result_json = ?, error = ? "
                "WHERE run_id = ?",
                (status, finished_at, result_json, error, run_id),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist run completion for run_id=%s", run_id)


def get_run_record(run_id: str, path: Path | None = None) -> RunRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT run_id, graph_id, status, trigger_source, started_at, finished_at, "
            "result_json, error FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return RunRow(**dict(row))


def list_run_records(
    graph_id: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    limit: int = 50,
    offset: int = 0,
    path: Path | None = None,
) -> tuple[list[RunRowSummary], int]:
    """Returns (page of summaries, total matching count) for pagination --
    ordered newest-first (started_at descending) since that's the useful
    default for browsing recent history."""
    clauses: list[str] = []
    params: list[Any] = []
    if graph_id is not None:
        clauses.append("graph_id = ?")
        params.append(graph_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if trigger_source is not None:
        clauses.append("trigger_source = ?")
        params.append(trigger_source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) AS n FROM runs {where}", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT run_id, graph_id, status, trigger_source, started_at, finished_at "
            f"FROM runs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [RunRowSummary(**dict(r)) for r in rows], total

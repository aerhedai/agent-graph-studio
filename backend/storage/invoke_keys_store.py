"""Durable, local SQLite store for per-graph invoke API keys (spec-029).

Mirrors `backend/storage/graphs_store.py`'s override-path pattern (env var
for test isolation, real path under ~/.agent-graph-studio by default) and
its per-call short-lived connection approach.

Unlike `backend/connections/store.py`'s Fernet-encrypted credentials (which
must later be decrypted and reused for outbound calls), an invoke key is an
inbound bearer credential this app only ever needs to *compare*, never read
back -- so only a one-way hash (`sha256`) is stored, never anything
reversible. The plaintext token is generated and returned exactly once, by
`generate_invoke_key`, and is unrecoverable after that -- matching how every
real API-key platform (Stripe, GitHub, etc.) handles this class of secret.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_invoke_keys (
    key_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    label TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 60,
    created_at TEXT NOT NULL,
    created_by TEXT,
    last_used_at TEXT
)
"""

_TOKEN_PREFIX = "agsk_"
_DISPLAY_PREFIX_LEN = 12
""""agsk_" (5 chars) + 7 more -- enough to eyeball-distinguish keys in a
listing without showing anything sensitive; the underlying token has ~43
chars of url-safe entropy after the prefix."""


@dataclass
class InvokeKeyRow:
    key_id: str
    graph_id: str
    label: str
    key_hash: str
    key_prefix: str
    timeout_seconds: int
    created_at: str
    created_by: str | None = None
    last_used_at: str | None = None


def invoke_keys_db_path() -> Path:
    """The real store location, overridable via an env var purely for test
    isolation -- same override pattern as graphs_store.graphs_db_path()."""
    override = os.environ.get("AGENT_GRAPH_STUDIO_INVOKE_KEYS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "invoke_keys.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or invoke_keys_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.execute(_SCHEMA)
    return conn


def _row_to_key(row: sqlite3.Row) -> InvokeKeyRow:
    return InvokeKeyRow(**dict(row))


def _generate_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_invoke_key(
    graph_id: str,
    label: str,
    created_at: str,
    timeout_seconds: int = 60,
    created_by: str | None = None,
    path: Path | None = None,
) -> tuple[InvokeKeyRow, str]:
    """Creates and persists a new key, returning (row, plaintext_token).
    The plaintext is never stored and this is the only place it's ever
    available -- the caller must show it to the user now or lose it."""
    token = _generate_token()
    key_id = secrets.token_hex(8)
    row = InvokeKeyRow(
        key_id=key_id,
        graph_id=graph_id,
        label=label,
        key_hash=_hash_token(token),
        key_prefix=token[:_DISPLAY_PREFIX_LEN],
        timeout_seconds=timeout_seconds,
        created_at=created_at,
        created_by=created_by,
        last_used_at=None,
    )
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO graph_invoke_keys "
            "(key_id, graph_id, label, key_hash, key_prefix, timeout_seconds, created_at, created_by, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.key_id,
                row.graph_id,
                row.label,
                row.key_hash,
                row.key_prefix,
                row.timeout_seconds,
                row.created_at,
                row.created_by,
                row.last_used_at,
            ),
        )
    return row, token


def list_invoke_keys(graph_id: str, path: Path | None = None) -> list[InvokeKeyRow]:
    """Metadata only -- callers must never surface `key_hash` back to a
    client; the field exists on the row purely for `validate_invoke_key`'s
    own internal comparison."""
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT key_id, graph_id, label, key_hash, key_prefix, timeout_seconds, "
            "created_at, created_by, last_used_at "
            "FROM graph_invoke_keys WHERE graph_id = ? ORDER BY created_at DESC",
            (graph_id,),
        ).fetchall()
    return [_row_to_key(r) for r in rows]


def validate_invoke_key(graph_id: str, token: str, now: str | None = None, path: Path | None = None) -> InvokeKeyRow | None:
    """Looks up a key by (graph_id, hash(token)) -- scoped to that one
    graph_id, never any other. Stamps last_used_at on success when `now` is
    given (contract-preview and invoke calls both count as "used")."""
    token_hash = _hash_token(token)
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT key_id, graph_id, label, key_hash, key_prefix, timeout_seconds, "
            "created_at, created_by, last_used_at "
            "FROM graph_invoke_keys WHERE graph_id = ? AND key_hash = ?",
            (graph_id, token_hash),
        ).fetchone()
        if row is None:
            return None
        if now is not None:
            conn.execute(
                "UPDATE graph_invoke_keys SET last_used_at = ? WHERE key_id = ?",
                (now, row["key_id"]),
            )
    result = _row_to_key(row)
    if now is not None:
        result.last_used_at = now
    return result


def revoke_invoke_key(graph_id: str, key_id: str, path: Path | None = None) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute(
            "DELETE FROM graph_invoke_keys WHERE graph_id = ? AND key_id = ?",
            (graph_id, key_id),
        )
    return cursor.rowcount > 0

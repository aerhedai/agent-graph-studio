"""spec-025: SQLite-backed storage for per-user MCP API-key/bearer-token
credentials -- mirrors backend/mcp/oauth_token_storage.py's pattern exactly
(env-var-overridable path, short-lived per-call sqlite3 connections,
Fernet-encrypted secret column using the same AGENT_GRAPH_STUDIO_ENCRYPTION_KEY
the connections store and oauth_token_storage already require).

A separate table from both the connections store and oauth_token_storage:
this is the api_key/bearer counterpart of what oauth_token_storage is for
OAuth -- each user who connects to a shared, admin-configured `mcp_server`
connection via `auth_type: "api_key"`/`"bearer"` pastes *their own* key,
stored per (user_id, connection_name), never a single connection-wide
secret shared by everyone (that would defeat the entire "bring your own
credentials" point, same as OAuth tokens already being per-user).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from backend.connections.errors import MissingEncryptionKeyError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_api_keys (
    user_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    api_key TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, connection_name)
)
"""


@dataclass(frozen=True)
class McpApiKeyRow:
    user_id: str
    connection_name: str
    api_key: str
    updated_at: str


def mcp_api_keys_db_path() -> Path:
    override = os.environ.get("AGENT_GRAPH_STUDIO_MCP_API_KEYS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "mcp_api_keys.db"


def _fernet() -> Fernet:
    # Deliberately duplicated from oauth_token_storage.py's identical check
    # rather than importing its private helper cross-module -- same "small
    # duplication over shared utils" precedent already established there.
    raw = os.environ.get("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY")
    if not raw:
        raise MissingEncryptionKeyError("no value set")
    try:
        return Fernet(raw.encode())
    except Exception as e:
        raise MissingEncryptionKeyError(f"not a valid Fernet key ({e})") from e


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or mcp_api_keys_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.execute(_SCHEMA)
    return conn


def save_api_key(user_id: str, connection_name: str, api_key: str, updated_at: str, path: Path | None = None) -> None:
    encrypted = _fernet().encrypt(api_key.encode()).decode()
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO mcp_api_keys (user_id, connection_name, api_key, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, connection_name) DO UPDATE SET
                api_key = excluded.api_key,
                updated_at = excluded.updated_at
            """,
            (user_id, connection_name, encrypted, updated_at),
        )


def get_api_key(user_id: str, connection_name: str, path: Path | None = None) -> str | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT api_key FROM mcp_api_keys WHERE user_id = ? AND connection_name = ?",
            (user_id, connection_name),
        ).fetchone()
    if row is None:
        return None
    return _fernet().decrypt(row[0].encode()).decode()


def has_api_key(user_id: str, connection_name: str, path: Path | None = None) -> bool:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM mcp_api_keys WHERE user_id = ? AND connection_name = ?",
            (user_id, connection_name),
        ).fetchone()
    return row is not None


def delete_api_key(user_id: str, connection_name: str, path: Path | None = None) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute(
            "DELETE FROM mcp_api_keys WHERE user_id = ? AND connection_name = ?",
            (user_id, connection_name),
        )
        return cursor.rowcount > 0

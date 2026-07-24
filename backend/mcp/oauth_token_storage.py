"""spec-021: SQLite-backed storage for per-user MCP OAuth tokens -- mirrors
backend/storage/users_store.py's pattern exactly (env-var-overridable path,
short-lived per-call sqlite3 connections, dataclass rows). Secrets
(access/refresh tokens, client secret) are Fernet-encrypted using the same
AGENT_GRAPH_STUDIO_ENCRYPTION_KEY the connections store already requires --
one required encryption secret, not a second one to configure.

A separate table from the connections store itself
(backend/connections/store.py), not folded into ConnectionProfile.config:
tokens are refreshed far more often than a connection's static config
changes, and the connections store is one whole-file read-modify-write per
change -- putting frequently-refreshed token state there would mean every
single refresh rewrites every user's every connection. This table's rows
are independently addressable, matching how often they actually change.

`client_id`/`client_secret` are stored per-token-row too, even though
they're really a property of the underlying OAuth client registration
(shared across every token this connection ever gets) -- a small,
deliberate duplication (this project's own "small duplication over
cross-module coupling" precedent) so backend/mcp/oauth_flow.py's refresh
logic never needs to import/query the connections store at all.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from backend.connections.errors import MissingEncryptionKeyError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_oauth_tokens (
    user_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at TEXT,
    token_type TEXT NOT NULL DEFAULT 'Bearer',
    scope TEXT,
    token_endpoint TEXT NOT NULL,
    client_id TEXT,
    client_secret TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, connection_name)
)
"""


@dataclass(frozen=True)
class McpOAuthTokenRow:
    user_id: str
    connection_name: str
    access_token: str
    refresh_token: str | None
    expires_at: str | None
    token_type: str
    scope: str | None
    token_endpoint: str
    client_id: str | None
    client_secret: str | None
    updated_at: str


def mcp_oauth_tokens_db_path() -> Path:
    override = os.environ.get("AGENT_GRAPH_STUDIO_MCP_OAUTH_TOKENS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "mcp_oauth_tokens.db"


def _fernet() -> Fernet:
    # Deliberately duplicated from backend/connections/store.py's identical
    # check rather than importing its private helper cross-module -- same
    # "small duplication over shared utils" precedent as
    # backend/storage/graphs_store.py's/runs_store.py's _add_column_if_missing.
    raw = os.environ.get("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY")
    if not raw:
        raise MissingEncryptionKeyError("no value set")
    try:
        return Fernet(raw.encode())
    except Exception as e:
        raise MissingEncryptionKeyError(f"not a valid Fernet key ({e})") from e


def _encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().decrypt(value.encode()).decode()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or mcp_oauth_tokens_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.execute(_SCHEMA)
    return conn


def save_token(
    user_id: str,
    connection_name: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: str | None,
    token_type: str,
    scope: str | None,
    token_endpoint: str,
    client_id: str | None,
    client_secret: str | None,
    updated_at: str,
    path: Path | None = None,
) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO mcp_oauth_tokens
                (user_id, connection_name, access_token, refresh_token, expires_at,
                 token_type, scope, token_endpoint, client_id, client_secret, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, connection_name) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                token_type = excluded.token_type,
                scope = excluded.scope,
                token_endpoint = excluded.token_endpoint,
                client_id = excluded.client_id,
                client_secret = excluded.client_secret,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                connection_name,
                _encrypt(access_token),
                _encrypt(refresh_token),
                expires_at,
                token_type,
                scope,
                token_endpoint,
                client_id,
                _encrypt(client_secret),
                updated_at,
            ),
        )


def get_token(user_id: str, connection_name: str, path: Path | None = None) -> McpOAuthTokenRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM mcp_oauth_tokens WHERE user_id = ? AND connection_name = ?",
            (user_id, connection_name),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["access_token"] = _decrypt(data["access_token"])
    data["refresh_token"] = _decrypt(data["refresh_token"])
    data["client_secret"] = _decrypt(data["client_secret"])
    return McpOAuthTokenRow(**data)


def delete_token(user_id: str, connection_name: str, path: Path | None = None) -> bool:
    with _connect(path) as conn:
        cursor = conn.execute(
            "DELETE FROM mcp_oauth_tokens WHERE user_id = ? AND connection_name = ?",
            (user_id, connection_name),
        )
        return cursor.rowcount > 0

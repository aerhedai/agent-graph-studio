"""Durable, local SQLite store for platform accounts (spec-020) -- mirrors
`backend/storage/graphs_store.py`'s pattern exactly (env-var override path
for test isolation, short-lived per-call connections, dataclass rows).
Holds both `users` and `invited_emails` in one store, the same "related
concerns share one file" precedent already established by
`backend/connections/store.py`.

Two questions, two mechanisms, never conflated (spec-020 §4): a Google
sign-in answers "is this really you"; the invited_emails allowlist answers
"are you allowed here." A valid Google identity that isn't invited is a
clean, specific rejection, not a silent account creation.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    invited_by TEXT
);
CREATE TABLE IF NOT EXISTS invited_emails (
    email TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    invited_by TEXT,
    invited_at TEXT NOT NULL
)
"""


class MissingAdminEmailError(RuntimeError):
    """spec-020: raised eagerly at API startup when
    AGENT_GRAPH_STUDIO_ADMIN_EMAIL isn't set -- there is no way to bootstrap
    a first admin (who alone can invite anyone else) without it, the exact
    chicken-and-egg problem this env var exists to avoid."""

    def __init__(self) -> None:
        super().__init__(
            "AGENT_GRAPH_STUDIO_ADMIN_EMAIL is not set -- refusing to start without a "
            "first admin to bootstrap (see docs/DEPLOYMENT.md)."
        )


@dataclass(frozen=True)
class UserRow:
    id: str
    email: str
    display_name: str
    role: str
    created_at: str
    invited_by: str | None


@dataclass(frozen=True)
class InviteRow:
    email: str
    role: str
    invited_by: str | None
    invited_at: str


def users_db_path() -> Path:
    override = os.environ.get("AGENT_GRAPH_STUDIO_USERS_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "users.db"


def _admin_email() -> str:
    email = os.environ.get("AGENT_GRAPH_STUDIO_ADMIN_EMAIL")
    if not email:
        raise MissingAdminEmailError()
    return email


def ensure_admin_email_configured() -> None:
    """Public entry point for backend/api/app.py's eager startup check."""
    _admin_email()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or users_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.executescript(_SCHEMA)
    return conn


def add_invite(
    email: str, role: str, invited_by: str | None, invited_at: str, path: Path | None = None
) -> InviteRow:
    """Upsert -- re-inviting an already-invited email updates its role/
    inviter rather than erroring, matching graphs_store.set_active_state's
    upsert precedent for the same "caller-idempotent" reasoning."""
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO invited_emails (email, role, invited_by, invited_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET role = excluded.role, invited_by = excluded.invited_by",
            (email, role, invited_by, invited_at),
        )
    return InviteRow(email=email, role=role, invited_by=invited_by, invited_at=invited_at)


def get_invite(email: str, path: Path | None = None) -> InviteRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, role, invited_by, invited_at FROM invited_emails WHERE email = ?", (email,)
        ).fetchone()
    return InviteRow(**dict(row)) if row is not None else None


def list_invites(path: Path | None = None) -> list[InviteRow]:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT email, role, invited_by, invited_at FROM invited_emails ORDER BY invited_at DESC"
        ).fetchall()
    return [InviteRow(**dict(r)) for r in rows]


def create_user(
    user_id: str,
    email: str,
    display_name: str,
    role: str,
    created_at: str,
    invited_by: str | None,
    path: Path | None = None,
) -> UserRow:
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO users (id, email, display_name, role, created_at, invited_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, email, display_name, role, created_at, invited_by),
        )
    return UserRow(
        id=user_id, email=email, display_name=display_name, role=role, created_at=created_at, invited_by=invited_by
    )


def get_user_by_email(email: str, path: Path | None = None) -> UserRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, email, display_name, role, created_at, invited_by FROM users WHERE email = ?", (email,)
        ).fetchone()
    return UserRow(**dict(row)) if row is not None else None


def get_user_by_id(user_id: str, path: Path | None = None) -> UserRow | None:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, email, display_name, role, created_at, invited_by FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return UserRow(**dict(row)) if row is not None else None


def ensure_admin_bootstrapped(admin_email: str, bootstrapped_at: str, path: Path | None = None) -> None:
    """Called once at startup (backend/api/app.py's _lifespan) -- idempotent,
    safe to call on every boot. Adds the configured admin email to the
    allowlist with role="admin" if it isn't already present; never
    downgrades an existing invite's role (a real admin who was since
    changed to member, if that ever becomes possible, should not be
    silently re-promoted on every restart)."""
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO invited_emails (email, role, invited_by, invited_at) VALUES (?, 'admin', NULL, ?) "
            "ON CONFLICT(email) DO NOTHING",
            (admin_email, bootstrapped_at),
        )

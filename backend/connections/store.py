"""Local, per-machine store of named connection profiles
(~/.agent-graph-studio/connections.json by default) -- spec-006 §4/§5.
Never committed to any repo, never referenced from graph JSON directly (only
by name, resolved at run time -- see resolver.py).

spec-017: file contents are a Fernet-encrypted token, not readable JSON --
connection secrets (bot tokens, API keys) no longer sit in plaintext on
disk. A pre-spec-017 plaintext file is auto-migrated: the first read that
fails to decrypt falls back to legacy json.loads, then immediately
re-persists encrypted -- self-healing, no separate migration command.

spec-021: `ConnectionProfile.user_id` -- `None` means a global/shared
connection, visible to and usable by every user (today's exact behavior,
preserved unchanged for Ollama/Anthropic/Telegram/manually-configured
mcp_server connections and any shared-API-key caller). A real user id means
that connection is private to that user (the mechanism a per-user
OAuth-authenticated mcp_server connection, or any connection a user simply
chooses to keep private, uses). Uniqueness is on `(user_id, name)`, not
`name` alone -- two different users may each have their own connection
named e.g. "my-gmail" with no collision, and a user's own connection may
share a name with an unrelated global one."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

from backend.connections.errors import DuplicateConnectionError, MissingEncryptionKeyError


class ConnectionProfile(BaseModel):
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None


def connections_path() -> Path:
    """The real store location, overridable via an env var purely for test
    isolation (tests must never touch the actual user's home directory)."""
    override = os.environ.get("AGENT_GRAPH_STUDIO_CONNECTIONS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "connections.json"


def _encryption_key() -> bytes:
    """Raises MissingEncryptionKeyError -- eagerly, not lazily -- if the key
    is absent or malformed. Called both by every store operation here and,
    explicitly at API startup (backend/api/app.py's ensure_encryption_key_
    configured), so "the backend refuses to start" is deterministic rather
    than incidental on whichever operation happens to touch this first."""
    raw = os.environ.get("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY")
    if not raw:
        raise MissingEncryptionKeyError("no value set")
    try:
        Fernet(raw.encode())
    except Exception as e:
        raise MissingEncryptionKeyError(f"not a valid Fernet key ({e})") from e
    return raw.encode()


def _fernet() -> Fernet:
    return Fernet(_encryption_key())


def ensure_encryption_key_configured() -> None:
    """Public entry point for backend/api/app.py's eager startup check --
    raises MissingEncryptionKeyError, discards the key otherwise. Exists so
    "the backend refuses to start without one" is a real, explicit check
    performed unconditionally on every boot, not just an incidental side
    effect of some other operation touching the connections store."""
    _encryption_key()


def _load_all(path: Path | None = None) -> list[ConnectionProfile]:
    target = path or connections_path()
    if not target.exists():
        return []
    raw = target.read_bytes()
    if not raw:
        return []
    try:
        decrypted = _fernet().decrypt(raw)
        data = json.loads(decrypted)
    except InvalidToken:
        # Pre-spec-017 plaintext file -- migrate it in place, once, now.
        data = json.loads(raw)
        profiles = [ConnectionProfile.model_validate(c) for c in data.get("connections", [])]
        _save_all(profiles, path)
        return profiles
    return [ConnectionProfile.model_validate(c) for c in data.get("connections", [])]


def _save_all(profiles: list[ConnectionProfile], path: Path | None = None) -> None:
    target = path or connections_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    plaintext = json.dumps({"connections": [p.model_dump() for p in profiles]}, indent=2)
    target.write_bytes(_fernet().encrypt(plaintext.encode()))


def list_connections(user_id: str | None = None, path: Path | None = None) -> list[ConnectionProfile]:
    """A caller's own connections (if `user_id` given) plus every global
    (`user_id=None`) connection -- the "what can this caller pick from"
    view (canvas picker, `GET /connections`). An unauthenticated/shared-key
    caller (`user_id=None`) sees only global connections, exactly today's
    pre-spec-021 behavior. For "every connection regardless of owner", see
    `list_connections_unscoped` -- used only by startup node-type
    regeneration, which must not miss another user's mcp_server
    connections."""
    all_profiles = _load_all(path)
    if user_id is None:
        return [c for c in all_profiles if c.user_id is None]
    return [c for c in all_profiles if c.user_id == user_id or c.user_id is None]


def list_connections_unscoped(path: Path | None = None) -> list[ConnectionProfile]:
    return _load_all(path)


def get_connection(name: str, user_id: str | None = None, path: Path | None = None) -> ConnectionProfile | None:
    """Exact `(user_id, name)` lookup -- `user_id=None` means "the global
    connection named `name`", not "any connection named `name`". Callers
    needing "this user's own, falling back to global" resolution semantics
    (execution-time connection resolution) use `resolve_connection_for_user`
    instead."""
    return next((c for c in _load_all(path) if c.name == name and c.user_id == user_id), None)


def resolve_connection_for_user(
    name: str, user_id: str | None, path: Path | None = None
) -> ConnectionProfile | None:
    """Execution-time resolution policy (spec-021): prefer `user_id`'s own
    connection named `name`; fall back to a global connection of the same
    name. Used wherever a graph is actually being run/introspected for a
    specific user -- never by the connection-management API endpoints
    themselves, which operate on exact `(user_id, name)` pairs via
    `get_connection` so a user can't accidentally "resolve into" someone
    else's same-named private connection."""
    if user_id is not None:
        own = get_connection(name, user_id=user_id, path=path)
        if own is not None:
            return own
    return get_connection(name, user_id=None, path=path)


def add_connection(
    name: str, type_name: str, config: dict[str, Any], user_id: str | None = None, path: Path | None = None
) -> ConnectionProfile:
    profiles = _load_all(path)
    if any(c.name == name and c.user_id == user_id for c in profiles):
        raise DuplicateConnectionError(name)
    profile = ConnectionProfile(name=name, type=type_name, config=config, user_id=user_id)
    profiles.append(profile)
    _save_all(profiles, path)
    return profile


def delete_connection(name: str, user_id: str | None = None, path: Path | None = None) -> bool:
    profiles = _load_all(path)
    remaining = [c for c in profiles if not (c.name == name and c.user_id == user_id)]
    if len(remaining) == len(profiles):
        return False
    _save_all(remaining, path)
    return True


def update_connection_config(
    name: str, user_id: str | None, config: dict[str, Any], path: Path | None = None
) -> ConnectionProfile | None:
    """spec-021: the first mutation a connection's config can undergo after
    creation -- every connection type before this was write-once (create,
    or delete-and-recreate). Needed specifically so an `mcp_server`
    connection's discovered/registered OAuth client info
    (`requires_oauth`/`oauth_client_id`/`oauth_client_secret`) can be
    persisted back onto the connection that triggered discovering it,
    rather than needing a second, parallel place to store it. Returns None
    if no connection matches `(user_id, name)`, mirroring get_connection's
    exact-scope semantics -- never silently creates one."""
    profiles = _load_all(path)
    updated: ConnectionProfile | None = None
    for i, profile in enumerate(profiles):
        if profile.name == name and profile.user_id == user_id:
            updated = profile.model_copy(update={"config": config})
            profiles[i] = updated
            break
    if updated is None:
        return None
    _save_all(profiles, path)
    return updated


def set_connection_owner(
    name: str, current_user_id: str | None, new_user_id: str | None, path: Path | None = None
) -> ConnectionProfile | None:
    """spec-023: the one mutation "promote to global" needs -- moves a
    connection from `(current_user_id, name)` to `(new_user_id, name)`,
    same read-modify-write shape as update_connection_config immediately
    above. Returns None if the source doesn't exist, or if a connection
    already exists at the destination `(new_user_id, name)` -- uniqueness
    preserved exactly like add_connection's own check, never silently
    overwrites an existing connection at the target scope."""
    profiles = _load_all(path)
    if any(c.name == name and c.user_id == new_user_id for c in profiles):
        return None
    updated: ConnectionProfile | None = None
    for i, profile in enumerate(profiles):
        if profile.name == name and profile.user_id == current_user_id:
            updated = profile.model_copy(update={"user_id": new_user_id})
            profiles[i] = updated
            break
    if updated is None:
        return None
    _save_all(profiles, path)
    return updated

"""Local, per-machine store of named connection profiles
(~/.agent-graph-studio/connections.json by default) -- spec-006 §4/§5.
Never committed to any repo, never referenced from graph JSON directly (only
by name, resolved at run time -- see resolver.py).

spec-017: file contents are a Fernet-encrypted token, not readable JSON --
connection secrets (bot tokens, API keys) no longer sit in plaintext on
disk. A pre-spec-017 plaintext file is auto-migrated: the first read that
fails to decrypt falls back to legacy json.loads, then immediately
re-persists encrypted -- self-healing, no separate migration command.
"""

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


def list_connections(path: Path | None = None) -> list[ConnectionProfile]:
    return _load_all(path)


def get_connection(name: str, path: Path | None = None) -> ConnectionProfile | None:
    return next((c for c in _load_all(path) if c.name == name), None)


def add_connection(
    name: str, type_name: str, config: dict[str, Any], path: Path | None = None
) -> ConnectionProfile:
    profiles = _load_all(path)
    if any(c.name == name for c in profiles):
        raise DuplicateConnectionError(name)
    profile = ConnectionProfile(name=name, type=type_name, config=config)
    profiles.append(profile)
    _save_all(profiles, path)
    return profile


def delete_connection(name: str, path: Path | None = None) -> bool:
    profiles = _load_all(path)
    remaining = [c for c in profiles if c.name != name]
    if len(remaining) == len(profiles):
        return False
    _save_all(remaining, path)
    return True

"""Local, per-machine store of named connection profiles
(~/.agent-graph-studio/connections.json by default) -- spec-006 §4/§5.
Never committed to any repo, never referenced from graph JSON directly (only
by name, resolved at run time -- see resolver.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.connections.errors import DuplicateConnectionError


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


def _load_all(path: Path | None = None) -> list[ConnectionProfile]:
    target = path or connections_path()
    if not target.exists():
        return []
    data = json.loads(target.read_text())
    return [ConnectionProfile.model_validate(c) for c in data.get("connections", [])]


def _save_all(profiles: list[ConnectionProfile], path: Path | None = None) -> None:
    target = path or connections_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"connections": [p.model_dump() for p in profiles]}, indent=2))


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

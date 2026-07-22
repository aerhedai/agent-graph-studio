"""Tiny store for app-level settings (spec-018 §4) -- currently just the
operator-provided public base URL, used to auto-register external webhooks
(Telegram's setWebhook/deleteWebhook). A plain JSON file, not encrypted --
unlike backend/connections/store.py, nothing stored here is a secret.

Mirrors every other store in this codebase's env-override-path convention
(AGENT_GRAPH_STUDIO_SETTINGS_PATH) purely for test isolation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    override = os.environ.get("AGENT_GRAPH_STUDIO_SETTINGS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".agent-graph-studio" / "settings.json"


def _load(path: Path | None = None) -> dict:
    target = path or settings_path()
    if not target.exists():
        return {}
    return json.loads(target.read_text())


def _save(data: dict, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2))


def get_public_base_url(path: Path | None = None) -> str | None:
    return _load(path).get("public_base_url")


def set_public_base_url(url: str, path: Path | None = None) -> None:
    data = _load(path)
    data["public_base_url"] = url
    _save(data, path)

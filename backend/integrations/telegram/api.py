"""Plain, shared outbound call to Telegram's Bot API -- used by both the
spec-019 `telegram_messaging`/`telegram_chat_management` nodes and
`backend/api/app.py`'s webhook auto-registration (setWebhook/deleteWebhook,
spec-018). Moved here from app.py so it isn't duplicated between the two
callers.

Plain urllib -- this codebase's established convention for outbound HTTP
calls (see backend/connections/ollama_connection.py), not httpx (a
transitive dependency only, never used directly elsewhere here)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def call_telegram_api(token: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram API call to '{method}' failed: {e}") from e
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API '{method}' rejected the request: {body.get('description', body)}")
    return body

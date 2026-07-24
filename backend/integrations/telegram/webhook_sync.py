"""Telegram's implementation of the generic webhook-sync interface
(backend/triggers/webhook_sync.py) -- registered as an import side effect,
same pattern as node-type/connection-type registration elsewhere in this
codebase. Moved out of backend/api/app.py (spec-018's original,
Telegram-hardcoded version) so app.py's activate_graph/deactivate_graph
don't need to know Telegram exists at all.
"""

from __future__ import annotations

from typing import Any

from backend.integrations.telegram.api import call_telegram_api
from backend.schema.models import NodeSpec
from backend.triggers.webhook_sync import WebhookSyncHandler, register_webhook_sync_handler


def _sync_on_activate(
    _webhook_node: NodeSpec,
    adapter_node: NodeSpec,
    full_url: str,
    resolved_connections: dict[str, Any],
) -> None:
    connection_name = adapter_node.config.get("bot_token_connection")
    token = resolved_connections.get(connection_name)
    if not isinstance(token, str):
        raise RuntimeError(
            f"telegram_adapter '{adapter_node.id}' references unresolved "
            f"connection {connection_name!r}"
        )
    call_telegram_api(token, "setWebhook", {"url": full_url})


def _sync_on_deactivate(
    _webhook_node: NodeSpec, adapter_node: NodeSpec, resolved_connections: dict[str, Any]
) -> None:
    connection_name = adapter_node.config.get("bot_token_connection")
    token = resolved_connections.get(connection_name)
    if not isinstance(token, str):
        return
    call_telegram_api(token, "deleteWebhook", {})


register_webhook_sync_handler(
    WebhookSyncHandler(
        adapter_node_type="telegram_adapter",
        sync_on_activate=_sync_on_activate,
        sync_on_deactivate=_sync_on_deactivate,
    )
)

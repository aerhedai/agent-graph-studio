"""spec-019 §4: an integration-agnostic interface for "does this trigger
adapter support auto webhook registration on Activate/Deactivate" --
Telegram (backend/integrations/telegram/webhook_sync.py) is the first
implementation, registered here the same way node types and connection
types register themselves elsewhere in this codebase. `activate_graph`/
`deactivate_graph` (backend/api/app.py) call through this generic
interface -- adding a second adapter with the same capability later needs
zero changes there, only a new registered handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.schema.models import GraphSpec, NodeSpec


@dataclass(frozen=True)
class WebhookSyncHandler:
    adapter_node_type: str
    """The trigger_adapter node type this handles, e.g. "telegram_adapter"."""
    sync_on_activate: Callable[[NodeSpec, NodeSpec, str, dict[str, Any]], None]
    """(webhook_node, adapter_node, full_webhook_url, resolved_connections) ->
    None. Raises on failure -- the caller rolls back the whole activation
    (fail-closed, matching the existing invalid-cron-expression precedent)."""
    sync_on_deactivate: Callable[[NodeSpec, NodeSpec, dict[str, Any]], None]
    """(webhook_node, adapter_node, resolved_connections) -> None.
    Best-effort -- the caller logs and continues on failure, since
    deactivation's primary job (removing the local registration) must
    still succeed even if the external API is briefly unreachable."""


_handlers: dict[str, WebhookSyncHandler] = {}


def register_webhook_sync_handler(handler: WebhookSyncHandler) -> None:
    _handlers[handler.adapter_node_type] = handler


def get_handler(adapter_node_type: str) -> WebhookSyncHandler | None:
    return _handlers.get(adapter_node_type)


def adapter_pairs_for_graph(graph: GraphSpec) -> list[tuple[NodeSpec, NodeSpec]]:
    """Every (webhook_trigger, adapter) pair in the graph whose adapter type
    has a registered sync handler -- generalizes the old, Telegram-only
    traversal to any adapter type. A graph with no such pair (a
    generic_adapter, or no webhook_trigger at all) yields an empty list, a
    no-op for the caller."""
    nodes_by_id = {n.id: n for n in graph.nodes}
    pairs: list[tuple[NodeSpec, NodeSpec]] = []
    for node in graph.nodes:
        if node.type != "webhook_trigger":
            continue
        for edge in graph.edges:
            if edge.kind == "sub_node" and edge.slot == "trigger_adapter" and edge.to.node == node.id:
                source = nodes_by_id.get(edge.from_.node)
                if source is not None and source.type in _handlers:
                    pairs.append((node, source))
    return pairs

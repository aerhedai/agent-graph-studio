"""spec-019 §4: backend/triggers/webhook_sync.py's generic registry
mechanism itself -- proven with a fake adapter type, independent of
Telegram, to show the interface is genuinely extensible and not just
working because Telegram happens to be the one registered handler."""

from __future__ import annotations

from backend.schema.loader import parse_graph_json
from backend.triggers.webhook_sync import (
    WebhookSyncHandler,
    adapter_pairs_for_graph,
    get_handler,
    register_webhook_sync_handler,
)


def _fake_adapter_graph(adapter_type: str) -> str:
    import json

    return json.dumps(
        {
            "version": "0.1",
            "nodes": [
                {"id": "trigger", "type": "webhook_trigger", "config": {}},
                {"id": "adapter", "type": adapter_type, "config": {}},
            ],
            "edges": [
                {
                    "kind": "sub_node",
                    "slot": "trigger_adapter",
                    "from": {"node": "adapter"},
                    "to": {"node": "trigger"},
                },
            ],
        }
    )


def test_telegram_handler_is_registered_by_default():
    handler = get_handler("telegram_adapter")
    assert handler is not None
    assert handler.adapter_node_type == "telegram_adapter"


def test_unregistered_adapter_type_has_no_handler():
    assert get_handler("generic_adapter") is None


def test_generic_adapter_graph_yields_no_sync_pairs():
    graph = parse_graph_json(_fake_adapter_graph("generic_adapter"))
    assert adapter_pairs_for_graph(graph) == []


def test_a_second_registered_handler_type_is_picked_up_generically():
    """Proves the interface itself is the extensibility point -- a brand
    new adapter type registering itself the same way Telegram does becomes
    discoverable via the exact same generic traversal, with zero changes
    to adapter_pairs_for_graph or any caller."""
    register_webhook_sync_handler(
        WebhookSyncHandler(
            adapter_node_type="_test_fake_adapter",
            sync_on_activate=lambda *a: None,
            sync_on_deactivate=lambda *a: None,
        )
    )
    try:
        assert get_handler("_test_fake_adapter") is not None
        # Note: "_test_fake_adapter" isn't a real registered *node* type, so
        # this graph would fail real node-type validation -- this test
        # exercises adapter_pairs_for_graph's own traversal logic directly
        # (structural edge/type matching), not full graph validation.
        graph = parse_graph_json(_fake_adapter_graph("_test_fake_adapter"))
        pairs = adapter_pairs_for_graph(graph)
        assert len(pairs) == 1
        assert pairs[0][1].type == "_test_fake_adapter"
    finally:
        # Don't leak this fake handler into other tests in the same
        # process -- the registry is a module-level singleton.
        from backend.triggers import webhook_sync as webhook_sync_module

        webhook_sync_module._handlers.pop("_test_fake_adapter", None)

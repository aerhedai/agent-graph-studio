from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.app as app_module
import backend.integrations.telegram.webhook_sync as telegram_webhook_sync_module
from backend.api.app import app
from backend.connections.store import add_connection
from backend.schema.loader import parse_graph_json
from backend.storage import settings_store
from backend.triggers import registry as trigger_registry
from backend.triggers.webhook_sync import adapter_pairs_for_graph

# spec-017: must match tests/conftest.py's TEST_API_KEY.
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _telegram_graph(bot_token_connection: str = "my-bot") -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {
                "id": "adapter",
                "type": "telegram_adapter",
                "config": {"bot_token_connection": bot_token_connection},
            },
            {"id": "out", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
            {"from": {"node": "trigger", "slot": "message_text"}, "to": {"node": "out", "slot": "text"}},
        ],
    }


def _generic_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
        ],
    }


def _deactivate_quietly(graph_id: str) -> None:
    client.post(f"/graphs/{graph_id}/deactivate")


# --- adapter_pairs_for_graph traversal ---------------------------------


def test_finds_telegram_adapter_pair():
    graph = parse_graph_json(__import__("json").dumps(_telegram_graph()))
    pairs = adapter_pairs_for_graph(graph)
    assert len(pairs) == 1
    webhook_node, adapter_node = pairs[0]
    assert webhook_node.id == "trigger"
    assert adapter_node.id == "adapter"
    assert adapter_node.type == "telegram_adapter"


def test_generic_adapter_graph_yields_no_telegram_pairs():
    graph = parse_graph_json(__import__("json").dumps(_generic_graph()))
    assert adapter_pairs_for_graph(graph) == []


def test_graph_with_no_webhook_trigger_at_all_yields_no_pairs():
    graph = parse_graph_json(
        __import__("json").dumps(
            {
                "version": "0.1",
                "nodes": [{"id": "in", "type": "text_input", "config": {"value": "hi"}}],
                "edges": [],
            }
        )
    )
    assert adapter_pairs_for_graph(graph) == []


# --- settings store ----------------------------------------------------------


def test_settings_store_get_returns_none_when_unset():
    assert settings_store.get_public_base_url() is None


def test_settings_store_round_trips():
    settings_store.set_public_base_url("https://example.com")
    assert settings_store.get_public_base_url() == "https://example.com"


def test_get_settings_endpoint():
    assert client.get("/settings").json() == {"public_base_url": None}
    settings_store.set_public_base_url("https://example.com")
    assert client.get("/settings").json() == {"public_base_url": "https://example.com"}


def test_put_settings_saves_and_reports_unreachable_warning():
    response = client.put("/settings", json={"public_base_url": "https://this-does-not-resolve.invalid"})
    assert response.status_code == 200
    body = response.json()
    assert body["public_base_url"] == "https://this-does-not-resolve.invalid"
    assert body["warning"] is not None
    # Saved despite being unreachable -- not a hard block.
    assert settings_store.get_public_base_url() == "https://this-does-not-resolve.invalid"


def test_put_settings_strips_trailing_slash():
    response = client.put("/settings", json={"public_base_url": "https://example.com/"})
    assert response.json()["public_base_url"] == "https://example.com"


# --- activate/deactivate wired to a mocked Telegram API ---------------------


def test_activate_with_telegram_adapter_calls_set_webhook(monkeypatch):
    settings_store.set_public_base_url("https://public.example.com")
    add_connection("my-bot", "telegram", {"bot_token": "fake-token-123"})

    calls = []

    def fake_call(token, method, params):
        calls.append((token, method, params))
        return {"ok": True, "result": True}

    monkeypatch.setattr(telegram_webhook_sync_module, "call_telegram_api", fake_call)

    graph_id = "telegram-activate-graph"
    try:
        response = client.post(f"/graphs/{graph_id}/activate", json=_telegram_graph())
        assert response.status_code == 200
        assert len(calls) == 1
        token, method, params = calls[0]
        assert token == "fake-token-123"
        assert method == "setWebhook"
        assert params["url"].startswith("https://public.example.com/webhooks/")
        assert "?key=" in params["url"] or "key=" in params["url"]
    finally:
        _deactivate_quietly(graph_id)


def test_activate_with_telegram_adapter_but_no_public_base_url_fails_and_rolls_back():
    assert settings_store.get_public_base_url() is None
    add_connection("my-bot", "telegram", {"bot_token": "fake-token-123"})

    graph_id = "telegram-no-url-graph"
    response = client.post(f"/graphs/{graph_id}/activate", json=_telegram_graph())
    assert response.status_code == 422
    assert trigger_registry.get_active(graph_id) is None
    # Rolled back cleanly -- the webhook route must not remain registered.
    assert client.post(f"/webhooks/{graph_id}/trigger", json={}).status_code == 404


def test_activate_rolls_back_when_telegram_api_rejects(monkeypatch):
    settings_store.set_public_base_url("https://public.example.com")
    add_connection("my-bot", "telegram", {"bot_token": "fake-token-123"})

    def failing_call(token, method, params):
        raise RuntimeError("Telegram API 'setWebhook' rejected the request: bad token")

    monkeypatch.setattr(telegram_webhook_sync_module, "call_telegram_api", failing_call)

    graph_id = "telegram-reject-graph"
    response = client.post(f"/graphs/{graph_id}/activate", json=_telegram_graph())
    assert response.status_code == 502
    assert trigger_registry.get_active(graph_id) is None
    assert client.post(f"/webhooks/{graph_id}/trigger", json={}).status_code == 404


def test_deactivate_calls_delete_webhook(monkeypatch):
    settings_store.set_public_base_url("https://public.example.com")
    add_connection("my-bot", "telegram", {"bot_token": "fake-token-123"})

    calls = []

    def fake_call(token, method, params):
        calls.append(method)
        return {"ok": True, "result": True}

    monkeypatch.setattr(telegram_webhook_sync_module, "call_telegram_api", fake_call)

    graph_id = "telegram-deactivate-graph"
    client.post(f"/graphs/{graph_id}/activate", json=_telegram_graph())
    calls.clear()

    response = client.post(f"/graphs/{graph_id}/deactivate")
    assert response.status_code == 200
    assert calls == ["deleteWebhook"]


def test_deactivate_succeeds_even_if_delete_webhook_fails(monkeypatch):
    """spec-018 §4: deactivation's primary job must still succeed even if
    Telegram's own API is briefly unreachable -- deliberately asymmetric
    from activate's fail-closed behavior."""
    settings_store.set_public_base_url("https://public.example.com")
    add_connection("my-bot", "telegram", {"bot_token": "fake-token-123"})

    def fake_call_ok_then_fail(token, method, params):
        if method == "deleteWebhook":
            raise RuntimeError("Telegram is briefly unreachable")
        return {"ok": True, "result": True}

    monkeypatch.setattr(telegram_webhook_sync_module, "call_telegram_api", fake_call_ok_then_fail)

    graph_id = "telegram-flaky-deactivate-graph"
    client.post(f"/graphs/{graph_id}/activate", json=_telegram_graph())

    response = client.post(f"/graphs/{graph_id}/deactivate")
    assert response.status_code == 200
    assert response.json() == {"status": "inactive"}
    assert trigger_registry.get_active(graph_id) is None


def test_activate_without_telegram_adapter_never_calls_telegram_api(monkeypatch):
    calls = []
    monkeypatch.setattr(
        telegram_webhook_sync_module, "call_telegram_api", lambda *a, **k: calls.append(1)
    )

    graph_id = "non-telegram-graph"
    try:
        response = client.post(f"/graphs/{graph_id}/activate", json=_generic_graph())
        assert response.status_code == 200
        assert calls == []
    finally:
        _deactivate_quietly(graph_id)

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.connections.store import get_connection
from backend.mcp.client import McpToolInfo

# spec-017: must match tests/conftest.py's TEST_API_KEY (the isolated_api_key
# fixture sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value).
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

_FAKE_TOOL = McpToolInfo(name="do_thing", param_names=[], param_json_types={}, required_names=frozenset())


def _entry_by_key(entries: list[dict], key: str) -> dict:
    return next(e for e in entries if e["key"] == key)


def test_get_app_catalog_returns_exactly_three_entries_with_real_values():
    resp = client.get("/app-catalog")
    assert resp.status_code == 200
    entries = resp.json()
    assert {e["key"] for e in entries} == {"gmail", "context7", "discord"}

    gmail = _entry_by_key(entries, "gmail")
    assert gmail["server_url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert gmail["credential_type"] == "google_gmail_oauth2"
    assert gmail["requires_oauth"] is True
    assert "gmail.readonly" in gmail["default_scope"]
    assert "gmail.send" in gmail["default_scope"]

    context7 = _entry_by_key(entries, "context7")
    assert context7["server_url"] == "https://mcp.context7.com/mcp"
    assert context7["credential_type"] is None
    assert context7["requires_oauth"] is False
    assert context7["setup_instructions"] is None

    discord = _entry_by_key(entries, "discord")
    assert discord["server_url"] is None
    assert discord["credential_type"] == "discord_webhook_oauth2"
    assert discord["requires_oauth"] is True
    assert discord["setup_instructions"] is not None
    assert "discord-mcp-server" in discord["setup_instructions"]


def test_app_catalog_entries_never_include_a_secret_field():
    resp = client.get("/app-catalog")
    raw = json.dumps(resp.json()).lower()
    for forbidden in ("client_secret", "api_key", "\"secret\"", "token"):
        assert forbidden not in raw, f"catalog response unexpectedly contains {forbidden!r}"


def test_connection_built_from_gmail_catalog_prefill_round_trips_through_post_connections(monkeypatch):
    """Proves the catalog adds zero new execution path: a config built the
    same way the frontend's pre-fill would build it (catalog's non-secret
    fields + an admin-supplied client id/secret) creates a real connection
    through the exact same POST /connections every hand-filled connection
    already uses. Discovery is mocked to succeed unauthenticated -- matching
    this project's own already-documented, live-confirmed finding that
    Google's real Gmail MCP server allows unauthenticated tools/list (only
    tools/call actually enforces OAuth), so requires_oauth is deliberately
    NOT pre-filled into the submitted config (see McpServerConnectionConfig's
    own default) and is not asserted on here either -- that's the server's
    own probe-then-register logic's call to make, not the catalog's."""
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [_FAKE_TOOL])

    entries = client.get("/app-catalog").json()
    gmail = _entry_by_key(entries, "gmail")

    config = {
        "transport": "remote",
        "url": gmail["server_url"],
        "auth_type": gmail["auth_type"],
        "oauth_scope": gmail["default_scope"],
        "credential_type": gmail["credential_type"],
        "oauth_client_id": "fake-client-id.apps.googleusercontent.com",
        "oauth_client_secret": "fake-client-secret",
    }
    resp = client.post("/connections", json={"name": "catalog-gmail", "type": "mcp_server", "config": config})
    assert resp.status_code == 201, resp.text
    assert resp.json()["type"] == "mcp_server"

    stored = get_connection("catalog-gmail", user_id=None)
    assert stored is not None
    assert stored.config["url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert stored.config["credential_type"] == "google_gmail_oauth2"
    assert stored.config["oauth_client_id"] == "fake-client-id.apps.googleusercontent.com"


def test_connection_built_from_context7_catalog_prefill_needs_no_extra_credentials(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [_FAKE_TOOL])

    entries = client.get("/app-catalog").json()
    context7 = _entry_by_key(entries, "context7")

    config = {
        "transport": "remote",
        "url": context7["server_url"],
        "requires_oauth": context7["requires_oauth"],
        "auth_type": context7["auth_type"],
    }
    resp = client.post("/connections", json={"name": "catalog-context7", "type": "mcp_server", "config": config})
    assert resp.status_code == 201, resp.text

    stored = get_connection("catalog-context7", user_id=None)
    assert stored is not None
    assert stored.config["url"] == "https://mcp.context7.com/mcp"


def test_connection_built_from_discord_catalog_prefill_requires_admin_supplied_url(monkeypatch):
    monkeypatch.setattr(generated_nodes_module, "list_tools", lambda config: [_FAKE_TOOL])

    entries = client.get("/app-catalog").json()
    discord = _entry_by_key(entries, "discord")
    assert discord["server_url"] is None  # confirms the pre-fill leaves url for the admin to supply

    config = {
        "transport": "remote",
        "url": "https://my-self-hosted-discord-mcp.example.com/mcp",  # the admin's own deployment
        "auth_type": discord["auth_type"],
        "oauth_scope": discord["default_scope"],
        "credential_type": discord["credential_type"],
        "oauth_client_id": "fake-discord-client-id",
        "oauth_client_secret": "fake-discord-client-secret",
    }
    resp = client.post("/connections", json={"name": "catalog-discord", "type": "mcp_server", "config": config})
    assert resp.status_code == 201, resp.text

    stored = get_connection("catalog-discord", user_id=None)
    assert stored is not None
    assert stored.config["url"] == "https://my-self-hosted-discord-mcp.example.com/mcp"
    assert stored.config["credential_type"] == "discord_webhook_oauth2"

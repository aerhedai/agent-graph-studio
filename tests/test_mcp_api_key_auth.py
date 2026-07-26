"""spec-025: the mcp_server connection type's api_key/bearer auth path --
create (defers node generation, same shape as the OAuth path's own defer)
-> POST /connections/{name}/api-key (no redirect needed, the caller already
has their own key) -> real node types generated for that caller. Mirrors
tests/test_mcp_oauth_connections_api.py's own structure and mocking
precedent (generated_nodes_module.list_tools/call_tool monkeypatched
exactly as every other mcp_server connection test)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.mcp import api_key_storage
from backend.mcp.client import McpToolInfo
from backend.storage import users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

SEND_TOOL = McpToolInfo(
    name="send_message",
    param_names=["text"],
    param_json_types={"text": "string"},
    required_names=frozenset({"text"}),
)


def _token_for(user_id: str, email: str, role: str = "member") -> str:
    users_store.ensure_admin_bootstrapped("bootstrap-admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id=user_id,
        email=email,
        display_name=email,
        role=role,
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    return auth_jwt.issue_token(user.id, user.email, user.role)


def _create_api_key_connection(name: str, token: str, auth_type: str = "api_key") -> dict:
    response = client.post(
        "/connections",
        json={
            "name": name,
            "type": "mcp_server",
            "config": {
                "transport": "remote",
                "url": "https://mock-api-key-mcp.example.com/mcp",
                "auth_type": auth_type,
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_api_key_connection_defers_node_generation():
    token = _token_for("apikey-user-a", "apikey-user-a@example.com")
    with patch.object(generated_nodes_module, "list_tools") as fake_list_tools:
        info = _create_api_key_connection("apikey-conn-a", token)
        fake_list_tools.assert_not_called()
    assert info["auth_type"] == "api_key"
    assert info["api_key_connected"] is False

    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert not any("apikey-conn-a" in t["type"] for t in node_types)


def test_setting_api_key_generates_real_node_types():
    token = _token_for("apikey-user-b", "apikey-user-b@example.com")
    _create_api_key_connection("apikey-conn-b", token)

    with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]) as fake_list_tools:
        response = client.post(
            "/connections/apikey-conn-b/api-key",
            json={"api_key": "sk-real-looking-key"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_connected"] is True

    # The stored key is what actually gets attached as the Bearer header --
    # confirms the discovery call used the real, just-pasted key.
    called_config = fake_list_tools.call_args[0][0]
    assert called_config.headers["Authorization"] == "Bearer sk-real-looking-key"

    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert any("apikey-conn-b" in t["type"] for t in node_types)


def test_api_key_is_stored_per_caller_not_shared():
    admin_token = _token_for("apikey-admin-c", "apikey-admin-c@example.com", role="admin")
    member_token = _token_for("apikey-member-c", "apikey-member-c@example.com")

    create = client.post(
        "/connections",
        json={
            "name": "shared-apikey-conn",
            "type": "mcp_server",
            "config": {"transport": "remote", "url": "https://mock-api-key-mcp.example.com/mcp", "auth_type": "api_key"},
            "scope": "global",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201

    with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]):
        client.post(
            "/connections/shared-apikey-conn/api-key",
            json={"api_key": "admins-own-key"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert api_key_storage.get_api_key("apikey-admin-c", "shared-apikey-conn") == "admins-own-key"
    assert api_key_storage.get_api_key("apikey-member-c", "shared-apikey-conn") is None

    as_member = client.get("/connections", headers={"Authorization": f"Bearer {member_token}"}).json()
    entry = next(c for c in as_member if c["name"] == "shared-apikey-conn")
    assert entry["api_key_connected"] is False


def test_oauth2_connection_rejects_the_api_key_route():
    token = _token_for("apikey-user-d", "apikey-user-d@example.com")
    with patch.object(generated_nodes_module, "list_tools", return_value=[]):
        created = client.post(
            "/connections",
            json={
                "name": "oauth-conn-d",
                "type": "mcp_server",
                "config": {"transport": "stdio", "command": "true"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert created.status_code == 201, created.text
    response = client.post(
        "/connections/oauth-conn-d/api-key",
        json={"api_key": "irrelevant"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_a_second_user_cannot_set_a_key_against_a_private_connection_they_dont_own():
    owner_token = _token_for("apikey-owner-e", "apikey-owner-e@example.com")
    stranger_token = _token_for("apikey-stranger-e", "apikey-stranger-e@example.com")
    _create_api_key_connection("private-apikey-conn-e", owner_token)

    response = client.post(
        "/connections/private-apikey-conn-e/api-key",
        json={"api_key": "should-not-work"},
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert response.status_code == 404
    assert api_key_storage.get_api_key("apikey-stranger-e", "private-apikey-conn-e") is None

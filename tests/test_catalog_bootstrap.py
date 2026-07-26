"""spec-025: the admin-only catalog-bootstrap action -- (re)generates a
global mcp_server connection's real node types using the admin's own
already-connected credential (OAuth token or api_key), so a catalog entry's
nodes exist before any other user has connected. Mirrors
tests/test_admin_connections.py's promote-to-global test structure/mocking
precedent exactly (same MCP_URL/SEND_TOOL shape, same
generated_nodes_module.list_tools monkeypatch)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.mcp import api_key_storage, oauth_flow, oauth_token_storage
from backend.mcp.client import McpToolInfo
from backend.storage import users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

MCP_URL = "https://mock-catalog-mcp.example.com/mcp"
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


def test_non_admin_cannot_bootstrap():
    member_token = _token_for("cat-member-a", "cat-member-a@example.com")
    response = client.post(
        "/connections/nonexistent/catalog-bootstrap", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert response.status_code == 403


def test_bootstrap_requires_admins_own_credential_first():
    admin_token = _token_for("cat-admin-b", "cat-admin-b@example.com", role="admin")
    create = client.post(
        "/connections",
        json={
            "name": "catalog-conn-b",
            "type": "mcp_server",
            "config": {"transport": "remote", "url": MCP_URL, "auth_type": "api_key"},
            "scope": "global",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201, create.text

    response = client.post(
        "/connections/catalog-conn-b/catalog-bootstrap", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 409
    assert "connect your own account" in response.json()["detail"].lower()


def test_bootstrap_generates_global_node_types_before_any_other_user_connects():
    admin_token = _token_for("cat-admin-c", "cat-admin-c@example.com", role="admin")
    admin_id = "cat-admin-c"
    member_token = _token_for("cat-member-c", "cat-member-c@example.com")

    create = client.post(
        "/connections",
        json={
            "name": "catalog-conn-c",
            "type": "mcp_server",
            "config": {"transport": "remote", "url": MCP_URL, "auth_type": "api_key"},
            "scope": "global",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201, create.text

    # The admin connects their own account -- exactly the "admin's own
    # connect is sufficient" resolved answer to SPEC-025's open question.
    api_key_storage.save_api_key(admin_id, "catalog-conn-c", "admins-real-key", "2026-01-01T00:00:00+00:00")

    with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]) as fake_list_tools:
        bootstrapped = client.post(
            "/connections/catalog-conn-c/catalog-bootstrap", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert bootstrapped.status_code == 200, bootstrapped.text
    assert bootstrapped.json()["generated_types"] == ["mcp__catalog-conn-c__send_message"]
    called_config = fake_list_tools.call_args[0][0]
    assert called_config.headers["Authorization"] == "Bearer admins-real-key"

    # A second, non-admin user -- who has never connected anything -- sees
    # the resulting node in their own palette immediately.
    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {member_token}"}).json()
    assert any("catalog-conn-c" in t["type"] for t in node_types)


def test_bootstrap_accepts_admins_own_oauth_token_too():
    admin_token = _token_for("cat-admin-d", "cat-admin-d@example.com", role="admin")
    admin_id = "cat-admin-d"

    with patch.object(
        oauth_flow,
        "discover_oauth_server",
        return_value=oauth_flow.DiscoveredOAuthServer(
            authorization_endpoint="https://mock-catalog-mcp.example.com/authorize",
            token_endpoint="https://mock-catalog-mcp.example.com/token",
            registration_endpoint=None,
        ),
    ):
        create = client.post(
            "/connections",
            json={
                "name": "catalog-conn-d",
                "type": "mcp_server",
                "config": {
                    "transport": "remote",
                    "url": MCP_URL,
                    "oauth_client_id": "preexisting-client",
                    "oauth_client_secret": "preexisting-secret",
                },
                "scope": "global",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert create.status_code == 201, create.text

    oauth_token_storage.save_token(
        admin_id,
        "catalog-conn-d",
        access_token="admins-oauth-token",
        refresh_token=None,
        expires_at="2099-01-01T00:00:00+00:00",
        token_type="Bearer",
        scope=None,
        token_endpoint="https://mock-catalog-mcp.example.com/token",
        client_id="preexisting-client",
        client_secret="preexisting-secret",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]) as fake_list_tools:
        bootstrapped = client.post(
            "/connections/catalog-conn-d/catalog-bootstrap", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert bootstrapped.status_code == 200, bootstrapped.text
    called_config = fake_list_tools.call_args[0][0]
    assert called_config.headers["Authorization"] == "Bearer admins-oauth-token"


def test_bootstrap_404s_for_unknown_connection():
    admin_token = _token_for("cat-admin-e", "cat-admin-e@example.com", role="admin")
    response = client.post(
        "/connections/does-not-exist/catalog-bootstrap", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404

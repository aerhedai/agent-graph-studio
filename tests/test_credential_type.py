"""spec-025: `credential_type` on an mcp_server connection is a named,
reusable auth requirement distinct from the connection instance and its
storage type -- lets a user hold several same-shaped connections (e.g. two
Gmail-authenticated ones) and have a node's picker filter to just those."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.storage import users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _create_connection(payload: dict, token: str):
    # Mirrors tests/test_mcp_server_integrations.py's own precedent:
    # create_connection's immediate node-type generation would otherwise
    # try to spawn a real stdio subprocess for a fake command.
    with patch.object(generated_nodes_module, "list_tools", return_value=[]):
        return client.post("/connections", json=payload, headers={"Authorization": f"Bearer {token}"})


def _token_for(user_id: str, email: str) -> str:
    users_store.ensure_admin_bootstrapped("bootstrap-admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id=user_id,
        email=email,
        display_name=email,
        role="member",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    return auth_jwt.issue_token(user.id, user.email, user.role)


def test_connection_info_reflects_credential_type():
    token = _token_for("cred-user-a", "cred-user-a@example.com")
    response = _create_connection(
        {
            "name": "work-gmail",
            "type": "mcp_server",
            "config": {
                "transport": "stdio",
                "command": "true",
                "credential_type": "google_gmail_oauth2",
            },
        },
        token,
    )
    assert response.status_code == 201
    assert response.json()["credential_type"] == "google_gmail_oauth2"

    listed = client.get("/connections", headers={"Authorization": f"Bearer {token}"}).json()
    entry = next(c for c in listed if c["name"] == "work-gmail")
    assert entry["credential_type"] == "google_gmail_oauth2"


def test_connections_without_credential_type_are_unaffected():
    token = _token_for("cred-user-b", "cred-user-b@example.com")
    response = _create_connection(
        {"name": "no-cred-type", "type": "mcp_server", "config": {"transport": "stdio", "command": "true"}},
        token,
    )
    assert response.status_code == 201
    assert response.json()["credential_type"] is None


def test_two_connections_of_the_same_credential_type_are_both_listed():
    """The actual use case: a user holds two differently-named connections
    tagged with the same credential type, so a node's picker can offer both
    (e.g. "Work Gmail" vs "Personal Gmail")."""
    token = _token_for("cred-user-c", "cred-user-c@example.com")
    for name in ("work-gmail-2", "personal-gmail-2"):
        response = _create_connection(
            {
                "name": name,
                "type": "mcp_server",
                "config": {
                    "transport": "stdio",
                    "command": "true",
                    "credential_type": "google_gmail_oauth2",
                },
            },
            token,
        )
        assert response.status_code == 201

    listed = client.get("/connections", headers={"Authorization": f"Bearer {token}"}).json()
    tagged = [c["name"] for c in listed if c["credential_type"] == "google_gmail_oauth2"]
    assert set(tagged) == {"work-gmail-2", "personal-gmail-2"}


def test_non_mcp_server_connections_have_no_credential_type():
    token = _token_for("cred-user-d", "cred-user-d@example.com")
    response = client.post(
        "/connections",
        json={"name": "plain-anthropic", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["credential_type"] is None

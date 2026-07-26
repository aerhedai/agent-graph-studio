"""spec-025: popup-based OAuth UX -- /connections/oauth/start?popup=true
carries that flag through the whole external round trip via the state token
(the only thing that survives it, same mechanism redirect_to already uses),
so /connections/oauth/callback renders a postMessage-and-close HTML page
instead of a top-level redirect. The existing top-level-redirect flow (no
popup=true) is unchanged -- covered already by
tests/test_mcp_oauth_connections_api.py, not duplicated here. Mirrors that
file's exact fixtures/mocking precedent."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.mcp import oauth_flow
from backend.mcp.client import McpToolInfo
from backend.storage import settings_store, users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

MCP_URL = "https://mock-popup-mcp.example.com/mcp"
SEND_TOOL = McpToolInfo(
    name="send_message",
    param_names=["text"],
    param_json_types={"text": "string"},
    required_names=frozenset({"text"}),
)


def _token_for(user_id: str, email: str) -> str:
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id=user_id,
        email=email,
        display_name=email,
        role="member",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    return auth_jwt.issue_token(user.id, user.email, user.role)


def _discovered():
    return oauth_flow.DiscoveredOAuthServer(
        authorization_endpoint="https://mock-popup-mcp.example.com/authorize",
        token_endpoint="https://mock-popup-mcp.example.com/token",
        registration_endpoint="https://mock-popup-mcp.example.com/register",
    )


def _create_oauth_connection(name: str, token: str) -> None:
    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        with patch.object(oauth_flow, "register_client", return_value=("client-id", "client-secret")):
            created = client.post(
                "/connections",
                json={"name": name, "type": "mcp_server", "config": {"transport": "remote", "url": MCP_URL}},
                headers={"Authorization": f"Bearer {token}"},
            )
    assert created.status_code == 201, created.text


def test_popup_start_carries_the_flag_through_the_state_token():
    token = _token_for("popup-user-a", "popup-a@example.com")
    settings_store.set_public_base_url("https://backend.example.com")
    _create_oauth_connection("popup-conn-a", token)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        start = client.get(
            "/connections/oauth/start",
            params={"name": "popup-conn-a", "redirect_to": "https://app.example.com/canvas", "popup": "true"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    assert start.status_code == 302
    state = start.cookies["mcp_oauth_state"]
    claims = auth_jwt.verify_mcp_oauth_state_token(state)
    assert claims is not None
    assert claims.popup is True


def test_popup_callback_renders_postmessage_page_instead_of_redirecting():
    token = _token_for("popup-user-b", "popup-b@example.com")
    settings_store.set_public_base_url("https://backend.example.com")
    _create_oauth_connection("popup-conn-b", token)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        start = client.get(
            "/connections/oauth/start",
            params={"name": "popup-conn-b", "redirect_to": "https://app.example.com/canvas", "popup": "true"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    state = start.cookies["mcp_oauth_state"]

    token_response = {
        "access_token": "real-popup-token",
        "refresh_token": None,
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        with patch.object(oauth_flow, "exchange_code_for_token", return_value=token_response):
            with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]):
                callback = client.get(
                    "/connections/oauth/callback",
                    params={"code": "real-code", "state": state},
                    cookies={"mcp_oauth_state": state},
                    follow_redirects=False,
                )

    # Not a redirect at all -- a real HTML page, unlike the non-popup flow.
    assert callback.status_code == 200
    assert callback.headers["content-type"].startswith("text/html")
    body = callback.text
    assert "window.opener" in body
    assert "postMessage" in body
    assert "window.close()" in body
    assert "popup-conn-b" in body
    # The target origin is derived from redirect_to, never a wildcard "*" --
    # a real security property, not just a functional one.
    assert "https://app.example.com" in body
    assert '"*"' not in body


def test_popup_callback_error_path_also_renders_postmessage_page():
    token = _token_for("popup-user-c", "popup-c@example.com")
    settings_store.set_public_base_url("https://backend.example.com")
    _create_oauth_connection("popup-conn-c", token)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        start = client.get(
            "/connections/oauth/start",
            params={"name": "popup-conn-c", "redirect_to": "https://app.example.com/canvas", "popup": "true"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    state = start.cookies["mcp_oauth_state"]

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        with patch.object(oauth_flow, "exchange_code_for_token", side_effect=oauth_flow.McpOAuthError("token exchange failed")):
            callback = client.get(
                "/connections/oauth/callback",
                params={"code": "real-code", "state": state},
                cookies={"mcp_oauth_state": state},
                follow_redirects=False,
            )

    assert callback.status_code == 200
    assert callback.headers["content-type"].startswith("text/html")
    assert "token exchange failed" in callback.text
    assert "postMessage" in callback.text

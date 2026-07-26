"""spec-021: the mcp_server connection type's OAuth-aware flow through the
real HTTP surface -- create (detects OAuth requirement, registers a client,
defers node generation) -> /connections/oauth/start (real browser redirect)
-> /connections/oauth/callback (completes the exchange, generates real node
types). backend/mcp/oauth_flow.py's own discovery/exchange logic is already
deeply tested for real against a mock OAuth+MCP server in
tests/test_mcp_oauth_flow.py -- here it's mocked at the API-route boundary
(the same precedent tests/test_platform_auth.py already set for Google's
own exchange_code_for_userinfo), and backend/mcp/generated_nodes.py's own
`list_tools`/`call_tool` are monkeypatched exactly as
tests/test_mcp_server_integrations.py already does for every other
mcp_server connection test.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.mcp import oauth_flow, oauth_token_storage
from backend.mcp.client import McpConnectionError, McpToolInfo
from backend.storage import settings_store, users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

MCP_URL = "https://mock-mcp.example.com/mcp"
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


def _set_public_base_url() -> None:
    settings_store.set_public_base_url("https://backend.example.com")


def _discovered(
    registration_endpoint: str | None = "https://mock-mcp.example.com/register",
    scopes_supported: list[str] | None = None,
):
    return oauth_flow.DiscoveredOAuthServer(
        authorization_endpoint="https://mock-mcp.example.com/authorize",
        token_endpoint="https://mock-mcp.example.com/token",
        registration_endpoint=registration_endpoint,
        scopes_supported=scopes_supported,
    )


def _create_oauth_connection(name: str, token: str) -> dict:
    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        with patch.object(oauth_flow, "register_client", return_value=("dyn-client-id", "dyn-client-secret")):
            response = client.post(
                "/connections",
                json={"name": name, "type": "mcp_server", "config": {"transport": "remote", "url": MCP_URL}},
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 201, response.text
    return response.json()


# --- create_connection: OAuth detection + deferred node generation --------


def test_create_oauth_requiring_connection_registers_client_and_defers_node_generation():
    """spec-025: create_connection now tries a plain, unauthenticated
    tools/list FIRST (see its own docstring -- some real servers advertise
    OAuth metadata that doesn't actually gate anything, confirmed live
    against Context7 and kpidepot.com), so list_tools *is* called once here
    -- but a genuinely OAuth-requiring server rejects that unauthenticated
    call, which is exactly what real McpConnectionError represents. Node
    generation is still correctly deferred either way."""
    token = _token_for("user-a", "a@example.com")
    _set_public_base_url()
    with patch.object(
        generated_nodes_module, "list_tools", side_effect=McpConnectionError("401 Unauthorized")
    ) as fake_list_tools:
        _create_oauth_connection("my-oauth-mcp", token)
        assert fake_list_tools.call_count == 1

    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert not any("my-oauth-mcp" in t["type"] for t in node_types)


def test_create_oauth_requiring_connection_without_dynamic_registration_requires_preconfigured_client():
    token = _token_for("user-a", "a@example.com")
    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered(registration_endpoint=None)):
        response = client.post(
            "/connections",
            json={
                "name": "no-dynreg-mcp",
                "type": "mcp_server",
                "config": {"transport": "remote", "url": MCP_URL},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    assert "pre-registered" in response.json()["detail"]
    # Rolled back -- not left behind half-configured.
    listed = client.get("/connections", headers={"Authorization": f"Bearer {token}"}).json()
    assert "no-dynreg-mcp" not in {c["name"] for c in listed}


def test_create_non_oauth_remote_connection_is_completely_unaffected():
    """Regression: a remote mcp_server connection whose discovery probe
    genuinely fails (not an OAuth server at all) falls through to the
    existing, unchanged list_tools-based flow."""
    token = _token_for("user-a", "a@example.com")
    with patch.object(oauth_flow, "discover_oauth_server", side_effect=oauth_flow.McpOAuthError("not oauth")):
        with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]):
            response = client.post(
                "/connections",
                json={
                    "name": "plain-remote-mcp",
                    "type": "mcp_server",
                    "config": {"transport": "remote", "url": MCP_URL},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 201
    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert any("plain-remote-mcp" in t["type"] for t in node_types)


def test_server_advertising_real_oauth_metadata_but_not_actually_gating_tools_connects_immediately():
    """spec-025: the exact real bug this phase fixed -- confirmed live
    against Context7 and kpidepot.com, both of which advertise a real
    authorization_endpoint/token_endpoint/registration_endpoint (unlike
    test_create_non_oauth_remote_connection_is_completely_unaffected's
    "not an OAuth server at all" case) but don't actually require a
    credential to call their tools. discover_oauth_server here returns
    valid-looking metadata rather than raising -- if create_connection
    trusted that over the plain tools/list succeeding, this would
    incorrectly kick off a broken OAuth dance instead of connecting
    immediately. discover_oauth_server must never even be called once the
    unauthenticated probe already succeeded."""
    token = _token_for("user-a", "a@example.com")
    with patch.object(oauth_flow, "discover_oauth_server") as fake_discover:
        with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]):
            response = client.post(
                "/connections",
                json={
                    "name": "looks-like-oauth-but-isnt",
                    "type": "mcp_server",
                    "config": {"transport": "remote", "url": MCP_URL},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 201, response.text
    fake_discover.assert_not_called()
    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert any("looks-like-oauth-but-isnt" in t["type"] for t in node_types)


# --- /connections/oauth/start ----------------------------------------------


def test_oauth_start_requires_authentication():
    response = client.get(
        "/connections/oauth/start", params={"name": "whatever", "redirect_to": "https://app.example.com"}
    )
    assert response.status_code == 401


def test_oauth_start_redirects_to_real_authorization_endpoint_with_state_cookie():
    token = _token_for("user-a", "a@example.com")
    _set_public_base_url()
    _create_oauth_connection("start-test-mcp", token)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        response = client.get(
            "/connections/oauth/start",
            params={"name": "start-test-mcp", "redirect_to": "https://app.example.com"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://mock-mcp.example.com/authorize")
    assert "code_challenge=" in response.headers["location"]
    assert "resource=" in response.headers["location"]
    assert "mcp_oauth_state" in response.cookies


def test_oauth_start_includes_scope_from_discovered_metadata_when_none_configured():
    """Real regression, found live against Google's actual Gmail MCP
    server: the authorization URL was built with no `scope` param at all
    (build_authorization_url's own `scope` argument was never actually
    passed from this route), which Google's real consent screen rejects
    outright with "Missing required parameter: scope". A connection with
    no explicit oauth_scope set falls back to whatever the server's own
    discovery advertised as scopes_supported."""
    token = _token_for("user-a", "a@example.com")
    _set_public_base_url()
    _create_oauth_connection("scope-fallback-mcp", token)

    with patch.object(
        oauth_flow,
        "discover_oauth_server",
        return_value=_discovered(scopes_supported=["scope.one", "scope.two"]),
    ):
        response = client.get(
            "/connections/oauth/start",
            params={"name": "scope-fallback-mcp", "redirect_to": "https://app.example.com"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "scope=scope.one+scope.two" in response.headers["location"]


def test_oauth_start_prefers_explicit_oauth_scope_over_discovered_default():
    token = _token_for("user-a", "a@example.com")
    _set_public_base_url()
    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        with patch.object(oauth_flow, "register_client", return_value=("dyn-id", "dyn-secret")):
            client.post(
                "/connections",
                json={
                    "name": "explicit-scope-mcp",
                    "type": "mcp_server",
                    "config": {"transport": "remote", "url": MCP_URL, "oauth_scope": "gmail.readonly gmail.compose"},
                },
                headers={"Authorization": f"Bearer {token}"},
            )

    with patch.object(
        oauth_flow, "discover_oauth_server", return_value=_discovered(scopes_supported=["mail.google.com"])
    ):
        response = client.get(
            "/connections/oauth/start",
            params={"name": "explicit-scope-mcp", "redirect_to": "https://app.example.com"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "scope=gmail.readonly+gmail.compose" in response.headers["location"]
    assert "mail.google.com" not in response.headers["location"]


def test_second_user_cannot_start_oauth_for_first_users_connection():
    token_a = _token_for("user-a", "a@example.com")
    token_b = _token_for("user-b", "b@example.com")
    _set_public_base_url()
    _create_oauth_connection("a-only-mcp", token_a)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        response = client.get(
            "/connections/oauth/start",
            params={"name": "a-only-mcp", "redirect_to": "https://app.example.com"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert response.status_code == 404


# --- /connections/oauth/callback -------------------------------------------


def test_oauth_callback_completes_flow_stores_token_and_generates_node_types():
    token = _token_for("user-a", "a@example.com")
    _set_public_base_url()
    _create_oauth_connection("callback-test-mcp", token)

    with patch.object(oauth_flow, "discover_oauth_server", return_value=_discovered()):
        start = client.get(
            "/connections/oauth/start",
            params={"name": "callback-test-mcp", "redirect_to": "https://app.example.com"},
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    state = start.cookies["mcp_oauth_state"]

    token_response = {
        "access_token": "real-access-token",
        "refresh_token": "real-refresh-token",
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

    assert callback.status_code == 302
    assert "mcp_oauth_connected=callback-test-mcp" in callback.headers["location"]

    stored = oauth_token_storage.get_token("user-a", "callback-test-mcp")
    assert stored is not None
    assert stored.access_token == "real-access-token"

    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    assert any("callback-test-mcp" in t["type"] for t in node_types)


def test_oauth_callback_rejects_state_cookie_mismatch():
    response = client.get(
        "/connections/oauth/callback",
        params={"code": "real-code", "state": "some-state"},
        cookies={"mcp_oauth_state": "a-different-state"},
    )
    assert response.status_code == 400


def test_oauth_callback_does_not_require_authentication():
    """The callback route itself is unauthenticated (see _AUTH_EXEMPT_PATHS)
    -- a plain browser navigation from the OAuth provider carries no JWT.
    An invalid/expired state is still cleanly rejected, just not via 401."""
    unauth = TestClient(app)
    response = unauth.get(
        "/connections/oauth/callback",
        params={"code": "real-code", "state": "garbage"},
        cookies={"mcp_oauth_state": "garbage"},
    )
    assert response.status_code == 400
    assert response.status_code != 401

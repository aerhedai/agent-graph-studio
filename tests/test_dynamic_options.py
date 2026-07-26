"""spec-025 Phase 5: dynamic option loading -- generate_node_types_for_
connection wires up a live-values binding for a recognized server shape
(this phase's live-verified example: Context7's resolve-library-id feeding
query-docs's libraryId), and POST /node-types/{type}/options/{field} serves
those values for real, mirroring resolve-slots' own mocking precedent
(generated_nodes_module.list_tools/call_tool monkeypatched exactly as every
other mcp_server connection test in this project)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.mcp import oauth_flow, option_bindings
from backend.mcp.client import McpToolInfo
from backend.storage import users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})

MCP_URL = "https://mock-context7-shaped-mcp.example.com/mcp"
RESOLVE_TOOL = McpToolInfo(
    name="resolve-library-id",
    param_names=["query", "libraryName"],
    param_json_types={"query": "string", "libraryName": "string"},
    required_names=frozenset({"query", "libraryName"}),
)
QUERY_DOCS_TOOL = McpToolInfo(
    name="query-docs",
    param_names=["libraryId", "query"],
    param_json_types={"libraryId": "string", "query": "string"},
    required_names=frozenset({"libraryId", "query"}),
)

RAW_LIBRARY_LIST = """Available Libraries:

- Title: React
- Context7-compatible library ID: /reactjs/react.dev
- Description: The official React docs.
----------
- Title: React Native
- Context7-compatible library ID: /facebook/react-native
- Description: React Native docs.
"""


def _token_for(user_id: str, email: str, role: str = "admin") -> str:
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id=user_id,
        email=email,
        display_name=email,
        role=role,
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    return auth_jwt.issue_token(user.id, user.email, user.role)


def _create_connection(name: str, token: str) -> None:
    # scope="global" keeps generated type names in the plain
    # "mcp__<name>__<tool>" form -- a private connection's own generated
    # types are namespaced "mcp__u_<owner>__<name>__<tool>" instead
    # (SPEC-023), irrelevant to what this test is actually exercising
    # (the binding registration/lookup itself, not connection scoping).
    with patch.object(oauth_flow, "discover_oauth_server", side_effect=oauth_flow.McpOAuthError("not an oauth server")):
        with patch.object(generated_nodes_module, "list_tools", return_value=[RESOLVE_TOOL, QUERY_DOCS_TOOL]):
            created = client.post(
                "/connections",
                json={
                    "name": name,
                    "type": "mcp_server",
                    "config": {"transport": "remote", "url": MCP_URL},
                    "scope": "global",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert created.status_code == 201, created.text


def test_context7_shaped_connection_gets_a_dynamic_option_binding_registered():
    token = _token_for("dynopt-user-a", "dynopt-a@example.com")
    _create_connection("dynopt-conn-a", token)

    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {token}"}).json()
    query_docs_type = next(t for t in node_types if t["type"] == "mcp__dynopt-conn-a__query-docs")
    assert query_docs_type["dynamic_option_slots"] == ["libraryId"]
    resolve_type = next(t for t in node_types if t["type"] == "mcp__dynopt-conn-a__resolve-library-id")
    assert resolve_type["dynamic_option_slots"] == []


def test_options_endpoint_returns_real_parsed_choices():
    token = _token_for("dynopt-user-b", "dynopt-b@example.com")
    _create_connection("dynopt-conn-b", token)

    with patch.object(generated_nodes_module, "call_tool", return_value=RAW_LIBRARY_LIST) as fake_call_tool:
        response = client.post(
            "/node-types/mcp__dynopt-conn-b__query-docs/options/libraryId",
            json={"connection_name": "dynopt-conn-b", "current_config": {"query": "react"}},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    options = response.json()
    assert options == [
        {"label": "React (/reactjs/react.dev)", "value": "/reactjs/react.dev"},
        {"label": "React Native (/facebook/react-native)", "value": "/facebook/react-native"},
    ]
    # The binding really did call resolve-library-id, forwarding the
    # caller's in-progress "query" value, not some hardcoded arg.
    called_args, called_kwargs = fake_call_tool.call_args[0], fake_call_tool.call_args[1]
    assert called_args[1] == "resolve-library-id"
    assert called_args[2] == {"query": "react", "libraryName": "react"}


def test_options_endpoint_404s_for_a_field_with_no_binding():
    token = _token_for("dynopt-user-c", "dynopt-c@example.com")
    _create_connection("dynopt-conn-c", token)

    response = client.post(
        "/node-types/mcp__dynopt-conn-c__resolve-library-id/options/query",
        json={"connection_name": "dynopt-conn-c", "current_config": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_stale_bindings_are_cleared_when_a_connection_no_longer_matches_the_shape():
    """Regression coverage for unregister_for_node_type -- a refreshed
    connection whose server no longer exposes resolve-library-id (or is a
    completely different tool set now) must not keep serving a stale
    binding for a type name that could be reused."""
    token = _token_for("dynopt-user-d", "dynopt-d@example.com")
    _create_connection("dynopt-conn-d", token)
    assert option_bindings.get_option_binding("mcp__dynopt-conn-d__query-docs", "libraryId") is not None

    with patch.object(generated_nodes_module, "list_tools", return_value=[QUERY_DOCS_TOOL]):
        refreshed = client.post(
            "/connections/dynopt-conn-d/refresh-capabilities", headers={"Authorization": f"Bearer {token}"}
        )
    assert refreshed.status_code == 200, refreshed.text
    assert option_bindings.get_option_binding("mcp__dynopt-conn-d__query-docs", "libraryId") is None

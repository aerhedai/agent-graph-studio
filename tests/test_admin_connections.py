"""spec-023: admin-only global-connection management -- creating a
connection with scope="global", mutating/deleting an existing global
connection, promoting an admin's own private connection to global, and the
admin-only "private connection names" support view. The shared-API-key
caller (no signed-in user at all) is deliberately unrestricted throughout,
matching its long-standing pre-spec-020 access -- see _require_admin's own
docstring in backend/api/app.py.

Regression coverage for what must NOT change: GET /connections/oauth/start
and POST /connections/{name}/refresh-capabilities stay open to any
authenticated user regardless of who owns the connection profile (SPEC-021's
whole point), and a user's own private connections are completely
unaffected by any of this.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.mcp.generated_nodes as generated_nodes_module
from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.connections.store import get_connection
from backend.execution.types import ExecutionContext
from backend.mcp import oauth_flow, oauth_token_storage
from backend.mcp.client import McpToolInfo
from backend.schema.models import NodeSpec
from backend.storage import users_store

client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


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


# --- POST /connections with scope="global" ---------------------------------


def test_non_admin_creating_global_connection_is_rejected():
    member_token = _token_for("member-a", "member-a@example.com")
    response = client.post(
        "/connections",
        json={"name": "shared-ollama", "type": "ollama", "config": {"base_url": "http://x:11434"}, "scope": "global"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    assert get_connection("shared-ollama", user_id=None) is None


def test_admin_creating_global_connection_succeeds_and_is_visible_to_others():
    admin_token = _token_for("admin-a", "admin-a@example.com", role="admin")
    member_token = _token_for("member-b", "member-b@example.com")

    response = client.post(
        "/connections",
        json={"name": "shared-anthropic", "type": "anthropic", "config": {"api_key": "sk-shared"}, "scope": "global"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["is_global"] is True
    assert body["can_manage"] is True

    as_member = client.get("/connections", headers={"Authorization": f"Bearer {member_token}"})
    listed = {c["name"]: c for c in as_member.json()}
    assert "shared-anthropic" in listed
    assert listed["shared-anthropic"]["is_global"] is True
    assert listed["shared-anthropic"]["can_manage"] is False


def test_default_scope_is_still_private_for_a_signed_in_user():
    member_token = _token_for("member-c", "member-c@example.com")
    response = client.post(
        "/connections",
        json={"name": "my-own-anthropic", "type": "anthropic", "config": {"api_key": "sk-mine"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 201
    assert response.json()["is_global"] is False
    assert get_connection("my-own-anthropic", user_id="member-c") is not None
    assert get_connection("my-own-anthropic", user_id=None) is None


def test_shared_api_key_caller_can_still_create_a_global_connection_unrestricted():
    """The shared key predates the admin/member role system entirely --
    unaffected by this spec, exactly as its own docstring in app.py says."""
    response = client.post(
        "/connections", json={"name": "cli-created", "type": "ollama", "config": {"base_url": "http://x:11434"}}
    )
    assert response.status_code == 201
    assert get_connection("cli-created", user_id=None) is not None


# --- PUT / DELETE on an existing global connection --------------------------


def test_non_admin_cannot_delete_or_edit_a_global_connection():
    admin_token = _token_for("admin-b", "admin-b@example.com", role="admin")
    member_token = _token_for("member-d", "member-d@example.com")
    client.post(
        "/connections",
        json={"name": "global-target", "type": "anthropic", "config": {"api_key": "sk-1"}, "scope": "global"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    edit = client.put(
        "/connections/global-target",
        json={"config": {"api_key": "sk-2"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert edit.status_code == 403

    delete = client.delete("/connections/global-target", headers={"Authorization": f"Bearer {member_token}"})
    assert delete.status_code == 403
    assert get_connection("global-target", user_id=None) is not None


def test_admin_can_delete_and_edit_a_global_connection():
    admin_token = _token_for("admin-c", "admin-c@example.com", role="admin")
    client.post(
        "/connections",
        json={"name": "global-target-2", "type": "anthropic", "config": {"api_key": "sk-1"}, "scope": "global"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    edit = client.put(
        "/connections/global-target-2",
        json={"config": {"api_key": "sk-2"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert edit.status_code == 200

    delete = client.delete("/connections/global-target-2", headers={"Authorization": f"Bearer {admin_token}"})
    assert delete.status_code == 204
    assert get_connection("global-target-2", user_id=None) is None


def test_a_users_own_private_connection_is_completely_unaffected():
    member_token = _token_for("member-e", "member-e@example.com")
    client.post(
        "/connections",
        json={"name": "my-private", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    edit = client.put(
        "/connections/my-private",
        json={"config": {"api_key": "sk-2"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert edit.status_code == 200

    delete = client.delete("/connections/my-private", headers={"Authorization": f"Bearer {member_token}"})
    assert delete.status_code == 204


def test_editing_or_deleting_someone_elses_private_connection_is_404_not_403():
    """Matches this file's own "don't reveal existence" convention --
    a stranger's private connection isn't distinguishable from a name that
    doesn't exist at all, not surfaced as a permission error."""
    owner_token = _token_for("owner-a", "owner-a@example.com")
    stranger_token = _token_for("stranger-a", "stranger-a@example.com")
    client.post(
        "/connections",
        json={"name": "owner-only", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    edit = client.put(
        "/connections/owner-only",
        json={"config": {"api_key": "sk-2"}},
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert edit.status_code == 404

    delete = client.delete("/connections/owner-only", headers={"Authorization": f"Bearer {stranger_token}"})
    assert delete.status_code == 404


# --- POST /connections/{name}/promote-to-global -----------------------------


def test_admin_can_promote_their_own_private_connection_to_global():
    admin_token = _token_for("admin-d", "admin-d@example.com", role="admin")
    client.post(
        "/connections",
        json={"name": "my-gmail-like", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_connection("my-gmail-like", user_id="admin-d") is not None

    promoted = client.post(
        "/connections/my-gmail-like/promote-to-global", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert promoted.status_code == 200
    body = promoted.json()
    assert body["is_global"] is True
    assert body["can_manage"] is True

    assert get_connection("my-gmail-like", user_id="admin-d") is None
    global_profile = get_connection("my-gmail-like", user_id=None)
    assert global_profile is not None
    assert global_profile.config == {"api_key": "sk-1"}  # carried over as-is, per the resolved open question


def test_non_admin_cannot_promote_their_own_connection_to_global():
    member_token = _token_for("member-f", "member-f@example.com")
    client.post(
        "/connections",
        json={"name": "member-owned", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    response = client.post(
        "/connections/member-owned/promote-to-global", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert response.status_code == 403
    assert get_connection("member-owned", user_id="member-f") is not None


def test_admin_cannot_promote_another_users_private_connection():
    admin_token = _token_for("admin-e", "admin-e@example.com", role="admin")
    other_token = _token_for("member-g", "member-g@example.com")
    client.post(
        "/connections",
        json={"name": "not-yours", "type": "anthropic", "config": {"api_key": "sk-1"}},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    response = client.post(
        "/connections/not-yours/promote-to-global", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert get_connection("not-yours", user_id="member-g") is not None


# --- GET /connections/private-summary ---------------------------------------


def test_private_summary_is_admin_only_and_names_only():
    admin_token = _token_for("admin-f", "admin-f@example.com", role="admin")
    member_token = _token_for("member-h", "member-h@example.com")
    client.post(
        "/connections",
        json={"name": "secret-conn", "type": "anthropic", "config": {"api_key": "sk-super-secret"}},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    denied = client.get("/connections/private-summary", headers={"Authorization": f"Bearer {member_token}"})
    assert denied.status_code == 403

    allowed = client.get("/connections/private-summary", headers={"Authorization": f"Bearer {admin_token}"})
    assert allowed.status_code == 200
    entries = allowed.json()
    matching = [e for e in entries if e["name"] == "secret-conn"]
    assert len(matching) == 1
    assert matching[0]["user_id"] == "member-h"
    assert matching[0]["type"] == "anthropic"
    assert set(matching[0].keys()) == {"user_id", "name", "type"}  # never config/secrets


# --- Regression: OAuth connect/refresh must stay open to any user ----------


def test_oauth_start_and_refresh_capabilities_remain_unaffected_by_admin_gating():
    """These two routes were never gated by this spec and must not become
    so -- SPEC-021's entire point is that any authenticated user can
    connect their own account to a global mcp_server connection."""
    admin_token = _token_for("admin-g", "admin-g@example.com", role="admin")
    member_token = _token_for("member-i", "member-i@example.com")
    client.post(
        "/connections",
        json={
            "name": "global-mcp-plain",
            "type": "mcp_server",
            "config": {"transport": "stdio", "command": "true"},
            "scope": "global",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Not OAuth-requiring, so /oauth/start correctly 422s rather than 403 --
    # the point here is proving it's not blocked by role at all.
    start = client.get(
        "/connections/oauth/start",
        params={"name": "global-mcp-plain", "redirect_to": "https://app.example.com"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert start.status_code != 403

    refresh = client.post(
        "/connections/global-mcp-plain/refresh-capabilities", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert refresh.status_code != 403


# --- Real bug found live: discovery/execution for a *global* OAuth-requiring
# mcp_server connection (only reachable at all once this spec made global
# OAuth connections creatable in the first place -- generate_node_types_for_
# connection/_make_execute previously conflated "who owns/names this
# connection" with "whose token to use", which was never exercised before). --

MCP_URL = "https://mock-global-mcp.example.com/mcp"
SEND_TOOL = McpToolInfo(
    name="send_message",
    param_names=["text"],
    param_json_types={"text": "string"},
    required_names=frozenset({"text"}),
)


def test_promote_to_global_discovers_using_promoting_admins_token_and_names_types_globally():
    admin_token = _token_for("admin-h", "admin-h@example.com", role="admin")
    admin_id = "admin-h"

    with patch.object(
        oauth_flow,
        "discover_oauth_server",
        return_value=oauth_flow.DiscoveredOAuthServer(
            authorization_endpoint="https://mock-global-mcp.example.com/authorize",
            token_endpoint="https://mock-global-mcp.example.com/token",
            registration_endpoint=None,
        ),
    ):
        # Not requires_oauth=True directly -- that's only ever set by
        # create_connection's own discovery branch (below), which is what
        # actually defers generation. Setting it explicitly up front skips
        # that branch entirely and hits the unconditional immediate-
        # generation path instead, which has no token yet -- a 502, not the
        # real deferred-generation flow this test means to exercise.
        create = client.post(
            "/connections",
            json={
                "name": "promote-target",
                "type": "mcp_server",
                "config": {
                    "transport": "remote",
                    "url": MCP_URL,
                    "oauth_client_id": "preexisting-client",
                    "oauth_client_secret": "preexisting-secret",
                },
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert create.status_code == 201

    # The admin already has their own real token for this connection, from
    # back when it was private to them (exactly my-gmail's real situation).
    oauth_token_storage.save_token(
        admin_id,
        "promote-target",
        access_token="admins-real-token",
        refresh_token=None,
        expires_at="2099-01-01T00:00:00+00:00",
        token_type="Bearer",
        scope=None,
        token_endpoint="https://mock-global-mcp.example.com/token",
        client_id="preexisting-client",
        client_secret="preexisting-secret",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    with patch.object(generated_nodes_module, "list_tools", return_value=[SEND_TOOL]) as fake_list_tools:
        promoted = client.post(
            "/connections/promote-target/promote-to-global", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert promoted.status_code == 200, promoted.text

    # Discovery used the admin's own token -- the real fix.
    called_config = fake_list_tools.call_args[0][0]
    assert called_config.headers["Authorization"] == "Bearer admins-real-token"

    # And the resulting node type is named as *global*, not user-namespaced,
    # even though discovery happened via a specific user's token.
    node_types = client.get("/node-types", headers={"Authorization": f"Bearer {admin_token}"}).json()
    type_names = {t["type"] for t in node_types}
    assert "mcp__promote-target__send_message" in type_names
    assert not any("admin-h" in t for t in type_names)


def test_global_connection_node_execution_uses_the_running_users_own_token():
    """The actual functional requirement this whole spec exists for: a
    second user, independently connected to the same global connection,
    can use its generated node with *their own* token -- not the promoting
    admin's, and not a "no owning user" error."""
    admin_token = _token_for("admin-i", "admin-i@example.com", role="admin")
    with patch.object(
        oauth_flow,
        "discover_oauth_server",
        return_value=oauth_flow.DiscoveredOAuthServer(
            authorization_endpoint="https://mock-global-mcp.example.com/authorize",
            token_endpoint="https://mock-global-mcp.example.com/token",
            registration_endpoint=None,
        ),
    ):
        created = client.post(
            "/connections",
            json={
                "name": "shared-exec-target",
                "type": "mcp_server",
                "config": {"transport": "remote", "url": MCP_URL, "oauth_client_id": "c", "oauth_client_secret": "s"},
                "scope": "global",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert created.status_code == 201, created.text

    # A completely different user, who has never touched this connection's
    # config, connects their own account.
    oauth_token_storage.save_token(
        "second-user-executor",
        "shared-exec-target",
        access_token="second-users-own-token",
        refresh_token=None,
        expires_at="2099-01-01T00:00:00+00:00",
        token_type="Bearer",
        scope=None,
        token_endpoint="https://mock-global-mcp.example.com/token",
        client_id="c",
        client_secret="s",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    execute = generated_nodes_module._make_execute("shared-exec-target", SEND_TOOL, None, None)
    ctx = ExecutionContext(
        node=NodeSpec(id="n1", type="mcp__shared-exec-target__send_message"),
        inputs={"text": "hello"},
        resources={
            "running_user_id": "second-user-executor",
            "approval_prompt": lambda *_args, **_kwargs: True,  # skip the interactive gate for this test
        },
    )
    with patch.object(generated_nodes_module, "call_tool", return_value="ok") as fake_call_tool:
        result = execute(ctx)
    assert result.outputs == {"result": "ok"}
    called_config = fake_call_tool.call_args[0][0]
    assert called_config.headers["Authorization"] == "Bearer second-users-own-token"

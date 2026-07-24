"""spec-021: connections become user-scoped -- `(user_id, name) -> config`
instead of `name -> config`, `user_id=None` meaning global/shared (every
pre-spec-021 connection, unchanged). Mirrors tests/test_platform_auth.py's
pattern for issuing real JWTs for two distinct test users, and
tests/test_connections_api.py's TestClient conventions.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.connections.errors import DuplicateConnectionError
from backend.connections.resolver import resolve_connections
from backend.connections.store import (
    add_connection,
    delete_connection,
    get_connection,
    list_connections,
    list_connections_unscoped,
    resolve_connection_for_user,
)
from backend.schema.models import GraphSpec, NodeSpec
from backend.storage import users_store

# spec-017/020: must match tests/conftest.py's TEST_API_KEY.
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


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


# --- store-level: exact scope, resolution policy, uniqueness -------------


def test_two_users_can_each_have_a_connection_with_the_same_name():
    add_connection("shared-name", "anthropic", {"api_key": "a-key"}, user_id="user-a")
    add_connection("shared-name", "anthropic", {"api_key": "b-key"}, user_id="user-b")

    a = get_connection("shared-name", user_id="user-a")
    b = get_connection("shared-name", user_id="user-b")
    assert a is not None and a.config == {"api_key": "a-key"}
    assert b is not None and b.config == {"api_key": "b-key"}


def test_duplicate_check_is_scoped_per_user():
    add_connection("my-conn", "anthropic", {"api_key": "x"}, user_id="user-a")
    # Same name, different user -- not a duplicate.
    add_connection("my-conn", "anthropic", {"api_key": "y"}, user_id="user-b")
    # Same name, same user -- a real duplicate.
    try:
        add_connection("my-conn", "anthropic", {"api_key": "z"}, user_id="user-a")
        assert False, "expected DuplicateConnectionError"
    except DuplicateConnectionError:
        pass


def test_get_connection_is_exact_scope_not_fallback():
    add_connection("global-only", "anthropic", {"api_key": "g"}, user_id=None)
    assert get_connection("global-only", user_id="user-a") is None
    assert get_connection("global-only", user_id=None) is not None


def test_resolve_connection_for_user_prefers_own_then_falls_back_to_global():
    add_connection("both", "anthropic", {"api_key": "global-key"}, user_id=None)
    add_connection("both", "anthropic", {"api_key": "mine-key"}, user_id="user-a")

    mine = resolve_connection_for_user("both", "user-a")
    assert mine is not None and mine.config == {"api_key": "mine-key"}

    other_user = resolve_connection_for_user("both", "user-b")
    assert other_user is not None and other_user.config == {"api_key": "global-key"}

    unauthenticated = resolve_connection_for_user("both", None)
    assert unauthenticated is not None and unauthenticated.config == {"api_key": "global-key"}


def test_resolve_connection_for_user_returns_none_when_neither_exists():
    assert resolve_connection_for_user("nope", "user-a") is None


def test_list_connections_is_mine_plus_global_not_other_users():
    add_connection("global-conn", "anthropic", {"api_key": "g"}, user_id=None)
    add_connection("a-private", "anthropic", {"api_key": "a"}, user_id="user-a")
    add_connection("b-private", "anthropic", {"api_key": "b"}, user_id="user-b")

    as_a = {c.name for c in list_connections(user_id="user-a")}
    assert as_a == {"global-conn", "a-private"}

    unauthenticated = {c.name for c in list_connections(user_id=None)}
    assert unauthenticated == {"global-conn"}


def test_list_connections_unscoped_sees_every_users_connections():
    add_connection("global-conn", "anthropic", {"api_key": "g"}, user_id=None)
    add_connection("a-private", "anthropic", {"api_key": "a"}, user_id="user-a")
    add_connection("b-private", "anthropic", {"api_key": "b"}, user_id="user-b")

    names = {c.name for c in list_connections_unscoped()}
    assert names == {"global-conn", "a-private", "b-private"}


def test_delete_connection_is_scoped_and_does_not_touch_another_users_same_name():
    add_connection("dup-name", "anthropic", {"api_key": "a"}, user_id="user-a")
    add_connection("dup-name", "anthropic", {"api_key": "b"}, user_id="user-b")

    assert delete_connection("dup-name", user_id="user-a") is True
    assert get_connection("dup-name", user_id="user-a") is None
    assert get_connection("dup-name", user_id="user-b") is not None


# --- resolve_connections: llm_call/agent-style config-referenced conns ---


def test_resolve_connections_uses_the_running_users_own_private_connection():
    add_connection("my-anthropic", "anthropic", {"api_key": "user-a-secret"}, user_id="user-a")
    graph = GraphSpec(
        version="0.1",
        nodes=[
            NodeSpec(
                id="n1",
                type="llm_call",
                config={"connection": "my-anthropic", "model": "x", "system_prompt": "", "max_tokens": 10},
            )
        ],
        edges=[],
    )
    resolved = resolve_connections(graph, user_id="user-a")
    assert "my-anthropic" in resolved


def test_resolve_connections_does_not_leak_another_users_private_connection():
    from backend.connections.errors import ConnectionNotFoundError

    add_connection("owner-only", "anthropic", {"api_key": "secret"}, user_id="user-a")
    graph = GraphSpec(
        version="0.1",
        nodes=[
            NodeSpec(
                id="n1",
                type="llm_call",
                config={"connection": "owner-only", "model": "x", "system_prompt": "", "max_tokens": 10},
            )
        ],
        edges=[],
    )
    try:
        resolve_connections(graph, user_id="user-b")
        assert False, "expected ConnectionNotFoundError"
    except ConnectionNotFoundError:
        pass
    # And a shared-key/trigger-fired caller (user_id=None) can't see it either.
    try:
        resolve_connections(graph, user_id=None)
        assert False, "expected ConnectionNotFoundError"
    except ConnectionNotFoundError:
        pass


# --- API-level: two real users, via the real HTTP surface ----------------


def test_connections_list_is_isolated_between_two_real_users():
    token_a = _token_for("user-a", "a@example.com")
    token_b = _token_for("user-b", "b@example.com")

    create = client.post(
        "/connections",
        json={"name": "a-only", "type": "anthropic", "config": {"api_key": "sk-a"}},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert create.status_code == 201

    as_a = client.get("/connections", headers={"Authorization": f"Bearer {token_a}"})
    assert "a-only" in {c["name"] for c in as_a.json()}

    as_b = client.get("/connections", headers={"Authorization": f"Bearer {token_b}"})
    assert "a-only" not in {c["name"] for c in as_b.json()}

    as_shared_key = client.get("/connections")
    assert "a-only" not in {c["name"] for c in as_shared_key.json()}


def test_user_cannot_delete_another_users_connection():
    token_a = _token_for("user-a", "a@example.com")
    token_b = _token_for("user-b", "b@example.com")

    client.post(
        "/connections",
        json={"name": "a-private", "type": "anthropic", "config": {"api_key": "sk-a"}},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    delete_as_b = client.delete("/connections/a-private", headers={"Authorization": f"Bearer {token_b}"})
    assert delete_as_b.status_code == 404

    delete_as_a = client.delete("/connections/a-private", headers={"Authorization": f"Bearer {token_a}"})
    assert delete_as_a.status_code == 204


def test_authenticated_human_can_still_manage_a_pre_existing_global_connection():
    """Regression: a real signed-in user must still be able to see/delete
    the shared-key-created global connections that predate spec-021 (the
    existing single-operator workflow) -- resolve_connection_for_user's
    "mine, falling back to global" policy is what makes this work."""
    token_a = _token_for("user-a", "a@example.com")

    create = client.post(
        "/connections", json={"name": "legacy-global", "type": "anthropic", "config": {"api_key": "sk-legacy"}}
    )
    assert create.status_code == 201

    as_a = client.get("/connections", headers={"Authorization": f"Bearer {token_a}"})
    assert "legacy-global" in {c["name"] for c in as_a.json()}

    delete_as_a = client.delete("/connections/legacy-global", headers={"Authorization": f"Bearer {token_a}"})
    assert delete_as_a.status_code == 204

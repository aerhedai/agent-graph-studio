"""spec-021: graph sharing + per-user connection-slot mapping -- a graph
marked `sharing="shared"` declares named connection slots; a non-author
runner maps each to one of their own connections once, remembered
thereafter. Mirrors tests/test_per_user_connections.py and
tests/test_platform_auth.py's conventions (real JWTs for two distinct test
users, TestClient against the real HTTP surface).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.connections.resolver import resolve_connections
from backend.connections.store import add_connection
from backend.schema.models import GraphSpec, NodeSpec
from backend.storage import graph_sharing_store, users_store

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


def _simple_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "n1", "type": "text_input", "config": {"value": "hello"}},
            {
                "id": "n2",
                "type": "llm_call",
                "config": {"connection": "my-anthropic", "model": "x", "max_tokens": 10},
            },
            {"id": "n3", "type": "text_output", "config": {}},
        ],
        "edges": [
            {"from": {"node": "n1", "slot": "text"}, "to": {"node": "n2", "slot": "prompt"}},
            {"from": {"node": "n2", "slot": "response"}, "to": {"node": "n3", "slot": "text"}},
        ],
    }


# --- create/read a shared graph ---------------------------------------------


def test_create_shared_graph_persists_and_returns_declared_slots():
    token = _token_for("author", "author@example.com")
    response = client.post(
        "/graphs",
        json={
            "name": "shared-graph",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sharing"] == "shared"
    assert body["connection_slots"] == [{"slot_name": "my-anthropic", "connection_type": "anthropic"}]

    fetched = client.get(f"/graphs/{body['graph_id']}", headers={"Authorization": f"Bearer {token}"})
    assert fetched.json()["sharing"] == "shared"
    assert fetched.json()["connection_slots"] == [{"slot_name": "my-anthropic", "connection_type": "anthropic"}]


def test_private_graph_has_no_slots_by_default():
    token = _token_for("author", "author@example.com")
    response = client.post(
        "/graphs", json={"name": "private-graph", "spec": _simple_graph()}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sharing"] == "private"
    assert body["connection_slots"] == []


def test_switching_a_shared_graph_back_to_private_clears_its_slots():
    token = _token_for("author", "author@example.com")
    create = client.post(
        "/graphs",
        json={
            "name": "toggle-graph",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    graph_id = create.json()["graph_id"]

    update = client.put(
        f"/graphs/{graph_id}", json={"sharing": "private"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert update.status_code == 200
    assert update.json()["sharing"] == "private"
    assert update.json()["connection_slots"] == []
    assert graph_sharing_store.list_slots(graph_id) == []


# --- POST /runs pre-flight: unmapped slots block the run --------------------


def test_non_author_running_shared_graph_with_unmapped_slot_gets_409():
    author_token = _token_for("author", "author@example.com")
    runner_token = _token_for("runner", "runner@example.com")

    create = client.post(
        "/graphs",
        json={
            "name": "needs-mapping",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {author_token}"},
    )
    graph_id = create.json()["graph_id"]

    response = client.post(
        f"/runs?graph_id={graph_id}",
        json=_simple_graph(),
        headers={"Authorization": f"Bearer {runner_token}"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["missing_slots"] == [{"slot_name": "my-anthropic", "connection_type": "anthropic"}]


def test_author_running_their_own_shared_graph_is_never_asked_to_map():
    token = _token_for("author", "author@example.com")
    add_connection("my-anthropic", "anthropic", {"api_key": "sk-author"}, user_id="author")
    create = client.post(
        "/graphs",
        json={
            "name": "author-runs-own",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    graph_id = create.json()["graph_id"]

    response = client.post(
        f"/runs?graph_id={graph_id}", json=_simple_graph(), headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 202


# --- setting and reusing a mapping ------------------------------------------


def test_set_connection_mapping_rejects_unknown_connection():
    token = _token_for("runner", "runner@example.com")
    author_token = _token_for("author", "author@example.com")
    create = client.post(
        "/graphs",
        json={
            "name": "mapping-target",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {author_token}"},
    )
    graph_id = create.json()["graph_id"]

    response = client.post(
        f"/graphs/{graph_id}/connection-mapping",
        json={"slot_name": "my-anthropic", "connection_name": "does-not-exist"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_set_connection_mapping_rejects_undeclared_slot():
    token = _token_for("runner", "runner@example.com")
    author_token = _token_for("author", "author@example.com")
    add_connection("runner-anthropic", "anthropic", {"api_key": "sk-runner"}, user_id="runner")
    create = client.post(
        "/graphs",
        json={
            "name": "mapping-target-2",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {author_token}"},
    )
    graph_id = create.json()["graph_id"]

    response = client.post(
        f"/graphs/{graph_id}/connection-mapping",
        json={"slot_name": "not-a-real-slot", "connection_name": "runner-anthropic"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_mapping_a_slot_unblocks_the_run_and_is_remembered_for_next_time():
    author_token = _token_for("author", "author@example.com")
    runner_token = _token_for("runner", "runner@example.com")
    add_connection("runner-anthropic", "anthropic", {"api_key": "sk-runner"}, user_id="runner")

    create = client.post(
        "/graphs",
        json={
            "name": "map-and-run",
            "spec": _simple_graph(),
            "sharing": "shared",
            "connection_slots": [{"slot_name": "my-anthropic", "connection_type": "anthropic"}],
        },
        headers={"Authorization": f"Bearer {author_token}"},
    )
    graph_id = create.json()["graph_id"]

    mapping = client.post(
        f"/graphs/{graph_id}/connection-mapping",
        json={"slot_name": "my-anthropic", "connection_name": "runner-anthropic"},
        headers={"Authorization": f"Bearer {runner_token}"},
    )
    assert mapping.status_code == 200

    listed = client.get(f"/graphs/{graph_id}/connection-mapping", headers={"Authorization": f"Bearer {runner_token}"})
    assert listed.json() == [{"slot_name": "my-anthropic", "connection_name": "runner-anthropic"}]

    # First run: no longer 409, since the slot is now mapped.
    first_run = client.post(
        f"/runs?graph_id={graph_id}", json=_simple_graph(), headers={"Authorization": f"Bearer {runner_token}"}
    )
    assert first_run.status_code == 202

    # Second run: same, remembered mapping, no re-prompt.
    second_run = client.post(
        f"/runs?graph_id={graph_id}", json=_simple_graph(), headers={"Authorization": f"Bearer {runner_token}"}
    )
    assert second_run.status_code == 202


# --- resolver-level: the actual connection substitution ---------------------


def test_resolve_connections_uses_the_slot_mapped_connection_not_the_literal_name():
    add_connection("my-anthropic", "anthropic", {"api_key": "sk-author"}, user_id="author")
    add_connection("runner-own-anthropic", "anthropic", {"api_key": "sk-runner"}, user_id="runner")

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

    # Without a mapping: resolves nothing for "runner" (no own connection
    # named "my-anthropic", and the author's is private, not global).
    from backend.connections.errors import ConnectionNotFoundError

    try:
        resolve_connections(graph, user_id="runner")
        assert False, "expected ConnectionNotFoundError"
    except ConnectionNotFoundError:
        pass

    # With a mapping: "my-anthropic" resolves to the runner's own connection.
    resolved = resolve_connections(graph, user_id="runner", slot_mappings={"my-anthropic": "runner-own-anthropic"})
    assert "my-anthropic" in resolved

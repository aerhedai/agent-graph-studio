from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import MissingApiKeyError, app, ensure_api_key_configured
from backend.connections.errors import MissingEncryptionKeyError
from backend.connections.store import (
    _load_all,
    connections_path,
    ensure_encryption_key_configured,
    get_connection,
    list_connections,
)

# spec-017: must match tests/conftest.py's TEST_API_KEY.
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _unauthenticated_client() -> TestClient:
    return TestClient(app)


def _webhook_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
        ],
    }


def _deactivate_quietly(graph_id: str) -> None:
    client.post(f"/graphs/{graph_id}/deactivate")


# --- encryption key ---------------------------------------------------------


def test_missing_encryption_key_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", raising=False)
    with pytest.raises(MissingEncryptionKeyError):
        ensure_encryption_key_configured()


def test_malformed_encryption_key_raises(monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", "not-a-real-fernet-key")
    with pytest.raises(MissingEncryptionKeyError):
        ensure_encryption_key_configured()


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        ensure_api_key_configured()


# --- migration ---------------------------------------------------------


def test_plaintext_connections_file_is_migrated_to_encrypted_on_first_read():
    path = connections_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_plaintext = json.dumps(
        {"connections": [{"name": "legacy", "type": "anthropic", "config": {"api_key": "sk-legacy-secret"}}]}
    )
    path.write_text(legacy_plaintext)

    # First read migrates it -- no data loss, still resolves correctly.
    profiles = list_connections()
    assert len(profiles) == 1
    assert profiles[0].name == "legacy"
    assert profiles[0].config == {"api_key": "sk-legacy-secret"}

    # The file on disk is no longer plaintext.
    raw = path.read_bytes()
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert b"sk-legacy-secret" not in raw

    # And a second read (post-migration) still works via the normal path.
    assert get_connection("legacy").config == {"api_key": "sk-legacy-secret"}


def test_load_all_on_missing_file_returns_empty_without_touching_encryption():
    assert not connections_path().exists()
    assert _load_all() == []


# --- API-key auth: header, query param, absence, wrong value ---------------


def test_request_without_credential_is_401():
    unauth = _unauthenticated_client()
    response = unauth.get("/connections")
    assert response.status_code == 401


def test_request_with_wrong_credential_is_401():
    unauth = _unauthenticated_client()
    response = unauth.get("/connections", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401


def test_request_with_correct_header_is_200():
    response = client.get("/connections")
    assert response.status_code == 200


def test_request_with_correct_query_param_is_200_with_no_header_at_all():
    unauth = _unauthenticated_client()
    response = unauth.get("/connections?key=test-api-key")
    assert response.status_code == 200


def test_health_endpoint_is_unauthenticated():
    unauth = _unauthenticated_client()
    response = unauth.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_and_docs_are_unauthenticated():
    unauth = _unauthenticated_client()
    assert unauth.get("/openapi.json").status_code == 200
    assert unauth.get("/docs").status_code == 200


# --- the critical proof: dynamically-added webhook routes are ALSO protected ---


def test_dynamically_registered_webhook_route_is_actually_protected():
    """The one part of this spec with real potential for a silent gap --
    app-level `dependencies=[Depends(require_api_key)]` must cover routes
    added later via `app.add_api_route` (SPEC-009's webhook routes), not
    just the ones defined directly in this module. Verified directly,
    not assumed."""
    graph_id = "auth-webhook-proof-graph"
    try:
        activate = client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        assert activate.status_code == 200
        endpoint = activate.json()["triggers"][0]["endpoint_or_schedule"]
        bare_path = f"/webhooks/{graph_id}/trigger"
        assert endpoint == f"{bare_path}?key=test-api-key"

        unauth = _unauthenticated_client()

        # No credential at all -- must 401, not silently fire the trigger.
        no_cred = unauth.post(bare_path, json={})
        assert no_cred.status_code == 401

        # Wrong credential (header) -- must 401.
        wrong_cred = unauth.post(bare_path, json={}, headers={"Authorization": "Bearer wrong-key"})
        assert wrong_cred.status_code == 401

        # Correct credential via query param ONLY (no header) -- the exact
        # mechanism an external caller like Telegram would use, since it
        # can't set a custom header -- must succeed.
        via_query = unauth.post(f"{bare_path}?key=test-api-key", json={})
        assert via_query.status_code == 200
        assert "run_id" in via_query.json()
    finally:
        _deactivate_quietly(graph_id)

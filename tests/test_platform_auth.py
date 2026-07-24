"""spec-020: platform authentication -- Google sign-in, invite-only
allowlist, JWT sessions, dual-credential require_auth. Mirrors
tests/test_production_hardening.py's structure exactly (module-level
authenticated `client`, a `_unauthenticated_client()` helper).

The one real network call in this whole flow -- google_oauth.exchange_code_
for_userinfo, an actual HTTPS round trip to Google -- is mocked here for the
allowlist/JWT-issuance logic tests; a real, human click-through of Google's
own consent screen is the live verification step for this spec, run
separately (not part of this automated suite), matching the precedent
already set for Telegram/Slack.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.auth import google_oauth
from backend.auth import jwt as auth_jwt
from backend.auth.errors import MissingGoogleOAuthConfigError, MissingJwtSecretError
from backend.storage import settings_store, users_store

# spec-017/020: must match tests/conftest.py's TEST_API_KEY.
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _unauthenticated_client() -> TestClient:
    return TestClient(app)


def _set_public_base_url() -> None:
    settings_store.set_public_base_url("https://backend.example.com")


# --- fail-closed startup: each of the three new required secrets -----------


def test_missing_jwt_secret_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_JWT_SECRET", raising=False)
    with pytest.raises(MissingJwtSecretError):
        auth_jwt.ensure_jwt_secret_configured()


def test_missing_google_client_id_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(MissingGoogleOAuthConfigError):
        google_oauth.ensure_google_oauth_configured()


def test_missing_google_client_secret_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(MissingGoogleOAuthConfigError):
        google_oauth.ensure_google_oauth_configured()


def test_missing_admin_email_raises(monkeypatch):
    monkeypatch.delenv("AGENT_GRAPH_STUDIO_ADMIN_EMAIL", raising=False)
    with pytest.raises(users_store.MissingAdminEmailError):
        users_store.ensure_admin_email_configured()


# --- JWT issuance/verification ----------------------------------------------


def test_issued_session_token_round_trips():
    token = auth_jwt.issue_token("user-1", "person@example.com", "member")
    claims = auth_jwt.verify_token(token)
    assert claims is not None
    assert claims.user_id == "user-1"
    assert claims.email == "person@example.com"
    assert claims.role == "member"


def test_state_token_round_trips_redirect_to():
    state = auth_jwt.issue_state_token("https://app.example.com")
    assert auth_jwt.verify_state_token(state) == "https://app.example.com"


def test_state_token_rejected_as_session_token():
    state = auth_jwt.issue_state_token("https://app.example.com")
    assert auth_jwt.verify_token(state) is None


def test_session_token_rejected_as_state_token():
    token = auth_jwt.issue_token("user-1", "person@example.com", "member")
    assert auth_jwt.verify_state_token(token) is None


def test_garbage_token_is_rejected():
    assert auth_jwt.verify_token("not-a-real-jwt") is None


# --- require_auth: dual credential ------------------------------------------


def test_shared_api_key_still_works_and_sets_no_user():
    response = client.get("/auth/me")
    # A shared-key caller has no human identity -- /auth/me is inherently
    # about "who is signed in," so this is 401, not a nonsensical 200.
    assert response.status_code == 401


def test_valid_jwt_authenticates_and_sets_request_state_user():
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    admin = users_store.create_user(
        user_id="admin-id",
        email="admin@example.com",
        display_name="Admin",
        role="admin",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    token = auth_jwt.issue_token(admin.id, admin.email, admin.role)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"user_id": "admin-id", "email": "admin@example.com", "display_name": "Admin", "role": "admin"}


def test_invalid_jwt_falls_back_to_shared_key_check_and_401s():
    unauth = _unauthenticated_client()
    response = unauth.get("/connections", headers={"Authorization": "Bearer not-a-real-jwt-or-the-shared-key"})
    assert response.status_code == 401


def test_valid_jwt_authenticates_ordinary_routes_too():
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id="member-id",
        email="member@example.com",
        display_name="Member",
        role="member",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by="admin-id",
    )
    token = auth_jwt.issue_token(user.id, user.email, user.role)
    response = client.get("/connections", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


# --- invite allowlist: admin-only, accept/reject ----------------------------


def _admin_token() -> str:
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    admin = users_store.create_user(
        user_id="admin-id",
        email="admin@example.com",
        display_name="Admin",
        role="admin",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    return auth_jwt.issue_token(admin.id, admin.email, admin.role)


def test_admin_can_invite():
    token = _admin_token()
    response = client.post(
        "/auth/invite", json={"email": "newperson@example.com", "role": "member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "newperson@example.com"
    assert body["role"] == "member"
    assert body["invited_by"] == "admin-id"
    assert users_store.get_invite("newperson@example.com") is not None


def test_non_admin_cannot_invite():
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    member = users_store.create_user(
        user_id="member-id",
        email="member@example.com",
        display_name="Member",
        role="member",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by="admin-id",
    )
    token = auth_jwt.issue_token(member.id, member.email, member.role)
    response = client.post(
        "/auth/invite", json={"email": "x@example.com"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert users_store.get_invite("x@example.com") is None


def test_shared_key_caller_cannot_invite():
    response = client.post("/auth/invite", json={"email": "x@example.com"})
    assert response.status_code == 403


# --- admin bootstrap ---------------------------------------------------------


def test_admin_email_is_bootstrapped_onto_the_allowlist_as_admin():
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    invite = users_store.get_invite("admin@example.com")
    assert invite is not None
    assert invite.role == "admin"


def test_admin_bootstrap_is_idempotent_and_never_downgrades():
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    first = users_store.get_invite("admin@example.com")
    # A second boot (or a real restart) must not clobber invited_at/role.
    users_store.ensure_admin_bootstrapped("admin@example.com", "2027-01-01T00:00:00+00:00")
    second = users_store.get_invite("admin@example.com")
    assert second == first


# --- Google OAuth login/callback route wiring (network call mocked) --------


def test_login_without_public_base_url_configured_is_422():
    unauth = _unauthenticated_client()
    response = unauth.get("/auth/google/login?redirect_to=https://app.example.com")
    assert response.status_code == 422


def test_login_redirects_to_google_with_state_cookie():
    _set_public_base_url()
    unauth = _unauthenticated_client()
    response = unauth.get(
        "/auth/google/login?redirect_to=https://app.example.com", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith(google_oauth.AUTHORIZE_URL)
    assert "oauth_state" in response.cookies


def test_callback_with_mismatched_state_cookie_is_400():
    _set_public_base_url()
    unauth = _unauthenticated_client()
    login = unauth.get("/auth/google/login?redirect_to=https://app.example.com", follow_redirects=False)
    real_state = login.headers["location"].split("state=")[1].split("&")[0]
    response = unauth.get(
        f"/auth/google/callback?code=irrelevant&state={real_state}",
        cookies={"oauth_state": "a-different-state-value"},
    )
    assert response.status_code == 400


def test_callback_for_uninvited_email_redirects_with_not_invited_error():
    _set_public_base_url()
    unauth = _unauthenticated_client()
    login = unauth.get("/auth/google/login?redirect_to=https://app.example.com", follow_redirects=False)
    state = login.cookies["oauth_state"]

    with patch.object(
        google_oauth,
        "exchange_code_for_userinfo",
        return_value=google_oauth.GoogleUserInfo(email="stranger@example.com", email_verified=True, name="Stranger"),
    ):
        response = unauth.get(
            f"/auth/google/callback?code=real-code&state={state}",
            cookies={"oauth_state": state},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "auth_error=not_invited" in response.headers["location"]
    assert users_store.get_user_by_email("stranger@example.com") is None


def test_callback_for_invited_email_creates_user_and_redirects_with_token():
    _set_public_base_url()
    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    users_store.add_invite("invited@example.com", "member", invited_by="admin@example.com", invited_at="2026-01-01T00:00:00+00:00")

    unauth = _unauthenticated_client()
    login = unauth.get("/auth/google/login?redirect_to=https://app.example.com", follow_redirects=False)
    state = login.cookies["oauth_state"]

    with patch.object(
        google_oauth,
        "exchange_code_for_userinfo",
        return_value=google_oauth.GoogleUserInfo(email="invited@example.com", email_verified=True, name="Invited Person"),
    ):
        response = unauth.get(
            f"/auth/google/callback?code=real-code&state={state}",
            cookies={"oauth_state": state},
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://app.example.com#token=")

    user = users_store.get_user_by_email("invited@example.com")
    assert user is not None
    assert user.display_name == "Invited Person"
    assert user.role == "member"

    token = location.split("#token=")[1]
    claims = auth_jwt.verify_token(token)
    assert claims is not None
    assert claims.email == "invited@example.com"


# --- created_by / run_by population ------------------------------------------


def test_graph_created_via_shared_key_has_null_created_by():
    response = client.post("/graphs", json={"name": "shared-key-graph", "spec": {"version": "0.1", "nodes": [], "edges": []}})
    assert response.status_code == 201
    assert response.json()["created_by"] is None


def test_graph_created_via_jwt_has_created_by_set_to_user_id():
    token = _admin_token()
    response = client.post(
        "/graphs",
        json={"name": "human-graph", "spec": {"version": "0.1", "nodes": [], "edges": []}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["created_by"] == "admin-id"

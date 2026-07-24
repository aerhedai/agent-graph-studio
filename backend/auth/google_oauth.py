"""spec-020: real Google OAuth2/OIDC for platform login -- a minimal-scope
grant (openid/email/profile only), deliberately separate from whatever
broader, sensitive-scope Gmail data-access grant SPEC-021 requests later,
even though both go through the same provider. Plain urllib for the actual
HTTP calls, matching this project's established outbound-HTTP convention
(see backend/connections/ollama_connection.py) -- no google-auth-oauthlib
dependency; these are just documented REST endpoints.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from backend.auth.errors import MissingGoogleOAuthConfigError

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "openid email profile"


def _client_id() -> str:
    value = os.environ.get("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID")
    if not value:
        raise MissingGoogleOAuthConfigError("CLIENT_ID")
    return value


def _client_secret() -> str:
    value = os.environ.get("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET")
    if not value:
        raise MissingGoogleOAuthConfigError("CLIENT_SECRET")
    return value


def ensure_google_oauth_configured() -> None:
    """Public entry point for backend/api/app.py's eager startup check."""
    _client_id()
    _client_secret()


class GoogleOAuthError(RuntimeError):
    """The code-for-token exchange or the userinfo call itself failed --
    distinct from MissingGoogleOAuthConfigError (a configuration problem,
    checked at startup) since this can only happen mid-request, against a
    real, specific sign-in attempt."""


def build_authorization_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


@dataclass(frozen=True)
class GoogleUserInfo:
    email: str
    email_verified: bool
    name: str


def _post_form(url: str, data: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.URLError as e:
        raise GoogleOAuthError(f"Google token exchange failed: {e}") from e


def _get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.URLError as e:
        raise GoogleOAuthError(f"Google userinfo request failed: {e}") from e


def exchange_code_for_userinfo(code: str, redirect_uri: str) -> GoogleUserInfo:
    """The real, live call this spec's acceptance criteria actually
    depend on -- a full code-for-token-for-userinfo round trip against
    Google's real endpoints, not something mockable-and-forgotten."""
    token_response = _post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise GoogleOAuthError(f"Google token response had no access_token: {token_response}")

    userinfo = _get_json(USERINFO_URL, access_token)
    email = userinfo.get("email")
    if not email:
        raise GoogleOAuthError(f"Google userinfo response had no email: {userinfo}")

    return GoogleUserInfo(
        email=email,
        email_verified=bool(userinfo.get("email_verified", False)),
        name=userinfo.get("name", email),
    )

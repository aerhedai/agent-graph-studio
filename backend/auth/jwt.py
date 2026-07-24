"""spec-020: our own session tokens -- issued after a successful Google
sign-in, verified on every subsequent human-facing request. HS256
(symmetric, one shared signing secret), matching this project's existing
"one shared secret" precedent elsewhere rather than a PKI/asymmetric setup
this project's actual scale doesn't need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from backend.auth.errors import MissingJwtSecretError

ALGORITHM = "HS256"
DEFAULT_EXPIRES_IN_HOURS = 12
"""spec-020 §7's resolved open question: a moderate-lived token with
silent re-authentication against Google on expiry, rather than a separate
refresh-token dance -- kept simple deliberately, revisit if disruptive."""


def _jwt_secret() -> str:
    """Raises MissingJwtSecretError -- eagerly, not lazily -- if the secret
    is absent. Called both by issue_token/verify_token and, explicitly at
    API startup (backend/api/app.py's ensure_jwt_secret_configured), so
    "the backend refuses to start" is deterministic, not incidental on
    whichever request happens to touch this first. Unlike the connections
    encryption key (a real Fernet key, format-validated), any non-empty
    string is a structurally valid HMAC signing secret -- no equivalent
    "malformed key" failure mode to check for here."""
    secret = os.environ.get("AGENT_GRAPH_STUDIO_JWT_SECRET")
    if not secret:
        raise MissingJwtSecretError()
    return secret


def ensure_jwt_secret_configured() -> None:
    """Public entry point for backend/api/app.py's eager startup check."""
    _jwt_secret()


@dataclass(frozen=True)
class Claims:
    user_id: str
    email: str
    role: str


def issue_token(user_id: str, email: str, role: str, expires_in_hours: int = DEFAULT_EXPIRES_IN_HOURS) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expires_in_hours),
    }
    return pyjwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def verify_token(token: str) -> Claims | None:
    """Returns None on any verification failure (expired, bad signature,
    malformed) rather than raising -- callers (require_auth) treat "not a
    valid JWT" as "maybe it's the shared API key instead," not a hard
    error in its own right."""
    try:
        payload = pyjwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
    except pyjwt.InvalidTokenError:
        return None
    if payload.get("typ") is not None:
        # A state token (see below) accidentally presented as a session
        # token -- reject, don't accept cross-purpose tokens just because
        # they're signed with the same secret.
        return None
    try:
        return Claims(user_id=payload["sub"], email=payload["email"], role=payload["role"])
    except KeyError:
        return None


STATE_TOKEN_EXPIRES_MINUTES = 5
"""Short-lived by design -- this token only needs to survive one real
human clicking through Google's consent screen, not a normal session."""


def issue_state_token(redirect_to: str) -> str:
    """spec-020: carries the frontend origin to redirect back to after a
    successful sign-in through Google's own redirect round trip, tamper-
    evident via the same signing secret as session tokens but a distinct
    claim shape (`typ`) so it can never be accepted as a real session
    token (see verify_token's explicit rejection above) -- and vice versa,
    verify_state_token below never accepts a real session token either."""
    now = datetime.now(timezone.utc)
    payload = {
        "typ": "oauth_state",
        "redirect_to": redirect_to,
        "iat": now,
        "exp": now + timedelta(minutes=STATE_TOKEN_EXPIRES_MINUTES),
    }
    return pyjwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def verify_state_token(token: str) -> str | None:
    """Returns the embedded redirect_to, or None if the token is invalid,
    expired, or not actually a state token."""
    try:
        payload = pyjwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
    except pyjwt.InvalidTokenError:
        return None
    if payload.get("typ") != "oauth_state":
        return None
    return payload.get("redirect_to")

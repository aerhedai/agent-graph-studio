from __future__ import annotations


class MissingJwtSecretError(RuntimeError):
    """spec-020: raised eagerly at API startup when
    AGENT_GRAPH_STUDIO_JWT_SECRET isn't set -- refusing to start, same
    fail-closed precedent as backend/connections/store.py's
    MissingEncryptionKeyError and backend/api/app.py's MissingApiKeyError.
    Deliberately a distinct secret from the connections encryption key --
    signing session tokens and encrypting connection secrets are different
    security domains; one key should not do both."""

    def __init__(self) -> None:
        super().__init__(
            "AGENT_GRAPH_STUDIO_JWT_SECRET is not set -- refusing to start without a real "
            "signing secret for session tokens (see docs/DEPLOYMENT.md)."
        )


class MissingGoogleOAuthConfigError(RuntimeError):
    """spec-020: raised eagerly at API startup when either
    AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID or
    AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET isn't set."""

    def __init__(self, missing: str) -> None:
        super().__init__(
            f"AGENT_GRAPH_STUDIO_GOOGLE_{missing} is not set -- refusing to start without "
            "real Google OAuth credentials for platform sign-in (see docs/DEPLOYMENT.md)."
        )

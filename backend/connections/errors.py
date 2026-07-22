from __future__ import annotations


class DuplicateConnectionError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"A connection named '{name}' already exists")
        self.name = name


class ConnectionNotFoundError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No connection named '{name}' is configured on this machine")
        self.name = name


class MissingEncryptionKeyError(RuntimeError):
    """spec-017: raised eagerly at API startup (and by any store operation
    that needs it) when AGENT_GRAPH_STUDIO_ENCRYPTION_KEY isn't set or isn't
    a valid Fernet key -- refusing to start is the point, not a fallback."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"AGENT_GRAPH_STUDIO_ENCRYPTION_KEY is not configured correctly: {detail} "
            "-- refusing to start without a real encryption key for connection secrets "
            "(see docs/DEPLOYMENT.md)."
        )

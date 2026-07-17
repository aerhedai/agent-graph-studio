from __future__ import annotations


class DuplicateConnectionError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"A connection named '{name}' already exists")
        self.name = name


class ConnectionNotFoundError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No connection named '{name}' is configured on this machine")
        self.name = name

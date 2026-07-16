from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class ConnectionTestResult:
    success: bool
    message: str


@dataclass(frozen=True)
class ConnectionDefinition:
    type_name: str
    category: Literal["local", "cloud"]
    config_model: type[BaseModel]
    build_client: Callable[[BaseModel], Any]
    test_connection: Callable[[BaseModel], ConnectionTestResult]


class ConnectionRegistry:
    """Plugin-style registry of connection types, mirroring
    backend/registry/base.py's NodeRegistry exactly -- a class (not a bare
    module-level dict) so it's dependency-injectable for isolated tests."""

    def __init__(self) -> None:
        self._defs: dict[str, ConnectionDefinition] = {}

    def register(self, definition: ConnectionDefinition) -> None:
        if definition.type_name in self._defs:
            raise ValueError(f"Duplicate connection type registration: {definition.type_name}")
        self._defs[definition.type_name] = definition

    def get(self, type_name: str) -> ConnectionDefinition | None:
        return self._defs.get(type_name)

    def all_types(self) -> list[str]:
        return list(self._defs.keys())


default_connection_registry = ConnectionRegistry()


def register_connection_type(
    type_name: str,
    category: Literal["local", "cloud"],
    config_model: type[BaseModel],
    build_client: Callable[[BaseModel], Any],
    test_connection: Callable[[BaseModel], ConnectionTestResult],
    registry: ConnectionRegistry = default_connection_registry,
) -> None:
    """Plain registration call (not a decorator, unlike @register_node) --
    a connection type bundles three things (schema, build_client, test)
    rather than wrapping a single execute function. Called as an import
    side effect at the bottom of each connection-type module, same
    "registration happens on import" precedent as the node registry."""
    registry.register(
        ConnectionDefinition(
            type_name=type_name,
            category=category,
            config_model=config_model,
            build_client=build_client,
            test_connection=test_connection,
        )
    )

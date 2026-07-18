"""Resolves connection names referenced in a graph's node configs into real,
ready-to-use clients -- called by the CLI/API layer *before* run_graph, so
engine.py and every node's execute() body stay unaware named connections
exist at all (spec-006 §4). Generic across node types: any config key that
is exactly "connection" or ends with "_connection" is treated as a
connection reference, not just llm_call/agent's single "connection" field --
generalized in spec-011 §4 so a node type needing more than one named
connection (e.g. ingest_document's vector store + embedding model) requires
no special-casing here or in validation/rules.py's check_missing_connections,
which imports this exact same helper rather than re-implementing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.connections.base import ConnectionRegistry, default_connection_registry
from backend.connections.errors import ConnectionNotFoundError
from backend.connections.store import ConnectionProfile, get_connection
from backend.schema.models import GraphSpec


def connection_reference_names(config: dict[str, Any]) -> list[str]:
    """Every string-valued config key on one node that names a connection --
    a key is a connection reference if it's exactly "connection" or ends
    with "_connection"."""
    return [
        value
        for key, value in config.items()
        if (key == "connection" or key.endswith("_connection")) and isinstance(value, str)
    ]


def _referenced_connection_names(graph: GraphSpec) -> set[str]:
    names: set[str] = set()
    for node in graph.nodes:
        names.update(connection_reference_names(node.config))
    return names


def resolve_connections(
    graph: GraphSpec,
    path: Path | None = None,
    registry: ConnectionRegistry = default_connection_registry,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name in _referenced_connection_names(graph):
        profile = get_connection(name, path=path)
        if profile is None:
            raise ConnectionNotFoundError(name)
        definition = registry.get(profile.type)
        if definition is None:
            raise ValueError(f"Connection '{name}' has unknown type '{profile.type}'")
        config = definition.config_model.model_validate(profile.config)
        resolved[name] = definition.build_client(config)
    return resolved


def resolve_connection_profiles(
    graph: GraphSpec, path: Path | None = None
) -> dict[str, ConnectionProfile]:
    """spec-008 §5: raw type+config (not a built client) for every
    connection referenced in the graph -- `agent`'s complete_with_tools
    capability takes validated config directly (same shape as
    test_connection/list_models) rather than a pre-built LLMClient, since
    tool-calling is a materially different API shape (chat/messages) from
    what AnthropicLLMClient/OllamaLLMClient already implement. Kept
    separate from resolve_connections so llm_call's existing resolution
    path is completely untouched."""
    resolved: dict[str, ConnectionProfile] = {}
    for name in _referenced_connection_names(graph):
        profile = get_connection(name, path=path)
        if profile is None:
            raise ConnectionNotFoundError(name)
        resolved[name] = profile
    return resolved

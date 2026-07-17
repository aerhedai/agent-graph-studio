"""Resolves connection names referenced in a graph's node configs into real,
ready-to-use clients -- called by the CLI/API layer *before* run_graph, so
engine.py and every node's execute() body stay unaware named connections
exist at all (spec-006 §4). Generic across node types: any node whose config
has a "connection" key gets resolved, not just llm_call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.connections.base import ConnectionRegistry, default_connection_registry
from backend.connections.errors import ConnectionNotFoundError
from backend.connections.store import ConnectionProfile, get_connection
from backend.schema.models import GraphSpec


def _referenced_connection_names(graph: GraphSpec) -> set[str]:
    return {
        node.config["connection"]
        for node in graph.nodes
        if isinstance(node.config.get("connection"), str)
    }


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

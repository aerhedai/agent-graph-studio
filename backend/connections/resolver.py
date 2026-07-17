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
from backend.connections.store import get_connection
from backend.schema.models import GraphSpec


def resolve_connections(
    graph: GraphSpec,
    path: Path | None = None,
    registry: ConnectionRegistry = default_connection_registry,
) -> dict[str, Any]:
    names = {
        node.config["connection"]
        for node in graph.nodes
        if isinstance(node.config.get("connection"), str)
    }
    resolved: dict[str, Any] = {}
    for name in names:
        profile = get_connection(name, path=path)
        if profile is None:
            raise ConnectionNotFoundError(name)
        definition = registry.get(profile.type)
        if definition is None:
            raise ValueError(f"Connection '{name}' has unknown type '{profile.type}'")
        config = definition.config_model.model_validate(profile.config)
        resolved[name] = definition.build_client(config)
    return resolved

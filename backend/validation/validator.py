from __future__ import annotations

from pathlib import Path

from backend.registry.base import NodeRegistry, default_registry
from backend.schema.models import GraphSpec
from backend.validation.errors import GraphValidationError, ValidationIssue
from backend.validation.rules import (
    check_config_schema,
    check_cycles,
    check_missing_connections,
    check_required_inputs,
    check_structural,
    check_sub_node_edges,
    check_type_mismatches,
    check_unregistered_types,
    sub_node_referenced_ids,
)


def validate_graph(
    graph: GraphSpec,
    registry: NodeRegistry = default_registry,
    connections_path: Path | None = None,
) -> None:
    """Validate a graph against spec §5 (plus spec-006's connection-
    reference check and spec-012's cluster/sub-node checks, which supersede
    spec-008's narrower agent-tool-reference checks outright). Raises
    GraphValidationError, carrying every issue found, if the graph is
    invalid. Runs entirely before any execution starts."""
    issues: list[ValidationIssue] = []

    structural_issues, valid_edges = check_structural(graph)
    issues += structural_issues

    unregistered_issues, unregistered_ids = check_unregistered_types(graph, registry)
    issues += unregistered_issues

    sub_node_ids = sub_node_referenced_ids(graph)

    issues += check_required_inputs(graph, registry, valid_edges, unregistered_ids, sub_node_ids)
    issues += check_type_mismatches(graph, registry, valid_edges, unregistered_ids)
    issues += check_cycles(graph, valid_edges)
    issues += check_config_schema(graph, registry, unregistered_ids)
    issues += check_missing_connections(graph, connections_path)
    issues += check_sub_node_edges(graph, registry, unregistered_ids)

    if issues:
        raise GraphValidationError(issues)

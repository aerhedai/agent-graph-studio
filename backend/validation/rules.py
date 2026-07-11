from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from backend.registry.base import NodeRegistry
from backend.schema.models import EdgeSpec, GraphSpec
from backend.schema.topo import kahn_order
from backend.validation.errors import ValidationIssue


def check_structural(graph: GraphSpec) -> tuple[list[ValidationIssue], list[EdgeSpec]]:
    """Every edge must reference node ids that actually exist in the graph.

    Not one of spec §5's literal 4 rules, but a prerequisite: the other
    checks assume edges reference real nodes and would otherwise raise
    KeyErrors on dangling references. Returns the issues found plus the
    subset of edges that passed (safe for downstream checks to use).
    """
    node_ids = {n.id for n in graph.nodes}
    issues: list[ValidationIssue] = []
    valid_edges: list[EdgeSpec] = []
    for edge in graph.edges:
        problems = []
        if edge.from_.node not in node_ids:
            problems.append(f"edge references unknown source node '{edge.from_.node}'")
        if edge.to.node not in node_ids:
            problems.append(f"edge references unknown destination node '{edge.to.node}'")
        if problems:
            for p in problems:
                issues.append(ValidationIssue("structural", None, p))
        else:
            valid_edges.append(edge)
    return issues, valid_edges


def check_unregistered_types(graph: GraphSpec, registry: NodeRegistry) -> tuple[list[ValidationIssue], set[str]]:
    """Spec §5 bullet 4: every node's type must be registered."""
    issues: list[ValidationIssue] = []
    unregistered_ids: set[str] = set()
    for node in graph.nodes:
        if registry.get(node.type) is None:
            issues.append(
                ValidationIssue(
                    "unregistered_type", node.id, f"node type '{node.type}' is not registered"
                )
            )
            unregistered_ids.add(node.id)
    return issues, unregistered_ids


def check_required_inputs(
    graph: GraphSpec,
    registry: NodeRegistry,
    valid_edges: list[EdgeSpec],
    unregistered_ids: set[str],
) -> list[ValidationIssue]:
    """Spec §5 bullet 1: every required input slot must have an incoming edge."""
    covered = {(e.to.node, e.to.slot) for e in valid_edges}
    issues: list[ValidationIssue] = []
    for node in graph.nodes:
        if node.id in unregistered_ids:
            continue
        definition = registry.get(node.type)
        for slot in definition.inputs:
            if slot.required and (node.id, slot.name) not in covered:
                issues.append(
                    ValidationIssue(
                        "missing_required_input",
                        node.id,
                        f"required input slot '{slot.name}' has no incoming edge",
                    )
                )
    return issues


def check_type_mismatches(
    graph: GraphSpec,
    registry: NodeRegistry,
    valid_edges: list[EdgeSpec],
    unregistered_ids: set[str],
) -> list[ValidationIssue]:
    """Spec §5 bullet 2: edges must connect slots of compatible type."""
    issues: list[ValidationIssue] = []
    nodes_by_id = {n.id: n for n in graph.nodes}
    for edge in valid_edges:
        src_node = nodes_by_id[edge.from_.node]
        dst_node = nodes_by_id[edge.to.node]
        if src_node.id in unregistered_ids or dst_node.id in unregistered_ids:
            continue
        src_def = registry.get(src_node.type)
        dst_def = registry.get(dst_node.type)

        src_slot = next((s for s in src_def.outputs if s.name == edge.from_.slot), None)
        dst_slot = next((s for s in dst_def.inputs if s.name == edge.to.slot), None)

        if src_slot is None:
            issues.append(
                ValidationIssue(
                    "type_mismatch",
                    src_node.id,
                    f"node has no output slot '{edge.from_.slot}'",
                )
            )
            continue
        if dst_slot is None:
            issues.append(
                ValidationIssue(
                    "type_mismatch",
                    dst_node.id,
                    f"node has no input slot '{edge.to.slot}'",
                )
            )
            continue
        if not src_slot.type.is_compatible_with(dst_slot.type):
            issues.append(
                ValidationIssue(
                    "type_mismatch",
                    dst_node.id,
                    f"edge {src_node.id}.{edge.from_.slot} ({src_slot.type.base.value}) -> "
                    f"{dst_node.id}.{edge.to.slot} ({dst_slot.type.base.value}) is a type mismatch",
                )
            )
    return issues


def check_cycles(graph: GraphSpec, valid_edges: list[EdgeSpec]) -> list[ValidationIssue]:
    """Spec §5 bullet 3: the graph must not contain a cycle (MVP has no loop support)."""
    node_ids = [n.id for n in graph.nodes]
    _, remaining = kahn_order(node_ids, valid_edges)
    if remaining:
        return [
            ValidationIssue(
                "cycle", None, f"cycle detected involving node(s): {sorted(remaining)}"
            )
        ]
    return []


def check_config_schema(
    graph: GraphSpec, registry: NodeRegistry, unregistered_ids: set[str]
) -> list[ValidationIssue]:
    """Every node's config must validate against its type's config model.

    Not one of spec §5's literal 4 bullets, but required by §4 (every node
    type has a config shape) and CLAUDE.md's "validate before execution."
    """
    issues: list[ValidationIssue] = []
    for node in graph.nodes:
        if node.id in unregistered_ids:
            continue
        definition = registry.get(node.type)
        try:
            definition.config_model.model_validate(node.config)
        except PydanticValidationError as e:
            issues.append(ValidationIssue("invalid_config", node.id, str(e)))
    return issues

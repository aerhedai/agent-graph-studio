from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from backend.registry.base import NodeRegistry, effective_inputs, effective_outputs
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


def agent_tool_referenced_ids(graph: GraphSpec) -> set[str]:
    """Every node id referenced as a tool by any `agent` node (spec-008
    §4) -- such a node's inputs legitimately come from the agent's direct
    tool calls at runtime, not graph edges, so check_required_inputs must
    not flag them as missing just because no edge feeds them. Defensive
    about malformed `tools` config the same way every other rule is;
    check_config_schema reports the real error for that case."""
    ids: set[str] = set()
    for node in graph.nodes:
        if node.type != "agent":
            continue
        tools = node.config.get("tools") if isinstance(node.config, dict) else None
        if not isinstance(tools, list):
            continue
        ids.update(tool_id for tool_id in tools if isinstance(tool_id, str))
    return ids


def check_required_inputs(
    graph: GraphSpec,
    registry: NodeRegistry,
    valid_edges: list[EdgeSpec],
    unregistered_ids: set[str],
    tool_referenced_ids: set[str] = frozenset(),
) -> list[ValidationIssue]:
    """Spec §5 bullet 1: every required input slot must have an incoming edge."""
    covered = {(e.to.node, e.to.slot) for e in valid_edges}
    issues: list[ValidationIssue] = []
    for node in graph.nodes:
        if node.id in unregistered_ids or node.id in tool_referenced_ids:
            continue
        definition = registry.get(node.type)
        inputs = effective_inputs(definition, node)
        if inputs is None:
            # Unresolvable schema (e.g. malformed config) -- check_config_schema
            # reports the real error; don't pile on a confusing second one.
            continue
        for slot in inputs:
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

        src_outputs = effective_outputs(src_def, src_node)
        dst_inputs = effective_inputs(dst_def, dst_node)
        if src_outputs is None or dst_inputs is None:
            # Unresolvable schema on one end (e.g. malformed config) --
            # check_config_schema reports the real error.
            continue

        src_slot = next((s for s in src_outputs if s.name == edge.from_.slot), None)
        dst_slot = next((s for s in dst_inputs if s.name == edge.to.slot), None)

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


def check_missing_connections(
    graph: GraphSpec, connections_path: Path | None = None
) -> list[ValidationIssue]:
    """Spec-006 §6: a node referencing a connection name not present in the
    local store must produce a clear, specific error naming it -- reusing
    the exact same "aggregate issues, raise GraphValidationError" mechanism
    every other rule already has, rather than a bespoke exception path.
    Generic across node types and across however many connection-typed
    config fields one node has (spec-011 §4): reuses
    connection_reference_names, the same convention-based key detection
    resolve_connections() uses, so the two can never drift apart.
    """
    from backend.connections.resolver import connection_reference_names
    from backend.connections.store import get_connection

    issues: list[ValidationIssue] = []
    known: dict[str, bool] = {}
    for node in graph.nodes:
        if not isinstance(node.config, dict):
            continue
        for name in connection_reference_names(node.config):
            if name not in known:
                known[name] = get_connection(name, path=connections_path) is not None
            if not known[name]:
                issues.append(
                    ValidationIssue(
                        "missing_connection",
                        node.id,
                        f"references connection '{name}' which isn't configured on this machine",
                    )
                )
    return issues


def check_agent_tool_references(
    graph: GraphSpec, valid_edges: list[EdgeSpec], unregistered_ids: set[str]
) -> list[ValidationIssue]:
    """Spec-008 §4: an `agent` node's tool references bypass edge-based
    input gathering entirely (ADR-008) -- so (a) every referenced node must
    actually exist, and (b) a referenced node must not *also* have normal
    incoming edges, which would create two ambiguous sources of truth for
    the same inputs. Defensive about `tools` not being a list of strings
    (malformed config) -- check_config_schema reports that real error,
    same pattern as check_missing_connections.
    """
    node_ids = {n.id for n in graph.nodes}
    edge_targets = {e.to.node for e in valid_edges}
    issues: list[ValidationIssue] = []
    for node in graph.nodes:
        if node.id in unregistered_ids or node.type != "agent":
            continue
        tools = node.config.get("tools") if isinstance(node.config, dict) else None
        if not isinstance(tools, list):
            continue
        for tool_id in tools:
            if not isinstance(tool_id, str):
                continue
            if tool_id not in node_ids:
                issues.append(
                    ValidationIssue(
                        "unknown_tool_reference",
                        node.id,
                        f"references tool node '{tool_id}' which does not exist in the graph",
                    )
                )
            elif tool_id in edge_targets:
                issues.append(
                    ValidationIssue(
                        "tool_node_has_conflicting_edges",
                        node.id,
                        f"tool node '{tool_id}' has normal incoming graph edges -- a tool "
                        "node's inputs must come only from the agent's tool calls, not edges",
                    )
                )
    return issues


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

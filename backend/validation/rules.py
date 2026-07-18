from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from backend.registry.base import (
    NodeDefinition,
    NodeRegistry,
    OutputSlotSpec,
    effective_inputs,
    effective_outputs,
)
from backend.schema.models import EdgeSpec, GraphSpec
from backend.schema.topo import kahn_order
from backend.validation.errors import ValidationIssue


def check_structural(graph: GraphSpec) -> tuple[list[ValidationIssue], list[EdgeSpec]]:
    """Every edge must reference node ids that actually exist in the graph.

    Not one of spec §5's literal 4 rules, but a prerequisite: the other
    checks assume edges reference real nodes and would otherwise raise
    KeyErrors on dangling references. Returns the issues found plus the
    subset of edges that passed (safe for downstream checks to use).

    Dangling-reference checking applies to every edge regardless of `kind`
    (spec-012 §4), but `valid_edges` only ever contains `kind == "data"`
    edges -- `sub_node` edges are not part of topological/data-flow
    ordering, so the data-flow-specific rules (check_required_inputs,
    check_type_mismatches, check_cycles) must never see them; they're
    validated separately by check_sub_node_edges instead.
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
        elif edge.kind == "data":
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


def sub_node_referenced_ids(graph: GraphSpec) -> set[str]:
    """Every node id that is the source (`from.node`) of a `sub_node` edge
    (spec-012 §4) -- such a node's inputs come from direct invocation by
    its root (mirroring ADR-008's tool-call bypass, now edge-based and
    generalized to every sub-node kind: `model`, `memory`, `tools`, trigger
    adapters), not from graph edges, so check_required_inputs must not
    flag it as missing an edge just because none feeds it.

    Supersedes SPEC-008's narrower, config-list-based
    agent_tool_referenced_ids outright, not dual-maintained alongside it --
    tool references are sub_node edges now too, not a separate
    config-scanning mechanism (same "delete the superseded mechanism"
    precedent as SPEC-006 §8's removal of backend/llm/providers.py)."""
    return {e.from_.node for e in graph.edges if e.kind == "sub_node"}


def check_required_inputs(
    graph: GraphSpec,
    registry: NodeRegistry,
    valid_edges: list[EdgeSpec],
    unregistered_ids: set[str],
    sub_node_ids: set[str] = frozenset(),
) -> list[ValidationIssue]:
    """Spec §5 bullet 1: every required input slot must have an incoming edge."""
    covered = {(e.to.node, e.to.slot) for e in valid_edges}
    issues: list[ValidationIssue] = []
    for node in graph.nodes:
        if node.id in unregistered_ids or node.id in sub_node_ids:
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


def _effective_outputs_for_root(
    definition: NodeDefinition, node: NodeSpec, graph: GraphSpec, registry: NodeRegistry
) -> list[OutputSlotSpec] | None:
    """Like effective_outputs, but graph-aware for cluster root types whose
    output ports mirror a connected sub-node (spec-012 §4,
    `resolve_slots_from_sub_node`) -- e.g. `webhook_trigger`'s real ports
    are whatever its connected `trigger_adapter` declares
    (`generic_adapter`'s `payload` vs `telegram_adapter`'s `message_text`/
    `sender_id`/`chat_id`), not a fixed list. Every other node type (where
    `resolve_slots_from_sub_node` is None) falls straight through to the
    ordinary, non-graph-aware `effective_outputs` -- completely unaffected.
    Lives here (not in registry/base.py) because only validation's callers
    already have the whole graph in scope; `effective_inputs`/
    `effective_outputs`'s own signature stays single-node, unwidened.
    """
    if definition.resolve_slots_from_sub_node is None:
        return effective_outputs(definition, node)
    slot = definition.resolve_slots_from_sub_node
    sub_edge = next(
        (e for e in graph.edges if e.kind == "sub_node" and e.to.node == node.id and e.slot == slot),
        None,
    )
    if sub_edge is None:
        return []
    sub_node = next((n for n in graph.nodes if n.id == sub_edge.from_.node), None)
    if sub_node is None:
        return []
    sub_definition = registry.get(sub_node.type)
    if sub_definition is None:
        return []
    return effective_outputs(sub_definition, sub_node)


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

        src_outputs = _effective_outputs_for_root(src_def, src_node, graph, registry)
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


def check_sub_node_edges(
    graph: GraphSpec, registry: NodeRegistry, unregistered_ids: set[str]
) -> list[ValidationIssue]:
    """Spec-012 §4: validates every `sub_node` edge and each root's declared
    slots as a whole --
      - the root's type must actually declare the named slot
      - the connected sub-node's type must satisfy the slot's
        `accepts_role` (skipped when `accepts_role` is None, e.g. `tools`,
        which accepts any node type -- SPEC-008's existing "any node type
        can be a tool" precedent, unchanged)
      - each slot's cardinality (exactly-one / zero-or-one / zero-or-more)
        must be satisfied once all of a root's sub_node edges are counted
      - a sub-node must not *also* have a normal incoming data edge, which
        would create two ambiguous sources of truth for its inputs --
        generalizes ADR-008's tool-node-specific version of this same check
        (spec-008 §4) to every sub-node kind
    """
    node_by_id = {n.id: n for n in graph.nodes}
    data_edge_targets = {e.to.node for e in graph.edges if e.kind == "data"}
    issues: list[ValidationIssue] = []

    connections: dict[str, dict[str, list[str]]] = {}

    for edge in graph.edges:
        if edge.kind != "sub_node":
            continue
        if edge.to.node not in node_by_id or edge.from_.node not in node_by_id:
            # Dangling reference -- check_structural already reports this
            # as a "structural" issue; don't also crash looking it up here.
            continue
        if edge.to.node in unregistered_ids or edge.from_.node in unregistered_ids:
            continue
        root_node = node_by_id[edge.to.node]
        sub_node = node_by_id[edge.from_.node]
        root_def = registry.get(root_node.type)
        sub_def = registry.get(sub_node.type)

        slot_spec = (root_def.sub_node_slots or {}).get(edge.slot)
        if slot_spec is None:
            issues.append(
                ValidationIssue(
                    "unknown_sub_node_slot",
                    root_node.id,
                    f"node type '{root_node.type}' has no sub-node slot '{edge.slot}'",
                )
            )
            continue

        if slot_spec.accepts_role is not None and sub_def.sub_node_role != slot_spec.accepts_role:
            issues.append(
                ValidationIssue(
                    "incompatible_sub_node_type",
                    root_node.id,
                    f"slot '{edge.slot}' requires a node with role '{slot_spec.accepts_role}', "
                    f"but '{sub_node.id}' (type '{sub_node.type}') has role {sub_def.sub_node_role!r}",
                )
            )
            continue

        if sub_node.id in data_edge_targets:
            issues.append(
                ValidationIssue(
                    "sub_node_has_conflicting_edges",
                    root_node.id,
                    f"sub-node '{sub_node.id}' has normal incoming data edges -- a sub-node's "
                    "inputs must come only from its root's direct invocation, not edges",
                )
            )

        connections.setdefault(root_node.id, {}).setdefault(edge.slot, []).append(sub_node.id)

    for node in graph.nodes:
        if node.id in unregistered_ids:
            continue
        definition = registry.get(node.type)
        for slot_name, slot_spec in (definition.sub_node_slots or {}).items():
            count = len(connections.get(node.id, {}).get(slot_name, []))
            if slot_spec.cardinality == "one" and count != 1:
                issues.append(
                    ValidationIssue(
                        "sub_node_cardinality",
                        node.id,
                        f"slot '{slot_name}' requires exactly one connected sub-node, found {count}",
                    )
                )
            elif slot_spec.cardinality == "zero_or_one" and count > 1:
                issues.append(
                    ValidationIssue(
                        "sub_node_cardinality",
                        node.id,
                        f"slot '{slot_name}' accepts at most one connected sub-node, found {count}",
                    )
                )
            # "many": any count, including zero, is fine.

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

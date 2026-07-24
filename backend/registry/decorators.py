from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from backend.registry.base import (
    InputSlotSpec,
    NodeDefinition,
    NodeRegistry,
    OutputSlotSpec,
    ResolveSlots,
    SubNodeSlotSpec,
    default_registry,
)


def register_node(
    type_name: str,
    inputs: list[InputSlotSpec],
    outputs: list[OutputSlotSpec],
    config_model: type[BaseModel],
    category: str,
    result_slot: str | None = None,
    resolve_slots: ResolveSlots | None = None,
    sub_node_slots: dict[str, SubNodeSlotSpec] | None = None,
    sub_node_role: str | None = None,
    resolve_slots_from_sub_node: str | None = None,
    integration: str | None = None,
    capability_group: str | None = None,
    registry: NodeRegistry = default_registry,
) -> Callable:
    """Decorator bundling a node type's input/output/config schema and its
    execution function into one NodeDefinition, registered as an import
    side effect (ARCHITECTURE.md §3).

    `category` (spec-013 §4/§5): required -- which palette section this
    type belongs to. See NodeDefinition's own docstring.

    `result_slot`, if set, names one of this type's own input slots whose
    value the engine should capture into the graph-level result when this
    node executes -- the generic mechanism by which a node type opts into
    being a graph output, without the engine special-casing any type name.

    `resolve_slots`, if set, resolves this type's actual input/output slots
    per graph instance instead of using the fixed `inputs`/`outputs` above --
    for node types whose schema depends on their own config (e.g. `code`).

    `sub_node_slots`/`sub_node_role`/`resolve_slots_from_sub_node` (spec-012
    §4): the cluster-node pattern's registration-time capabilities -- see
    NodeDefinition's own docstrings for each.
    """

    def decorator(execute_fn: Callable) -> Callable:
        registry.register(
            NodeDefinition(
                type_name=type_name,
                inputs=inputs,
                outputs=outputs,
                config_model=config_model,
                execute=execute_fn,
                category=category,
                result_slot=result_slot,
                resolve_slots=resolve_slots,
                sub_node_slots=sub_node_slots,
                sub_node_role=sub_node_role,
                resolve_slots_from_sub_node=resolve_slots_from_sub_node,
                integration=integration,
                capability_group=capability_group,
            )
        )
        return execute_fn

    return decorator

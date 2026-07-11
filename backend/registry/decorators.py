from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from backend.registry.base import (
    InputSlotSpec,
    NodeDefinition,
    NodeRegistry,
    OutputSlotSpec,
    default_registry,
)


def register_node(
    type_name: str,
    inputs: list[InputSlotSpec],
    outputs: list[OutputSlotSpec],
    config_model: type[BaseModel],
    result_slot: str | None = None,
    registry: NodeRegistry = default_registry,
) -> Callable:
    """Decorator bundling a node type's input/output/config schema and its
    execution function into one NodeDefinition, registered as an import
    side effect (ARCHITECTURE.md §3).

    `result_slot`, if set, names one of this type's own input slots whose
    value the engine should capture into the graph-level result when this
    node executes -- the generic mechanism by which a node type opts into
    being a graph output, without the engine special-casing any type name.
    """

    def decorator(execute_fn: Callable) -> Callable:
        registry.register(
            NodeDefinition(
                type_name=type_name,
                inputs=inputs,
                outputs=outputs,
                config_model=config_model,
                execute=execute_fn,
                result_slot=result_slot,
            )
        )
        return execute_fn

    return decorator

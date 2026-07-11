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
    registry: NodeRegistry = default_registry,
) -> Callable:
    """Decorator bundling a node type's input/output/config schema and its
    execution function into one NodeDefinition, registered as an import
    side effect (ARCHITECTURE.md §3)."""

    def decorator(execute_fn: Callable) -> Callable:
        registry.register(
            NodeDefinition(
                type_name=type_name,
                inputs=inputs,
                outputs=outputs,
                config_model=config_model,
                execute=execute_fn,
            )
        )
        return execute_fn

    return decorator

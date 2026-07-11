from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from backend.schema.types import SlotTypeSpec


@dataclass(frozen=True)
class InputSlotSpec:
    name: str
    type: SlotTypeSpec
    required: bool = True


@dataclass(frozen=True)
class OutputSlotSpec:
    name: str
    type: SlotTypeSpec


@dataclass(frozen=True)
class NodeDefinition:
    type_name: str
    inputs: list[InputSlotSpec]
    outputs: list[OutputSlotSpec]
    config_model: type[BaseModel]
    execute: Callable[..., Any]
    result_slot: str | None = None


class NodeRegistry:
    """Plugin-style registry of node types (ARCHITECTURE.md §3).

    A class rather than a bare module-level dict so it's dependency-
    injectable -- validation (and later execution) accept an explicit
    registry instance, defaulting to `default_registry`, which lets tests
    build isolated registries without touching or polluting the real one.
    """

    def __init__(self) -> None:
        self._defs: dict[str, NodeDefinition] = {}

    def register(self, definition: NodeDefinition) -> None:
        if definition.type_name in self._defs:
            raise ValueError(f"Duplicate node type registration: {definition.type_name}")
        if definition.result_slot is not None and not any(
            slot.name == definition.result_slot for slot in definition.inputs
        ):
            raise ValueError(
                f"'{definition.type_name}' declares result_slot="
                f"'{definition.result_slot}' but has no matching input slot"
            )
        self._defs[definition.type_name] = definition

    def get(self, type_name: str) -> NodeDefinition | None:
        return self._defs.get(type_name)

    def all_types(self) -> list[str]:
        return list(self._defs.keys())


default_registry = NodeRegistry()

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel

from backend.schema.models import NodeSpec
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


ResolveSlots = Callable[[NodeSpec], "tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None"]


@dataclass(frozen=True)
class SubNodeSlotSpec:
    """One named sub-node slot a root (cluster) node type declares (spec-012
    §4) -- e.g. `agent` declares `model`/`memory`/`tools`, `webhook_trigger`
    declares `trigger_adapter`. Mirrors the same "capability declared at
    registration, engine stays generic" pattern already used for
    `resolve_slots` (SPEC-002) and connection capabilities like `embed`
    (SPEC-011), applied to node types instead."""

    cardinality: Literal["one", "zero_or_one", "many"]
    accepts_role: str | None = None
    """The `sub_node_role` a connected sub-node's type must declare to be
    valid in this slot (e.g. "model" for the `model` slot). None means any
    node type is accepted -- the `tools` slot's shape, matching SPEC-008's
    existing "any node type can be a tool" precedent."""


@dataclass(frozen=True)
class NodeDefinition:
    type_name: str
    inputs: list[InputSlotSpec]
    outputs: list[OutputSlotSpec]
    config_model: type[BaseModel]
    execute: Callable[..., Any]
    category: str
    """spec-013 §4/§5: which palette section this type belongs to (e.g.
    "triggers", "core", "ai", "data", "connectivity") -- required, not
    optional, so a new node type can't silently end up uncategorized in
    the palette. Canonical lowercase keys, matched against a frontend-side
    {label, color, icon} map (same pattern already established for
    connection `category` values "local"/"cloud", spec-006). The palette
    itself derives its section list from whatever categories are actually
    present across registered types, never a hardcoded list here or on
    the frontend."""
    result_slot: str | None = None
    resolve_slots: ResolveSlots | None = None
    """Optional per-instance schema resolver, for node types whose actual
    input/output slots vary per graph instance (e.g. `code`, whose ports
    depend on each node's own function_source) rather than being fixed for
    the whole type. When set, `inputs`/`outputs` above are ignored in favor
    of calling this with the node; callers should go through
    `effective_inputs`/`effective_outputs` rather than reading `.inputs`/
    `.outputs` directly. Every other (static-schema) node type leaves this
    None and is completely unaffected."""
    sub_node_slots: dict[str, SubNodeSlotSpec] | None = None
    """Root (cluster) node types only (spec-012 §4): the named sub-node
    slots this type declares, e.g. `agent`'s `{"model": ..., "memory": ...,
    "tools": ...}`. None for every non-root type."""
    sub_node_role: str | None = None
    """Sub-node-eligible types only (spec-012 §4): the role this type can
    fill in some root's slot, e.g. `model`'s `"model"`, the trigger
    adapters' `"trigger_adapter"`. None for ordinary node types and for
    root types themselves (a root doesn't plug into another root's slot in
    this spec)."""
    resolve_slots_from_sub_node: str | None = None
    """Root types only, and only when the root's own output ports should
    mirror whichever sub-node is currently connected to one of its slots
    (spec-012 §4) -- e.g. `webhook_trigger`'s outputs are whatever its
    connected `trigger_adapter` declares (`generic_adapter`'s `payload` vs
    `telegram_adapter`'s `message_text`/`sender_id`/`chat_id`). Names the
    slot to mirror. None for every other type, including `agent` (whose own
    `answer` output is fixed regardless of which `model` is connected)."""


def effective_inputs(definition: NodeDefinition, node: NodeSpec) -> list[InputSlotSpec] | None:
    """Static `.inputs` for ordinary node types; for dynamic-schema types,
    calls `resolve_slots` and returns None if it couldn't resolve one (e.g.
    malformed config) -- callers should skip the node in that case and let
    config-schema validation report the real error."""
    if definition.resolve_slots is None:
        return definition.inputs
    resolved = definition.resolve_slots(node)
    return resolved[0] if resolved is not None else None


def effective_outputs(definition: NodeDefinition, node: NodeSpec) -> list[OutputSlotSpec] | None:
    if definition.resolve_slots is None:
        return definition.outputs
    resolved = definition.resolve_slots(node)
    return resolved[1] if resolved is not None else None


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
        if (
            definition.result_slot is not None
            and definition.resolve_slots is None
            and not any(slot.name == definition.result_slot for slot in definition.inputs)
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

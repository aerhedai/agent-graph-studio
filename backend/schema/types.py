from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SlotType(str, Enum):
    TEXT = "text"
    JSON = "json"
    FILE_REF = "file_ref"
    EMBEDDING = "embedding"
    IMAGE = "image"
    BOOLEAN = "boolean"
    LIST = "list"


class SlotTypeSpec(BaseModel):
    """Describes the type of a node's input or output slot.

    `element_type` is only meaningful when `base` is `SlotType.LIST` (e.g.
    `list<text>`). Compatibility is exact-match for the MVP node set (all
    four node types are text-only); this is the intended extension point for
    coercion rules (e.g. json -> text) in a future spec, without touching
    the validator or engine.
    """

    base: SlotType
    element_type: Optional["SlotTypeSpec"] = None

    def is_compatible_with(self, other: "SlotTypeSpec") -> bool:
        return self == other


SlotTypeSpec.model_rebuild()

TEXT = SlotTypeSpec(base=SlotType.TEXT)

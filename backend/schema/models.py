from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EdgeEndpoint(BaseModel):
    node: str
    slot: str | None = None
    """None only valid on a `sub_node`-kind edge -- a sub-node edge names
    the root's slot via EdgeSpec's own top-level `slot` field instead,
    since a sub-node isn't a normal typed output/input port (spec-012 §4)."""


class EdgeSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["data", "sub_node"] = "data"
    """Defaults to "data" for backward compatibility with every graph from
    SPEC-001-011, none of which carry this field at all (spec-012 §5)."""
    from_: EdgeEndpoint = Field(alias="from")
    to: EdgeEndpoint
    slot: str | None = None
    """sub_node edges only: which of `to`'s declared sub-node slots this
    fills (e.g. "model", "tools"). Unused (must be None) for data edges,
    which name their slots via from.slot/to.slot instead."""

    @model_validator(mode="after")
    def _check_shape_matches_kind(self) -> "EdgeSpec":
        if self.kind == "data":
            if self.from_.slot is None or self.to.slot is None:
                raise ValueError("a 'data' edge requires both from.slot and to.slot")
            if self.slot is not None:
                raise ValueError("a 'data' edge must not set the top-level 'slot' field")
        else:  # sub_node
            if self.slot is None:
                raise ValueError("a 'sub_node' edge requires a top-level 'slot' naming the root's slot")
            if self.from_.slot is not None or self.to.slot is not None:
                raise ValueError("a 'sub_node' edge must not set from.slot or to.slot")
        return self


class NodeSpec(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class GraphSpec(BaseModel):
    version: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_node_ids(self) -> "GraphSpec":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                duplicates.add(node.id)
            seen.add(node.id)
        if duplicates:
            raise ValueError(f"duplicate node id(s): {sorted(duplicates)}")
        return self

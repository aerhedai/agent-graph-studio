from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EdgeEndpoint(BaseModel):
    node: str
    slot: str


class EdgeSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: EdgeEndpoint = Field(alias="from")
    to: EdgeEndpoint


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

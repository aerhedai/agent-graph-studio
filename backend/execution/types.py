from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.execution.trace import TokenCost
from backend.schema.models import NodeSpec


@dataclass
class NodeResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    token_cost: TokenCost = field(default_factory=TokenCost)


@dataclass
class ExecutionContext:
    """`resources` is a generic, caller-populated bag (e.g. {"llm_client": ...})
    that the engine passes through unchanged to every node -- it has no
    knowledge of what any given node type needs. Each node's execute() body
    looks up what it needs by key and falls back to its own default
    construction if absent."""

    node: NodeSpec
    inputs: dict[str, Any]
    resources: dict[str, Any] = field(default_factory=dict)

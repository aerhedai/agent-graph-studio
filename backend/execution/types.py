from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.execution.trace import TokenCost
from backend.llm.client import LLMClient
from backend.schema.models import NodeSpec


@dataclass
class NodeResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    token_cost: TokenCost = field(default_factory=TokenCost)


@dataclass
class ExecutionContext:
    node: NodeSpec
    inputs: dict[str, Any]
    llm_client: LLMClient | None = None

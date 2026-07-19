"""`memory` node type: holds an agent's conversation-window settings
(spec-012 §4), pulled out of `agent`'s own inline config (SPEC-008) into
its own pluggable sub-node -- connectable via a `sub_node` edge to any root
node type declaring a `memory`-role slot. `agent` treats this slot as
zero-or-one: if no `memory` sub-node is connected, no windowing happens at
all (the full conversation is kept) -- the sensible default for "no memory
sub-node wired in."

Never directly executed by the engine, same as `model` -- see model.py's
docstring for why.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.decorators import register_node


class MemoryConfig(BaseModel):
    type: Literal["window"] = "window"
    max_messages: int = Field(gt=0, default=20)


@register_node(
    "memory",
    inputs=[],
    outputs=[],
    config_model=MemoryConfig,
    category="ai",
    sub_node_role="memory",
)
def execute_memory(ctx: ExecutionContext) -> NodeResult:
    raise NodeExecutionError(
        "'memory' is a sub-node type -- it should never be executed directly by "
        "the engine; this indicates a scheduling bug, not a graph authoring error"
    )

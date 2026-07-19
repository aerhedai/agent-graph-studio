"""`model` node type: holds a model connection's settings (connection name,
model name, system prompt, max tokens) -- spec-012 §4, pulled out of
`agent`'s own inline config (SPEC-008) into its own reusable, pluggable
sub-node. Connectable to any root node type declaring a `model`-role
sub-node slot via a `sub_node` edge; `agent` is the only consumer today,
but nothing here is agent-specific.

Never directly executed by the engine -- excluded from the round-based
scheduler's `pending` set (backend/execution/engine.py) exactly like every
other sub-node-role type, since it's a pure config carrier, read directly
by its root's own execute() via ctx.resources["sub_nodes"] + nodes_by_id.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.decorators import register_node


class ModelConfig(BaseModel):
    connection: str
    model: str
    system_prompt: str = ""
    max_tokens: int = Field(gt=0)


@register_node(
    "model",
    inputs=[],
    outputs=[],
    config_model=ModelConfig,
    category="ai",
    sub_node_role="model",
)
def execute_model(ctx: ExecutionContext) -> NodeResult:
    raise NodeExecutionError(
        "'model' is a sub-node type -- it should never be executed directly by "
        "the engine; this indicates a scheduling bug, not a graph authoring error"
    )

"""`tool_group` node type: a pure structural container an `agent` connects
to as its one `tools` sub-node, which itself holds any number of real tool
nodes -- spec-014, replacing spec-008/012's "any node type can be wired
directly into agent's tools slot" (agent.tools is now `cardinality="one",
accepts_role="tool_group"`; a group's own `tools` slot keeps the old
permissive `accepts_role=None` one level down).

The first "hybrid" node type in the registry: simultaneously a root (it
declares its own `sub_node_slots`) and a sub-node (it declares its own
`sub_node_role`). Nothing else needs to change to support this -- both
`NodeDefinition` fields are independent and already optional.

Never directly executed by the engine -- excluded from the round-based
scheduler's `pending` set (backend/execution/engine.py) exactly like every
other sub-node-role type, since it's a pure structural carrier with no
config and no behavior of its own; `agent.py` reads straight through it via
ctx.resources["sub_nodes"] to reach the real tool node ids.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import SubNodeSlotSpec
from backend.registry.decorators import register_node


class ToolGroupConfig(BaseModel):
    pass


@register_node(
    "tool_group",
    inputs=[],
    outputs=[],
    config_model=ToolGroupConfig,
    category="tools",
    sub_node_slots={
        "tools": SubNodeSlotSpec(cardinality="many", accepts_role=None),
    },
    sub_node_role="tool_group",
)
def execute_tool_group(ctx: ExecutionContext) -> NodeResult:
    raise NodeExecutionError(
        "'tool_group' is a sub-node type -- it should never be executed directly by "
        "the engine; this indicates a scheduling bug, not a graph authoring error"
    )

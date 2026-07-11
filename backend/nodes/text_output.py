from __future__ import annotations

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class TextOutputConfig(BaseModel):
    pass


@register_node(
    "text_output",
    inputs=[InputSlotSpec("text", TEXT)],
    outputs=[],
    config_model=TextOutputConfig,
    result_slot="text",
)
def execute_text_output(ctx: ExecutionContext) -> NodeResult:
    TextOutputConfig.model_validate(ctx.node.config)
    return NodeResult(outputs={})

from __future__ import annotations

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class TextInputConfig(BaseModel):
    value: str


@register_node(
    "text_input",
    inputs=[],
    outputs=[OutputSlotSpec("text", TEXT)],
    config_model=TextInputConfig,
    category="core",
)
def execute_text_input(ctx: ExecutionContext) -> NodeResult:
    config = TextInputConfig.model_validate(ctx.node.config)
    return NodeResult(outputs={"text": config.value})

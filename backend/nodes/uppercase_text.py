from __future__ import annotations

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class UppercaseTextConfig(BaseModel):
    pass


@register_node(
    "uppercase_text",
    inputs=[InputSlotSpec("text", TEXT)],
    outputs=[OutputSlotSpec("text", TEXT)],
    config_model=UppercaseTextConfig,
    category="core",
)
def execute_uppercase_text(ctx: ExecutionContext) -> NodeResult:
    UppercaseTextConfig.model_validate(ctx.node.config)
    return NodeResult(outputs={"text": ctx.inputs["text"].upper()})

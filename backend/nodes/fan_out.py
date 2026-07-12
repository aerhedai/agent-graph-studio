"""`fan_out` node: splits one input into `worker_count` parallel branches
(ARCHITECTURE.md §5, spec-004). Concurrency needs no special-casing here or
in the engine -- fan_out's N branch_N outputs all become available in the
same scheduling round, so the engine's generic layered scheduler runs every
downstream branch-consumer node in that round concurrently, for free.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class FanOutConfig(BaseModel):
    worker_count: int = Field(gt=0)


def _resolve_fanout_slots(
    node: NodeSpec,
) -> tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None:
    try:
        config = FanOutConfig.model_validate(node.config)
    except Exception:
        return None
    inputs = [InputSlotSpec("value", TEXT)]
    outputs = [OutputSlotSpec(f"branch_{i + 1}", TEXT) for i in range(config.worker_count)]
    return inputs, outputs


@register_node(
    "fan_out",
    inputs=[],
    outputs=[],
    config_model=FanOutConfig,
    resolve_slots=_resolve_fanout_slots,
)
def execute_fan_out(ctx: ExecutionContext) -> NodeResult:
    config = FanOutConfig.model_validate(ctx.node.config)
    value = ctx.inputs["value"]
    outputs = {f"branch_{i + 1}": value for i in range(config.worker_count)}
    return NodeResult(outputs=outputs)

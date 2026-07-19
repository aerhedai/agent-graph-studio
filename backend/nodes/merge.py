"""`merge` node: blocks until `expected_input_count` branches have all
completed, then produces one combined output in index order (ARCHITECTURE.md
§5, spec-004).

If any upstream branch fails, merge's corresponding input slot never arrives
-- the engine's existing generic skip mechanism (unchanged since SPEC-001)
means merge is simply never invoked at all in that case: no trace record, no
partial/wrong result, ever. The actual failure is clearly visible on the
failing branch's own trace record, exactly like every other failure-
propagation case in this system. No merge-specific error bookkeeping needed.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class MergeConfig(BaseModel):
    expected_input_count: int = Field(gt=0)


def _resolve_merge_slots(
    node: NodeSpec,
) -> tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None:
    try:
        config = MergeConfig.model_validate(node.config)
    except Exception:
        return None
    inputs = [InputSlotSpec(f"input_{i + 1}", TEXT) for i in range(config.expected_input_count)]
    outputs = [OutputSlotSpec("result", TEXT)]
    return inputs, outputs


@register_node(
    "merge",
    inputs=[],
    outputs=[],
    config_model=MergeConfig,
    category="core",
    resolve_slots=_resolve_merge_slots,
)
def execute_merge(ctx: ExecutionContext) -> NodeResult:
    config = MergeConfig.model_validate(ctx.node.config)
    # Index order, not completion order, per spec-004 §5's own stated
    # preference -- simpler and more predictable.
    values = [ctx.inputs[f"input_{i + 1}"] for i in range(config.expected_input_count)]
    return NodeResult(outputs={"result": json.dumps(values)})

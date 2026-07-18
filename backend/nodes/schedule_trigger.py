"""`schedule_trigger` node: a zero-input entry point that fires its
containing graph on a cron-style interval once activated (spec-009 §3/§4).

An ordinary node type in every other respect -- it declares zero required
inputs, so the existing round-based scheduler (spec-004) treats it as
"ready" in the very first round with no engine changes at all. `execute()`
always succeeds and produces a fresh timestamp regardless of *how* the
graph run was started (a real scheduler tick, or a manual POST /runs) --
this is what lets trigger-based and manual invocation coexist cleanly
(spec-009 §6).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class ScheduleTriggerConfig(BaseModel):
    cron: str


@register_node(
    "schedule_trigger",
    inputs=[],
    outputs=[OutputSlotSpec("fired_at", TEXT)],
    config_model=ScheduleTriggerConfig,
)
def execute_schedule_trigger(ctx: ExecutionContext) -> NodeResult:
    return NodeResult(outputs={"fired_at": datetime.now(timezone.utc).isoformat()})

"""`generic_adapter` node type: the passthrough trigger-adapter behavior --
the webhook POST body becomes the `payload` output, unchanged from
SPEC-009's original `webhook_trigger`. Moved here verbatim as part of
spec-012's cluster-node generalization: `webhook_trigger` is now a root
node whose actual parsing behavior is delegated entirely to whichever
`trigger_adapter` sub-node is connected to it.

Never scheduled/executed by the engine's own round-based scheduler --
invoked directly by `webhook_trigger`'s own execute(), the same
ADR-008-style direct-call bypass every sub-node in this spec uses. Unlike
`model`/`memory` (pure inert config, never actually invoked), this is the
one sub-node kind (trigger adapters) that *is* actually invoked --
mirroring how an `agent` invokes its tool nodes directly.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class GenericAdapterConfig(BaseModel):
    pass


@register_node(
    "generic_adapter",
    inputs=[InputSlotSpec("payload", TEXT)],
    outputs=[OutputSlotSpec("payload", TEXT)],
    config_model=GenericAdapterConfig,
    category="triggers",
    sub_node_role="trigger_adapter",
)
def execute_generic_adapter(ctx: ExecutionContext) -> NodeResult:
    return NodeResult(outputs={"payload": json.dumps(ctx.inputs["payload"])})

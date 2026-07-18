"""`webhook_trigger` node: a zero-input entry point that fires its
containing graph when its derived webhook URL is POSTed to, once activated
(spec-009 §3/§4).

Implementation note (deviation from spec §5's literal "payload (json)"
wording, forced by an existing constraint, not a silent scope change):
every node type registered so far (code, llm_call, mcp_call, ...) is
TEXT-only -- `SlotType.JSON` exists in the enum but has zero real usage
anywhere, and slot-type compatibility is exact-match with no coercion
(schema/types.py explicitly defers json->text coercion to "a future
spec"). A strictly JSON-typed `payload` output could never connect to any
node type that exists today, which would make this spec's own "confirm the
POST body reaches a downstream node" acceptance criterion undemonstrable.
`payload` is therefore a TEXT slot carrying the JSON-serialized request
body -- same information, wire-compatible with every existing node
(a `code` node can `json.loads()` it), and revisit only once a real
JSON-consuming node type exists.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class WebhookTriggerConfig(BaseModel):
    pass


@register_node(
    "webhook_trigger",
    inputs=[],
    outputs=[OutputSlotSpec("payload", TEXT)],
    config_model=WebhookTriggerConfig,
)
def execute_webhook_trigger(ctx: ExecutionContext) -> NodeResult:
    payload = ctx.resources.get("trigger_payloads", {}).get(ctx.node.id, {})
    return NodeResult(outputs={"payload": json.dumps(payload)})

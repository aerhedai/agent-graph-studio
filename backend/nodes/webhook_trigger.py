"""`webhook_trigger` node: a zero-input entry point that fires its
containing graph when its derived webhook URL is POSTed to, once activated
(spec-009 §3/§4).

spec-012: now a cluster root node whose actual parsing behavior is
delegated entirely to whichever `trigger_adapter` sub-node is connected to
its one `trigger_adapter` slot (cardinality "one") -- `generic_adapter`
(today's original passthrough behavior, unchanged) or `telegram_adapter`
(structured Telegram parsing), or any future adapter type tagging itself
with the same `sub_node_role="trigger_adapter"`, with zero changes needed
here. `webhook_trigger`'s own output ports mirror whichever adapter is
connected (`resolve_slots_from_sub_node="trigger_adapter"`) -- resolved
graph-aware in validation (backend/validation/rules.py's
`_effective_outputs_for_root`) and client-side in the canvas (both
adapters are ordinary static-schema types, so the canvas needs no new
backend endpoint to know their ports).
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import SubNodeSlotSpec
from backend.registry.base import default_registry as default_node_registry
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec


class WebhookTriggerConfig(BaseModel):
    pass


@register_node(
    "webhook_trigger",
    inputs=[],
    outputs=[],
    config_model=WebhookTriggerConfig,
    category="triggers",
    sub_node_slots={
        "trigger_adapter": SubNodeSlotSpec(cardinality="one", accepts_role="trigger_adapter"),
    },
    resolve_slots_from_sub_node="trigger_adapter",
)
def execute_webhook_trigger(ctx: ExecutionContext) -> NodeResult:
    sub_nodes: dict[tuple[str, str], list[str]] = ctx.resources.get("sub_nodes", {})
    adapter_ids = sub_nodes.get((ctx.node.id, "trigger_adapter"), [])
    if len(adapter_ids) != 1:
        # Defensive only -- validate_graph()'s check_sub_node_edges already
        # guarantees exactly one connected trigger_adapter before a run
        # ever starts (cardinality="one"), same precedent as agent's model
        # slot.
        raise NodeExecutionError(
            f"webhook_trigger '{ctx.node.id}' has {len(adapter_ids)} connected "
            "'trigger_adapter' sub-nodes, expected exactly 1"
        )

    nodes_by_id: dict[str, NodeSpec] = ctx.resources.get("nodes_by_id", {})
    adapter_node = nodes_by_id[adapter_ids[0]]
    adapter_definition = default_node_registry.get(adapter_node.type)
    if adapter_definition is None:
        raise NodeExecutionError(
            f"trigger_adapter '{adapter_node.id}' has unregistered type '{adapter_node.type}'"
        )

    payload = ctx.resources.get("trigger_payloads", {}).get(ctx.node.id, {})
    adapter_ctx = ExecutionContext(node=adapter_node, inputs={"payload": payload}, resources=ctx.resources)
    return adapter_definition.execute(adapter_ctx)

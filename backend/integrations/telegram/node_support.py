"""Shared dynamic-schema resolution and execution logic for
`telegram_messaging`/`telegram_chat_management` (backend/nodes/telegram_*.py)
-- both node types are "one node per capability group, an `action` config
field selects the specific manifest method" (spec-019 §4), differing only in
which slice of backend/integrations/telegram/manifest.py they draw from.
Shared here rather than duplicated across the two node modules.
"""

from __future__ import annotations

import json
from typing import Callable

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.integrations.telegram.api import call_telegram_api
from backend.integrations.telegram.manifest import TelegramMethodSpec
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


def _find(methods: tuple[TelegramMethodSpec, ...], action: str | None) -> TelegramMethodSpec | None:
    return next((m for m in methods if m.action == action), None)


def resolve_slots_for(
    methods: tuple[TelegramMethodSpec, ...],
) -> Callable[[NodeSpec], "tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None"]:
    def resolve(node: NodeSpec) -> tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None:
        method = _find(methods, node.config.get("action"))
        if method is None:
            return None
        inputs = [InputSlotSpec(p, TEXT, required=True) for p in method.required_params]
        inputs += [InputSlotSpec(p, TEXT, required=False) for p in method.optional_params]
        return inputs, [OutputSlotSpec("result", TEXT)]

    return resolve


def execute_telegram_action(ctx: ExecutionContext, methods: tuple[TelegramMethodSpec, ...]) -> NodeResult:
    action = ctx.node.config.get("action")
    method = _find(methods, action)
    if method is None:
        raise NodeExecutionError(f"Unknown Telegram action: {action!r}")

    connection_name = ctx.node.config.get("bot_token_connection")
    token = ctx.resources.get("connections", {}).get(connection_name)
    if not isinstance(token, str):
        raise NodeExecutionError(
            f"telegram node references unresolved bot_token_connection {connection_name!r}"
        )

    # An unwired optional param is simply omitted -- Telegram applies its
    # own default, same convention as mcp_call's optional params.
    params = {name: ctx.inputs[name] for name in method.param_names if name in ctx.inputs}

    try:
        body = call_telegram_api(token, method.telegram_method, params)
    except RuntimeError as e:
        raise NodeExecutionError(f"Telegram '{method.telegram_method}' failed: {e}") from e

    return NodeResult(outputs={"result": json.dumps(body.get("result", body))}, side_effect=True)

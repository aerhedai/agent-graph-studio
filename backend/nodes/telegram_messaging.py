"""`telegram_messaging` node type (spec-019 §3): sends/edits/deletes
messages via Telegram's Bot API -- the manifest fallback's "can it reply"
proving case (Telegram has no official MCP server, so this doesn't go
through spec-019's dynamic MCP path). One node, an `action` config field
selecting which of Telegram's messaging methods to call
(backend/integrations/telegram/manifest.py's MESSAGING_METHODS),
dynamically resolving its own ports per selected action -- the same
resolve_slots mechanism `code`/`mcp_call` already use.

Any registered node type can already be used as an agent tool (ADR-008/
spec-014) with zero special-casing -- dropping this node (configured to
send_message) into a tool_group under an agent, with chat_id wired from
`telegram_adapter`'s output, is what lets an agent reply into the chat it
received a message from.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.integrations.telegram.manifest import MESSAGING_METHODS
from backend.integrations.telegram.node_support import execute_telegram_action, resolve_slots_for
from backend.registry.decorators import register_node


class TelegramMessagingConfig(BaseModel):
    bot_token_connection: str
    action: Literal["send_message", "send_photo", "send_document", "edit_message", "delete_message"]


@register_node(
    "telegram_messaging",
    inputs=[],
    outputs=[],
    config_model=TelegramMessagingConfig,
    category="apps",
    resolve_slots=resolve_slots_for(MESSAGING_METHODS),
    integration="telegram",
    capability_group="Messaging",
)
def execute_telegram_messaging(ctx: ExecutionContext) -> NodeResult:
    return execute_telegram_action(ctx, MESSAGING_METHODS)

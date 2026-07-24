"""`telegram_chat_management` node type (spec-019 §3): reads chat/chat-member
info via Telegram's Bot API. Same dynamic-schema, action-selecting shape as
`telegram_messaging` -- see that module's docstring for the pattern; this
one draws from CHAT_MANAGEMENT_METHODS instead of MESSAGING_METHODS.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.execution.types import ExecutionContext, NodeResult
from backend.integrations.telegram.manifest import CHAT_MANAGEMENT_METHODS
from backend.integrations.telegram.node_support import execute_telegram_action, resolve_slots_for
from backend.registry.decorators import register_node


class TelegramChatManagementConfig(BaseModel):
    bot_token_connection: str
    action: Literal["get_chat", "get_chat_member"]


@register_node(
    "telegram_chat_management",
    inputs=[],
    outputs=[],
    config_model=TelegramChatManagementConfig,
    category="apps",
    resolve_slots=resolve_slots_for(CHAT_MANAGEMENT_METHODS),
    integration="telegram",
    capability_group="Chat management",
)
def execute_telegram_chat_management(ctx: ExecutionContext) -> NodeResult:
    return execute_telegram_action(ctx, CHAT_MANAGEMENT_METHODS)

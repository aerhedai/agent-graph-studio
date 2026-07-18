"""`telegram_adapter` node type: parses a real Telegram Bot API webhook
payload (an "Update" object) into clean, structured outputs instead of raw
JSON -- spec-012 §4/§5.

Telegram's webhook body shape (the relevant subset, per their Bot API docs):
{
  "update_id": 123456789,
  "message": {
    "message_id": 1,
    "from": {"id": 111222333, "is_bot": false, "first_name": "Ada"},
    "chat": {"id": 111222333, "type": "private"},
    "date": 1234567890,
    "text": "hello bot"
  }
}

`bot_token_connection` is not used by parsing itself (no authentication
needed to read an already-delivered webhook body) -- kept in config for a
real future "send a reply back" use, explicitly deferred by this spec
(§3). Never scheduled/executed by the engine directly -- see
generic_adapter.py's docstring for why.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class TelegramAdapterConfig(BaseModel):
    bot_token_connection: str


@register_node(
    "telegram_adapter",
    inputs=[InputSlotSpec("payload", TEXT)],
    outputs=[
        OutputSlotSpec("message_text", TEXT),
        OutputSlotSpec("sender_id", TEXT),
        OutputSlotSpec("chat_id", TEXT),
    ],
    config_model=TelegramAdapterConfig,
    sub_node_role="trigger_adapter",
)
def execute_telegram_adapter(ctx: ExecutionContext) -> NodeResult:
    payload = ctx.inputs["payload"]
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        raise NodeExecutionError(
            "telegram_adapter expected a Telegram 'Update' payload with a 'message' field, "
            f"got: {payload!r}"
        )
    try:
        message_text = str(message["text"])
        sender_id = str(message["from"]["id"])
        chat_id = str(message["chat"]["id"])
    except KeyError as e:
        raise NodeExecutionError(f"telegram_adapter payload missing expected field: {e}") from e
    return NodeResult(
        outputs={"message_text": message_text, "sender_id": sender_id, "chat_id": chat_id}
    )

"""spec-019 §3: a declarative slice of Telegram's Bot API -- proposed and
resolved as a starting slice in the spec's own open questions (§6). Adding
a further Telegram method later (e.g. send_video) is a new entry here, not
new node-execution code -- both `telegram_messaging` and
`telegram_chat_management` (backend/nodes/telegram_*.py) read this table
generically.

Every method call is a plain GET-style request with query params
(Telegram's Bot API accepts this for all the methods below) -- see
backend/integrations/telegram/api.py's call_telegram_api. File-carrying
params (`photo`, `document`) accept a URL or a Telegram file_id as a plain
string, not a binary upload -- consistent with this project's TEXT-only
slot types (backend/schema/types.py); real multipart upload is future
work, not attempted here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramMethodSpec:
    action: str
    """The snake_case name this project's nodes expose as a config `action`
    value -- distinct from Telegram's own camelCase method name below, to
    stay consistent with this codebase's naming conventions elsewhere."""
    telegram_method: str
    """Telegram's actual Bot API method name, e.g. "sendMessage"."""
    capability_group: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()

    @property
    def param_names(self) -> tuple[str, ...]:
        return self.required_params + self.optional_params


MESSAGING_METHODS: tuple[TelegramMethodSpec, ...] = (
    TelegramMethodSpec(
        action="send_message",
        telegram_method="sendMessage",
        capability_group="Messaging",
        required_params=("chat_id", "text"),
        optional_params=("parse_mode",),
    ),
    TelegramMethodSpec(
        action="send_photo",
        telegram_method="sendPhoto",
        capability_group="Messaging",
        required_params=("chat_id", "photo"),
        optional_params=("caption",),
    ),
    TelegramMethodSpec(
        action="send_document",
        telegram_method="sendDocument",
        capability_group="Messaging",
        required_params=("chat_id", "document"),
        optional_params=("caption",),
    ),
    TelegramMethodSpec(
        action="edit_message",
        telegram_method="editMessageText",
        capability_group="Messaging",
        required_params=("chat_id", "message_id", "text"),
    ),
    TelegramMethodSpec(
        action="delete_message",
        telegram_method="deleteMessage",
        capability_group="Messaging",
        required_params=("chat_id", "message_id"),
    ),
)

CHAT_MANAGEMENT_METHODS: tuple[TelegramMethodSpec, ...] = (
    TelegramMethodSpec(
        action="get_chat",
        telegram_method="getChat",
        capability_group="Chat management",
        required_params=("chat_id",),
    ),
    TelegramMethodSpec(
        action="get_chat_member",
        telegram_method="getChatMember",
        capability_group="Chat management",
        required_params=("chat_id", "user_id"),
    ),
)

ALL_METHODS: tuple[TelegramMethodSpec, ...] = MESSAGING_METHODS + CHAT_MANAGEMENT_METHODS
_BY_ACTION: dict[str, TelegramMethodSpec] = {m.action: m for m in ALL_METHODS}


def get_method(action: str) -> TelegramMethodSpec | None:
    return _BY_ACTION.get(action)

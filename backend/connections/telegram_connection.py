"""The `telegram` connection type -- holds a Telegram Bot API token,
referenced by `telegram_adapter.bot_token_connection` (spec-012 §5). Not
yet used for anything beyond being a referenceable, validated connection
profile: sending replies via Telegram's own API is explicitly deferred
(spec-012 §3), so `test_connection` only confirms a token is present, not
that it's genuinely valid against Telegram's API -- a real network check
is real, deferrable follow-up work once send-reply is actually built.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type


class TelegramConnectionConfig(BaseModel):
    bot_token: str


def build_client(config: TelegramConnectionConfig) -> str:
    return config.bot_token


def test_connection(config: TelegramConnectionConfig) -> ConnectionTestResult:
    if not config.bot_token:
        return ConnectionTestResult(success=False, message="bot_token is empty")
    return ConnectionTestResult(
        success=True,
        message="Bot token configured (not yet verified against Telegram's API -- "
        "send-reply support is deferred)",
    )


register_connection_type(
    "telegram",
    category="cloud",
    config_model=TelegramConnectionConfig,
    build_client=build_client,
    test_connection=test_connection,
)

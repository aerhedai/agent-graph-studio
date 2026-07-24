"""Importing this package registers Telegram's webhook-sync handler
(backend/triggers/webhook_sync.py) as an import side effect."""

from backend.integrations.telegram import webhook_sync  # noqa: F401

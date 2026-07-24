"""Importing this package registers all v1 connection types into the default
connection registry.

Adding a new connection type means creating a new module here (declaring its
config schema, build_client, and test_connection via
register_connection_type) and importing it below -- no changes needed to
base.py, resolver.py, the API layer, or the frontend connection picker.
"""

from backend.connections import (  # noqa: F401
    anthropic_connection,
    mcp_server_connection,
    ollama_connection,
    telegram_connection,
    vector_store_connection,
)

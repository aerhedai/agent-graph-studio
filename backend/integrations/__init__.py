"""spec-019: hand-authored manifest-backed app integrations -- the
deliberate fallback for apps needing first-party reliability where no
trustworthy MCP server exists (Telegram, concretely). See
docs/specs/019-app-integrations.md §2 for why this coexists with, rather
than is replaced by, backend/mcp/generated_nodes.py's dynamic path.

Importing this package registers every integration's webhook-sync handler
(if any) as an import side effect -- mirrors backend/connections/__init__.py
and backend/nodes/__init__.py's own "import the package, get every
registration for free" pattern."""

from backend.integrations import telegram  # noqa: F401

"""spec-030: a small, source-controlled catalog of known apps -- turns "add
a new app connection" from filling out the full generic `mcp_server` form
from scratch into picking a known app and only supplying what's genuinely
admin-specific (an OAuth client's own ID/secret; a self-hosted server's own
URL).

Deliberately a plain Python list, not a database table -- same "code, not
runtime-mutable state" posture this project already uses for connection-type
registration (`register_connection_type`). An admin who wants to add or edit
an entry edits this file; it ships on the next deploy.

Every entry here is something this deployment has actually, genuinely
proven works -- not researched-but-untried:
- Gmail: Google's own official, publicly-hosted Gmail MCP server. Its real
  server URL and OAuth scope are used here, live-verified directly
  (`curl` against https://gmailmcp.googleapis.com/mcp/v1's real PRM
  discovery endpoint and a real MCP `initialize` call, both succeeding)
  while planning this spec -- not the private, self-hosted gateway URL
  this deployment's own `my-gmail` connection happens to use, which would
  be meaningless to any other self-hoster.
- Context7: a public, no-credential-required MCP server, live-verified per
  docs/specs/025-app-integration-catalog.md's own §8 implementation notes.
- Discord: via `discord-mcp-server/` (already in this repo root), a small
  standalone server purpose-built to prove spec-021's OAuth mechanism
  against a real, non-Google provider. Unlike Gmail/Context7, there is no
  single public server for Discord -- every admin who wants it self-hosts
  their own copy first (see that project's own README.md), so this entry
  has no pre-filled `server_url`, only a pre-filled credential_type/scope
  and a pointer to that setup doc.

A catalog entry never stores or implies a secret -- `oauth_client_id`/
`oauth_client_secret`/an API key are always entered fresh by the admin at
add-time. All three entries here use transport "remote" (a URL-based MCP
server); that's applied by the caller (the API route / frontend pre-fill),
not stored as a field here, since every current entry is the same value --
a future stdio/command-based entry would need this model extended then,
not preemptively now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AppCatalogEntry:
    key: str
    display_name: str
    description: str
    category: str
    """Matches ConnectionTypeInfo.category ("cloud" | "local")."""
    credential_type: str | None
    auth_type: Literal["oauth2", "api_key", "bearer"]
    server_url: str | None
    """Pre-filled when the app has one stable, public address (Gmail,
    Context7). None when every admin must self-host their own instance
    (Discord) -- the add-flow prompts for it instead, guided by
    `setup_instructions`."""
    default_scope: str | None
    requires_oauth: bool
    setup_instructions: str | None
    """Shown when server_url is None, or when the admin still needs to do
    real setup work (e.g. registering an OAuth client) before this entry
    is actually usable."""


CATALOG: list[AppCatalogEntry] = [
    AppCatalogEntry(
        key="gmail",
        display_name="Gmail",
        description="Read, send, and manage email through your Google account.",
        category="cloud",
        credential_type="google_gmail_oauth2",
        auth_type="oauth2",
        server_url="https://gmailmcp.googleapis.com/mcp/v1",
        default_scope=(
            "openid https://www.googleapis.com/auth/userinfo.email "
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.labels"
        ),
        requires_oauth=True,
        setup_instructions=(
            "You'll need a Google Cloud OAuth client with the Gmail API enabled "
            "(console.cloud.google.com/apis/credentials). Add "
            "{your public base URL}/connections/oauth/callback as an authorized redirect URI."
        ),
    ),
    AppCatalogEntry(
        key="context7",
        display_name="Context7",
        description="Up-to-date documentation lookup for libraries and frameworks.",
        category="cloud",
        credential_type=None,
        auth_type="oauth2",
        server_url="https://mcp.context7.com/mcp",
        default_scope=None,
        requires_oauth=False,
        setup_instructions=None,
    ),
    AppCatalogEntry(
        key="discord",
        display_name="Discord",
        description="Send messages to a Discord channel via an incoming webhook.",
        category="cloud",
        credential_type="discord_webhook_oauth2",
        auth_type="oauth2",
        server_url=None,
        default_scope="webhook.incoming",
        requires_oauth=True,
        setup_instructions=(
            "Requires self-hosting discord-mcp-server (included in this repo's root) and "
            "registering a Discord application with an OAuth2 redirect of "
            "{your public base URL}/connections/oauth/callback -- see discord-mcp-server/README.md."
        ),
    ),
]

"""A small, standalone, genuinely MCP-spec-conformant Discord MCP server --
proving the *generic* per-user OAuth mechanism built in agent-graph-studio
(backend/mcp/oauth_flow.py, SPEC-021) against a real, spec-correct server,
not a mocked one. Exactly one tool (`send_message`), backed by Discord's
`webhook.incoming` OAuth scope: a server admin authorizes a send-only
webhook for one specific channel -- no bot, no shared app-wide secret,
genuinely per-user/per-channel, matching this project's own confirmed
Discord-mechanism decision (see docs/specs/021-per-user-app-connections.md).

Why this is its own tiny server, not code inside agent-graph-studio's own
backend: proving the OAuth mechanism is generic means it must never
special-case Discord. This server is external, reached only over HTTP,
exactly like a real user's own third-party MCP server would be.

Architecture note on the OAuth wrapping -- read before changing anything:
Discord doesn't host RFC 8414 Authorization Server Metadata (no real
`/.well-known/oauth-authorization-server` of its own), so this server acts
as a thin, spec-conformant metadata host in front of Discord's real
endpoints. The metadata this server serves points `authorization_endpoint`
directly at Discord's real consent screen (the browser goes there
directly -- no proxy needed for that leg) but `token_endpoint` back at this
server's own `/token`, which proxies the exchange to Discord's real token
endpoint. Discord's `webhook.incoming` grant response includes the actual
usable credential (a webhook `url`) as an extra field alongside the
standard OAuth2 response body -- a field a fully-generic OAuth client
(agent-graph-studio's own oauth_flow.py) never looks at or depends on.
This server is the one place that legitimately reads and persists that
Discord-specific detail, keyed by a fresh, opaque access token of its own
minting, handed back in a completely standard-shaped OAuth2 token
response. From agent-graph-studio's perspective this is just an ordinary,
non-expiring access token -- nothing Discord-specific about it.
"""

from __future__ import annotations

import contextvars
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"


def _base_url() -> str:
    """The real, externally-reachable URL this server is running at (e.g.
    a Tailscale Funnel URL) -- used to build its own OAuth metadata (the
    `resource`/`issuer`/`token_endpoint` fields must be real, dereferenceable
    URLs, not localhost)."""
    override = os.environ.get("DISCORD_MCP_PUBLIC_BASE_URL")
    if not override:
        raise RuntimeError(
            "DISCORD_MCP_PUBLIC_BASE_URL must be set -- the real, externally-reachable "
            "URL this server is running at, used to build its own OAuth metadata."
        )
    return override.rstrip("/")


# --- token/webhook store (SQLite, mirrors agent-graph-studio's own storage
# conventions -- env-var-overridable path, short-lived per-call connections) ---


def _db_path() -> Path:
    override = os.environ.get("DISCORD_MCP_DB_PATH")
    return Path(override) if override else Path.home() / ".discord-mcp-server" / "tokens.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS webhook_tokens ("
        "access_token TEXT PRIMARY KEY, webhook_url TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


def _store_webhook(access_token: str, webhook_url: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO webhook_tokens (access_token, webhook_url, created_at) VALUES (?, ?, ?)",
            (access_token, webhook_url, datetime.now(timezone.utc).isoformat()),
        )


def _lookup_webhook(access_token: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT webhook_url FROM webhook_tokens WHERE access_token = ?", (access_token,)
        ).fetchone()
    return row[0] if row is not None else None


# --- the one tool --------------------------------------------------------

# Set by BearerAuthMiddleware per-request (task-scoped, safe under
# concurrent requests) -- read by send_message below. Deliberately not
# using FastMCP's own auth subsystem (get_access_token()/TokenVerifier):
# that subsystem assumes a fuller "I am the real authorization server"
# role than this thin third-party-delegating proxy plays. This mirrors
# exactly the same "small, self-contained, independently verifiable" bias
# the rest of this session's work has followed.
_current_webhook_url: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_webhook_url", default=None
)

mcp = FastMCP("discord-send", stateless_http=True)


@mcp.tool()
def send_message(content: str) -> str:
    """Send a message to the Discord channel this connection is authorized for."""
    webhook_url = _current_webhook_url.get()
    if webhook_url is None:
        raise RuntimeError("No Discord webhook associated with this connection -- reconnect it.")
    response = httpx.post(webhook_url, json={"content": content}, timeout=10.0)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord webhook POST failed: {response.status_code} {response.text}")
    return "sent"


# --- OAuth metadata + token proxy -----------------------------------------


async def protected_resource_metadata(request: Request) -> JSONResponse:
    base = _base_url()
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base]})


async def authorization_server_metadata(request: Request) -> JSONResponse:
    base = _base_url()
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": DISCORD_AUTHORIZE_URL,
            "token_endpoint": f"{base}/token",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
        }
    )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {"error": "invalid_response", "detail": response.text}


async def token_proxy(request: Request) -> JSONResponse:
    form = dict(await request.form())
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            DISCORD_TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    if response.status_code != 200:
        return JSONResponse(status_code=response.status_code, content=_safe_json(response))

    body = _safe_json(response)
    webhook = body.get("webhook")
    if not webhook or not webhook.get("url"):
        return JSONResponse(
            status_code=502,
            content={
                "error": "discord_response_missing_webhook",
                "detail": "Discord's token response had no webhook -- was 'webhook.incoming' "
                "actually the requested scope?",
            },
        )

    opaque_token = secrets.token_urlsafe(32)
    _store_webhook(opaque_token, webhook["url"])
    # A webhook.incoming grant doesn't expire -- no expires_in in this
    # response, so agent-graph-studio's oauth_flow.py client (which treats
    # a null expires_at as "never needs refreshing") never attempts to
    # refresh it.
    return JSONResponse({"access_token": opaque_token, "token_type": "Bearer", "scope": "webhook.incoming"})


# --- auth: checks Authorization before letting an /mcp request through ----


class BearerAuthMiddleware:
    """Wraps the mounted FastMCP app -- validates the Bearer token against
    this server's own webhook store, then stashes the resolved webhook URL
    in the contextvar send_message reads. A missing/unknown token gets a
    real 401 + WWW-Authenticate pointing at this server's own protected-
    resource metadata, per MCP's authorization spec -- the same shape
    agent-graph-studio's own oauth_flow.py discovery already expects and
    was verified against in that project's own test suite."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        token = auth_header[len("Bearer ") :] if auth_header.startswith("Bearer ") else None
        webhook_url = _lookup_webhook(token) if token else None

        if webhook_url is None:
            response = JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
                headers={
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata="{_base_url()}/.well-known/oauth-protected-resource"'
                    )
                },
            )
            await response(scope, receive, send)
            return

        reset_token = _current_webhook_url.set(webhook_url)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_webhook_url.reset(reset_token)


def build_app() -> Starlette:
    mcp_asgi_app = mcp.streamable_http_app()
    return Starlette(
        routes=[
            Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
            Route("/.well-known/oauth-authorization-server", authorization_server_metadata),
            Route("/token", token_proxy, methods=["POST"]),
            Mount("/", app=BearerAuthMiddleware(mcp_asgi_app)),
        ],
    )


app = build_app()


if __name__ == "__main__":
    port = int(os.environ.get("DISCORD_MCP_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)

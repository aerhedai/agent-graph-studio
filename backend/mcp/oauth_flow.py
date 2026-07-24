"""spec-021: generic MCP OAuth 2.1 client -- discovery (Protected Resource
Metadata -> Authorization Server Metadata, RFC 8414), PKCE, optional dynamic
client registration, code-for-token exchange, and refresh. Works against
*any* MCP server implementing MCP's own standardized authorization spec --
nothing here is Gmail- or Discord-specific.

Uses the `mcp` SDK's own discovery/registration helper functions
(`mcp.client.auth.utils`) directly, rather than hand-rolling RFC 8414 --
but drives the actual first-time authorization as two ordinary FastAPI
routes (`backend/api/app.py`'s connection OAuth start/callback, added in a
later phase), never the SDK's own `OAuthClientProvider` class. That class
performs the entire first-time authorization inline inside one async
generator -- on a 401 it awaits `redirect_handler` then immediately awaits
`callback_handler`, blocking the same call until a human completes a real
browser round trip. That's a CLI-shaped design (open a local browser, run
a local callback listener, block the one process) that doesn't fit a
multi-tenant web backend, where the callback has to be a real route hit by
the browser in a *separate*, later HTTP request. See
docs/specs/021-per-user-app-connections.md for the full reasoning.

Steady-state token attachment/refresh (`get_valid_access_token`) is also
deliberately NOT routed through `OAuthClientProvider`: reading its source
confirms it never recomputes a loaded token's expiry against wall-clock
time on `_initialize()` (`token_expiry_time` stays unset until a token is
freshly obtained or refreshed *within that same process instance* -- see
oauth2.py's `_initialize`/`update_token_expiry`). A freshly-constructed
provider instance (the natural shape here, matching this project's
existing "asyncio.run() per call" pattern in backend/mcp/remote_client.py)
would therefore treat *any* token loaded from storage as perpetually valid
until a real 401 happens, and then attempt a full interactive
re-authorization instead of a refresh -- the opposite of "transparent."
This module tracks a real absolute `expires_at` itself and refreshes
proactively via a plain, standard OAuth refresh POST (matching this
project's existing hand-rolled-HTTP style for `backend/auth/google_oauth.py`)
instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    extract_resource_metadata_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
)
from mcp.shared.auth import OAuthClientMetadata
from mcp.types import LATEST_PROTOCOL_VERSION

from backend.mcp import oauth_token_storage

DISCOVERY_TIMEOUT_SECONDS = 15.0
# A refreshed-just-in-time token is refreshed slightly early -- a real
# network round trip (the refresh call itself) shouldn't be able to race
# past the actual expiry between "checked valid" and "used".
REFRESH_SKEW_SECONDS = 60


@asynccontextmanager
async def _client_or(client: httpx.AsyncClient | None):
    """Every real caller (production code) gets its own real, closed-after-
    use httpx.AsyncClient, unchanged. Tests inject a client pre-configured
    with `transport=httpx.ASGITransport(app=...)` to exercise this real
    discovery/registration/exchange/refresh logic -- URL construction, the
    SDK's own response-parsing helpers, error handling -- against a real,
    small, spec-conformant local OAuth+MCP server with no actual network
    socket, rather than mocking this module's own logic away."""
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as owned:
        yield owned


class McpOAuthError(RuntimeError):
    """Discovery, registration, code exchange, or refresh failed --
    surfaced as a specific, clear error (never a bare/unwrapped exception),
    matching this project's `GoogleOAuthError`/`McpConnectionError`
    precedent."""


@dataclass(frozen=True)
class DiscoveredOAuthServer:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    scopes_supported: list[str] | None = None
    """From the Protected Resource Metadata's own `scopes_supported`
    (RFC 9728) -- a reasonable default authorization-request scope when
    the connection doesn't specify one explicitly, though a real provider
    may advertise broader scopes here than an operator actually wants to
    request (see McpServerConnectionConfig.oauth_scope)."""


def _parent_path_prm_candidates(mcp_server_url: str) -> list[str]:
    """Real-world finding, not a guess: Google's own official Gmail MCP
    server (confirmed live) publishes its Protected Resource Metadata at
    `/.well-known/oauth-protected-resource/mcp` for an actual endpoint at
    `/mcp/v1` -- a *parent* of the full path, not the full path itself that
    SEP-985's own path-based candidate (`build_protected_resource_metadata_
    discovery_urls`) assumes. This generates every progressively-shorter
    parent-path candidate as an additional fallback -- not Gmail-specific,
    any server with a similar versioned-endpoint convention benefits the
    same way."""
    from urllib.parse import urlparse

    parsed = urlparse(mcp_server_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    segments = [s for s in parsed.path.split("/") if s]
    return [f"{base}/.well-known/oauth-protected-resource/{'/'.join(segments[:i])}" for i in range(len(segments) - 1, 0, -1)]


async def _fetch_prm(client: httpx.AsyncClient, urls: list[str]):
    for url in urls:
        response = await client.get(url)
        prm = await handle_protected_resource_response(response)
        if prm is not None:
            return prm
    return None


async def _discover_async(mcp_server_url: str, client: httpx.AsyncClient | None = None) -> DiscoveredOAuthServer:
    async with _client_or(client) as client:
        # Try direct, unauthenticated discovery first -- confirmed live
        # against Google's real Gmail MCP server that `initialize` and
        # `tools/list` both succeed *without* auth (only `tools/call`
        # actually enforces it), so gating discovery behind a 401 probe
        # would incorrectly conclude "doesn't require OAuth" for a server
        # that plainly does. A direct GET on the well-known PRM path(s) is
        # also strictly safer than a probe -- no risk of an unintended
        # side effect from actually invoking a tool to trigger a 401.
        standard_candidates = build_protected_resource_metadata_discovery_urls(None, mcp_server_url)
        prm = await _fetch_prm(client, standard_candidates + _parent_path_prm_candidates(mcp_server_url))

        if prm is None:
            # Fall back to the probe-based path -- some servers only
            # reveal PRM via a real WWW-Authenticate challenge, never on an
            # unprompted GET. A real, minimal MCP `initialize` request --
            # the same shape a genuine MCP client sends.
            probe = await client.post(
                mcp_server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LATEST_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "agent-graph-studio", "version": "0.1"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            )
            if probe.status_code == 401:
                www_auth_url = extract_resource_metadata_from_www_auth(probe)
                prm = await _fetch_prm(
                    client, build_protected_resource_metadata_discovery_urls(www_auth_url, mcp_server_url)
                )

        if prm is None or not prm.authorization_servers:
            raise McpOAuthError(
                f"{mcp_server_url} doesn't advertise a discoverable authorization server "
                "(Protected Resource Metadata) via any known path -- not a spec-conformant "
                "MCP OAuth server, or it genuinely doesn't require OAuth."
            )

        auth_server_url = str(prm.authorization_servers[0])
        asm = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, mcp_server_url):
            response = await client.get(url)
            ok, asm = await handle_auth_metadata_response(response)
            if asm is not None:
                break
            if not ok:
                break
        if asm is None:
            raise McpOAuthError(
                f"Could not discover OAuth Authorization Server Metadata for {auth_server_url} "
                f"(advertised by {mcp_server_url})."
            )

        return DiscoveredOAuthServer(
            authorization_endpoint=str(asm.authorization_endpoint),
            token_endpoint=str(asm.token_endpoint),
            registration_endpoint=str(asm.registration_endpoint) if asm.registration_endpoint else None,
            scopes_supported=list(prm.scopes_supported) if prm.scopes_supported else None,
        )


def discover_oauth_server(mcp_server_url: str, client: httpx.AsyncClient | None = None) -> DiscoveredOAuthServer:
    """A server that doesn't actually require OAuth (or isn't reachable)
    raises McpOAuthError -- callers treat "discovery failed" as "this
    connection doesn't need/support the OAuth flow", not a hard crash."""
    return asyncio.run(_discover_async(mcp_server_url, client))


async def _register_client_async(
    registration_endpoint: str, redirect_uri: str, scope: str | None, client: httpx.AsyncClient | None = None
) -> tuple[str, str | None]:
    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        client_name="Agent Graph Studio",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
    )
    request = create_client_registration_request(None, metadata, registration_endpoint)
    async with _client_or(client) as client:
        response = await client.send(request)
    if response.status_code not in (200, 201):
        raise McpOAuthError(
            f"Dynamic client registration at {registration_endpoint} failed: "
            f"{response.status_code} {response.text}"
        )
    try:
        info = await handle_registration_response(response)
    except Exception as e:
        raise McpOAuthError(f"Dynamic client registration at {registration_endpoint} returned an invalid response: {e}") from e
    if not info.client_id:
        raise McpOAuthError(f"Dynamic client registration at {registration_endpoint} did not return a client_id")
    return info.client_id, info.client_secret


def register_client(
    registration_endpoint: str, redirect_uri: str, scope: str | None = None, client: httpx.AsyncClient | None = None
) -> tuple[str, str | None]:
    return asyncio.run(_register_client_async(registration_endpoint, redirect_uri, scope, client))


def new_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) -- code_verifier is not
    secret in the way a client_secret is (PKCE's whole point is that it's
    safe to round-trip through the browser/state token), but it must be
    unpredictable and used exactly once."""
    params = PKCEParameters.generate()
    return params.code_verifier, params.code_challenge


def build_authorization_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    resource: str,
    scope: str | None = None,
) -> str:
    import urllib.parse

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # RFC 8707 resource indicator -- ties the grant to this specific
        # MCP server, per MCP's own authorization spec.
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    return f"{authorization_endpoint}?{urllib.parse.urlencode(params)}"


async def _post_token_request(
    token_endpoint: str, data: dict[str, str], client: httpx.AsyncClient | None = None
) -> dict:
    async with _client_or(client) as client:
        response = await client.post(
            token_endpoint, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    if response.status_code != 200:
        raise McpOAuthError(f"Token request to {token_endpoint} failed: {response.status_code} {response.text}")
    return response.json()


async def _exchange_code_async(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    code_verifier: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return await _post_token_request(token_endpoint, data, client)


def exchange_code_for_token(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    code_verifier: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    return asyncio.run(
        _exchange_code_async(token_endpoint, code, redirect_uri, client_id, client_secret, code_verifier, client)
    )


async def _refresh_async(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    return await _post_token_request(token_endpoint, data, client)


def _compute_expires_at(expires_in: int | str | None) -> str | None:
    if expires_in is None:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


def _is_expiring_soon(expires_at: str) -> bool:
    expiry = datetime.fromisoformat(expires_at)
    return datetime.now(timezone.utc) >= expiry - timedelta(seconds=REFRESH_SKEW_SECONDS)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_token_response(
    user_id: str,
    connection_name: str,
    token_response: dict,
    token_endpoint: str,
    client_id: str | None,
    client_secret: str | None,
    path=None,
) -> None:
    """Persists a raw OAuth token response (from either the initial code
    exchange or a refresh) -- shared by both call sites so the field
    mapping (`expires_in` -> absolute `expires_at`, etc.) can't drift
    between them."""
    access_token = token_response.get("access_token")
    if not access_token:
        raise McpOAuthError(f"Token response had no access_token: {token_response}")
    oauth_token_storage.save_token(
        user_id,
        connection_name,
        access_token=access_token,
        refresh_token=token_response.get("refresh_token"),
        expires_at=_compute_expires_at(token_response.get("expires_in")),
        token_type=token_response.get("token_type", "Bearer"),
        scope=token_response.get("scope"),
        token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        updated_at=_utcnow_iso(),
        path=path,
    )


def get_valid_access_token(
    user_id: str, connection_name: str, path=None, client: httpx.AsyncClient | None = None
) -> str:
    """The steady-state entry point every MCP tool call goes through
    (backend/mcp/transport.py, generated_nodes.py, once wired up in a
    later phase) -- returns a real, currently-valid access token,
    transparently refreshing first if the stored one is expired or about
    to be. Raises McpOAuthError (surfaced by callers as a clear
    NodeExecutionError telling the user to reconnect) if there's no stored
    token, or refresh itself fails -- never silently returns a stale
    token, never attempts a mid-run interactive re-authorization."""
    row = oauth_token_storage.get_token(user_id, connection_name, path=path)
    if row is None:
        raise McpOAuthError(
            f"No OAuth connection found for '{connection_name}' -- connect it first (Settings -> Connections)."
        )
    if row.expires_at is not None and _is_expiring_soon(row.expires_at):
        if row.refresh_token is None:
            raise McpOAuthError(
                f"'{connection_name}''s access token has expired and there's no refresh token -- "
                "reconnect it (Settings -> Connections)."
            )
        try:
            refreshed = asyncio.run(
                _refresh_async(row.token_endpoint, row.refresh_token, row.client_id or "", row.client_secret, client)
            )
        except McpOAuthError as e:
            raise McpOAuthError(
                f"'{connection_name}''s access token expired and refreshing it failed ({e}) -- reconnect it."
            ) from e
        store_token_response(
            user_id,
            connection_name,
            {**refreshed, "refresh_token": refreshed.get("refresh_token", row.refresh_token)},
            row.token_endpoint,
            row.client_id,
            row.client_secret,
            path=path,
        )
        return refreshed["access_token"]
    return row.access_token

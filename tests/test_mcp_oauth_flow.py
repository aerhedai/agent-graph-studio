"""spec-021: backend/mcp/oauth_flow.py's discovery -> dynamic registration
-> code exchange -> transparent refresh pipeline, exercised against a real,
small, spec-conformant local OAuth+MCP server -- via httpx.ASGITransport
(no real network socket), not by mocking this module's own logic. Only the
transport layer is swapped for an in-process ASGI adapter; every line of
oauth_flow.py's own code, the SDK's discovery/registration helpers, and
real JSON (de)serialization all run for real.

Real, live verification against Google's actual Gmail MCP server and a
real Discord MCP server happens separately (spec-021's live-verification
phase) -- this is the automated-suite half, proving the generic mechanism
itself is correct before pointing it at any specific real app.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.mcp import oauth_flow, oauth_token_storage

MCP_URL = "https://mockmcp.test/mcp"
AUTHORIZE_URL = "https://mockmcp.test/authorize"
TOKEN_URL = "https://mockmcp.test/token"
REGISTER_URL = "https://mockmcp.test/register"
REDIRECT_URI = "https://app.example.com/connections/my-mock/mcp-oauth/callback"


def _build_mock_server(*, dynamic_registration: bool = True) -> FastAPI:
    """A minimal, real, spec-conformant OAuth-protected MCP server: no
    Authorization header -> 401 + WWW-Authenticate advertising Protected
    Resource Metadata -> Authorization Server Metadata -> (optional)
    dynamic client registration -> code exchange / refresh at /token."""
    app = FastAPI()
    app.state.issued_codes = {"real-code": {"client_id": None}}
    app.state.refresh_tokens = {"real-refresh-token"}

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        if "authorization" not in {k.lower() for k in request.headers.keys()}:
            return JSONResponse(
                status_code=401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="https://mockmcp.test/.well-known/'
                        'oauth-protected-resource"'
                    )
                },
                content={"error": "unauthorized"},
            )
        return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}})

    @app.get("/.well-known/oauth-protected-resource")
    async def protected_resource_metadata():
        return JSONResponse({"resource": MCP_URL, "authorization_servers": ["https://mockmcp.test"]})

    @app.get("/.well-known/oauth-authorization-server")
    async def authorization_server_metadata():
        body = {
            "issuer": "https://mockmcp.test",
            "authorization_endpoint": AUTHORIZE_URL,
            "token_endpoint": TOKEN_URL,
            "response_types_supported": ["code"],
        }
        if dynamic_registration:
            body["registration_endpoint"] = REGISTER_URL
        return JSONResponse(body)

    @app.post("/register")
    async def register(request: Request):
        body = await request.json()
        return JSONResponse(
            status_code=201,
            content={
                "client_id": "dynamic-client-id",
                "client_secret": "dynamic-client-secret",
                "redirect_uris": body["redirect_uris"],
            },
        )

    @app.post("/token")
    async def token(request: Request):
        form = await request.form()
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            if form.get("code") != "real-code":
                return JSONResponse(status_code=400, content={"error": "invalid_grant"})
            return JSONResponse(
                {
                    "access_token": "real-access-token",
                    "refresh_token": "real-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "mcp.read mcp.write",
                }
            )
        if grant_type == "refresh_token":
            if form.get("refresh_token") not in app.state.refresh_tokens:
                return JSONResponse(status_code=400, content={"error": "invalid_grant"})
            return JSONResponse(
                {
                    "access_token": "refreshed-access-token",
                    "refresh_token": "real-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})

    return app


def _test_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mockmcp.test")


# --- discovery -------------------------------------------------------------


def test_discover_oauth_server_finds_real_endpoints_via_401_and_metadata():
    app = _build_mock_server()

    async def flow():
        async with _test_client(app) as client:
            return await oauth_flow._discover_async(MCP_URL, client)

    result = asyncio.run(flow())
    assert result.authorization_endpoint == AUTHORIZE_URL
    assert result.token_endpoint == TOKEN_URL
    assert result.registration_endpoint == REGISTER_URL


def test_discover_raises_clearly_when_server_does_not_require_oauth():
    app = FastAPI()

    @app.post("/mcp")
    async def mcp_endpoint():
        return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}})

    async def flow():
        async with _test_client(app) as client:
            return await oauth_flow._discover_async(MCP_URL, client)

    try:
        asyncio.run(flow())
        assert False, "expected McpOAuthError"
    except oauth_flow.McpOAuthError as e:
        assert "doesn't advertise a discoverable authorization server" in str(e)


def test_discover_finds_prm_at_a_parent_path_when_server_never_401s():
    """Real-world regression, not a hypothetical: Google's actual official
    Gmail MCP server (confirmed live against the real
    https://gmailmcp.googleapis.com/mcp/v1 during this spec's own live
    verification) returns 200 for `initialize` *and* `tools/list` with no
    auth at all -- only `tools/call` actually enforces it -- but serves its
    Protected Resource Metadata unprompted at `/.well-known/oauth-protected-
    resource/mcp`, the *parent* of the real `/mcp/v1` endpoint path, not the
    exact full-path candidate SEP-985's own path-based candidate assumes.
    Discovery must still succeed via the direct-GET-first strategy, with no
    401 ever being triggered."""
    app = FastAPI()

    @app.post("/mcp/v1")
    async def mcp_endpoint():
        # Never a 401, at any method -- matches Gmail's real behavior for
        # both initialize and tools/list.
        return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}})

    # Registered at the *parent* path ("/mcp"), not the full "/mcp/v1".
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def prm():
        return JSONResponse({"resource": f"{MCP_URL}/v1", "authorization_servers": ["https://mockmcp.test"]})

    @app.get("/.well-known/oauth-authorization-server")
    async def asm():
        return JSONResponse(
            {
                "issuer": "https://mockmcp.test",
                "authorization_endpoint": AUTHORIZE_URL,
                "token_endpoint": TOKEN_URL,
                "response_types_supported": ["code"],
            }
        )

    async def flow():
        async with _test_client(app) as client:
            return await oauth_flow._discover_async(f"{MCP_URL}/v1", client)

    result = asyncio.run(flow())
    assert result.authorization_endpoint == AUTHORIZE_URL
    assert result.token_endpoint == TOKEN_URL


# --- dynamic client registration -------------------------------------------


def test_register_client_returns_real_client_credentials():
    app = _build_mock_server()

    async def flow():
        async with _test_client(app) as client:
            return await oauth_flow._register_client_async(REGISTER_URL, REDIRECT_URI, "mcp.read mcp.write", client)

    client_id, client_secret = asyncio.run(flow())
    assert client_id == "dynamic-client-id"
    assert client_secret == "dynamic-client-secret"


# --- authorization URL construction -----------------------------------------


def test_build_authorization_url_includes_pkce_and_resource_indicator():
    code_verifier, code_challenge = oauth_flow.new_pkce_pair()
    url = oauth_flow.build_authorization_url(
        AUTHORIZE_URL,
        client_id="dynamic-client-id",
        redirect_uri=REDIRECT_URI,
        state="opaque-state",
        code_challenge=code_challenge,
        resource=MCP_URL,
        scope="mcp.read mcp.write",
    )
    assert url.startswith(AUTHORIZE_URL + "?")
    assert f"code_challenge={code_challenge}" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "state=opaque-state" in url
    import urllib.parse

    assert urllib.parse.quote(MCP_URL, safe="") in url


# --- code exchange + full round trip into storage ---------------------------


def test_exchange_code_for_token_and_store_round_trips_through_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", _fresh_fernet_key())
    db_path = tmp_path / "mcp_oauth_tokens.db"
    app = _build_mock_server()

    async def flow():
        async with _test_client(app) as client:
            return await oauth_flow._exchange_code_async(
                TOKEN_URL, "real-code", REDIRECT_URI, "dynamic-client-id", "dynamic-client-secret",
                "a-code-verifier", client,
            )

    token_response = asyncio.run(flow())
    assert token_response["access_token"] == "real-access-token"

    oauth_flow.store_token_response(
        "user-a", "my-mock-mcp", token_response, TOKEN_URL, "dynamic-client-id", "dynamic-client-secret",
        path=db_path,
    )

    row = oauth_token_storage.get_token("user-a", "my-mock-mcp", path=db_path)
    assert row is not None
    assert row.access_token == "real-access-token"
    assert row.refresh_token == "real-refresh-token"
    assert row.client_secret == "dynamic-client-secret"
    # Stored encrypted on disk, not plaintext.
    raw = db_path.read_bytes()
    assert b"real-access-token" not in raw
    assert b"dynamic-client-secret" not in raw


# --- get_valid_access_token: transparent refresh ----------------------------


def test_get_valid_access_token_returns_stored_token_when_not_expiring(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", _fresh_fernet_key())
    db_path = tmp_path / "mcp_oauth_tokens.db"
    _seed_token(db_path, expires_in_seconds=3600)

    token = oauth_flow.get_valid_access_token("user-a", "my-mock-mcp", path=db_path)
    assert token == "real-access-token"


def test_get_valid_access_token_transparently_refreshes_when_expiring(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", _fresh_fernet_key())
    db_path = tmp_path / "mcp_oauth_tokens.db"
    # Already past the refresh skew window.
    _seed_token(db_path, expires_in_seconds=10)
    _patch_httpx_to_mock_server(monkeypatch, _build_mock_server())

    # get_valid_access_token is plain sync (does its own asyncio.run
    # internally, matching every other MCP call site in this codebase) --
    # no client injection needed once httpx.AsyncClient itself is patched
    # to route to the mock ASGI app regardless of what URL it's given.
    token = oauth_flow.get_valid_access_token("user-a", "my-mock-mcp", path=db_path)
    assert token == "refreshed-access-token"

    row = oauth_token_storage.get_token("user-a", "my-mock-mcp", path=db_path)
    assert row is not None
    assert row.access_token == "refreshed-access-token"
    # Refresh token preserved (this mock server doesn't rotate it).
    assert row.refresh_token == "real-refresh-token"


def test_get_valid_access_token_raises_clearly_when_never_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", _fresh_fernet_key())
    db_path = tmp_path / "mcp_oauth_tokens.db"
    try:
        oauth_flow.get_valid_access_token("user-a", "never-connected", path=db_path)
        assert False, "expected McpOAuthError"
    except oauth_flow.McpOAuthError as e:
        assert "connect it first" in str(e)


def test_get_valid_access_token_raises_clearly_when_refresh_token_is_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", _fresh_fernet_key())
    db_path = tmp_path / "mcp_oauth_tokens.db"
    _seed_token(db_path, expires_in_seconds=10, refresh_token="a-dead-refresh-token")
    _patch_httpx_to_mock_server(monkeypatch, _build_mock_server())  # only accepts "real-refresh-token"

    try:
        oauth_flow.get_valid_access_token("user-a", "my-mock-mcp", path=db_path)
        assert False, "expected McpOAuthError"
    except oauth_flow.McpOAuthError as e:
        assert "reconnect" in str(e)


# --- helpers -----------------------------------------------------------------


def _patch_httpx_to_mock_server(monkeypatch, app: FastAPI) -> None:
    """Patches oauth_flow's own `httpx.AsyncClient` constructor so that its
    internal `_client_or(None)` fallback (a plain sync caller like
    get_valid_access_token, which does its own top-level asyncio.run --
    injecting a pre-built client isn't an option there without risking a
    nested-event-loop crash) transparently routes to the mock ASGI server
    regardless of which real URL it's constructed against."""

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        return real_async_client(transport=httpx.ASGITransport(app=app), base_url="https://mockmcp.test")

    monkeypatch.setattr(oauth_flow.httpx, "AsyncClient", fake_async_client)


def _seed_token(db_path, expires_in_seconds: int, refresh_token: str = "real-refresh-token") -> None:
    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat()
    oauth_token_storage.save_token(
        "user-a",
        "my-mock-mcp",
        access_token="real-access-token",
        refresh_token=refresh_token,
        expires_at=expires_at,
        token_type="Bearer",
        scope="mcp.read mcp.write",
        token_endpoint=TOKEN_URL,
        client_id="dynamic-client-id",
        client_secret="dynamic-client-secret",
        updated_at=datetime.now(timezone.utc).isoformat(),
        path=db_path,
    )


def _fresh_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()

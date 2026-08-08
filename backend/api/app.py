"""FastAPI layer over the execution engine (spec-005).

Every route below is a plain `def`, never `async def` -- load-bearing, not a
style choice. `validate_graph()` (via POST /runs) and the resolve-slots logic
both transitively call `resolve_slots` for `mcp_call`, which internally does
its own `asyncio.run(...)` (backend/mcp/client.py). Calling that from a
coroutine already running on an event loop (which any `async def` route runs
on) raises "asyncio.run() cannot be called from a running event loop".
FastAPI/Starlette dispatches plain `def` routes through a worker thread
automatically (`run_in_threadpool`) -- the same "no event loop on this
thread" pattern already relied on for the `loop` node's recursive
`run_graph()` call. Every route here is plain `def` so this never has to be
reasoned about per-route.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

import backend.connections  # noqa: F401 -- import side effect registers every connection type
import backend.integrations  # noqa: F401 -- import side effect registers every integration's webhook-sync handler
import backend.nodes  # noqa: F401 -- import side effect registers every node type
from backend.api import runs
from backend.auth import google_oauth
from backend.auth import jwt as auth_jwt
from backend.api.schemas import (
    ActivateGraphResponse,
    ActiveGraphInfo,
    ConnectionInfo,
    ConnectionSlotSpec,
    ConnectionTypeInfo,
    CreateConnectionRequest,
    CreateGraphRequest,
    CreateInvokeKeyRequest,
    CreateInvokeKeyResponse,
    GraphDetail,
    GraphSummary,
    InvokeContractField,
    InvokeContractResponse,
    InvokeGraphRequest,
    InvokeGraphResponse,
    InvokeKeyInfo,
    InviteRequest,
    InviteResponse,
    MeResponse,
    NodeTypeInfo,
    OptionItem,
    PendingApprovalInfo,
    PrivateConnectionSummary,
    RefreshCapabilitiesResponse,
    ResolveApprovalRequest,
    ResolveOptionsRequest,
    ResolveSlotsRequest,
    ResolveSlotsResponse,
    RunListResponse,
    RunStatusResponse,
    RunSubmitResponse,
    RunSummary,
    SetApiKeyRequest,
    SetSlotMappingRequest,
    SettingsResponse,
    SlotInfo,
    SlotMappingResponse,
    SubNodeSlotInfo,
    TestConnectionRequest,
    TestConnectionResponse,
    TriggerInfo,
    UpdateConnectionRequest,
    UpdateGraphRequest,
    UpdateSettingsRequest,
    UpdateSettingsResponse,
)
from backend.connections.base import default_connection_registry
from backend.connections.errors import ConnectionNotFoundError, DuplicateConnectionError
from backend.connections.mcp_server_connection import McpServerConnectionConfig, transport_config
from backend.connections.resolver import resolve_connection_profiles, resolve_connections
from backend.connections.store import (
    add_connection,
    delete_connection,
    ensure_encryption_key_configured,
    get_connection,
    list_connections,
    list_connections_unscoped,
    resolve_connection_for_user,
    set_connection_owner,
    update_connection_config,
)
from backend.execution import approvals
from backend.mcp import api_key_storage, generated_nodes, oauth_flow, oauth_token_storage, option_bindings
from backend.mcp.client import McpConnectionError
from backend.registry.base import default_registry, effective_inputs, effective_outputs
from backend.schema.models import GraphSpec, NodeSpec
from backend.storage import graph_sharing_store, graphs_store, invoke_keys_store, runs_store, settings_store, users_store
from backend.triggers import registry as trigger_registry
from backend.triggers import runner as trigger_runner
from backend.triggers import scheduler as trigger_scheduler
from backend.triggers import webhook_sync
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph

# Loads ./.env into the process environment for local `uv run` use (Docker
# Compose sets real env vars directly, so this is a no-op there -- load_dotenv
# never overrides a variable already set in the real environment).
# Called before any ensure_*_configured() check below reads its env var, and
# before every one of this module's own routes can run -- none of the
# imports above read an env var at import time, only inside functions, so
# this is safe to place after them rather than needing to precede them.
load_dotenv()

logger = logging.getLogger(__name__)


class MissingApiKeyError(RuntimeError):
    """spec-017: raised eagerly at API startup when AGENT_GRAPH_STUDIO_API_KEY
    isn't set -- refusing to start is the point, mirroring
    backend/connections/store.py's MissingEncryptionKeyError exactly."""

    def __init__(self) -> None:
        super().__init__(
            "AGENT_GRAPH_STUDIO_API_KEY is not set -- refusing to start without a real "
            "shared credential (see docs/DEPLOYMENT.md)."
        )


# spec-017/020/021: paths reachable with no credential at all -- schema/
# shape only (not data), a health check for container orchestration
# (SPEC-016), the two routes a real Google sign-in round trip has to hit
# before any credential of ours can possibly exist yet, and the MCP OAuth
# callback (spec-021) -- the browser lands there from the OAuth provider's
# own redirect, carrying no JWT of ours; its identity comes from the
# signed state token instead (see mcp_connection_oauth_callback). The
# *start* of that flow (/connections/oauth/start) deliberately is NOT
# exempt -- it requires an already-signed-in human, unlike Google login.
_AUTH_EXEMPT_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/auth/google/login",
    "/auth/google/callback",
    "/connections/oauth/callback",
}


def _configured_api_key() -> str:
    key = os.environ.get("AGENT_GRAPH_STUDIO_API_KEY")
    if not key:
        raise MissingApiKeyError()
    return key


def ensure_api_key_configured() -> None:
    """Eager startup check, called from _lifespan -- see module docstring
    on MissingApiKeyError for why this can't be lazy."""
    _configured_api_key()


class AuthenticatedUser:
    """spec-020: populated onto `request.state.user` only when the caller
    authenticated via a real JWT (a logged-in human) -- None for a shared-
    API-key caller (webhooks, other machine-to-machine callers), which is
    exactly the signal `created_by`/`run_by` need to stay correctly null
    for a schedule/webhook-triggered run."""

    def __init__(self, user_id: str, email: str, role: str) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role


def _caller_user_id(http_request: Request) -> str | None:
    """spec-021: the one-line `request.state.user.user_id if ... else None`
    check repeated at every connection/run call site that needs to know
    which user (if any) is calling -- factored out once it started
    appearing more than the two spots (created_by/run_by) it was
    originally written inline for."""
    user = http_request.state.user
    return user.user_id if user else None


def _caller_role(http_request: Request) -> str | None:
    """spec-023: same factoring rationale as _caller_user_id -- every
    _connection_info call site needs this alongside the caller's user id
    to compute can_manage."""
    user = http_request.state.user
    return user.role if user else None


def _require_admin(http_request: Request) -> None:
    """spec-023: same factoring rationale as _caller_user_id -- inline at
    /auth/invite as the one role check in the whole app until now; now that
    connection scope/mutation/promote-to-global all need the identical
    check, one shared helper instead of four copies of the same two lines.

    A shared-API-key caller (no signed-in user at all, http_request.state
    .user is None) is deliberately let through, not blocked -- that
    credential predates the whole admin/member role system (SPEC-020) and
    has always had unrestricted access to global connections (the only
    kind that existed before per-user scoping); this spec's new
    restriction is about a signed-in *non-admin human*, not about
    tightening the shared key's own long-standing access."""
    user = http_request.state.user
    if user is not None and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


# spec-029: the only two route *path templates* (not literal paths -- these
# contain the `{graph_id}` placeholder exactly as FastAPI/Starlette expose it
# via `request.scope["route"].path`) an invoke key is ever accepted on. Never
# add another route here without re-reading require_auth's docstring below --
# this is the entire enforcement boundary for "an invoke key can't do
# anything except invoke/preview its own one graph."
_INVOKE_KEY_SCOPED_ROUTES = {"/graphs/{graph_id}/invoke", "/graphs/{graph_id}/contract"}


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    key: str | None = None,
) -> None:
    """spec-020: one global dependency (attached to the whole app, not
    per-route -- see `app = FastAPI(dependencies=...)` below, unchanged
    from spec-017's reasoning), still protecting every route including
    ones added dynamically later via `app.add_api_route` (the webhook
    routes).

    Accepts credentials, tried in this order:
    1. A real JWT session (`Authorization: Bearer <jwt>`) -- a logged-in
       human via Google sign-in (spec-020). Sets `request.state.user`.
    2. The spec-017 shared API key, exactly as before (`Authorization:
       Bearer <key>` or `?key=<key>` -- the mechanism external callers
       like Telegram's webhook callbacks use, since they can't set a
       custom header). Leaves `request.state.user` as None.
    3. spec-029: a per-graph invoke key, tried *only* when the matched
       route is one of `_INVOKE_KEY_SCOPED_ROUTES` above. This is
       deliberately a third branch inside this same dependency, not a
       second `Depends(...)` added to just those two routes -- the global
       dependency already runs (and would 401) before a second one ever
       got a chance, since FastAPI runs `app`-level dependencies first.
       Routing resolves before dependencies run, so
       `request.scope["route"].path`/`request.path_params["graph_id"]`
       are already populated here. Tiers 1-2 deliberately still work on
       these two routes too (unchanged) -- a logged-in human can preview
       a graph's contract or test-invoke it from its own settings panel
       without minting a key first; only an invoke key is scoped this
       narrowly. Sets `request.state.invoke_key` when this tier matches;
       every other credential path leaves it None.

    Deliberately one dependency accepting all of the above, rather than
    classifying routes as "human" vs "webhook" vs "invoke" and giving each
    a different dependency -- the acceptance bar is "every existing
    credential keeps working everywhere it already did (regression), and
    an invoke key works on exactly two routes and nowhere else," not
    mutual exclusion, and this keeps the "can't forget to protect one
    route" property a route-differentiated design would risk losing.
    """
    request.state.user = None
    request.state.invoke_key = None
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return

    supplied: str | None = None
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[len("Bearer ") :]
    elif key:
        supplied = key

    if supplied:
        claims = auth_jwt.verify_token(supplied)
        if claims is not None:
            request.state.user = AuthenticatedUser(user_id=claims.user_id, email=claims.email, role=claims.role)
            return

    configured = _configured_api_key()
    if supplied == configured:
        return

    route = request.scope.get("route")
    if supplied and getattr(route, "path", None) in _INVOKE_KEY_SCOPED_ROUTES:
        graph_id = request.path_params.get("graph_id")
        if graph_id is not None:
            key_row = invoke_keys_store.validate_invoke_key(graph_id, supplied, now=_utcnow_iso())
            if key_row is not None:
                request.state.invoke_key = key_row
                return

    raise HTTPException(status_code=401, detail="Missing or invalid credential")


def _utcnow_iso() -> str:
    # Mirrors backend/api/runs.py's private _utcnow_iso exactly -- this
    # project's established small-duplication-over-shared-utils convention
    # (see backend/nodes/agent.py's identical helper for the same reasoning).
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Spec-017/020: eager, explicit checks first -- every required secret
    # must be genuinely configured before this process is allowed to start
    # serving anything, not just incidentally validated the first time some
    # other operation happens to need them.
    ensure_encryption_key_configured()
    ensure_api_key_configured()
    auth_jwt.ensure_jwt_secret_configured()
    google_oauth.ensure_google_oauth_configured()
    users_store.ensure_admin_email_configured()
    # spec-020: idempotent -- safe on every boot, not just the first one.
    # Plain sqlite3 calls, no asyncio.run() involved anywhere in
    # users_store, so (unlike regenerate_all_on_startup below) this is
    # safe to call directly on this coroutine's own event-loop thread.
    users_store.ensure_admin_bootstrapped(os.environ["AGENT_GRAPH_STUDIO_ADMIN_EMAIL"], _utcnow_iso())
    # spec-019: rebuild every saved mcp_server connection's generated node
    # set on startup -- the palette must be correct immediately after a
    # restart, not only after each connection happens to be manually
    # refreshed.
    #
    # Dispatched via asyncio.to_thread, NOT called directly: this calls real
    # MCP discovery (backend/mcp/client.py's list_tools), which internally
    # does its own asyncio.run() (same sync-over-async pattern used
    # everywhere else MCP discovery happens). That fails outright when
    # called directly from this coroutine, since _lifespan already runs on
    # uvicorn's own event loop -- discovered live, restarting the backend
    # after this feature was added. Every other MCP-discovery call site
    # (POST /connections, POST /connections/{name}/refresh-capabilities) is
    # a plain synchronous route handler, dispatched through Starlette's own
    # worker thread, so it never hits this; startup is the one place this
    # module runs directly on the event loop thread.
    #
    # Bug fix: this must run BEFORE _reactivate_persisted_graphs below, not
    # after. A persisted graph referencing a dynamically-generated MCP node
    # type (e.g. mcp__web-search__search) can only re-validate successfully
    # once that type is actually registered -- with the old ordering,
    # _reactivate_persisted_graphs always ran against a freshly-wiped node
    # registry that hadn't been rebuilt yet, so validate_graph's
    # unregistered_type check would reject any such graph outright, every
    # single restart, regardless of whether the underlying MCP server was
    # even reachable. Found live: a real activated "Simple telegram
    # assistant" graph (using generated web-search MCP nodes) silently
    # failed to re-activate on every restart, logged and swallowed by
    # _reactivate_persisted_graphs's own per-graph try/except, leaving
    # `GET /graphs/active` reporting it inactive and its webhook route
    # returning a real 404 to external callers (Telegram) despite the graph
    # showing is_active=true in storage the whole time.
    await asyncio.to_thread(generated_nodes.regenerate_all_on_startup)
    # Spec-015: re-arm every persisted is_active graph's triggers on real
    # process startup -- `_reactivate_persisted_graphs` is defined later in
    # this module; that's fine, its name is only resolved when this
    # coroutine actually runs (real ASGI startup / TestClient's `with`
    # context), by which point the whole module has finished executing.
    _reactivate_persisted_graphs()
    yield


# spec-017/020: one dependency, accepting either a human's JWT session or
# the shared API key (see require_auth above) -- app-level `dependencies`
# attaches to the app's router itself, so routes added later via
# `app.add_api_route` (the dynamic webhook routes, SPEC-009) are covered
# too, not just the ones defined directly below. Local single-user tool
# origins remain permissive for CORS -- auth is the actual access control
# now, not CORS.
app = FastAPI(title="Agent Graph Studio API", lifespan=_lifespan, dependencies=[Depends(require_auth)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated (see _AUTH_EXEMPT_PATHS) -- for container
    orchestration health checks (SPEC-016's Compose setup can use this)."""
    return {"status": "ok"}


# --- spec-020: Google sign-in ------------------------------------------

_OAUTH_STATE_COOKIE = "oauth_state"


@app.get("/auth/google/login")
def google_login(redirect_to: str) -> RedirectResponse:
    """Unauthenticated (see _AUTH_EXEMPT_PATHS) -- the whole point is that
    no credential of ours can exist yet. `redirect_to` is the frontend's
    own origin (it passes `window.location.origin`), threaded through a
    signed, short-lived state token so it survives the round trip through
    Google's own redirect and back -- local dev (frontend and backend on
    different ports) and the Docker deployment (same origin, reverse-
    proxied) both work with zero extra configuration.

    The state token doubles as CSRF protection: set both as an httpOnly
    cookie *and* as Google's `state` param, and the callback below requires
    them to match. A validly-signed state token alone isn't enough --  it
    has to be the one issued to *this* browser for *this* login attempt,
    not just any state token an attacker could obtain from their own,
    separate, legitimate login flow.
    """
    public_base_url = settings_store.get_public_base_url()
    if not public_base_url:
        raise HTTPException(
            status_code=422,
            detail="No public base URL configured yet -- set one first (see Settings) "
            "before signing in with Google.",
        )
    state = auth_jwt.issue_state_token(redirect_to)
    redirect_uri = f"{public_base_url.rstrip('/')}/auth/google/callback"
    authorization_url = google_oauth.build_authorization_url(redirect_uri, state)
    response = RedirectResponse(url=authorization_url, status_code=302)
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        state,
        max_age=auth_jwt.STATE_TOKEN_EXPIRES_MINUTES * 60,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/auth/google/callback")
def google_callback(
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    """Unauthenticated (see _AUTH_EXEMPT_PATHS) -- Google redirects here
    with no credential of ours attached, by definition. Exchanges the real
    authorization code for real Google userinfo, checks the invite
    allowlist (a valid Google identity that isn't invited is a clean,
    specific rejection -- spec-020 §4), creates/looks-up the user, and
    redirects back to the frontend carrying our own session JWT in the URL
    *fragment* (`#token=...`), not a query string -- a fragment is never
    sent to any server (ours or a proxy in between), so the token never
    ends up in an access log."""
    if not oauth_state or oauth_state != state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch -- possible CSRF, please sign in again")
    redirect_to = auth_jwt.verify_state_token(state)
    if redirect_to is None:
        raise HTTPException(status_code=400, detail="OAuth state expired or invalid -- please sign in again")

    public_base_url = settings_store.get_public_base_url() or ""
    redirect_uri = f"{public_base_url.rstrip('/')}/auth/google/callback"
    try:
        userinfo = google_oauth.exchange_code_for_userinfo(code, redirect_uri)
    except google_oauth.GoogleOAuthError as e:
        response = RedirectResponse(url=f"{redirect_to}#auth_error={urllib.parse.quote(str(e))}", status_code=302)
        response.delete_cookie(_OAUTH_STATE_COOKIE)
        return response

    invite = users_store.get_invite(userinfo.email)
    if invite is None:
        response = RedirectResponse(url=f"{redirect_to}#auth_error=not_invited", status_code=302)
        response.delete_cookie(_OAUTH_STATE_COOKIE)
        return response

    user = users_store.get_user_by_email(userinfo.email)
    if user is None:
        user = users_store.create_user(
            user_id=str(uuid4()),
            email=userinfo.email,
            display_name=userinfo.name,
            role=invite.role,
            created_at=_utcnow_iso(),
            invited_by=invite.invited_by,
        )

    token = auth_jwt.issue_token(user.id, user.email, user.role)
    response = RedirectResponse(url=f"{redirect_to}#token={token}", status_code=302)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    return response


@app.get("/auth/me", response_model=MeResponse)
def get_me(http_request: Request) -> MeResponse:
    """Requires a real JWT session, not just any valid credential -- a
    shared-API-key caller has no human identity to report (`request.state
    .user` is None for it), so this is a clean 401 rather than a
    nonsensical "who am I" answer for a machine caller."""
    user = http_request.state.user
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in as a user")
    # Looked up fresh rather than trusting the JWT's own claims for
    # display_name -- keeps a token stable even if a user's profile
    # changes after it was issued, and the JWT never carried display_name
    # in the first place (kept minimal by design).
    row = users_store.get_user_by_id(user.user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return MeResponse(user_id=row.id, email=row.email, display_name=row.display_name, role=row.role)


@app.post("/auth/invite", response_model=InviteResponse)
def invite_user(request: InviteRequest, http_request: Request) -> InviteResponse:
    """Admin-only -- checks http_request.state.user.role directly (not a
    separate dependency) since this is the one route in the whole app that
    needs a role check at all; a dedicated require_admin dependency would
    be speculative generality for a single call site."""
    user = http_request.state.user
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can invite a new user")
    row = users_store.add_invite(request.email, request.role, invited_by=user.user_id, invited_at=_utcnow_iso())
    return InviteResponse(email=row.email, role=row.role, invited_by=row.invited_by, invited_at=row.invited_at)


def _slot_info_list(slots) -> list[SlotInfo]:
    return [
        SlotInfo(name=s.name, type=s.type.model_dump(), required=getattr(s, "required", True))
        for s in slots
    ]


@app.get("/node-types", response_model=list[NodeTypeInfo])
def list_node_types() -> list[NodeTypeInfo]:
    """The node palette's entire data source. `default_registry.all_types()`
    (backend/registry/base.py) is the *only* place any node type list is
    enumerated -- populated by @register_node(...) decorator side effects
    across backend/nodes/*.py at import time, plus (spec-019) runtime
    registrations from backend/mcp/generated_nodes.py for each saved
    `mcp_server` connection. No type name is hardcoded here or anywhere in
    the frontend; a new backend node type appears automatically either way.
    """
    infos: list[NodeTypeInfo] = []
    for type_name in default_registry.all_types():
        definition = default_registry.get(type_name)
        # spec-012: a root whose ports mirror a connected sub-node
        # (resolve_slots_from_sub_node, e.g. webhook_trigger) is dynamic in
        # the same "empty until resolved" sense as config-based dynamism --
        # just resolved by the canvas client-side from the connected
        # sub-node's own static outputs, not via POST /resolve-slots.
        is_dynamic = definition.resolve_slots is not None or definition.resolve_slots_from_sub_node is not None
        inputs = [] if is_dynamic else _slot_info_list(definition.inputs)
        outputs = [] if is_dynamic else _slot_info_list(definition.outputs)
        sub_node_slots = (
            {
                name: SubNodeSlotInfo(cardinality=spec.cardinality, accepts_role=spec.accepts_role)
                for name, spec in definition.sub_node_slots.items()
            }
            if definition.sub_node_slots is not None
            else None
        )
        infos.append(
            NodeTypeInfo(
                type=type_name,
                category=definition.category,
                config_schema=definition.config_model.model_json_schema(),
                dynamic_schema=is_dynamic,
                inputs=inputs,
                outputs=outputs,
                sub_node_slots=sub_node_slots,
                sub_node_role=definition.sub_node_role,
                resolve_slots_from_sub_node=definition.resolve_slots_from_sub_node,
                integration=definition.integration,
                capability_group=definition.capability_group,
                dynamic_option_slots=option_bindings.fields_with_bindings(type_name),
            )
        )
    return infos


# spec-025 Phase 5: (connection, field, args) -> (fetched_at, options) --
# module-level like `_runs`/`approvals` elsewhere in this file; an in-memory
# 60s cache is explicitly fine to lose on process restart, unlike anything
# in the durable stores.
_option_cache: dict[tuple[str, str, tuple], tuple[float, list[OptionItem]]] = {}


@app.post("/node-types/{type_name}/options/{field_name}", response_model=list[OptionItem])
def resolve_node_type_options(type_name: str, field_name: str, request: ResolveOptionsRequest, http_request: Request) -> list[OptionItem]:
    """spec-025 Phase 5: live values for a dynamic-options input slot
    (backend/mcp/option_bindings.py) -- mirrors resolve-node-slots' shape
    (a POST body carrying the caller's in-progress config, not a GET with
    query params, since the binding's build_args may need arbitrarily
    structured input). Cached 60s per (connection, field, args) -- a
    binding's source_tool is a real MCP call, no reason to repeat it on
    every keystroke."""
    binding = option_bindings.get_option_binding(type_name, field_name)
    if binding is None:
        raise HTTPException(status_code=404, detail=f"'{type_name}' has no dynamic options for field '{field_name}'")

    caller = _caller_user_id(http_request)
    profile = resolve_connection_for_user(request.connection_name, caller)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {request.connection_name!r}")
    if profile.type != "mcp_server":
        raise HTTPException(status_code=422, detail=f"Connection '{request.connection_name}' is not an mcp_server connection")
    config = McpServerConnectionConfig.model_validate(profile.config)

    arguments = binding.build_args(request.current_config)
    cache_key = (request.connection_name, field_name, tuple(sorted(arguments.items())))
    cached = _option_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < 60:
        return cached[1]

    # spec-023: token_user_id mirrors _make_execute's own resolution -- the
    # actual running caller's token for a global connection, the private
    # owner's for a private one.
    token_user_id = caller if profile.user_id is None else profile.user_id
    try:
        server_config = generated_nodes.server_config_for_execution(config, request.connection_name, token_user_id)
        raw = generated_nodes.call_tool(server_config, binding.source_tool, arguments)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to load options: {e}") from e

    options = [OptionItem(**item) for item in binding.parse(raw)]
    _option_cache[cache_key] = (time.monotonic(), options)
    return options


@app.post("/node-types/{type_name}/resolve-slots", response_model=ResolveSlotsResponse)
def resolve_node_slots(type_name: str, request: ResolveSlotsRequest) -> ResolveSlotsResponse:
    """Per-instance port resolution for dynamic-schema node types (code,
    mcp_call, fan_out, merge). Reuses the exact backend effective_inputs/
    effective_outputs logic (SPEC-002) against a throwaway probe NodeSpec --
    not a new resolution mechanism, just an HTTP-shaped way to call the
    existing one."""
    definition = default_registry.get(type_name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Unknown node type: {type_name!r}")

    probe_node = NodeSpec(id="_probe", type=type_name, config=request.config)
    inputs = effective_inputs(definition, probe_node)
    outputs = effective_outputs(definition, probe_node)
    if inputs is None or outputs is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve ports for '{type_name}' with the given config",
        )
    return ResolveSlotsResponse(inputs=_slot_info_list(inputs), outputs=_slot_info_list(outputs))


@app.post("/runs", response_model=RunSubmitResponse, status_code=202)
def submit_run(
    graph: GraphSpec, background_tasks: BackgroundTasks, http_request: Request, graph_id: str | None = None
) -> RunSubmitResponse:
    """Validates synchronously (reusing the exact backend validate_graph(),
    zero duplicated logic) and returns immediately; the run itself executes
    in a background worker thread. Necessary given SPEC-004's loops could run
    for a while -- the HTTP request must not be held open for the duration.

    Spec-006: validate_graph() already includes the missing_connection rule,
    so a graph referencing an unconfigured connection is rejected here with
    the same 422/issues shape as any other validation failure -- no separate
    error path. Once validation passes, every referenced connection is
    resolved to a real client (backend/connections/resolver.py) and handed
    to the run as `resources={"connections": ...}`, exactly the same opaque
    resources bag mechanism the engine has supported since SPEC-002.

    `graph_id` (spec-010, optional query param): GraphSpec has no
    server-side identity anywhere in this codebase -- POST /runs takes a raw
    graph body, same as always. Unlike an activated graph (whose graph_id is
    a required part of its activation URL, spec-009), a manual run's
    graph_id is caller-chosen and optional; omitted, it's stored as null in
    the run history rather than invented. See docs/specs/010-run-persistence.md
    §8 for why this was resolved as an explicit param rather than assumed.
    """
    # spec-020: None for a shared-API-key caller (no human initiator) --
    # same request.state.user convention as POST /graphs's created_by.
    # spec-021: also the connection-resolution identity -- validate_graph's
    # missing_connection check and resolve_connections/resolve_connection_
    # profiles below all need to know whose private connections to consider,
    # not just global ones.
    run_by = _caller_user_id(http_request)

    # spec-021: a shared graph run by someone other than its author needs
    # every declared slot mapped to one of *their own* connections first --
    # checked before validation even runs, since an unmapped slot isn't a
    # "graph is invalid" problem, it's a "you haven't set this up yet" one,
    # with its own structured response naming exactly what's missing.
    slot_mappings: dict[str, str] = {}
    if graph_id is not None:
        graph_row = graphs_store.get_graph(graph_id)
        if graph_row is not None and graph_row.sharing == "shared" and run_by is not None and run_by != graph_row.created_by:
            declared_slots = graph_sharing_store.list_slots(graph_id)
            slot_mappings = graph_sharing_store.get_mappings_for_user(run_by, graph_id)
            missing = [s for s in declared_slots if s.slot_name not in slot_mappings]
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "This shared graph needs you to map its connection slot(s) before running it.",
                        "missing_slots": [
                            {"slot_name": s.slot_name, "connection_type": s.connection_type} for s in missing
                        ],
                    },
                )

    try:
        validate_graph(graph, user_id=run_by, slot_mappings=slot_mappings)
    except GraphValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[
                {"rule": issue.rule, "node_id": issue.node_id, "message": issue.message}
                for issue in e.issues
            ],
        ) from e

    try:
        resolved_connections = resolve_connections(graph, user_id=run_by, slot_mappings=slot_mappings)
        resolved_connection_profiles = resolve_connection_profiles(graph, user_id=run_by, slot_mappings=slot_mappings)
    except ConnectionNotFoundError as e:
        # Only reachable via a race (store changed between validate_graph()
        # and here) -- validate_graph()'s missing_connection rule already
        # covers the common case with the same friendly error shape.
        raise HTTPException(status_code=422, detail=str(e)) from e

    run_id = str(uuid4())
    runs.create_run(run_id, graph_id=graph_id, trigger_source="manual", run_by=run_by)
    background_tasks.add_task(
        runs.execute_run,
        run_id,
        graph,
        {
            "connections": resolved_connections,
            "connection_profiles": resolved_connection_profiles,
            "running_user_id": run_by,
            "slot_mappings": slot_mappings,
        },
    )
    return RunSubmitResponse(run_id=run_id, status="running")


@app.get("/connection-types", response_model=list[ConnectionTypeInfo])
def list_connection_types() -> list[ConnectionTypeInfo]:
    """The connection picker's entire data source for type-appropriate
    fields and Local/Cloud tabs -- mirrors GET /node-types exactly.
    `default_connection_registry.all_types()` is the only place any
    connection type list is enumerated; nothing is hardcoded here or in the
    frontend. A necessary addition beyond spec-006 §5's literal endpoint
    list, same justification as SPEC-005's resolve-slots addition."""
    infos: list[ConnectionTypeInfo] = []
    for type_name in default_connection_registry.all_types():
        definition = default_connection_registry.get(type_name)
        infos.append(
            ConnectionTypeInfo(
                type=type_name,
                category=definition.category,
                config_schema=definition.config_model.model_json_schema(),
                supports_model_listing=definition.list_models is not None,
                supports_tool_calling=definition.complete_with_tools is not None,
                supports_embedding=definition.embed is not None,
            )
        )
    return infos


def _connection_info(profile, caller_user_id: str | None = None, caller_role: str | None = None) -> ConnectionInfo:
    """spec-021: computes requires_oauth/oauth_connected from the profile's
    own config and (if relevant) the token store -- the one place this
    logic lives, so every ConnectionInfo returned anywhere can't drift out
    of sync with the actual OAuth state.

    spec-023: oauth_connected is looked up by *caller_user_id*, not
    profile.user_id -- for a private connection those are always the same
    (list_connections only ever returns the caller's own private
    connections, never someone else's), but for a global connection
    (profile.user_id is None) the OAuth-connected state is inherently
    per-caller, not per-profile: each user who connects their own account
    to the same global connection gets their own token. is_global/
    can_manage are likewise computed here, the one place, so every route
    returning a ConnectionInfo agrees on who may manage what."""
    requires_oauth = False
    oauth_connected = False
    credential_type: str | None = None
    auth_type: str = "oauth2"
    api_key_connected = False
    if profile.type == "mcp_server":
        config = McpServerConnectionConfig.model_validate(profile.config)
        requires_oauth = config.requires_oauth
        credential_type = config.credential_type
        auth_type = config.auth_type
        if requires_oauth and caller_user_id is not None:
            oauth_connected = oauth_token_storage.get_token(caller_user_id, profile.name) is not None
        if auth_type != "oauth2" and caller_user_id is not None:
            api_key_connected = api_key_storage.has_api_key(caller_user_id, profile.name)
    is_global = profile.user_id is None
    can_manage = profile.user_id == caller_user_id or (is_global and caller_role == "admin")
    return ConnectionInfo(
        name=profile.name,
        type=profile.type,
        requires_oauth=requires_oauth,
        oauth_connected=oauth_connected,
        is_global=is_global,
        can_manage=can_manage,
        credential_type=credential_type,
        auth_type=auth_type,
        api_key_connected=api_key_connected,
    )


@app.get("/connections", response_model=list[ConnectionInfo])
def list_all_connections(http_request: Request) -> list[ConnectionInfo]:
    # spec-021: this caller's own connections plus every global one -- an
    # unauthenticated/shared-key caller (user_id=None) sees only global
    # connections, exactly pre-spec-021 behavior.
    caller = _caller_user_id(http_request)
    role = _caller_role(http_request)
    return [_connection_info(c, caller, role) for c in list_connections(user_id=caller)]


@app.get("/connections/{name}/models", response_model=list[str])
def list_connection_models(name: str, http_request: Request) -> list[str]:
    """spec-006 §9: real, live models available on this connection's actual
    backend (e.g. Ollama's /api/tags), for the llm_call model-field dropdown.
    Only meaningful for connection types where
    ConnectionTypeInfo.supports_model_listing is true -- the frontend checks
    that first via GET /connection-types rather than trial-and-erroring this
    endpoint against every connection."""
    profile = resolve_connection_for_user(name, _caller_user_id(http_request))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")

    definition = default_connection_registry.get(profile.type)
    if definition is None or definition.list_models is None:
        raise HTTPException(
            status_code=422,
            detail=f"Connection type '{profile.type}' does not support model listing",
        )

    config = definition.config_model.model_validate(profile.config)
    try:
        return definition.list_models(config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list models: {e}") from e


@app.post("/connections", response_model=ConnectionInfo, status_code=201)
def create_connection(request: CreateConnectionRequest, http_request: Request) -> ConnectionInfo:
    definition = default_connection_registry.get(request.type)
    if definition is None:
        raise HTTPException(status_code=422, detail=f"Unknown connection type: {request.type!r}")
    try:
        definition.config_model.model_validate(request.config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config for '{request.type}': {e}") from e

    # spec-023: an explicit scope="global" request is the only other way to
    # get user_id=None besides the pre-existing shared-API-key-caller path
    # -- gated to admin, checked before any side effect (same "validate
    # first" shape as every rollback below).
    if request.scope == "global":
        _require_admin(http_request)

    # spec-021: saved under the caller's own user id (None for a
    # shared-API-key caller or an admin's scope="global" request -- a
    # global connection, exactly pre-spec-021 behavior for the former).
    caller = _caller_user_id(http_request)
    caller_role = _caller_role(http_request)
    owner = None if request.scope == "global" else caller
    try:
        profile = add_connection(request.name, request.type, request.config, user_id=owner)
    except DuplicateConnectionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if request.type == "mcp_server":
        config = McpServerConnectionConfig.model_validate(profile.config)
        # spec-025: an api_key/bearer connection has exactly the same
        # "nothing to discover with yet" problem OAuth already solves below
        # -- no per-user key exists at creation time, so generation is
        # deferred the same way, just without any discovery/registration
        # step (there's nothing to discover; the admin declares auth_type
        # explicitly). POST /connections/{name}/api-key generates for real
        # once a user actually pastes their own key.
        if config.auth_type != "oauth2":
            return _connection_info(profile, caller, caller_role)

        # spec-021: a remote server whose OAuth requirement wasn't already
        # known (a plain re-save of an already-connected connection has
        # requires_oauth=True already and skips straight to normal
        # discovery below) gets probed once, here. If it turns out to
        # require OAuth, capability generation is deliberately deferred --
        # no per-user token exists yet, so there's nothing to list tools
        # with. mcp_connection_oauth_callback (below) generates them for
        # real once the user actually connects.
        #
        # spec-025: real servers exist (Context7, kpidepot.com -- both
        # confirmed live) whose discovery metadata advertises an
        # authorization/token/registration endpoint that doesn't actually
        # gate their tools at all (in kpidepot's case the advertised
        # registration_endpoint doesn't even resolve to a real DCR
        # response). Trusting advertised metadata over actual behavior
        # forces users through a broken OAuth dance -- or, if the picker
        # instead defaulted to api_key, a fake "paste a key" step -- for a
        # server that needs neither. So an unauthenticated tools/list is
        # tried FIRST; only a genuine failure there is treated as "this
        # server actually requires OAuth."
        if config.transport == "remote" and not config.requires_oauth:
            try:
                generated_nodes.list_tools(transport_config(config))
                server_actually_needs_oauth = False
            except McpConnectionError:
                server_actually_needs_oauth = True
            discovered = None
            if server_actually_needs_oauth:
                try:
                    discovered = oauth_flow.discover_oauth_server(config.url)
                except oauth_flow.McpOAuthError:
                    discovered = None
            if discovered is not None:
                client_id, client_secret = config.oauth_client_id, config.oauth_client_secret
                if client_id is None:
                    if discovered.registration_endpoint is None:
                        delete_connection(request.name, user_id=owner)
                        raise HTTPException(
                            status_code=422,
                            detail="This server requires OAuth but doesn't support dynamic client "
                            "registration -- supply oauth_client_id/oauth_client_secret (from a "
                            "pre-registered OAuth client for this server) when creating this connection.",
                        )
                    public_base_url = settings_store.get_public_base_url()
                    if not public_base_url:
                        delete_connection(request.name, user_id=owner)
                        raise HTTPException(
                            status_code=422,
                            detail="No public base URL configured yet -- set one first (see Settings) "
                            "before connecting an OAuth-requiring MCP server.",
                        )
                    try:
                        client_id, client_secret = oauth_flow.register_client(
                            discovered.registration_endpoint,
                            _mcp_oauth_redirect_uri(public_base_url),
                            config.oauth_scope,
                        )
                    except oauth_flow.McpOAuthError as e:
                        delete_connection(request.name, user_id=owner)
                        raise HTTPException(
                            status_code=502, detail=f"Dynamic client registration failed: {e}"
                        ) from e
                updated_profile = update_connection_config(
                    request.name,
                    owner,
                    {
                        **profile.config,
                        "requires_oauth": True,
                        "oauth_client_id": client_id,
                        "oauth_client_secret": client_secret,
                    },
                )
                assert updated_profile is not None  # just written above, under the same (name, owner)
                return _connection_info(updated_profile, caller, caller_role)

        # spec-019: an mcp_server connection's node types are generated
        # once, here, at creation -- not polled. A discovery failure rolls
        # the creation back rather than leaving a saved connection with
        # zero generated capabilities and no signal why (the same
        # fail-closed instinct as SPEC-018's activation rollback).
        try:
            generated_nodes.generate_node_types_for_connection(request.name, owner_user_id=owner)
        except Exception as e:
            delete_connection(request.name, user_id=owner)
            raise HTTPException(
                status_code=502, detail=f"Connection saved config is valid, but tool discovery failed: {e}"
            ) from e

    return _connection_info(profile, caller, caller_role)


def _mcp_oauth_redirect_uri(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/connections/oauth/callback"


_MCP_OAUTH_STATE_COOKIE = "mcp_oauth_state"


@app.get("/connections/oauth/start")
def mcp_connection_oauth_start(
    name: str, redirect_to: str, http_request: Request, popup: bool = False
) -> RedirectResponse:
    """spec-021: begins the per-user OAuth connect flow for an mcp_server
    connection already flagged requires_oauth=True (set at creation time,
    see create_connection above). Requires a real signed-in user -- unlike
    Google's own /auth/google/login (inherently pre-auth), connecting a
    personal app connection only makes sense for someone already signed
    into this platform. Mirrors SPEC-020's google_login/google_callback
    shape exactly (signed short-lived state token, doubled as an httpOnly
    cookie for CSRF protection, real browser redirect)."""
    user_id = _caller_user_id(http_request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in before connecting an app.")

    profile = resolve_connection_for_user(name, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    config = McpServerConnectionConfig.model_validate(profile.config)
    if not config.requires_oauth:
        raise HTTPException(status_code=422, detail=f"Connection '{name}' does not require OAuth.")
    if not config.oauth_client_id:
        raise HTTPException(status_code=500, detail=f"Connection '{name}' has no OAuth client configured.")

    public_base_url = settings_store.get_public_base_url()
    if not public_base_url:
        raise HTTPException(
            status_code=422,
            detail="No public base URL configured yet -- set one first (see Settings) before connecting.",
        )

    try:
        discovered = oauth_flow.discover_oauth_server(config.url)
    except oauth_flow.McpOAuthError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach '{name}''s OAuth server: {e}") from e

    # spec-021: explicit, user-set scope wins (matches what's actually
    # configured on that operator's OAuth consent screen); falls back to
    # whatever the server's own discovery advertised as supported.
    scope = config.oauth_scope or (" ".join(discovered.scopes_supported) if discovered.scopes_supported else None)

    code_verifier, code_challenge = oauth_flow.new_pkce_pair()
    state = auth_jwt.issue_mcp_oauth_state_token(user_id, name, code_verifier, redirect_to, popup=popup)
    authorization_url = oauth_flow.build_authorization_url(
        discovered.authorization_endpoint,
        client_id=config.oauth_client_id,
        redirect_uri=_mcp_oauth_redirect_uri(public_base_url),
        state=state,
        code_challenge=code_challenge,
        resource=config.url,
        scope=scope,
    )
    response = RedirectResponse(url=authorization_url, status_code=302)
    response.set_cookie(
        _MCP_OAUTH_STATE_COOKIE,
        state,
        max_age=auth_jwt.STATE_TOKEN_EXPIRES_MINUTES * 60,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/connections/oauth/callback", response_model=None)
def mcp_connection_oauth_callback(
    code: str, state: str, mcp_oauth_state: str | None = Cookie(default=None)
) -> RedirectResponse | HTMLResponse:
    """Unauthenticated (see _AUTH_EXEMPT_PATHS) -- the browser lands here
    from the OAuth provider's own redirect, carrying no JWT of ours.
    Identity (which user, which connection) comes entirely from the signed
    state token, exactly like google_callback."""
    if not mcp_oauth_state or mcp_oauth_state != state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch -- possible CSRF, please reconnect.")
    claims = auth_jwt.verify_mcp_oauth_state_token(state)
    if claims is None:
        raise HTTPException(status_code=400, detail="OAuth state expired or invalid -- please reconnect.")

    # spec-025: popup-based OAuth UX -- when /connections/oauth/start was
    # opened with popup=true, that's carried through the whole external
    # round trip via the state token (nothing else survives it), so the
    # callback renders a tiny page that messages the opener and closes
    # itself instead of navigating this (popup) window's top level.
    popup_target_origin = urllib.parse.urlsplit(claims.redirect_to)._replace(path="", query="", fragment="").geturl()

    def _popup_response(payload: dict[str, str]) -> HTMLResponse:
        response = HTMLResponse(
            "<!doctype html><title>Connecting...</title><script>"
            f"window.opener && window.opener.postMessage({json.dumps(payload)}, {json.dumps(popup_target_origin)});"
            "window.close();"
            "</script><p>You can close this window.</p>"
        )
        response.delete_cookie(_MCP_OAUTH_STATE_COOKIE)
        return response

    def _error_redirect(message: str) -> RedirectResponse | HTMLResponse:
        if claims.popup:
            return _popup_response({"type": "mcp_oauth_result", "error": message})
        response = RedirectResponse(
            url=f"{claims.redirect_to}#mcp_oauth_error={urllib.parse.quote(message)}", status_code=302
        )
        response.delete_cookie(_MCP_OAUTH_STATE_COOKIE)
        return response

    profile = resolve_connection_for_user(claims.connection_name, claims.user_id)
    if profile is None:
        return _error_redirect(f"Connection '{claims.connection_name}' no longer exists")
    config = McpServerConnectionConfig.model_validate(profile.config)

    public_base_url = settings_store.get_public_base_url() or ""
    try:
        discovered = oauth_flow.discover_oauth_server(config.url)
        token_response = oauth_flow.exchange_code_for_token(
            discovered.token_endpoint,
            code,
            _mcp_oauth_redirect_uri(public_base_url),
            config.oauth_client_id,
            config.oauth_client_secret,
            claims.code_verifier,
        )
        oauth_flow.store_token_response(
            claims.user_id,
            claims.connection_name,
            token_response,
            discovered.token_endpoint,
            config.oauth_client_id,
            config.oauth_client_secret,
        )
    except oauth_flow.McpOAuthError as e:
        return _error_redirect(str(e))

    # Now that a real token exists, generate this connection's real node
    # types -- deferred from create_connection, which had no token to list
    # tools with. spec-023: owner_user_id must be the *profile's* real
    # scope (None for a global connection the connecting user doesn't
    # necessarily own), not claims.user_id -- discovery_user_id is what
    # actually needs to be the connecting user, so their own fresh token is
    # what's used to call tools/list.
    try:
        generated_nodes.generate_node_types_for_connection(
            claims.connection_name, owner_user_id=profile.user_id, discovery_user_id=claims.user_id
        )
    except Exception as e:
        return _error_redirect(f"Connected, but tool discovery failed: {e}")

    if claims.popup:
        return _popup_response({"type": "mcp_oauth_result", "connected": claims.connection_name})
    response = RedirectResponse(
        url=f"{claims.redirect_to}#mcp_oauth_connected={urllib.parse.quote(claims.connection_name)}",
        status_code=302,
    )
    response.delete_cookie(_MCP_OAUTH_STATE_COOKIE)
    return response


@app.post("/connections/{name}/api-key", response_model=ConnectionInfo)
def set_connection_api_key(name: str, request: SetApiKeyRequest, http_request: Request) -> ConnectionInfo:
    """spec-025: the api_key/bearer counterpart of the OAuth connect flow --
    no redirect needed, the caller already has their own key in hand.
    Any authenticated user may set *their own* key against any connection
    they can see (their own private one, or a global one), mirroring
    /connections/oauth/start's own "no admin gate on using a connection"
    principle -- only mutating a connection's config is admin-gated for a
    global one, never OAuth-connecting/setting-a-key against it."""
    caller = _caller_user_id(http_request)
    caller_role = _caller_role(http_request)
    if caller is None:
        raise HTTPException(status_code=401, detail="Sign in before connecting an app.")

    profile = resolve_connection_for_user(name, caller)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.type != "mcp_server":
        raise HTTPException(status_code=422, detail=f"Connection '{name}' is not an mcp_server connection")
    config = McpServerConnectionConfig.model_validate(profile.config)
    if config.auth_type == "oauth2":
        raise HTTPException(status_code=422, detail=f"Connection '{name}' uses OAuth, not an API key.")

    api_key_storage.save_api_key(caller, name, request.api_key, _utcnow_iso())

    # Now that a real key exists for this caller, generate this
    # connection's real node types -- deferred from create_connection,
    # same shape as the OAuth callback just above.
    try:
        generated_nodes.generate_node_types_for_connection(
            name, owner_user_id=profile.user_id, discovery_user_id=caller
        )
    except Exception as e:
        api_key_storage.delete_api_key(caller, name)
        raise HTTPException(status_code=502, detail=f"Saved, but tool discovery failed: {e}") from e

    return _connection_info(profile, caller, caller_role)


@app.post("/connections/{name}/test", response_model=TestConnectionResponse)
def test_connection_endpoint(name: str, request: TestConnectionRequest, http_request: Request) -> TestConnectionResponse:
    """Tests a real, lightweight round-trip against the connection's actual
    backend. A failed connectivity check is an expected outcome (wrong
    host, server down, bad key), not a server error -- always a normal 200
    with success=False, never a non-2xx.

    If `request.type`/`request.config` are given, tests that configuration
    directly without requiring it to be saved yet (the canvas's "Test before
    Save" flow, spec-006 §3). Otherwise re-tests the already-saved
    connection named `name`."""
    if request.type is not None and request.config is not None:
        type_name, config_dict = request.type, request.config
    else:
        profile = resolve_connection_for_user(name, _caller_user_id(http_request))
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
        type_name, config_dict = profile.type, profile.config

    definition = default_connection_registry.get(type_name)
    if definition is None:
        raise HTTPException(status_code=422, detail=f"Unknown connection type: {type_name!r}")

    try:
        config = definition.config_model.model_validate(config_dict)
    except Exception as e:
        return TestConnectionResponse(success=False, message=f"Invalid config: {e}")

    result = definition.test_connection(config)
    return TestConnectionResponse(success=result.success, message=result.message)


@app.delete("/connections/{name}", status_code=204)
def delete_connection_endpoint(name: str, http_request: Request) -> None:
    # spec-021: resolve first (mine, falling back to global -- same policy
    # as every other connection lookup here) to find which exact scope
    # actually owns `name`, then delete that scope specifically. A plain
    # `delete_connection(name, user_id=caller)` would wrongly 404 when an
    # authenticated human deletes a pre-spec-021 global connection (e.g.
    # Ollama/Anthropic), breaking the existing single-operator workflow.
    profile = resolve_connection_for_user(name, _caller_user_id(http_request))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    # spec-023: a global connection is admin-managed -- a non-admin deleting
    # their own private connection (profile.user_id is their own id) is
    # completely unaffected by this check.
    if profile.user_id is None:
        _require_admin(http_request)
    delete_connection(name, user_id=profile.user_id)
    # spec-019: a no-op for any connection that never had generated node
    # types (every type except mcp_server) -- cheap and correct either way.
    generated_nodes.unregister_for_connection(name, owner_user_id=profile.user_id)
    # spec-021: a no-op for any connection that was never OAuth-connected --
    # avoids leaving a stored token orphaned once its connection is gone.
    if profile.type == "mcp_server" and profile.user_id is not None:
        oauth_token_storage.delete_token(profile.user_id, name)


@app.put("/connections/{name}", response_model=ConnectionInfo)
def update_connection_endpoint(name: str, request: UpdateConnectionRequest, http_request: Request) -> ConnectionInfo:
    """spec-023: config mutation gets its own route -- update_connection_config
    existed internally (spec-021, for persisting discovered OAuth client
    info) but had no HTTP route of its own. Resolves the caller's own
    private connection first, then falls back to a global one; a private
    connection belonging to someone else is a 404 (same "don't reveal
    existence" pattern used throughout this file), not distinguished from
    "doesn't exist at all"."""
    caller = _caller_user_id(http_request)
    caller_role = _caller_role(http_request)
    profile = get_connection(name, user_id=caller)
    if profile is None:
        profile = get_connection(name, user_id=None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.user_id is None:
        _require_admin(http_request)
    definition = default_connection_registry.get(profile.type)
    if definition is None:
        raise HTTPException(status_code=422, detail=f"Unknown connection type: {profile.type!r}")
    try:
        definition.config_model.model_validate(request.config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config for '{profile.type}': {e}") from e
    updated = update_connection_config(name, profile.user_id, request.config)
    assert updated is not None  # just resolved above, under the same (name, profile.user_id)
    return _connection_info(updated, caller, caller_role)


@app.post("/connections/{name}/promote-to-global", response_model=ConnectionInfo)
def promote_connection_to_global(name: str, http_request: Request) -> ConnectionInfo:
    """spec-023: lets an admin turn their own existing private connection
    into a global one without deleting and recreating it -- closes exactly
    the situation a connection created before this spec existed is in
    (private to whoever happened to create it, with no other way to make it
    visible to every user). Carries the config over as-is (resolved open
    question -- no re-discovery/re-registration). No token migration
    needed: SPEC-021's oauth_token_storage is keyed by (connecting user,
    connection name), never by the connection profile's own ownership, so
    the admin's existing token for this connection (if any) stays valid
    unchanged after promotion."""
    _require_admin(http_request)
    caller = _caller_user_id(http_request)
    caller_role = _caller_role(http_request)
    # Must be the admin's own private connection -- get_connection's
    # exact-scope semantics naturally 404 both "someone else's" and
    # "already global" without a separate check.
    profile = get_connection(name, user_id=caller)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No private connection named {name!r} owned by you")
    updated = set_connection_owner(name, caller, None)
    if updated is None:
        raise HTTPException(status_code=409, detail=f"A global connection named {name!r} already exists")
    if updated.type == "mcp_server":
        try:
            # spec-023: the promoting admin's own token (if they have one --
            # e.g. they used this connection privately before promoting it,
            # exactly the my-gmail situation this action exists for) is what
            # discovery uses; the resulting node types are still named as
            # global (owner_user_id=None), unaffected by whose token
            # discovered them.
            generated_nodes.generate_node_types_for_connection(name, owner_user_id=None, discovery_user_id=caller)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Promoted to global, but tool discovery failed: {e} -- retry via refresh-capabilities",
            ) from e
    return _connection_info(updated, caller, caller_role)


@app.post("/connections/{name}/catalog-bootstrap", response_model=RefreshCapabilitiesResponse)
def bootstrap_catalog_connection(name: str, http_request: Request) -> RefreshCapabilitiesResponse:
    """spec-025: the admin-only, explicit-intent action for a pre-populated
    catalog entry -- registers a global mcp_server connection's real node
    types using the admin's own already-connected credential (OAuth token
    or api_key, per resolved open question: "admin's own connect is
    sufficient", no separate disposable discovery-only credential needed).

    Mechanically this calls the exact same generate_node_types_for_connection
    that refresh_capabilities and the OAuth-callback/api-key-set routes
    already call -- there is no second discovery code path. What this adds
    is (a) an admin-only gate, unlike refresh_capabilities which any caller
    who can resolve the connection may call, and (b) an explicit, checkable
    precondition that the admin has personally connected first, so the
    error is "connect your own account to this app first", not an opaque
    502 from a live tools/list call with no credential attached at all."""
    _require_admin(http_request)
    caller = _caller_user_id(http_request)
    if caller is None:
        raise HTTPException(status_code=401, detail="Sign in before bootstrapping a catalog connection.")
    profile = get_connection(name, user_id=None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No global connection named {name!r}")
    if profile.type != "mcp_server":
        raise HTTPException(status_code=422, detail=f"Connection '{name}' is not an mcp_server connection")
    config = McpServerConnectionConfig.model_validate(profile.config)
    if config.auth_type == "oauth2" and config.requires_oauth:
        has_credential = oauth_token_storage.get_token(caller, name) is not None
    elif config.auth_type != "oauth2":
        has_credential = api_key_storage.has_api_key(caller, name)
    else:
        has_credential = True  # doesn't require any per-user credential at all
    if not has_credential:
        raise HTTPException(
            status_code=409,
            detail=f"Connect your own account to '{name}' first (Settings -> Connections), then bootstrap it.",
        )
    try:
        generated_types = generated_nodes.generate_node_types_for_connection(
            name, owner_user_id=profile.user_id, discovery_user_id=caller
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Bootstrap failed: {e}") from e
    return RefreshCapabilitiesResponse(generated_types=generated_types)


@app.get("/connections/private-summary", response_model=list[PrivateConnectionSummary])
def list_private_connections_summary(http_request: Request) -> list[PrivateConnectionSummary]:
    """spec-023: admin-only, names/types/owners only -- never config or
    secrets. Resolved open question: admin gets this much visibility into
    other users' private connections for support/debugging, nothing more."""
    _require_admin(http_request)
    return [
        PrivateConnectionSummary(user_id=c.user_id, name=c.name, type=c.type)
        for c in list_connections_unscoped()
        if c.user_id is not None
    ]


@app.post("/connections/{name}/refresh-capabilities", response_model=RefreshCapabilitiesResponse)
def refresh_capabilities(name: str, http_request: Request) -> RefreshCapabilitiesResponse:
    """spec-019: re-runs live discovery for an `mcp_server` connection and
    updates its generated node set -- discovery is refreshed explicitly,
    never polled (see backend/mcp/generated_nodes.py's own docstring). A
    failed refresh leaves the previously-generated set intact (see
    generate_node_types_for_connection's ordering)."""
    owner = _caller_user_id(http_request)
    profile = resolve_connection_for_user(name, owner)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.type != "mcp_server":
        raise HTTPException(status_code=422, detail=f"Connection '{name}' is not an mcp_server connection")
    try:
        # spec-021: regenerate under the *profile's actual owner*
        # (profile.user_id), not necessarily the caller -- a caller who
        # resolved to a global connection (profile.user_id is None) must
        # regenerate it as global, not accidentally claim it as their own.
        # spec-023: discovery_user_id is always the actual caller, though --
        # for a global connection, discovery needs *some* real user's token,
        # and the caller triggering this refresh is the only one available
        # in this context (their own token if they have one; a clear
        # "reconnect" error via _server_config_for if they don't).
        generated_types = generated_nodes.generate_node_types_for_connection(
            name, owner_user_id=profile.user_id, discovery_user_id=owner
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to refresh capabilities: {e}") from e
    return RefreshCapabilitiesResponse(generated_types=generated_types)


@app.delete("/connections/{name}/vectors", status_code=204)
def clear_connection_vectors(name: str, http_request: Request) -> None:
    """spec-011 §7: clears a vector_store connection's stored chunks without
    deleting the connection profile itself -- avoids needing to delete and
    recreate an entire connection just to start over during testing."""
    profile = resolve_connection_for_user(name, _caller_user_id(http_request))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name!r}")
    if profile.type != "vector_store":
        raise HTTPException(
            status_code=422, detail=f"Connection '{name}' is not a vector_store connection"
        )
    definition = default_connection_registry.get(profile.type)
    config = definition.config_model.model_validate(profile.config)
    client = definition.build_client(config)
    client.clear()


# spec-018: the one app-level setting needed to auto-register external
# webhooks (Telegram's setWebhook/deleteWebhook) -- see
# backend/storage/settings_store.py's module docstring for why this is a
# separate plain (unencrypted) store, not a *_connection field or a
# per-graph setting.


@app.get("/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    return SettingsResponse(public_base_url=settings_store.get_public_base_url())


@app.put("/settings", response_model=UpdateSettingsResponse)
def update_settings(request: UpdateSettingsRequest) -> UpdateSettingsResponse:
    """Spec-018 §6's resolved open question: a lightweight, non-blocking
    reachability check against the new value's /health (SPEC-017) --
    surfaced as a warning, never a hard block, since a URL can be correct
    but momentarily unreachable (e.g. a tunnel not yet started)."""
    url = request.public_base_url.rstrip("/")
    settings_store.set_public_base_url(url)
    warning: str | None = None
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            if resp.status != 200:
                warning = f"{url}/health responded with status {resp.status}"
    except Exception as e:
        warning = f"Could not reach {url}/health: {e}"
    return UpdateSettingsResponse(public_base_url=url, warning=warning)


@app.get("/runs", response_model=RunListResponse)
def list_runs(
    graph_id: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RunListResponse:
    """spec-010: paginated run history, read exclusively from the durable
    SQLite store (backend/storage/runs_store.py) -- listing is inherently
    about history/browsing, not the live "still running" hot path that
    GET /runs/{run_id} optimizes for, so there's no in-memory fallback to
    reason about here. Summaries only (no trace/result) per spec §5."""
    rows, total = runs_store.list_run_records(
        graph_id=graph_id,
        status=status,
        trigger_source=trigger_source,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(
        runs=[
            RunSummary(
                run_id=r.run_id,
                graph_id=r.graph_id,
                status=r.status,
                trigger_source=r.trigger_source,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    """spec-010: falls back to the durable SQLite store when a run is no
    longer in the in-memory `_runs` dict (e.g. the API process was
    restarted since it ran) -- this is what makes a run's result queryable
    long after the process that ran it. The in-memory path stays primary
    (checked first) since it's the only place `running_node_ids` (live
    per-node progress) exists at all; a persisted-only record has none to
    report, since spec-010's write point is after run_graph returns, not
    during."""
    record = runs.get_run_snapshot(run_id)
    if record is not None:
        pending_approvals = [
            PendingApprovalInfo(approval_id=p.approval_id, tool_name=p.tool_name, arguments=p.arguments)
            for p in approvals.list_pending_for_run(run_id)
        ]
        return RunStatusResponse(
            run_id=record.run_id,
            status=record.status,
            graph_id=record.graph_id,
            trigger_source=record.trigger_source,
            running_node_ids=record.running_node_ids,
            active_sub_node_ids=record.active_sub_node_ids,
            pending_approvals=pending_approvals,
            trace=record.trace,
            result=record.result,
            error=record.error,
            run_by=record.run_by,
        )

    row = runs_store.get_run_record(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")

    result: dict[str, Any] | None = None
    trace: list[Any] = []
    if row.result_json is not None:
        parsed = json.loads(row.result_json)
        result = parsed.get("result")
        trace = parsed.get("trace", [])
    return RunStatusResponse(
        run_id=row.run_id,
        status=row.status,
        graph_id=row.graph_id,
        trigger_source=row.trigger_source,
        running_node_ids=[],
        active_sub_node_ids=[],
        pending_approvals=[],
        trace=trace,
        result=result,
        error=row.error,
        run_by=row.run_by,
    )


@app.post("/runs/{run_id}/approvals/{approval_id}")
def resolve_run_approval(run_id: str, approval_id: str, request: ResolveApprovalRequest) -> dict[str, str]:
    """spec-019: answers a pending approval-gated tool call from the canvas
    (backend/execution/approvals.py) -- unblocks the node's execute() call
    that's waiting on it. `run_id` isn't itself used to look up the
    approval (approval_id alone is already globally unique) but is part of
    the URL for symmetry with every other /runs/{run_id}/... route and so
    a client can't accidentally resolve an approval against the wrong run
    without it being visible in the URL."""
    if not approvals.resolve_approval(approval_id, request.approved, remember=request.remember):
        raise HTTPException(status_code=404, detail=f"Unknown or already-resolved approval_id: {approval_id!r}")
    return {"status": "resolved"}


# --- spec-009: trigger nodes (schedule + webhook) ---------------------------
#
# `graph_id` has no persisted identity anywhere else in this codebase (no
# `id` field on GraphSpec, no server-side "save a graph" concept -- the
# canvas's own save/load is a local file download/upload, per SPEC-005).
# Rather than invent a whole new /graphs CRUD resource the spec never asked
# for, POST /graphs/{graph_id}/activate carries the full GraphSpec as its
# own request body: `graph_id` is caller-chosen, and the graph is cached in
# `backend.triggers.registry` purely in-memory, for exactly as long as it's
# active -- consistent with this spec's own explicitly-accepted "no
# persistence across restarts" scope line (§3).


def _webhook_path(graph_id: str, node_id: str) -> str:
    return f"/webhooks/{graph_id}/{node_id}"


def _to_schema_trigger(t: trigger_registry.TriggerRecord) -> TriggerInfo:
    return TriggerInfo(node_id=t.node_id, type=t.type, endpoint_or_schedule=t.endpoint_or_schedule)


def _make_webhook_handler(graph_id: str, node_id: str):
    # Plain `def`, never `async def` -- same blanket policy as every other
    # route in this module (see module docstring): FastAPI/Starlette parses
    # `payload` before calling the handler regardless of sync/async, so this
    # needs no `await request.json()` to stay a plain sync callable.
    def webhook_handler(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, str]:
        try:
            run_id = trigger_runner.fire(
                graph_id, node_id, payload=payload, trigger_source="webhook"
            )
        except trigger_runner.GraphNotActiveError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"run_id": run_id}

    return webhook_handler


def _deactivate(graph_id: str) -> None:
    trigger_scheduler.remove_jobs_for_graph(graph_id)
    prefix = f"/webhooks/{graph_id}/"
    app.router.routes = [
        route for route in app.router.routes if not getattr(route, "path", "").startswith(prefix)
    ]
    trigger_registry.clear_active(graph_id)


# spec-018/019: auto-registering a trigger adapter's external webhook on
# Activate/Deactivate -- plain graph-edge traversal plus the generic
# integration-agnostic interface (backend/triggers/webhook_sync.py). No
# adapter type is named here; Telegram is just the first registered
# handler (backend/integrations/telegram/webhook_sync.py).


def _sync_webhooks_on_activate(
    graph_id: str,
    graph: GraphSpec,
    triggers: list[trigger_registry.TriggerRecord],
    user_id: str | None = None,
) -> None:
    """Called after _register_triggers succeeds. A failure here rolls back
    the whole activation (matching the existing invalid-cron-expression
    precedent) -- spec-018 §4's resolved decision: Activate must not report
    success while the actual external wiring silently didn't happen.

    spec-021: `user_id` is the activating caller's id (or the graph's
    `created_by`, for startup re-activation, which has no live caller) --
    resolve_connections needs it to see that user's own private connections,
    not just global ones."""
    pairs = webhook_sync.adapter_pairs_for_graph(graph)
    if not pairs:
        return

    public_base_url = settings_store.get_public_base_url()
    if not public_base_url:
        raise HTTPException(
            status_code=422,
            detail="This graph has a trigger adapter that needs a registered webhook, but no "
            "public base URL is configured yet -- set one first (see Settings) before activating.",
        )

    try:
        resolved_connections = resolve_connections(graph, user_id=user_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    trigger_by_node_id = {t.node_id: t for t in triggers}
    for webhook_node, adapter_node in pairs:
        handler = webhook_sync.get_handler(adapter_node.type)
        # adapter_pairs_for_graph only returns pairs whose adapter type has
        # a registered handler, so this is always non-None here -- asserted
        # rather than silently trusted.
        assert handler is not None
        # The reported endpoint already carries `?key=...` (SPEC-017) --
        # the exact same URL the trigger chip shows, immediately usable.
        full_url = f"{public_base_url}{trigger_by_node_id[webhook_node.id].endpoint_or_schedule}"
        try:
            handler.sync_on_activate(webhook_node, adapter_node, full_url, resolved_connections)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e


def _sync_webhooks_on_deactivate(graph_id: str, graph: GraphSpec, user_id: str | None = None) -> None:
    """Best-effort, unlike activate's fail-closed behavior -- deactivation's
    primary job (removing the local route/registration) must still succeed
    even if the external API is briefly unreachable; a stray webhook the
    external service will 404 against on its next delivery attempt anyway
    is a smaller problem than a graph stuck unable to deactivate. `user_id`:
    see _sync_webhooks_on_activate above."""
    pairs = webhook_sync.adapter_pairs_for_graph(graph)
    if not pairs:
        return
    try:
        resolved_connections = resolve_connections(graph, user_id=user_id)
    except ConnectionNotFoundError:
        logger.exception("Could not resolve connections to deregister webhook(s) for graph_id=%s", graph_id)
        return
    for webhook_node, adapter_node in pairs:
        handler = webhook_sync.get_handler(adapter_node.type)
        assert handler is not None
        try:
            handler.sync_on_deactivate(webhook_node, adapter_node, resolved_connections)
        except RuntimeError:
            logger.exception(
                "Failed to deregister webhook for adapter '%s', graph_id=%s", adapter_node.id, graph_id
            )


def _register_triggers(graph_id: str, graph: GraphSpec) -> list[trigger_registry.TriggerRecord]:
    """The actual registration work (a cron job per `schedule_trigger`, a
    dynamic webhook route per `webhook_trigger`) -- spec-015 §4: extracted
    so both `activate_graph` (the HTTP endpoint) and the startup
    re-activation pass call the exact same code, never two copies to keep
    in sync. Raises HTTPException on an invalid cron expression, same as
    before this extraction; the startup caller wraps this in a broader
    try/except instead of relying on this raising HTTPException
    specifically (it's just a convenient exception type to reuse, not an
    HTTP-layer concept the startup path actually needs)."""
    triggers: list[trigger_registry.TriggerRecord] = []
    for node in graph.nodes:
        if node.type == "schedule_trigger":
            cron = node.config.get("cron", "")
            try:
                trigger_scheduler.add_schedule_job(
                    graph_id, node.id, cron, lambda gid=graph_id, nid=node.id: trigger_runner.fire(gid, nid)
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid cron expression for node '{node.id}': {e}",
                ) from e
            triggers.append(
                trigger_registry.TriggerRecord(
                    node_id=node.id, type="schedule_trigger", endpoint_or_schedule=cron
                )
            )
        elif node.type == "webhook_trigger":
            path = _webhook_path(graph_id, node.id)
            app.add_api_route(path, _make_webhook_handler(graph_id, node.id), methods=["POST"])
            # spec-017: the *reported* endpoint carries the API key as a
            # query param, ready to use directly in an external service's
            # webhook config (e.g. Telegram's setWebhook) -- but route
            # registration above uses the bare `path`, since a route
            # pattern isn't a URL and can't include a query string.
            display_path = f"{path}?key={_configured_api_key()}"
            triggers.append(
                trigger_registry.TriggerRecord(
                    node_id=node.id, type="webhook_trigger", endpoint_or_schedule=display_path
                )
            )
    return triggers


@app.post("/graphs/{graph_id}/activate", response_model=ActivateGraphResponse)
def activate_graph(graph_id: str, graph: GraphSpec, http_request: Request) -> ActivateGraphResponse:
    """Registers a cron job per `schedule_trigger` node and a dynamic
    webhook route per `webhook_trigger` node. Validates first via the exact
    same validate_graph() every other entry point uses (422 with the same
    issues shape on failure). Re-activating an already-active graph_id
    replaces the prior registration outright rather than erroring --
    activation is idempotent from the caller's perspective.

    Spec-015: also persists is_active=true + the activated spec to
    `graphs_store`, upserting a row if `graph_id` was never explicitly
    saved via POST /graphs first (SPEC-009's original "graph_id is
    caller-chosen" contract, unchanged) -- this is what makes startup
    re-activation possible."""
    activated_by = _caller_user_id(http_request)
    try:
        validate_graph(graph, user_id=activated_by)
    except GraphValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[
                {"rule": issue.rule, "node_id": issue.node_id, "message": issue.message}
                for issue in e.issues
            ],
        ) from e

    if trigger_registry.get_active(graph_id) is not None:
        _deactivate(graph_id)

    try:
        triggers = _register_triggers(graph_id, graph)
        # spec-018/019: auto-registers a trigger adapter's external webhook,
        # if this graph has one with a registered sync handler -- a no-op
        # for every other graph. Failure here rolls back exactly like an
        # invalid cron expression does.
        _sync_webhooks_on_activate(graph_id, graph, triggers, user_id=activated_by)
    except HTTPException:
        _deactivate(graph_id)  # don't leave a half-registered graph behind
        raise

    trigger_registry.set_active(graph_id, graph, triggers, created_by=activated_by)
    graphs_store.set_active_state(graph_id, graph.model_dump_json(), is_active=True, updated_at=_utcnow_iso())
    return ActivateGraphResponse(status="active", triggers=[_to_schema_trigger(t) for t in triggers])


@app.post("/graphs/{graph_id}/deactivate")
def deactivate_graph(graph_id: str, http_request: Request) -> dict[str, str]:
    active = trigger_registry.get_active(graph_id)
    if active is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' is not active")
    # spec-018/019: best-effort deregistration of a trigger adapter's
    # external webhook, if any -- see _sync_webhooks_on_deactivate's own
    # docstring for why this is deliberately not fatal to deactivation
    # itself, unlike activate.
    _sync_webhooks_on_deactivate(graph_id, active.graph, user_id=_caller_user_id(http_request))
    _deactivate(graph_id)
    graphs_store.set_is_active(graph_id, is_active=False, updated_at=_utcnow_iso())
    return {"status": "inactive"}


@app.get("/graphs/active", response_model=list[ActiveGraphInfo])
def list_active_graphs() -> list[ActiveGraphInfo]:
    return [
        ActiveGraphInfo(
            graph_id=g.graph_id, triggers=[_to_schema_trigger(t) for t in g.triggers]
        )
        for g in trigger_registry.list_active()
    ]


def _reactivate_persisted_graphs() -> None:
    """Spec-015 §4: the actual fix for triggers vanishing on a backend
    restart. Re-registers every graph flagged is_active=true in
    `graphs_store` via the exact same `_register_triggers` the /activate
    endpoint uses -- one broken persisted graph (e.g. its spec no longer
    validates against a since-changed node registry) must not prevent any
    other graph from re-activating, so each graph's re-activation is
    independently try/excepted and logged rather than one loop that could
    abort partway through."""
    for row in graphs_store.list_active_graphs():
        try:
            graph = GraphSpec.model_validate_json(row.spec_json)
            # spec-021: no live caller at startup -- the graph's own
            # created_by (spec-020) stands in for "whose private
            # connections this graph may reference," exactly the identity
            # that activated it originally.
            validate_graph(graph, user_id=row.created_by)
            triggers = _register_triggers(row.graph_id, graph)
            trigger_registry.set_active(row.graph_id, graph, triggers, created_by=row.created_by)
        except Exception:
            logger.exception("Failed to re-activate graph_id=%s on startup", row.graph_id)


# spec-015: saved graphs, giving GraphSpec a real server-side identity.
# Registered after GET /graphs/active (above) so Starlette's registration-
# order route matching tries the literal "/graphs/active" path first --
# GET /graphs/{graph_id} below would otherwise swallow it (graph_id="active").


def _slot_specs_from_store(graph_id: str) -> list[ConnectionSlotSpec]:
    return [
        ConnectionSlotSpec(slot_name=s.slot_name, connection_type=s.connection_type)
        for s in graph_sharing_store.list_slots(graph_id)
    ]


@app.post("/graphs", response_model=GraphDetail, status_code=201)
def create_graph(request: CreateGraphRequest, http_request: Request) -> GraphDetail:
    graph_id = str(uuid4())
    now = _utcnow_iso()
    # spec-020: http_request.state.user is None for a shared-API-key caller
    # (no human initiator) -- created_by stays correctly null in that case,
    # set only for a real logged-in human via require_auth.
    created_by = _caller_user_id(http_request)
    row = graphs_store.create_graph(
        graph_id, request.name, request.spec.model_dump_json(), now, created_by=created_by, sharing=request.sharing
    )
    if request.sharing == "shared":
        graph_sharing_store.set_slots(
            graph_id, [(s.slot_name, s.connection_type) for s in request.connection_slots]
        )
    return GraphDetail(
        graph_id=row.graph_id,
        name=row.name,
        spec=request.spec,
        is_active=row.is_active,
        created_by=row.created_by,
        sharing=row.sharing,
        connection_slots=request.connection_slots if request.sharing == "shared" else [],
    )


@app.get("/graphs", response_model=list[GraphSummary])
def list_graphs() -> list[GraphSummary]:
    return [
        GraphSummary(graph_id=g.graph_id, name=g.name, is_active=g.is_active, updated_at=g.updated_at, sharing=g.sharing)
        for g in graphs_store.list_graphs()
    ]


@app.get("/graphs/{graph_id}", response_model=GraphDetail)
def get_graph(graph_id: str) -> GraphDetail:
    row = graphs_store.get_graph(graph_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return GraphDetail(
        graph_id=row.graph_id,
        name=row.name,
        spec=GraphSpec.model_validate_json(row.spec_json),
        is_active=row.is_active,
        created_by=row.created_by,
        sharing=row.sharing,
        connection_slots=_slot_specs_from_store(graph_id) if row.sharing == "shared" else [],
    )


@app.put("/graphs/{graph_id}", response_model=GraphDetail)
def update_graph(graph_id: str, request: UpdateGraphRequest) -> GraphDetail:
    spec_json = request.spec.model_dump_json() if request.spec is not None else None
    row = graphs_store.update_graph(
        graph_id, _utcnow_iso(), name=request.name, spec_json=spec_json, sharing=request.sharing
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if row.sharing == "shared" and request.connection_slots is not None:
        graph_sharing_store.set_slots(
            graph_id, [(s.slot_name, s.connection_type) for s in request.connection_slots]
        )
    elif row.sharing == "private":
        # spec-021: switched back to private (or created that way) -- its
        # declared slots and every runner's mapping against them are
        # meaningless now; don't leave them around as stale, confusing state.
        graph_sharing_store.clear_slots(graph_id)
    return GraphDetail(
        graph_id=row.graph_id,
        name=row.name,
        spec=GraphSpec.model_validate_json(row.spec_json),
        is_active=row.is_active,
        created_by=row.created_by,
        sharing=row.sharing,
        connection_slots=_slot_specs_from_store(graph_id) if row.sharing == "shared" else [],
    )


@app.delete("/graphs/{graph_id}", status_code=204)
def delete_graph(graph_id: str) -> None:
    """Deactivates first if currently active (spec-015 §7's resolved open
    question) -- DELETE's usual "just make it gone" semantics, not a
    separate forced manual deactivate step first."""
    if trigger_registry.get_active(graph_id) is not None:
        _deactivate(graph_id)
    deleted = graphs_store.delete_graph(graph_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    graph_sharing_store.clear_slots(graph_id)


@app.post("/graphs/{graph_id}/connection-mapping", response_model=SlotMappingResponse)
def set_connection_mapping(graph_id: str, request: SetSlotMappingRequest, http_request: Request) -> SlotMappingResponse:
    """spec-021: a non-author runner's one-time "which of your own
    connections fills this slot" step -- remembered thereafter (POST /runs'
    pre-flight check above never asks again once a mapping exists). Requires
    a real signed-in user; validates the named connection is actually
    visible to them (their own, or global) before accepting the mapping, so
    a typo'd or someone-else's connection name can't be silently stored."""
    user_id = _caller_user_id(http_request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in to map a connection slot.")

    graph_row = graphs_store.get_graph(graph_id)
    if graph_row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if graph_row.sharing != "shared":
        raise HTTPException(status_code=422, detail=f"Graph '{graph_id}' is not shared -- it has no connection slots.")

    declared = {s.slot_name for s in graph_sharing_store.list_slots(graph_id)}
    if request.slot_name not in declared:
        raise HTTPException(status_code=422, detail=f"'{request.slot_name}' is not a declared slot on this graph.")

    if resolve_connection_for_user(request.connection_name, user_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {request.connection_name!r}")

    graph_sharing_store.set_mapping(user_id, graph_id, request.slot_name, request.connection_name)
    return SlotMappingResponse(slot_name=request.slot_name, connection_name=request.connection_name)


@app.get("/graphs/{graph_id}/connection-mapping", response_model=list[SlotMappingResponse])
def list_connection_mappings(graph_id: str, http_request: Request) -> list[SlotMappingResponse]:
    """The caller's own current slot mappings for this graph -- lets the
    canvas show which slots are already mapped before prompting for the
    rest, rather than only finding out via a 409 on POST /runs."""
    user_id = _caller_user_id(http_request)
    if user_id is None:
        return []
    mappings = graph_sharing_store.get_mappings_for_user(user_id, graph_id)
    return [SlotMappingResponse(slot_name=k, connection_name=v) for k, v in mappings.items()]


# --- spec-029: invoke API -------------------------------------------------

DEFAULT_INVOKE_TIMEOUT_SECONDS = 60
"""Used when POST /invoke is called by a JWT or the shared API key -- tiers
1-2 of require_auth carry no configured timeout of their own (that's an
invoke key's own property, set at creation), so there's nothing to look up
for those callers."""

_MAX_INVOKE_TIMEOUT_SECONDS = 300


class ContractError(Exception):
    """A malformed invoke contract -- two text_input (or two text_output)
    nodes resolving to the same external field name (same explicit `label`,
    or one node's `label` colliding with another's raw id). Distinct from
    GraphValidationError, which covers the graph's own structural validity;
    this is specifically about the invoke-facing contract layered on top.
    Raised by _build_contract, translated to a 422 by every route that
    calls it -- an ambiguous contract must fail loudly, not silently run
    the wrong node."""


def _build_contract(
    graph: GraphSpec,
) -> tuple[dict[str, str], dict[str, str], list[InvokeContractField], list[InvokeContractField]]:
    """Derives the invoke-facing contract from a graph's structure --
    spec-029's whole point is reusing `text_input`/`text_output` nodes as
    the external boundary rather than inventing a parallel metadata layer.
    An external field's name is that node's `label` if set, else its own
    `id`. Returns (input_name -> node_id, output_name -> node_id, input
    fields, output fields); raises ContractError on a name collision
    within one direction."""
    input_name_to_node_id: dict[str, str] = {}
    output_name_to_node_id: dict[str, str] = {}
    input_fields: list[InvokeContractField] = []
    output_fields: list[InvokeContractField] = []

    for node in graph.nodes:
        if node.type == "text_input":
            name = node.config.get("label") or node.id
            if name in input_name_to_node_id:
                raise ContractError(f"Two text_input nodes both resolve to external field name '{name}'")
            input_name_to_node_id[name] = node.id
            # spec-029 §7's resolved open question: a non-empty saved
            # default makes the field optional (used if omitted); empty or
            # absent makes it required.
            default = node.config.get("value") or None
            input_fields.append(
                InvokeContractField(
                    name=name, node_id=node.id, direction="input", required=default is None, default=default
                )
            )
        elif node.type == "text_output":
            name = node.config.get("label") or node.id
            if name in output_name_to_node_id:
                raise ContractError(f"Two text_output nodes both resolve to external field name '{name}'")
            output_name_to_node_id[name] = node.id
            output_fields.append(InvokeContractField(name=name, node_id=node.id, direction="output", required=False))

    return input_name_to_node_id, output_name_to_node_id, input_fields, output_fields


def _apply_inputs(graph: GraphSpec, input_name_to_node_id: dict[str, str], supplied: dict[str, str]) -> None:
    """Overrides each supplied external input's value directly on the
    matching `text_input` node's `config["value"]` -- the same field
    `TextInputConfig.value`/`execute_text_input` already read, NOT
    SPEC-025's `input_values` mechanism (that only satisfies a declared
    INPUT SLOT with no incoming edge; `text_input` declares `inputs=[]`, so
    it has no slot for `input_values` to attach to at all).

    `graph` must be a disposable, freshly-parsed GraphSpec (every call site
    parses fresh from `graphs_store.get_graph(...).spec_json` immediately
    before this runs) -- mutating its `NodeSpec.config` dicts in place can
    never write back to the persisted row, which only ever sees the
    original JSON string again on the next `get_graph` call."""
    nodes_by_id = {node.id: node for node in graph.nodes}
    for name, value in supplied.items():
        nodes_by_id[input_name_to_node_id[name]].config["value"] = value


@app.get("/graphs/{graph_id}/contract", response_model=InvokeContractResponse)
def get_graph_contract(graph_id: str) -> InvokeContractResponse:
    """spec-029: the invoke-facing input/output field list for this graph,
    derived from its text_input/text_output nodes -- lets a caller (or the
    graph's own settings panel, for a human previewing before sharing a
    key) discover what to send without reading the raw graph JSON.
    Reachable by a JWT, the shared API key, or an invoke key scoped to
    this graph_id (require_auth's tier 3)."""
    graph_row = graphs_store.get_graph(graph_id)
    if graph_row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    graph = GraphSpec.model_validate_json(graph_row.spec_json)
    try:
        _, _, input_fields, output_fields = _build_contract(graph)
    except ContractError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return InvokeContractResponse(graph_id=graph_id, inputs=input_fields, outputs=output_fields)


@app.post("/graphs/{graph_id}/invoke", response_model=InvokeGraphResponse)
def invoke_graph(graph_id: str, request: InvokeGraphRequest, http_request: Request) -> InvokeGraphResponse:
    """spec-029: runs this one persisted graph synchronously (blocking the
    request, no BackgroundTasks dispatch) and returns its named outputs --
    the callable-workflow counterpart to POST /runs (which takes a whole
    graph body and returns immediately). Never accepts a graph body itself
    -- always loads the persisted spec by graph_id, so a caller only ever
    needs the contract (GET .../contract), not the graph's internals.

    Connection resolution runs as the graph owner's own identity
    (`graph_row.created_by`) with no slot mappings -- an invoke caller
    isn't a signed-in human with connections of their own to map. If a
    genuinely different runner identity for invoke ever becomes a real
    need, that's new scope for a later spec, not silently handled here.

    Known limitation, not fixed by this spec: a graph that hits a mid-run
    approval gate (backend/execution/approvals.py) has no human watching
    to answer it when invoked externally -- it will exceed its timeout and
    the underlying run can never complete. Inherent to approvals' existing
    canvas-facing design, not introduced here."""
    graph_row = graphs_store.get_graph(graph_id)
    if graph_row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")

    graph = GraphSpec.model_validate_json(graph_row.spec_json)
    try:
        input_name_to_node_id, output_name_to_node_id, input_fields, _ = _build_contract(graph)
    except ContractError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    unknown = sorted(set(request.inputs) - set(input_name_to_node_id))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unrecognized input field(s): {unknown}")

    missing = sorted(f.name for f in input_fields if f.required and f.name not in request.inputs)
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required input field(s): {missing}")

    _apply_inputs(graph, input_name_to_node_id, request.inputs)

    run_by = graph_row.created_by
    try:
        validate_graph(graph, user_id=run_by)
    except GraphValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=[{"rule": issue.rule, "node_id": issue.node_id, "message": issue.message} for issue in e.issues],
        ) from e

    try:
        resolved_connections = resolve_connections(graph, user_id=run_by)
        resolved_connection_profiles = resolve_connection_profiles(graph, user_id=run_by)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    invoke_key = http_request.state.invoke_key
    timeout_seconds = invoke_key.timeout_seconds if invoke_key is not None else DEFAULT_INVOKE_TIMEOUT_SECONDS

    run_id = str(uuid4())
    runs.create_run(run_id, graph_id=graph_id, trigger_source="invoke", run_by=run_by)
    # A plain background thread, not BackgroundTasks -- this request must
    # block until either the run finishes or the timeout elapses, neither
    # of which BackgroundTasks (which only runs after the response is
    # already sent) can express. `execute_run` is already a plain
    # synchronous function (backend/api/runs.py) that persists its own
    # final state via runs_store in a `finally` block regardless of who
    # calls it or how long it takes -- daemon=True plus no further handling
    # here is sufficient for the timeout case: the thread keeps running
    # and persisting normally after this request returns.
    thread = threading.Thread(
        target=runs.execute_run,
        args=(
            run_id,
            graph,
            {
                "connections": resolved_connections,
                "connection_profiles": resolved_connection_profiles,
                "running_user_id": run_by,
                "slot_mappings": {},
            },
        ),
        daemon=True,
    )
    thread.start()
    thread.join(timeout_seconds)

    if thread.is_alive():
        raise HTTPException(
            status_code=504,
            detail={
                "message": "Graph did not finish within the invoke timeout. It is still running in the "
                "background -- poll GET /runs/{run_id} for the eventual result.",
                "run_id": run_id,
            },
        )

    snapshot = runs.get_run_snapshot(run_id)
    assert snapshot is not None  # just created above via runs.create_run, must exist
    if snapshot.status == "failed":
        # Only reachable if run_graph() itself raised (a scheduler-level
        # failure, not an individual node's) -- see the per-node check
        # below for the far more common case.
        raise HTTPException(status_code=500, detail={"message": snapshot.error, "run_id": run_id})

    # A single node's own execution error never makes run_graph() raise --
    # backend/execution/engine.py's `_execute_node` catches it and records
    # `error` on that node's own TraceRecord instead (CLAUDE.md's "never
    # silently swallow node execution errors... propagate a structured
    # error to the graph-level trace"), while the run otherwise completes
    # normally with whatever downstream nodes got skipped. An invoke
    # caller integrating this as a callable operation needs a definitive
    # pass/fail signal, not to have to separately poll GET /runs/{run_id}
    # and inspect the trace themselves to notice a null output was
    # actually a failure -- so this checks for it here.
    failed_record = next((record for record in snapshot.trace if record.error is not None), None)
    if failed_record is not None:
        raise HTTPException(
            status_code=500,
            detail={"message": failed_record.error, "failed_node_id": failed_record.node_id, "run_id": run_id},
        )

    outputs = {name: (snapshot.result or {}).get(node_id) for name, node_id in output_name_to_node_id.items()}
    return InvokeGraphResponse(run_id=run_id, outputs=outputs)


def _invoke_key_info(row: invoke_keys_store.InvokeKeyRow) -> InvokeKeyInfo:
    return InvokeKeyInfo(
        key_id=row.key_id,
        label=row.label,
        key_prefix=row.key_prefix,
        timeout_seconds=row.timeout_seconds,
        created_at=row.created_at,
        created_by=row.created_by,
        last_used_at=row.last_used_at,
    )


@app.post("/graphs/{graph_id}/invoke-keys", response_model=CreateInvokeKeyResponse, status_code=201)
def create_invoke_key(graph_id: str, request: CreateInvokeKeyRequest, http_request: Request) -> CreateInvokeKeyResponse:
    """spec-029: mints a new invoke key, returned in plaintext exactly
    once -- never retrievable again after this response. Standard auth
    only (JWT or the shared key, never an invoke key -- see
    _INVOKE_KEY_SCOPED_ROUTES, this path is deliberately excluded): key
    management is a human/admin action, not something an invoke key can do
    to itself."""
    graph_row = graphs_store.get_graph(graph_id)
    if graph_row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if not (1 <= request.timeout_seconds <= _MAX_INVOKE_TIMEOUT_SECONDS):
        raise HTTPException(
            status_code=422, detail=f"timeout_seconds must be between 1 and {_MAX_INVOKE_TIMEOUT_SECONDS}"
        )
    row, token = invoke_keys_store.generate_invoke_key(
        graph_id,
        request.label,
        _utcnow_iso(),
        timeout_seconds=request.timeout_seconds,
        created_by=_caller_user_id(http_request),
    )
    return CreateInvokeKeyResponse(key=_invoke_key_info(row), token=token)


@app.get("/graphs/{graph_id}/invoke-keys", response_model=list[InvokeKeyInfo])
def list_graph_invoke_keys(graph_id: str) -> list[InvokeKeyInfo]:
    graph_row = graphs_store.get_graph(graph_id)
    if graph_row is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return [_invoke_key_info(row) for row in invoke_keys_store.list_invoke_keys(graph_id)]


@app.delete("/graphs/{graph_id}/invoke-keys/{key_id}", status_code=204)
def delete_invoke_key(graph_id: str, key_id: str) -> None:
    deleted = invoke_keys_store.revoke_invoke_key(graph_id, key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Invoke key '{key_id}' not found")

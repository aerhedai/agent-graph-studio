# SPEC-025: App Integration Catalog — Credential Types, Pre-Populated Nodes, API-Key Auth

**Status:** Draft — ready for review
**Author:** Rohan
**Supersedes:** `024-connectors.md` and `024b-integration.md` — both assumed a FastAPI + SQLAlchemy + Postgres (Supabase) + React Flow stack that does not exist in this project (verified directly: zero SQLAlchemy/Postgres/Supabase anywhere in `backend/`, no React Flow in `frontend/package.json`). Their genuinely good ideas are carried forward here, rewritten against the actual stack; their domain model (a second, parallel `providers`/`connections`/`actions`/`node_instances` system) is not adopted — see §2.
**Depends on:** SPEC-006 (connections abstraction), SPEC-019 (dynamic MCP node generation), SPEC-021 (per-user MCP OAuth engine — `backend/mcp/oauth_flow.py`, proven live against two real providers, now persistently hosted), SPEC-023 (admin-managed global connections, the connection-picker type-filtering fix this spec's "credential type" concept directly extends)

## 1. Goal

Get to "press Connect or paste an API key, and the app's nodes just work" for a genuinely broad set of apps, without hand-building a bespoke integration per app and without a second parallel backend system. Concretely, close the two real gaps found while using the system as it exists today:

1. **A node type's connection field only shows connections of the right *storage* type** (e.g. `mcp_server`), not the right *credential* — a user with three different Gmail-shaped connections (work, personal, a shared team one) has no way to declare "this node needs a Gmail-authenticated connection specifically" independent of which literal connection they pick. `CredentialType` (§4) is the missing, reusable unit.
2. **Nodes for an app only appear in the palette after someone has completed a live OAuth handshake** (`generate_node_types_for_connection` requires a real `tools/list` call, which requires a token). For a pre-vetted, "we already know this app works" integration (Gmail, Linear, Atlassian, etc. — see the live-verified shortlist from this session), there's no reason a user should need to wait for someone else to connect first before the node even shows up.

## 2. Why this, why now — and why not 024/024b's approach

This session live-proved the hard part already: a fully generic, self-hosted, per-user MCP OAuth engine (`oauth_flow.py`), working against two structurally different real providers (Google's official server, a from-scratch Discord proxy), with admin-managed global connections (SPEC-023) letting any number of platform users independently connect their own account to the same shared connection profile. Research this session also surfaced that several major SaaS apps (Atlassian, Stripe, Zoom, Figma, Linear) now ship their own official, first-party, OAuth-protected remote MCP servers — the same shape as Gmail's — meaning the existing mechanism should work against them with zero new backend code, unverified only because nobody's tried yet.

024 and 024b both independently proposed solving this by standing up a **second, parallel system**: a Postgres/SQLAlchemy `providers`/`credential_types`/`connections`/`actions`/`node_instances` domain model, a separate FastAPI app tree (`backend/app/connectors/`), and (in 024's case) a JSON-Schema-in-a-database-row approach to node config that conflicts with this project's explicit "every node type defines its schema as a Pydantic model" rule. Adopting either as written means either replacing everything SPEC-021/023 already built and proved, or running two competing connection systems side by side. Neither is necessary — every genuinely new idea in both drafts (`CredentialType` as a reusable unit, a pre-populated catalog, an `api_key` auth path, popup-based OAuth UX, dynamic option loading) can be built as an extension of the existing SQLite/Pydantic-based architecture.

## 3. Scope

**In scope:**

- **`CredentialType`**: a named, reusable auth requirement (e.g. `google_gmail_oauth2`, `telegram_bot_token`) distinct from a specific `Connection` instance and from the underlying connection storage type (`mcp_server`, etc.). A node's connection-reference field declares the `CredentialType` it needs (extends the exact `connectionTypes`/`connectionCapability` `json_schema_extra` mechanism just shipped in SPEC-023 with a third filter kind: `credentialType`), and the picker shows only the caller's own connections tagged with that credential type — letting one user hold several distinct Gmail-shaped connections ("Work Gmail", "Personal Gmail") and pick per node instance.
- **A pre-populated app catalog**: a small, admin-curated list of vetted apps (starting with the shortlist already researched this session), each declaring its known MCP server URL, its `CredentialType`, and a **cached snapshot of its tool schema** captured once (by an admin bootstrap action, not required per-user) — so node types can be registered and placed on the canvas before any specific user has connected anything. Running such a node without a personal connection fails with a clear "connect \<app\> first" error, exactly like today's per-user-token check already does — this spec changes *when nodes appear*, not the *execution-time* per-user resolution already built.
- **`api_key`/`bearer` auth path for `mcp_server` connections**, parallel to the existing OAuth path: a connection can declare `auth_type: "oauth2" | "api_key" | "bearer"`; the picker/Settings-Connections form renders either the existing "Connect" OAuth button or a simple "paste your key" field accordingly, and `_server_config_for` attaches the stored key as a header the same way it already attaches a Bearer OAuth token.
- **Popup-based OAuth UX**: `/connections/oauth/start` gains an option to be opened in a popup window (not a top-level navigation), with the callback posting a `window.opener.postMessage` and closing itself, so connecting an app doesn't navigate the user away from their canvas. The existing top-level-redirect flow remains supported (some browsers/contexts block popups) — this is additive, not a replacement.
- **Dynamic option loading** for a config field backed by a live list on the connected account (e.g. a channel dropdown) — one new endpoint mirroring the existing `resolve-slots` precedent, cached briefly per `(connection, field)`.

**Out of scope (deliberately, not deferred-and-forgotten):**

- Any Postgres/SQLAlchemy migration. This stays on the existing flat-file + SQLite architecture, extended the same way `oauth_token_storage.py`/`graph_sharing_store.py` already were.
- A generalized `connection_shares` (any user sharing any specific one of their own connections with any other specific user). SPEC-021/023's existing shared-graph slot-declaration model already covers the "a shared graph works for whoever runs it, using their own equivalent connection" case; a direct one-connection-to-another-specific-user share is a genuinely different feature, real but not needed to close the two gaps in §1 — flagged as a real future option, not built here.
- An `http`/OpenAPI-manifest executor for apps with no MCP server at all. The live-researched shortlist this spec targets first (Gmail, Linear, Atlassian, Stripe, Zoom, Figma, Discord) all have real MCP servers; broadening to non-MCP apps via direct REST manifests is a legitimate later phase, not required for the immediate goal.
- Rewriting SPEC-022 (unified per-app nodes, still an unstarted draft) — that spec's "one node per connection, Operation dropdown" goal is complementary to this one (this spec is about *which connections/nodes exist and how they're authenticated*; SPEC-022 is about *how many node types one connection produces*) and should proceed independently.

## 4. Design decisions (resolved)

- **One operator-registered OAuth app per provider, shared by every platform user** — already exactly how Gmail works today (the admin registers one Google Cloud OAuth client on the global `my-gmail` connection; every user clicks Connect against that same client, each getting their own isolated token). 024b's "we own the OAuth apps" framing was actually describing this already-built reality, just via unfamiliar terminology — no change needed here, stated explicitly so it's not re-litigated.
- **Credential type is metadata on top of the existing connection model, not a new storage entity requiring its own table with foreign keys into a parallel schema.** A `credential_type: str | None` field on `ConnectionProfile`/`McpServerConnectionConfig`, matching how `is_global`/`can_manage` were added to `ConnectionInfo` in SPEC-023 — extends what exists, no new persistence layer.
- **Pre-population is admin-curated, not automatic discovery of "every MCP server that might exist."** An admin explicitly adds an app to the catalog (name, MCP URL, credential type, auth type) and triggers a one-time schema-capture bootstrap (reusing `generate_node_types_for_connection`'s existing discovery call, just performed once ahead of any real end-user connecting) — matching this project's existing "admin manages global connections" model from SPEC-023, not a new automatic crawler.

## 5. Data model (illustrative)

```
McpServerConnectionConfig gains:
  credential_type: str | None = None   # e.g. "google_gmail_oauth2"
  auth_type: Literal["oauth2", "api_key", "bearer"] = "oauth2"
  api_key: str | None = None           # only when auth_type != "oauth2", encrypted at rest same as other secrets

Node config field's json_schema_extra gains a third filter kind, alongside the existing
connectionTypes/connectionCapability from SPEC-023:
  {"credentialType": "google_gmail_oauth2"}

New: a small "catalog bootstrap" admin action --
POST /connections/{name}/catalog-bootstrap
  admin-only; runs discovery/tools-list using an admin-supplied temporary token or an
  already-connected admin's own token, caches the resulting schema, registers node types
  with is_global naming, WITHOUT requiring any other user to have connected yet.

GET /connections/oauth/start?...&popup=true
  same flow as today, but the callback's success page posts a message and closes itself
  instead of redirecting the top-level window.

POST /node-types/{type}/options/{field}
  {connection_name, current_config} -> [{label, value}]
  mirrors resolve-slots' existing shape; cached 60s per (connection, field).
```

## 6. Acceptance criteria

- [ ] A node's connection field can declare `credentialType`, and the picker shows only the caller's own connections tagged with that type — verified with a user holding two differently-named connections of the same credential type, picking between them per node instance.
- [ ] An admin can add a new catalog app entry and bootstrap its node types via the admin's own connect, *before* any other user has connected — a second, non-admin user sees the resulting nodes in their palette immediately, and gets a clear "connect \<app\> first" error attempting to run one before they've personally connected — live-verified.
- [ ] At least one connection using `auth_type: "api_key"` works end-to-end: a user pastes a key (no OAuth redirect at all), and a generated node successfully calls the real API using it — live-verified against a real app.
- [ ] Popup-based OAuth connect completes successfully and returns control to the canvas without a full-page navigation — verified live in a real browser.
- [ ] A dynamic-options field renders a real, live-fetched dropdown (not free text) for at least one real app/field pair.
- [ ] At least one app from the live-researched shortlist (Linear, Atlassian, Stripe, Zoom, or Figma) is connected for real, end-to-end, proving the "any official MCP server works with zero new backend code" claim a second time, not just for Gmail.
- [ ] Full existing test suite passes, including SPEC-021/023's own tests (connection scoping, admin gating, per-user token resolution all unchanged).
- [ ] `git diff main -- backend/execution/engine.py` is empty.

## 7. Open questions

- **Exact shape of the "catalog bootstrap" admin token**: does the admin use *their own* real personal account's OAuth connect to seed the schema (simplest, matches how `my-gmail` was actually bootstrapped this session), or is a separate, disposable "discovery-only" credential worth supporting for apps where the admin doesn't want to personally connect their own account just to seed the catalog? Leaning toward "admin's own connect is sufficient" — proposing, not deciding, since it's a real design choice.
- **Should `connection_shares` (direct one-connection-to-specific-other-user sharing) be a fast-follow to this spec, or wait until a concrete use case shows the existing shared-graph-slot model isn't enough?** Leaning toward "wait" — asking rather than deciding silently, per this project's convention for flagged open questions.

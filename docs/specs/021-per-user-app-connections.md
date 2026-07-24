# SPEC-021: Per-User MCP App Connections — Generic OAuth via the Model Context Protocol

**Status:** Draft — ready for review
**Milestone:** Multi-user app integrations
**Author:** Rohan
**Depends on:** SPEC-020 (platform authentication — this spec has no meaning without real per-user identity), SPEC-019 (app integrations framework — dynamic MCP node generation, remote transport — this spec makes its connections user-scoped and OAuth-capable), SPEC-006 (connection profiles), SPEC-017 (encryption-at-rest model, reused unchanged)

## 1. Goal

Make `mcp_server` connections (SPEC-019) **user-scoped**, and add generic support for MCP servers that require a **per-user OAuth login** — the standardized flow MCP's own specification defines — so that connecting *any* OAuth-requiring MCP server (Google's official Gmail MCP server, a Discord MCP server, or any future app's MCP server) automatically gives each logged-in user their own real login flow and their own stored credentials, with **zero app-specific backend code**. Gmail and Discord are the two real, live proving cases — never special-cased implementations.

## 2. Why this, why now — and why this supersedes the original draft

This spec was originally scoped as "hand-build a Gmail connection type and a Discord connection type, each with its own OAuth code." That draft was wrong: it reintroduces exactly the per-app hand-coding this project has been trying to eliminate since SPEC-019, and it doesn't touch the actual mechanism (MCP's own dynamic node generation) that already solves "add an app without writing backend code for it."

The real gap, once SPEC-019 and SPEC-020 both exist, is narrower and more valuable: SPEC-019 already turns *any* MCP server's tools into real, individually-typed nodes automatically — but every `mcp_server` connection today is global (`name -> config`), so "connect Gmail" really means "the whole app shares one Gmail," not "each user connects their own." SPEC-020 gave the platform real per-user identity. This spec is the piece that actually lets each user bring their own app credentials, by making MCP connections user-scoped and letting them go through per-user OAuth when the target server requires it.

This is confirmed buildable, not speculative, based on real research during this spec's review:
- **Google runs an official, first-party Gmail MCP server** (`https://gmailmcp.googleapis.com/mcp/v1`) that requires OAuth — a real, trustworthy target, not a third-party guess.
- **Real community Discord MCP servers implement genuine per-user OAuth** (not a shared bot token) — explicitly built around "connects with YOUR permissions, no bot setup."
- **MCP's own specification standardizes this exact flow**: a server requiring auth responds `401`, advertises its authorization server via OAuth Protected Resource Metadata, and the client runs a standard OAuth 2.1 + PKCE flow (with optional dynamic client registration) to obtain a token attached to future calls.
- **The `mcp` Python SDK this project already depends on (v1.28.1) ships a complete client-side implementation of that flow** — `mcp.client.auth.oauth2.OAuthClientProvider`, a drop-in `httpx.Auth` subclass handling discovery, PKCE, and token refresh, with pluggable `TokenStorage` and `redirect_handler`/`callback_handler` hooks for exactly the kind of real-browser-redirect integration SPEC-020 already established for Google login.

This spec's job is largely **gluing that existing SDK machinery into our own per-user storage and web request/redirect flow** — not inventing OAuth discovery, PKCE, or client registration ourselves.

## 3. Scope

**In scope:**

- **`mcp_server` connections become user-scoped.** Store shape changes from `name -> config` to `(user_id, name) -> config`, still Fernet-encrypted at rest exactly as SPEC-017 built it — only the key changes, not the encryption model.
- **Detecting whether a given `mcp_server` connection requires OAuth at all.** The SDK's own discovery step (a `401` + Protected Resource Metadata on first contact) tells us this. A server that doesn't require it (today's stdio/manually-configured servers) is completely unaffected — this is additive, not a replacement of SPEC-019's existing connection flow.
- **A `TokenStorage` implementation backed by our own per-user encrypted connections store**, not the SDK's default in-memory/file storage — so a negotiated access token, refresh token, and any dynamically-registered client info persist per `(user_id, connection_name)` exactly like any other connection secret, and survive a restart.
- **Wiring the SDK's `redirect_handler`/`callback_handler` hooks into real HTTP endpoints.** Connecting a user-scoped, OAuth-requiring `mcp_server` connection does a real browser redirect to whichever authorization server that specific MCP server's own discovery points to (Google's, or anyone else's) — the same "real top-level navigation, never a background fetch" pattern SPEC-020 established for Google login — and a callback endpoint receives the resulting code and resumes the SDK's flow.
- **Transparent token refresh**, driven by the SDK's own expiry tracking — a node calling a tool through an OAuth-authenticated MCP connection never needs to know or handle "my token expired."
- **Two real, live proving cases**: Google's official Gmail MCP server, and a real Discord MCP server implementing per-user OAuth (the exact server confirmed during implementation — see Open Questions — mirroring SPEC-019's own precedent of confirming its remote-transport proving server during implementation rather than pinning one here).
- **Dynamically-generated nodes (SPEC-019) execute using the running user's own token**, not the connection-creator's — this is the actual point of the whole spec.
- **Graph sharing + per-user slot mapping**, unchanged in spirit from the original draft: a graph gains a `sharing: "private" | "shared"` field (private is the unchanged default). A shared graph's connection slots are **explicitly declared by the author** (name + required connection type — your confirmed choice over auto-inferring from `*_connection` fields, for the same "ambiguous the moment a user has more than one connection of a type" reasoning as before). The first time a non-author user runs a shared graph, any unmapped slot prompts them to pick one of their own connections; that mapping is remembered and reused silently afterward.
- **`resolve_connections` becomes user-aware**: given `(graph, running_user_id)`, a private graph resolves exactly as today (literal name, owner's store); a shared graph resolves each slot via that user's own mapping.

**Out of scope (future specs/work):**

- **Non-MCP, hand-built app integrations** (a bespoke Gmail/Discord REST client bypassing MCP entirely) — deliberately not built. If a real, trustworthy app genuinely has no MCP server, that's SPEC-019's manifest-fallback path (Telegram's precedent), not this spec's concern.
- **MCP servers authenticating via a static, non-OAuth secret** (e.g. a fixed API key baked into the server's own config) — these already work today via SPEC-019/006's existing manual-config connection flow. The `(user_id, name)` store-key change applies to them too (so two users can each configure their own static-keyed server), but no new OAuth machinery is needed for them.
- **Any app beyond Gmail + Discord as the proving pair** — the mechanism is generic by construction; a third OAuth-requiring MCP server later is a config addition (its URL, as a new `mcp_server` connection), not a redesign.
- **Org-wide/team-shared credentials** (one connection several users draw from directly) — still explicitly out of scope; this spec's model remains "each runner supplies their own."
- **Google's OAuth app verification process for sensitive Gmail scopes** — the same real external dependency and timeline risk SPEC-020 already disclosed for login scopes, now also applying to Gmail's MCP scopes. Fine for SPEC-020's invited test users; a genuine hurdle before wider release, disclosed here rather than assumed away.
- **Revoking a connection's provider-side access on disconnect** — same deferred gap as the original draft, not silently skipped, just not built here.

## 4. Design decisions (resolved)

- **Build on the MCP SDK's existing OAuth client (`mcp.client.auth.oauth2`), don't hand-roll RFC 8414 discovery, PKCE, or dynamic client registration.** The SDK already implements the full standardized flow correctly; this project's job is the storage and web-redirect glue, not re-implementing an OAuth client library.
- **Per-user token storage, not per-app.** The SDK's `TokenStorage` protocol (`get_tokens`/`set_tokens`/`get_client_info`/`set_client_info`) is implemented against our own connections store, scoped by `(user_id, connection_name)` — the SDK is agnostic about where tokens actually live; that storage decision is the one part of this flow this project genuinely owns.
- **Real browser redirect for the authorization code grant** (SPEC-020 precedent, reused) — the SDK's `redirect_handler` callback returns a real 302 to the user's actual browser, never a background poll; `callback_handler` is satisfied by a real FastAPI route receiving `code`/`state` from that redirect.
- **Explicit author-declared slots for shared graphs** — your confirmed choice, unchanged from the original draft's proposal.
- **Telegram stays outside this spec entirely** — it has no per-user OAuth model to build on; unchanged reasoning from the original draft.
- **Gmail and Discord are proving cases of one generic mechanism, never special-cased in code.** If either server's real-world OAuth discovery behaves differently than expected, that's a live-verification finding to document, not a reason to add server-specific branches to the connection or execution code.
- **Encryption-at-rest model is unchanged** — only the store's key shape changes; Fernet encryption, the single required encryption-key env var, and "refuse to start without it" all carry over exactly as SPEC-017 built them.

## 5. Data model (illustrative)

```
connections: name -> config                       (SPEC-006/017/019, today)
             becomes
connections: (user_id, name) -> config             (encrypted exactly as before)

mcp_oauth_tokens
  user_id, connection_id,
  access_token (encrypted),
  refresh_token (encrypted, nullable -- not every server issues one),
  expires_at (nullable), token_type,
  client_id, client_secret (encrypted, nullable -- dynamic registration may not need one),
  scope

graphs: + sharing ("private" | "shared", default "private")

graph_connection_slots      -- declared by the graph author for a shared graph
  graph_id, slot_name, connection_type (e.g. the target mcp_server connection's own name/type)

user_slot_mappings          -- filled in by each runner on first use
  user_id, graph_id, slot_name, connection_id
```

## 6. Acceptance criteria

- [ ] A real user can connect Google's official Gmail MCP server via a live OAuth redirect (SDK discovery + our redirect/callback wiring), ending with their own encrypted token stored scoped to their user id — verified live, non-mocked
- [ ] A real user can connect a real Discord MCP server implementing per-user OAuth the same way, with zero Discord-specific code beyond its connection config (the server's URL) — verified live
- [ ] A dynamically-generated node (SPEC-019) from the Gmail MCP connection performs a real action (e.g. sends or reads a real email) using that specific user's own token — live-verified, non-mocked
- [ ] A second user's graph run cannot see or use the first user's Gmail MCP connection — verified live (attempt and confirm it's genuinely inaccessible, not just untested)
- [ ] A graph marked `shared`, run by a non-author user for the first time, prompts for the missing connection slot(s) before running — verified live
- [ ] That same user's second run of the same shared graph reuses the remembered mapping with no further prompt — verified live
- [ ] An expired access token is transparently refreshed mid-run (via the SDK's own refresh handling) without surfacing as a node error — verified live (forcing/waiting for a real expiry, or an equivalent real test against the provider's refresh endpoint)
- [ ] Adding a third OAuth-requiring MCP server requires zero new backend code — only a new `mcp_server` connection config — demonstrated, not just claimed
- [ ] A private graph's behavior is completely unchanged from before this spec (regression-checked)
- [ ] Full existing test suite passes unchanged
- [ ] `git diff main -- backend/execution/engine.py` is empty

## 7. Open questions

- **Exact real Discord MCP server to use as the proving case** — to be confirmed during implementation (reachability, and whether its OAuth discovery actually conforms to the spec as advertised), not pinned here, mirroring SPEC-019's own precedent of choosing its remote-transport proving server during implementation rather than guessing in advance.
- **Whether every OAuth-requiring MCP server supports dynamic client registration.** Google's own OAuth implementations typically require a pre-registered client (like SPEC-020's Google Cloud OAuth client) rather than supporting RFC 7591 dynamic registration — the SDK supports both modes (`client_metadata_url` for dynamic registration, or pre-supplied client info), but which mode each real server actually needs is confirmed during implementation, not assumed here. If Gmail's MCP server needs its own pre-registered Google Cloud OAuth client (distinct from SPEC-020's login-only client, since the scopes differ), that's a new required credential to document in `docs/DEPLOYMENT.md`/`.env.example`, not a blocker.

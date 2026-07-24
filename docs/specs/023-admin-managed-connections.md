# SPEC-023: Admin-Managed Global Connections + a Real Connections Settings Page

**Status:** Draft — ready for review
**Author:** Rohan
**Depends on:** SPEC-006 (connections abstraction), SPEC-018 (canvas UX / Settings panel), SPEC-020 (platform authentication — the one existing `role` check this spec generalizes), SPEC-021 (per-user app connections — this spec restricts *who can manage* a connection's config, not the per-user OAuth/token mechanism SPEC-021 built, which is unchanged)

## 1. Goal

Two things, found together while debugging live Gmail/Discord MCP connections this session:

1. **There is currently no way to create a connection visible to every platform user at all.** Every connection a signed-in human creates is automatically private to them (`_caller_user_id` never returns `None` for a signed-in user — confirmed by reading the code, not assumed). So an "app integration" like a Gmail MCP server connection, meant to be something every user can independently OAuth-connect their own account to, is today invisible to everyone except whoever happened to create it.
2. **There is no dedicated connection-management surface, and no permission model for one.** The only way to view, edit, or delete a connection is the picker embedded in a node's config panel — which is how a connection got deleted by accident earlier this session. Every route touching connection CRUD is unauthenticated-by-role; `/auth/invite` is the *only* role-gated route in the entire app.

This spec adds: an explicit private/global choice on connection creation (global gated to admin), a real Connections page under Settings for managing global connections, and a per-node picker that becomes read/select/OAuth-connect-only for connections a given user isn't allowed to mutate.

## 2. Why this, why now

n8n's own model is the reference point raised in conversation: node *types* (Gmail, Discord, ...) are available to everyone, the underlying credential/connection infrastructure is admin-configured and not user-editable, and what a regular user gets is just "connect your own account" (OAuth). This project's SPEC-021 already built the hard part of that — a single connection profile can be independently OAuth-connected by any number of different users, each getting their own isolated token and namespaced node types (verified live this session: `generate_node_types_for_connection(..., owner_user_id=claims.user_id)` keys off the *connecting* user, not the profile's owner). What's missing is purely the administrative layer on top: making a connection global in the first place, and restricting who can create/edit/delete it once it exists.

## 3. Scope

**In scope:**

- **`CreateConnectionRequest` gains a `scope: Literal["private", "global"] = "private"` field.** `scope="global"` is only honored for a caller with `role == "admin"`; a non-admin requesting `scope="global"` gets a clear 403, not a silent downgrade to private (this project's "never silently swallow" convention, generalized to authorization).
- **Admin-only mutation of existing global connections.** `PUT`/`update_connection_config`, and `DELETE /connections/{name}` for any connection with `user_id: None` require `role == "admin"`; a non-admin gets 403. A user's own private connections are completely unaffected — full self-service, exactly as today.
- **`/connections/{name}/refresh-capabilities` and the OAuth `/connections/oauth/start` flow remain open to any authenticated user** regardless of who owns the connection profile — this is deliberate and must not regress. SPEC-021's entire point is that any user can OAuth-connect their own account to a *global* mcp_server connection; only mutating the profile's config (URL, client id/secret, scope) becomes admin-gated, not using it.
- **A new "Connections" section in `SettingsPanel.tsx`**, admin-only (same `me?.role === "admin"` gating pattern already used for "Invite a user"), listing every global connection with create/edit/delete/test — the real management surface that doesn't exist today.
- **The per-node `ConnectionPicker` stops offering global-connection creation/deletion to non-admins.** A non-admin still sees "+ New connection" (for their own private ones, unaffected) and still sees every accessible connection (their own + all global ones, filtered by type per the just-shipped picker fix) with "Connect" for any that need OAuth — they just can't create a new *global* one or delete/edit an *existing* global one from there. An admin keeps full capability everywhere, including a lightweight "promote to global" action on their own existing private connections (closes the exact situation `my-gmail` is in right now, without requiring delete-and-recreate).
- **`ConnectionInfo` gains enough for the frontend to know what it's looking at**: whether the connection is global, and whether the calling user is allowed to mutate it — computed server-side, not inferred client-side from role alone (a private connection's *owner* can always mutate their own, admin or not).

**Out of scope:**

- Any change to SPEC-021's per-user OAuth mechanism, token storage, or node-generation/namespacing — unchanged.
- Admin visibility into the *contents* (config, secrets) of another user's private connections. Out of scope deliberately — see Open Questions.
- A generalized RBAC system beyond this one distinction (admin/member). This project has exactly two roles today (SPEC-020); this spec doesn't add a third.

## 4. Design decisions (resolved)

- **Admin gate applies to *all* global connections, not just OAuth app-integration ones.** Confirmed with the user directly: a global Ollama server or any other shared infrastructure connection is admin-managed the same way a global Gmail MCP connection is — "global = infrastructure, admin-managed; private = the individual user's own," applied uniformly rather than special-cased per connection type.
- **OAuth login/connect is never gated.** The distinction this spec draws is entirely about who can change a connection's *configuration*, never about who can use it. A member connecting their own Gmail account to an admin-configured global connection is the primary use case this whole mechanism exists for.
- **Private connections are untouched.** No new restriction on a user's own private connections — they create/edit/delete/test those exactly as before this spec.

## 5. Data model (illustrative)

```
POST /connections
  body: { name, type, config, scope: "private" | "global" = "private" }
  403 if scope="global" and caller.role != "admin"

PUT /connections/{name}      (new -- config mutation didn't have its own route before; update_connection_config existed internally only)
DELETE /connections/{name}
  403 if the resolved connection has user_id=None and caller.role != "admin"
  (unaffected if the resolved connection is the caller's own private one)

POST /connections/{name}/promote-to-global   (new, admin-only)
  403 if caller.role != "admin"
  404 if the connection isn't the admin's own private connection (can't silently absorb another user's private connection)

ConnectionInfo gains:
  is_global: bool
  can_manage: bool   # true if caller may edit/delete this specific connection
```

## 6. Acceptance criteria

- [ ] A non-admin member's `POST /connections` with `scope="global"` returns 403 — live-verified, not just unit-tested.
- [ ] An admin's `POST /connections` with `scope="global"` succeeds and the resulting connection is visible to a second (non-admin) test user via `GET /connections`.
- [ ] That second user can `GET /connections/oauth/start` against the admin-created global connection and complete a real OAuth connect for their own account — regression-checked live, since this must not break.
- [ ] A non-admin's `DELETE`/edit attempt on a global connection returns 403; the same non-admin's delete/edit of their *own* private connection still succeeds.
- [ ] `SettingsPanel.tsx` shows a "Connections" section only when `me.role === "admin"`, listing global connections with working create/edit/delete/test.
- [ ] The per-node `ConnectionPicker`, for a non-admin, no longer offers a "global" option when creating a connection, and does not show Delete/Edit for a global connection in the list — verified live in the browser, not just by API test.
- [ ] `my-gmail` (currently private to the admin account from this session's live testing) can be promoted to global via the new action and then independently OAuth-connected by a second real or test user.
- [ ] Full existing test suite passes, including SPEC-021's own connection-scoping tests (which must continue to hold — global stays `user_id: None`, private stays user-scoped, unchanged).
- [ ] `git diff main -- backend/execution/engine.py` is empty.

## 7. Open questions

- **Should an admin be able to see the *list* of other users' private connection names (not their config/secrets) for support/debugging** (e.g. "user X has a private connection called 'my-ollama'"), or should admin have zero visibility into private connections at all, full stop? Leaning toward zero visibility (simplest, matches SPEC-021's existing privacy boundary) unless there's a concrete support need — asking rather than deciding silently, per this project's convention for flagged open questions.
- **Exact shape of "promote to global"**: does it require re-running OAuth discovery/registration (since a global connection's OAuth client might reasonably differ from what an individual happened to configure for their own private one), or does it carry the existing config over as-is? Proposing: carry over as-is (simplest, and it's exactly what's needed for `my-gmail` specifically) — but flagging since it's a real design choice, not obviously forced.

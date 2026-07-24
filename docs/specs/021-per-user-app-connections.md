# SPEC-021: Per-User App Connections — OAuth-Connected Accounts and Shared Graph Credentials

**Status:** Draft — ready for review
**Milestone:** Multi-user app integrations
**Author:** Rohan
**Depends on:** SPEC-020 (platform authentication — this spec has no meaning without real per-user identity), SPEC-006 (connection profiles), SPEC-019 (app integrations framework — the node/connection patterns this spec extends to be user-scoped)

## 1. Goal

Let each logged-in user connect their *own* Gmail and Discord accounts via real OAuth, store those credentials scoped to that user rather than globally, and let a graph be marked shared so that a different user can run it using *their own* connected accounts — not the graph author's.

## 2. Why this, why now

SPEC-019 built genuinely dynamic app integration, but every connection in this project is still global — one Telegram bot, one Zapier token, visible and usable by anyone with the (until SPEC-020) single shared key. That's fine for one operator. It stops being fine the moment a second real person uses the platform: their Gmail should not be reachable by someone else's graph, and a workflow one person builds ("summarize new emails and post to Discord") should be reusable by a colleague without that colleague ever seeing the author's credentials — they should supply their own. This is precisely the gap between "a personal automation tool" and "a real multi-user product," and it's the concrete reason today's earlier conversation about "recreating Zapier, self-hosted, free" needs this piece before it's real.

## 3. Scope

**In scope:**

- **A new OAuth-based connection flow**, for two apps as the proving case: **Gmail** and **Discord**. (Telegram is explicitly excluded from this OAuth pattern — it has no per-user OAuth model; a Telegram bot receives messages from whoever DMs it, identified by chat ID, not a login flow. Telegram connections stay exactly as SPEC-012/019 built them.)
  - A user clicks "Connect Gmail" (or Discord) from their own account settings.
  - Redirected to that provider's real consent screen, requesting the specific scopes that app's nodes need (e.g. `gmail.send`, `gmail.readonly` — deliberately broader/more sensitive than SPEC-020's login-only scope, and requested via a separate authorization, never bundled with login).
  - Provider redirects back with an authorization code; the backend exchanges it for an access + refresh token pair.
- **Connections become user-scoped.** The connections store's shape changes from `name -> config` to `(user_id, name) -> config`, still Fernet-encrypted at rest exactly as SPEC-017 built it — only the key changes, not the encryption model.
- **Token refresh**, transparent to node execution — an expired access token is refreshed via its stored refresh token before a call fails, not surfaced as a node error the first time it happens.
- **Graph sharing**: a graph gains a `sharing: "private" | "shared"` field (private is the existing, unchanged default). A shared graph's `*_connection` references are treated as **named slots**, not literal saved-connection lookups.
- **Per-user slot mapping**: the first time a user who isn't the graph's author runs a shared graph, any unmapped slot triggers a clear prompt — "this graph needs a Gmail connection; choose one of your own" — before the run proceeds. That mapping (`user_id, graph_id, slot_name -> connection_id`) is stored and reused silently on every subsequent run by that user. This mirrors how real workflow-template products (n8n's template gallery, Zapier's shared templates) actually handle this — explicit, one-time, remembered — rather than an ambiguous "auto-pick a same-type connection," which breaks the moment a user has more than one connection of the same type.
- **`resolve_connections` becomes user-aware**: given `(graph, running_user_id)`, a private graph resolves exactly as today (literal name, owner's store); a shared graph resolves each slot via that user's own mapping.
- **Discord's OAuth setup** as the second real proving case, alongside Gmail — both go through the same generic OAuth-connection mechanism, not two special-cased implementations, proving the pattern generalizes the same way SPEC-019 proved MCP generation generalizes beyond one app.

**Out of scope (future specs/work):**

- Any app beyond Gmail + Discord as the OAuth proving case — the mechanism is generic; adding a third app later is a data/config addition, not a redesign, matching this project's repeated "prove with two, generalize by construction" pattern.
- Scaling/queue infrastructure (Redis, Celery, Postgres) — a genuinely separate concern from *whose* credentials get used, not bundled in here.
- Org-wide/team-shared credentials (one connection several users draw from directly) — this spec's model is strictly "each runner supplies their own," never a shared pool.
- Google's OAuth app verification process for sensitive scopes — a real external dependency and timeline risk this spec cannot control. Fine for the invited test users SPEC-020 already scopes this platform to; a genuine hurdle before any wider release, disclosed here rather than assumed away.
- Revoking a connection's access from the provider's side when a user disconnects it in-app (should call the provider's real token-revocation endpoint, not just delete the local record) — flagged as a real, deferred gap, not silently skipped.

## 4. Design decisions (resolved)

- **Slot-based resolution for shared graphs, with an explicit one-time per-user mapping** — the deliberately more complex option, chosen over simpler auto-matching-by-type specifically because auto-matching is ambiguous the moment a user has more than one connection of a given type, and silent ambiguity in "which of your accounts does this workflow use" is a worse failure mode than one extra setup click.
- **Login scope (SPEC-020) and data-access scope (this spec) are never the same authorization**, even for the same provider (Google) — a user's login grant never implicitly carries Gmail-send permission; connecting Gmail is always its own, separate, visible consent step.
- **Telegram stays outside this spec's OAuth pattern entirely** — it has no per-user login concept to build on; forcing it into this shape would be a bad fit, not a missing feature (the same reasoning SPEC-012 used to exclude email/WhatsApp from the webhook-adapter pattern).
- **Token refresh is transparent, not a node-level concern** — a node calling a Gmail/Discord tool should never need to know or handle "my token expired"; that's resolved one layer down, at connection-resolution time, consistent with this project's existing "engine and nodes stay unaware of connection mechanics" principle (SPEC-006 §4).
- **Encryption-at-rest model is unchanged** — only the store's key shape changes (`name` → `(user_id, name)`); Fernet encryption, the single required encryption-key env var, and the "refuse to start without it" behavior all carry over exactly as SPEC-017 built them.

## 5. Data model (illustrative)

```
connections: name -> config   (SPEC-006/017, today)
             becomes
connections: (user_id, name) -> config   (encrypted exactly as before)

oauth_tokens
  user_id, connection_id, access_token (encrypted), refresh_token (encrypted),
  expires_at, provider ("google" | "discord"), scopes

graphs: + sharing ("private" | "shared", default "private")

graph_connection_slots      -- declared by the graph author for a shared graph
  graph_id, slot_name, connection_type (e.g. "gmail")

user_slot_mappings          -- filled in by each runner on first use
  user_id, graph_id, slot_name, connection_id
```

## 6. Acceptance criteria

- [ ] A real user can connect their own real Gmail account via a live Google OAuth consent screen; the resulting token is stored encrypted, scoped to that user only
- [ ] A real user can connect their own real Discord account the same way
- [ ] A node using a connected Gmail account performs a real action (e.g. sends a real email) — live-verified, non-mocked
- [ ] A second user's graph run cannot see or use the first user's Gmail connection — verified live (attempt and confirm it's genuinely inaccessible, not just untested)
- [ ] A graph marked `shared`, run by a non-author user for the first time, prompts for the missing connection slot(s) before running — verified live
- [ ] That same user's second run of the same shared graph reuses the remembered mapping with no further prompt — verified live
- [ ] An expired access token is transparently refreshed mid-run without surfacing as a node error — verified live (forcing/waiting for a real expiry, or an equivalent real test against the provider's refresh endpoint)
- [ ] A private graph's behavior is completely unchanged from before this spec (regression-checked)
- [ ] Full existing test suite passes unchanged
- [ ] `git diff main -- backend/execution/engine.py` is empty

## 7. Open questions

- **Exact Gmail/Discord scopes requested** — proposing the minimum needed for a send + read proving case (e.g. Gmail: `gmail.send`, `gmail.readonly`; Discord: bot-equivalent message scopes for a connected server) rather than broad access; final scope list to be pinned down against each provider's actual OAuth scope documentation during implementation, not guessed here.
- **What a graph author actually declares when marking a graph shared** — this spec assumes the author explicitly names each slot's required connection *type* (e.g. "this graph needs one Gmail connection") at authoring time. An alternative (inferring slots automatically from whichever `*_connection` fields exist) was considered and rejected here as more implicit and harder for an author to reason about, but flagged in case that judgment is wrong once real authoring UX is prototyped.

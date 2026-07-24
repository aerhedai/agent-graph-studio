# SPEC-020: Platform Authentication — Google Sign-In, Invite-Only Accounts

**Status:** Draft — ready for review
**Milestone:** Multi-user foundation (toward per-user app connections, SPEC-021)
**Author:** Rohan
**Depends on:** SPEC-017 (production hardening — the shared API key this spec narrows the scope of)

## 1. Goal

Give the platform real, multi-user identity — replacing the single shared API key (SPEC-017) as the *only* access control for every human-facing action — so that different people can log in as themselves, each with their own account, session, and (starting with SPEC-021) their own connected app accounts. Login is via Google OAuth; only pre-invited email addresses can ever complete it.

## 2. Why this, why now

Everything built so far assumes one operator with one shared secret. That's correct for where this project has been, and wrong for where it's headed: SPEC-021's whole premise — "each user connects their own Gmail/Discord" — requires the platform to know *which user* is asking in the first place. There's no way to build per-user app connections on top of a model with no concept of a user. This spec is the necessary foundation, and is scoped to be useful on its own even before SPEC-021 exists (real accounts, an audit trail of who did what, invite-gated access instead of "anyone with the one key").

## 3. Scope

**In scope:**

- **Google OAuth login for the platform itself** — a minimal-scope grant (`openid`, `email`, `profile` only). This is a genuinely different authorization from anything SPEC-021 will request later (e.g. `gmail.send`) — the same Google account, two separate, independently-scoped grants, never conflated into one. Confirming identity is not the same authorization as granting data access, even when both happen to go through the same provider.
- **Invite-only accounts**: a new `invited_emails` allowlist (email, invited_by, invited_at, role). Completing Google sign-in only creates/activates an account if the authenticated email is on this list. An email not on the list gets a clear, specific rejection, not a silent failure.
- **First-admin bootstrap**: a new required env var, `AGENT_GRAPH_STUDIO_ADMIN_EMAIL` — on startup, that email is auto-added to the allowlist with `role="admin"` if not already present, mirroring this project's existing "required env var, fails closed if missing" pattern (SPEC-017's API key / encryption key). This is what lets the very first admin account exist without a chicken-and-egg "an admin invites the first admin" problem.
- **A minimal `role` distinction**: `admin` (can invite others) vs `member` (cannot). No further RBAC than that.
- **JWT-based sessions** — stateless, no new server-side session store. A JWT is issued on successful login, verified on every subsequent request, matching this project's preference for light infrastructure over new persistent services.
- **The shared API key narrows scope, it doesn't disappear.** It remains the credential for paths that are never a logged-in human — webhook callbacks from external services (Telegram, etc.) that can't perform an OAuth login flow. Every human-facing canvas route moves to per-user JWT auth instead. Both mechanisms are checked by the same `require_api_key`-successor dependency, which accepts *either* a valid JWT *or* the shared key, keyed by which the specific route actually needs.
- **Minimal audit columns**: `graphs.created_by` and `runs.run_by` (nullable, since triggered/system runs have no human initiator) — now meaningful once real identity exists. No audit *UI* in this spec, just the data being captured so it exists for later.
- **Frontend**: a real login screen (replacing today's bare API-key-unlock gate) — "Sign in with Google," then the canvas as it exists today, unchanged otherwise.

**Out of scope (future specs):**

- Per-app OAuth connections (Gmail/Discord data access) — that's SPEC-021, which depends on this spec existing.
- Any RBAC beyond admin/member (no per-graph permissions, no team/workspace boundaries).
- Public self-registration — explicitly invite-only for now, matching where this project actually is.
- Login providers beyond Google (GitHub, email/password, etc.) — one provider, kept simple, is enough to prove the pattern; adding another later is additive, not a redesign.
- Revoking/expiring an existing session before its JWT naturally expires (no server-side session-kill list yet) — a real gap for "immediately deactivate a compromised account," disclosed here rather than silently assumed solved.

## 4. Design decisions (resolved)

- **Google OAuth for login, invite-allowlist for authorization** — these are two different questions ("is this really you" vs "are you allowed here") answered by two different mechanisms, not conflated into one. A valid Google identity that isn't on the allowlist is a clean, specific rejection.
- **JWT over server-side sessions** — no new persistent session store; consistent with this project's SQLite-light philosophy. The tradeoff (no instant server-side revocation) is accepted and disclosed, not hidden.
- **The shared API key is narrowed, not replaced.** Webhook-triggered execution has no human logging in — it needs to keep working exactly as SPEC-017 built it. Only human-facing routes move to JWT.
- **First-admin bootstrap via env var**, mirroring the exact pattern already established for the API key and encryption key (SPEC-017) — a required, fail-closed startup check, not a special one-off mechanism.
- **Role model kept to two values (admin/member) deliberately** — anything richer (per-resource permissions, teams) is a real future need but not one this spec's scope requires yet, and guessing at that shape now risks building the wrong thing before SPEC-021 reveals what's actually needed.

## 5. Data model (illustrative)

```
users
  id, email, display_name, role ("admin" | "member"), created_at, invited_by

invited_emails
  email (unique), role, invited_by, invited_at

graphs: + created_by (nullable, user id)
runs:   + run_by (nullable, user id -- null for schedule/webhook-triggered runs)
```

## 6. Acceptance criteria

- [ ] `AGENT_GRAPH_STUDIO_ADMIN_EMAIL` unset → backend refuses to start (same fail-closed convention as the API key / encryption key)
- [ ] On first real startup with that env var set, the admin email is present in `invited_emails` with `role="admin"`, verified live
- [ ] A real Google account matching an invited email can complete "Sign in with Google" and receive a working session — verified live, non-mocked, with a real Google OAuth consent screen
- [ ] A real Google account **not** on the allowlist is rejected with a clear, specific message — verified live
- [ ] An admin can invite a new email; that email can then complete sign-in; a non-admin cannot invite
- [ ] Every previously-human-facing route now requires a valid JWT; webhook-triggered routes continue to accept the shared API key exactly as before (regression-checked against SPEC-017's existing webhook-auth tests)
- [ ] `graphs.created_by` / `runs.run_by` are populated correctly for a real logged-in user's actions, and `null` for a schedule/webhook-triggered run
- [ ] Full existing test suite passes unchanged
- [ ] `git diff main -- backend/execution/engine.py` is empty

## 7. Open questions

- **JWT lifetime and refresh strategy** — proposing a moderate-lived token (e.g. a few hours) with silent re-authentication against Google when it expires, rather than a separate refresh-token dance, to keep this spec's scope tight. Open to revisiting if that proves too disruptive in practice.
  - Resolved: 12-hour session tokens, silent re-auth against Google on expiry — implemented as proposed, no refresh-token flow.
- **Whether the existing shared-API-key-only mode should remain available as a deployment option** (e.g. for a genuinely single-operator deployment that doesn't want Google OAuth at all) — proposing yes, keep it available behind a config flag, so this spec is additive to SPEC-017 rather than a hard breaking change for existing deployments. Flagging for confirmation since it affects how strictly "replaces" should be read above.
  - Resolved: no opt-out. Google OAuth credentials (`AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID`/`_SECRET`) are a mandatory startup requirement alongside the JWT secret and admin email, same fail-closed pattern as every other required secret — the shared API key narrows to a machine-to-machine credential (webhooks) rather than remaining a standalone human login path.

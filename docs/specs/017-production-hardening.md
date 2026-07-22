# SPEC-017: Production Hardening — Auth, Credential Encryption, Execution History UI

**Status:** Draft — ready for implementation
**Milestone:** Toward a real, self-hostable app (n8n-parity push)
**Author:** Rohan
**Depends on:** SPEC-006 (connection profiles — what gets encrypted), SPEC-010 (run persistence — what the history UI reads), SPEC-016 (deployment — the reason this can no longer wait)

## 1. Goal

Close the gap between "runs safely on my own laptop, reachable only by me" and "runs as a real, deployed, reachable-by-more-than-me application." Two things confirmed this week, by direct inspection, are not acceptable past that line: connection secrets (bot tokens, API keys) are stored as **plain, unencrypted JSON** on disk, and there is **zero authentication** on any API endpoint. Separately, bundled into this same "make it feel like a real app" push: the backend has had a fully-working `GET /runs` history endpoint since SPEC-010 with no frontend consumer at all — the biggest standing visual gap versus n8n's execution list.

## 2. Why this, why now

SPEC-016 makes this deployable somewhere other than localhost. Shipping that without this spec first would mean a publicly reachable service that can execute arbitrary code (`code` nodes, no sandboxing — a separate, already-documented, deliberately out-of-scope tradeoff from SPEC-002) with zero login and every credential sitting in cleartext. That combination is the actual blocker to "deploy this somewhere real," not a nice-to-have polish item.

## 3. Scope

In scope:
- **Credential encryption at rest**: connection profile `config` values (`backend/connections/store.py`) encrypted before being written to disk, decrypted only when actually resolving a connection for use. A real encryption key, generated once and required to be present (not silently defaulted to something predictable).
- **Basic API authentication**: a single shared credential (an API key or session token) required on every endpoint except whatever health-check is needed for container orchestration. This is deliberately *not* n8n's full multi-user Creator/Editor role system — that's real, separate, future work for if this ever needs multiple distinct human users. For now: one operator, one credential, everything-or-nothing access, matching this project's current single-user framing.
- Frontend: a minimal login/unlock screen if no valid session exists, and every API call from the canvas carries the credential.
- **Execution history UI**: a real canvas view (not just the backend endpoint) listing past runs via `GET /runs` — filterable by graph/status/trigger-source (the endpoint already supports this, SPEC-010 §3), and clicking a past run loads its full trace into the existing trace-inspector panel (reusing SPEC-005's `TraceRecord` rendering, not building a second one).

Out of scope (future specs):
- Multi-user accounts, roles, per-user credential scoping — real, deliberately deferred; this spec is "one operator, one shared secret," not a user management system
- OAuth/SSO — no justification for this at current scale
- Sandboxing the `code` node's arbitrary Python execution — a known, separate, already-documented tradeoff (SPEC-002 §7); auth reduces *who* can reach that risk, it doesn't remove the risk itself, and this spec does not attempt to
- Re-running a past run from the history view, editing history, or exporting history — v1 is read-only browsing

## 4. Design decisions (resolved)

- **Encryption: a single symmetric key (e.g. Fernet/AES via the `cryptography` package, already a transitive dependency of this project's stack — confirm and reuse rather than adding a new dependency if avoidable), read from an environment variable, required at startup — the process refuses to start without it rather than silently falling back to a default key.** This mirrors the "GIL/lock" discipline already applied elsewhere in this codebase: a missing security precondition is a hard failure, not a soft degradation.
- **Auth: a single shared bearer token/API key, checked via a FastAPI dependency applied globally** (not per-route, to avoid the "forgot to protect one endpoint" class of bug) — simplest correct approach for a single-operator tool, matching this project's repeated "don't over-engineer past what's actually needed" convention (SPEC-011's sqlite-vec-over-Chroma reasoning, SPEC-016's single-uvicorn-process reasoning).
- **Existing unencrypted `~/.agent-graph-studio/connections.json` needs a real migration path**, not silent breakage — on first startup under this spec, if an unencrypted file is detected, it's read once, re-written encrypted, and the plaintext original is not left lying around unencrypted afterward.
- **Execution history UI reuses SPEC-005's existing trace-inspector panel** rather than building a second trace-rendering component — a history entry, once clicked, populates the exact same `RunStatusResponse`-shaped state the live-run view already renders from.

## 5. Acceptance criteria

- [ ] A connection's stored secret is genuinely encrypted on disk — verified by reading the raw file bytes directly and confirming the token value is not present in plaintext anywhere in it
- [ ] The backend refuses to start if the encryption key is missing, with a clear error, not a silent fallback
- [ ] An existing plaintext `connections.json` from before this spec is automatically migrated to encrypted storage on first startup, with no data loss (verified by round-tripping a real connection through the migration and confirming it still resolves/works afterward)
- [ ] Every API endpoint (except an explicit health check) rejects requests without a valid credential; the canvas prompts for it and attaches it to every request once provided
- [ ] The canvas has a real execution history view: list of past runs, filterable, and clicking one loads its full real trace into the existing inspector panel — verified live against real run history accumulated from actual use, not seeded fixture data
- [ ] Full existing test suite passes unchanged (test fixtures/harness updated to supply the required auth credential and encryption key, since every existing API test will now need them)
- [ ] `git diff main -- backend/execution/engine.py` stays empty

## 6. Open questions

- Should the shared API credential be operator-configured (an env var you set yourself) or generated once and printed to the startup log? Recommend: operator-configured via env var, required at startup same as the encryption key — avoids a "check the logs to find your own password" workflow, and keeps both secrets provisioned the same way.
- Does the execution history view need real-time updates (a new run appearing while you're looking at the list), or is a manual refresh acceptable for v1? Recommend: manual refresh for v1 — the live-run view (already built) is where "watch it happen" belongs; history is for looking backward, not for live monitoring, so this doesn't need the same live-poll machinery.

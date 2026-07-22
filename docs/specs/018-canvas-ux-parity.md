# SPEC-018: Canvas UX Parity — Connection Picker Coverage & Auto-Registered Webhooks

**Status:** Draft — ready for implementation
**Milestone:** Toward a real, self-hostable app (n8n-parity push)
**Author:** Rohan
**Depends on:** SPEC-006 (connection profiles/picker), SPEC-012 (trigger adapters — `telegram_adapter`'s `bot_token_connection`), SPEC-015 (durable graph identity — this spec's auto-webhook-registration needs a stable graph/webhook path to register against)

## 1. Goal

Close two remaining "had to use curl/the terminal for something the canvas should just do" gaps, both hit directly this week: (1) any config field that references a connection *by name* only gets a real picker UI if that field is literally named `connection` — `bot_token_connection`, `embedding_model_connection`, etc. fall through to a plain text box; (2) activating a graph with a `telegram_adapter` still requires manually running Telegram's `setWebhook` API by hand outside the app entirely.

## 2. Why this, why now

Both surfaced directly while actually wiring up the real Telegram bot this week — not hypothetical gaps. #1 caused a real incident (a bot token got typed directly into a graph JSON file and pasted into a chat transcript, because the field looked like it wanted a value, not a reference, and there was no picker to make the distinction obvious). #2 is the literal remaining manual step between "click Activate" and "it actually works" for the single trigger type this project has real, live-tested experience with.

## 3. Scope

In scope:
- `ConfigPanel.tsx`'s field-name special-casing (`renderField`) extended from an exact match on `"connection"` to the same generic convention the backend already uses (`connection_reference_names()`, `backend/connections/resolver.py`: any key that is exactly `"connection"` or ends in `"_connection"`) — so `bot_token_connection`, `embedding_model_connection`, and any future field following this naming convention all get the real `<ConnectionPicker>`, not a plain text input, with zero per-field-name additions needed going forward.
- A delete action in `ConnectionPicker` for an existing saved connection (currently only reachable via `DELETE /connections/{name}`, curl-only) — replaces that workaround directly.
- **Auto-registering the Telegram webhook on Activate**: when an activated graph contains a `telegram_adapter`, the backend calls Telegram's `setWebhook` API itself, using that adapter's `bot_token_connection` (which — per SPEC-012's original deferral — currently sits unused; this spec is what finally reads it for something real) and a **operator-provided public base URL** (the app cannot discover its own externally-reachable address; this is a new, explicit setting, not something inferred).
- Symmetrically, deactivating a graph with a `telegram_adapter` calls Telegram's `deleteWebhook`, so a deactivated graph doesn't leave a dangling registration pointed at a route that no longer exists (the exact 404 failure mode diagnosed live this week).

Out of scope (future specs):
- Auto-registration for other trigger-adapter types beyond Telegram (e.g. a future Slack/Discord adapter) — this spec establishes the pattern for one real, already-built adapter; extending it to a not-yet-built one is that future adapter's own spec's concern
- Solving public reachability itself (tunneling, hosting) — SPEC-016's concern; this spec only consumes a public base URL, it doesn't produce one
- Sticky notes, canvas groups, data pinning — real, deferred UX items from the earlier gap-analysis discussion, intentionally not bundled into this spec to keep it scoped to the two concrete incidents above

## 4. Design decisions (resolved)

- **Connection-reference detection uses the same rule as the backend's `connection_reference_names()`, duplicated in the frontend rather than fetched from an endpoint** — this is presentation logic (which field gets which input widget), not shared executable behavior, so a small, obviously-correlated duplication (with a comment cross-referencing the backend function) is simpler than inventing an endpoint whose only job is describing a naming convention.
- **The public base URL is a new, explicit, operator-set value** — not stored per-connection (a bot token connection isn't inherently tied to one deployment's URL) and not stored per-graph (the same deployment serves all its graphs at one base URL). Stored as a single app-level setting (new small config, e.g. alongside where the encryption key / auth credential from SPEC-017 already live) rather than invented as a new per-node config field.
- **`setWebhook`/`deleteWebhook` failures during Activate/Deactivate are surfaced as part of that action's own error path** (the existing `activationError` state, SPEC from last week), not silently ignored — if Telegram rejects the webhook registration, Activate itself should reflect that failure, not report success while the actual external wiring silently didn't happen.

## 5. Acceptance criteria

- [ ] `bot_token_connection` (and any other `*_connection`-suffixed field) renders the real connection picker in the canvas, verified on the actual `telegram_adapter` node
- [ ] A connection can be deleted from the picker UI directly, without curl
- [ ] Activating a graph containing a `telegram_adapter` with a real bot token automatically results in Telegram's `getWebhookInfo` (checked live) showing the correct URL — no manual `setWebhook` call
- [ ] Deactivating that same graph results in Telegram's `getWebhookInfo` showing no webhook registered (or a cleared one) — no manual `deleteWebhook` call
- [ ] A real, live, end-to-end demonstration: message a real Telegram bot from a phone, with the *only* manual steps being (a) setting the public base URL once and (b) clicking Activate in the canvas — everything else, including the Telegram-side webhook registration, happens automatically
- [ ] Full existing test suite passes unchanged

## 6. Open questions

- Should the public base URL be validated (e.g. a real reachability check) when set, or trusted as-is? Recommend: a lightweight "Test" action (attempt a real request to `{base_url}/openapi.json` or similar) at the point it's set, surfaced as a warning if unreachable — not a hard block, since a URL can be correct but momentarily unreachable (e.g. a tunnel not yet started) without the setting itself being wrong.
  - Resolved: adopted as recommended, using SPEC-017's `/health` endpoint (unauthenticated, purpose-built for exactly this kind of check) rather than `/openapi.json`.

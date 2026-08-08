# SPEC-030: App Catalog Gallery — Pre-Filled, One-Click-Feeling App Connections

> **Amended by SPEC-031** (`docs/specs/031-connections-hub.md`): the "exactly 3 entries, verified-only" catalog-scope decision below (§3/§4) was deliberately widened — the catalog now also includes 7 real, live-confirmed-reachable but not yet end-to-end-proven apps (Linear, Atlassian, Stripe, Slack, Notion, Zoom, Figma), distinguished from the original 3 via a new `verified: bool` field. This section is left as written below as an accurate record of what was decided at the time, not edited in place.

**Status:** Draft
**Milestone:** User-facing ease-of-use push
**Author:** Rohan
**Depends on:** SPEC-021 (per-user MCP OAuth engine — `backend/mcp/oauth_flow.py`), SPEC-023 (admin-managed global connections), SPEC-025 (app integration catalog — `CredentialType`, `auth_type`, popup OAuth, "Bootstrap catalog nodes"; this spec fills the one piece SPEC-025 described but never actually shipped, see §2)

## 1. Goal

Turn "add a new app connection" from filling out a blank, fully-generic technical form (transport, server URL, OAuth client ID/secret, scope, credential type, auth type — eleven raw fields) into picking a known app from a small gallery and only being asked for the one or two things that are genuinely admin-specific (an OAuth client's own ID/secret; a self-hosted server's own URL). Starting with a small, honest set of apps this deployment has actually proven work — not a large list of guessed-at, never-tried integrations.

## 2. Why this, why now — and why not assume SPEC-025 already did this

SPEC-025's own §3 scope described "a pre-populated app catalog: a small, admin-curated list of vetted apps... each declaring its known MCP server URL, its `CredentialType`, and a cached snapshot of its tool schema." Checked directly against the real code before writing this spec: that data structure was never actually built. What SPEC-025 shipped is real and load-bearing (`credential_type`/`auth_type` fields on `McpServerConnectionConfig`, the popup OAuth flow, `POST /connections/{name}/catalog-bootstrap`) — but "catalog-bootstrap" only *re-scans a connection an admin already fully hand-configured*; there is no list anywhere of known apps' server URLs, scopes, or setup steps. Confirmed by grepping the whole backend and frontend for any catalog data structure — none exists. Adding "loads of different connections" today means filling the full generic form, from scratch, every single time, with no help remembering Gmail's correct scope string or Context7's server URL.

This matters now specifically because the plan is to add many app connections in a row, not because of any single blocking bug — this is a real, repeated-friction UX gap, not a defect.

## 3. Scope

**In scope:**

- **A small, source-controlled app catalog** (`backend/mcp/app_catalog.py`): a plain Python list of catalog entries, not a database table — this is reviewed, versioned data, the same "code, not runtime-mutable state" posture this project already uses for node-type/connection-type registration (`register_connection_type`). An admin who wants to add or edit an entry edits this file and it ships on the next deploy, exactly like adding a new connection type does today.
- **Starter catalog: exactly three entries**, each grounded in something this deployment has actually, genuinely proven works — not researched-but-untried:
  1. **Gmail** — Google's official, publicly-hosted Gmail MCP server. Real server URL, real `credential_type: "google_gmail_oauth2"`, a real working OAuth scope string — all already proven via `my-gmail`'s real (if currently expired) connection this session.
  2. **Context7** — a public, no-credential-required MCP server (`https://mcp.context7.com/mcp`), live-verified per SPEC-025's own §8 implementation notes.
  3. **Discord** — via `discord-mcp-server` (already in this repo root, a small standalone server purpose-built to prove SPEC-021's OAuth mechanism against a real, non-Google provider). Unlike Gmail/Context7, this app has no single public server URL — every admin who wants Discord support self-hosts their own copy of `discord-mcp-server` first (its own `README.md` already documents this) — so its catalog entry has no pre-filled URL, only a pre-filled `credential_type`/scope and a link to that setup doc.
- **`GET /app-catalog`** — lists the catalog entries (name, description, category, credential_type, auth_type, default scope, a pre-filled server URL when the app has one stable public address, `setup_instructions` text/link otherwise). No secrets ever live in a catalog entry, so this needs no special authorization beyond normal sign-in.
- **A genuine "Add from catalog" path alongside the existing generic form**, not a replacement for it: `ConnectionPicker`'s existing "+ New connection" button gains a first step — pick a catalog app card, or "Custom connection" to fall through to today's full generic form unchanged. Picking a catalog entry pre-fills every field the catalog already knows and shows only the remaining ones (an OAuth client ID/secret for Gmail; a server URL for Discord).
- **Zero new execution/storage path.** Submitting the pre-filled form still calls the exact same `POST /connections` (`CreateConnectionRequest`) every connection creation already goes through — the catalog is purely a client-side (and one small read-only backend list endpoint) convenience layered on top of what SPEC-021/023/025 already built, not a new connection mechanism.
- **Scope (private/global) follows the existing rule unchanged**: any signed-in user can browse the catalog and add a private connection from it (e.g. their own personal Gmail, distinct from an admin-managed shared one); only an admin caller may choose "global" — identical to `CreateConnectionRequest.scope`'s existing behavior today, the catalog changes nothing about who can create what.

**Out of scope (deliberately):**

- **A large, research-based app list.** Explicitly rejected per this session's own resolved decision — every starter entry must be something this deployment has actually proven, not merely researched. Growing the catalog with more apps (Slack, Notion, Linear, etc.) is real future work, once each is actually tried against a real server.
- **Runtime catalog management UI** (an admin adding/editing entries by clicking through the app, rather than editing the source file). The file-based approach matches this project's existing registration conventions and needs no new persistence layer; a management UI is future scope if the catalog grows large enough to justify it.
- **Non-MCP (raw REST/OpenAPI manifest) integrations.** All three starter entries are real MCP servers; broadening the catalog concept to apps with no MCP server at all is a different, larger feature (already flagged as out of scope in SPEC-025 §3 for the same reason).
- **Automatically discovering or crawling for "every possible MCP server."** Admin-curated only, same resolved decision SPEC-025 §4 already made for the underlying mechanism.
- **Caching/pre-capturing each catalog app's tool schema ahead of any admin connecting.** SPEC-025's original description included this; it's not needed to close the actual friction gap (filling the form) and adds real complexity (a schema cache that can drift from the real server) for no proven need yet — an admin still bootstraps after connecting, exactly like today.

## 4. Design decisions (resolved)

- **Catalog data is code, not a database table.** Consistent with `register_connection_type`, the node registry, and every other "extensible, admin-controlled, small-N" list in this codebase — reviewed via git, not editable at runtime. Revisit only if the catalog grows large enough that non-technical admins need to add entries without touching code (not the case at 3 entries).
- **A catalog entry never stores or implies a secret.** `oauth_client_id`/`oauth_client_secret`/an API key are always entered fresh by the admin at add-time and encrypted into the resulting connection through the existing `backend/connections/store.py` path — the catalog only pre-fills the *non-secret* shape (URL, scope, credential type, auth type).
- **A catalog entry's server URL is `str | None`.** Public, stable, single-address apps (Gmail, Context7) declare it directly. Self-hosted apps (Discord) leave it `None`; the add-flow shows a URL field with the catalog's own `setup_instructions` text pointing at how to actually get one (deploy `discord-mcp-server`, use its address). This is the honest way to represent "this app needs real setup work first" without pretending it's one-click when it isn't.
- **The catalog is additive UI, not a fork of connection creation.** `ConnectionPicker`'s existing full form is completely unchanged and still reachable directly ("Custom connection") — every existing test, every existing connection type not in the starter catalog, keeps working exactly as today.

## 5. Data model / implementation notes

- New `backend/mcp/app_catalog.py`:
  ```python
  @dataclass(frozen=True)
  class AppCatalogEntry:
      key: str                          # "gmail", "context7", "discord"
      display_name: str                 # "Gmail"
      description: str                  # one line, shown on the card
      category: str                     # "cloud" (matches ConnectionTypeInfo.category)
      credential_type: str | None       # "google_gmail_oauth2", None for Context7 (no auth)
      auth_type: Literal["oauth2", "api_key", "bearer"]
      server_url: str | None            # pre-filled when stable/public; None when self-hosted
      default_scope: str | None         # OAuth scope string, when applicable
      requires_oauth: bool
      setup_instructions: str | None    # shown when server_url is None, or OAuth client setup is needed
  CATALOG: list[AppCatalogEntry] = [ ... the three entries ... ]
  ```
- `GET /app-catalog` (`backend/api/app.py`): returns `CATALOG` as-is, mapped to a small `AppCatalogEntryInfo` response schema (`backend/api/schemas.py`) — no connection to any specific user's data, purely static.
- Frontend: `frontend/src/panels/ConnectionPicker.tsx`'s "+ New connection" button opens a small two-step flow: a `AppCatalogGallery` (new, small component — cards with name/description, "Add" per card, plus a "Custom connection" fallback card) → selecting a catalog entry pre-populates `draftConfig`/`draftType` (already-existing state in `ConnectionPicker`) from the entry's non-secret fields, then renders the *same* existing config form, just already filled in except for the genuinely-missing pieces. `SettingsPanel`'s equivalent "+ New global connection" affordance gets the same treatment for consistency.
- `frontend/src/api/client.ts`/`types.ts`: `fetchAppCatalog()` and `AppCatalogEntryInfo`, following the existing `request<T>` pattern.

## 6. Acceptance criteria

- [ ] `GET /app-catalog` returns exactly the three starter entries with the correct, real values (Gmail's actual working scope string, Context7's actual URL, Discord's `server_url: null` + setup instructions) — no secrets in the response.
- [ ] Picking "Gmail" from the gallery pre-fills `url`, `credential_type`, `oauth_scope`, `requires_oauth`/`auth_type` in the connection form, leaving only `oauth_client_id`/`oauth_client_secret` for the admin to fill in.
- [ ] Picking "Context7" pre-fills everything needed to create a working connection with zero additional input beyond a name.
- [ ] Picking "Discord" pre-fills `credential_type`/`auth_type`/scope but leaves `url` empty with `setup_instructions` visibly shown, pointing at `discord-mcp-server`'s own README.
- [ ] A connection created via the catalog flow is byte-for-byte the same shape, and behaves identically (test/bootstrap/OAuth-connect all work unchanged), as one created via the existing full form — proving the catalog added zero new execution paths.
- [ ] A non-admin signed-in user can browse the catalog and create a *private* connection from it; attempting "global" scope from the same flow is rejected exactly like the existing form already rejects it for a non-admin.
- [ ] "Custom connection" still reaches the exact original, unmodified generic form.
- [ ] Full test suite passes (backend + frontend).
- [ ] Live-verified: a real Context7 connection created end-to-end through the new catalog flow, against the real public Context7 server (no credential needed, so this is the one starter entry that can be demonstrated fully live without depending on a personal credential this session doesn't have).
- [ ] `git diff main -- backend/execution/engine.py` is empty — this is purely a connections/frontend feature.

## 7. Open questions

None outstanding — the two questions this session actually needed a decision on (spec-first vs. not; how conservative the starter list should be) were resolved directly with the user before this draft was written: write a real spec (this document), and ship only apps already proven working (Gmail, Context7, Discord), not a broader researched-but-untried list.

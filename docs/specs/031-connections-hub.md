# SPEC-031: Unified Connections Hub — Marketplace, Your Connections, Global Connections

**Status:** Draft
**Milestone:** User-facing ease-of-use push
**Author:** Rohan
**Depends on:** SPEC-030 (app catalog gallery — this spec revises SPEC-030's resolved "verified-only, exactly 3 entries" catalog-scope decision, see §2/§4), SPEC-023 (admin-managed global connections — the "Global Connections" tab is that same admin section relocated, not rebuilt), SPEC-025 (`CredentialType`/`auth_type`, popup OAuth, catalog-bootstrap — all reused unchanged)

## 1. Goal

Replace three currently-scattered, inconsistent ways of dealing with connections — a per-node inline picker+form (`ConnectionPicker.tsx`), an admin-only section buried inside the general Settings sheet (`SettingsPanel.tsx`), and (as of SPEC-030) a small catalog gallery only reachable from those two places — with **one consolidated hub**: a centered modal, dimming the rest of the app, with tabs for **Marketplace** (browse/add known apps), **Your Connections** (every connection you can see, of every type — mcp_server, anthropic, ollama, openai, gemini, vector_store, telegram), and **Global Connections** (admin-only — today's SettingsPanel admin section, moved here). Opening a node's connection field now opens this same hub, scoped to that field's constraints, instead of a separate inline form.

## 2. Why this, why now — and the catalog-scope revision

Confirmed directly (again) before writing this: SPEC-030 resolved "exactly 3 starter entries, only apps this deployment has actually proven work" as a deliberate, narrow decision. That's being revised here, explicitly, not silently: the marketplace now also includes **Linear, Atlassian, Stripe, Slack, Notion, Zoom, and Figma** — real, official, vendor-hosted MCP servers, each live-confirmed reachable (a real `initialize` POST against every one of their real URLs returns a real `401`, not a connection failure or a generic error page — i.e., a real, correctly-typed, auth-gated MCP endpoint exists at each address) but **not** completed end-to-end through a real OAuth handshake the way Gmail/Context7/Discord were. The catalog entry model gains a `verified: bool` field so the UI can honestly distinguish "proven end-to-end by this deployment" from "a real, reachable, documented server — try it and see." This is a deliberate widening of what SPEC-030 called "researched-but-untried," done knowingly rather than by drift.

Separately: today, picking or creating a connection for a node happens inline inside that node's config panel, while managing global connections happens in an entirely different sheet, and there's no single place to see everything you're connected to across every connection type. That fragmentation is the actual problem this spec closes.

## 3. Scope

**In scope:**

- **Catalog expansion** (`backend/mcp/app_catalog.py`): `AppCatalogEntry` gains `verified: bool`. Ten entries total — Gmail/Context7/Discord (`verified=True`, unchanged from SPEC-030) plus Linear/Atlassian/Stripe/Slack/Notion/Zoom/Figma (`verified=False`, real live-confirmed URLs, `default_scope=None` for all seven — deliberately relying on the already-built OAuth-discovery scope fallback (`DiscoveredOAuthServer.scopes_supported`, `backend/mcp/oauth_flow.py`) rather than guessing scope strings nobody has confirmed against these servers).
- **A new centered `Dialog`-based hub component** (`frontend/src/panels/ConnectionsHub.tsx`), replacing today's fragmented surfaces. A new `frontend/src/components/ui/dialog.tsx` shadcn primitive is added — `Sheet` is already built on the same underlying Radix `Dialog` import (confirmed by reading `sheet.tsx`), just side-anchored; `Dialog` is the same primitive, centered, with a dimming overlay, matching "comes in the middle, blurring everything else."
- **Three tabs** (reusing the already-installed `components/ui/tabs.tsx`):
  1. **Marketplace** — the SPEC-030 gallery, now showing all 10 entries with a "Proven" vs "Documented" badge (from `verified`), otherwise unchanged mechanics (pick an entry → pre-filled form → same `POST /connections`).
  2. **Your Connections** — every connection the caller can see (private + visible global), of every type, in one list — replacing the type-scoped dropdown `ConnectionPicker` used to show and the private-connections view `SettingsPanel` partially showed. Reuses `GET /connections` unchanged.
  3. **Global Connections** — admin-only tab (hidden entirely for a non-admin, matching today's existing role check), containing exactly what `SettingsPanel`'s admin connections section already does today (create/edit/delete/promote/bootstrap/reconnect/api-key) — relocated, not rebuilt.
- **Node-level integration**: a node's config field that references a connection now shows a compact trigger (current value + "Change") that opens the hub pre-scoped to that field's existing `allowedTypes`/`requiredCapability`/`requiredCredentialType` filters (unchanged filtering logic, just relocated from inline to the hub), with each connection row in "Your Connections" and each newly-created connection offering a "Use for this field" action that closes the hub and reports the name back — the same `onChange` contract `ConnectionPicker` already has today.
- **Zero backend execution/storage changes** beyond the catalog data file and its one new field. Every route (`POST /connections`, `GET /app-catalog`, OAuth start/callback, api-key, bootstrap, promote, delete) is reused exactly as-is.

**Out of scope (deliberately):**

- **Any change to how a connection is actually resolved/used at run time** (`backend/connections/resolver.py`, `backend/execution/engine.py`) — this is entirely a connection-management UI consolidation.
- **Completing a real OAuth handshake for the 7 newly-added, unverified entries.** Each needs the project owner's own registered OAuth app in that vendor's console — real setup work, not something this spec does on their behalf. `verified` flips to `true` for an entry only once that's actually done and demonstrated, matching SPEC-030's own bar.
- **A fourth "Local" tab, or any other type-specific tab.** "Your Connections" already lists every connection of every type together; splitting further isn't needed until it's actually unwieldy.
- **Editing/removing catalog entries via UI.** Same as SPEC-030 — still a code file, admin edits it directly.
- **Any change to `backend/execution/engine.py`.**

## 4. Design decisions (resolved)

- **Widening SPEC-030's catalog-scope decision is explicit, not silent.** SPEC-030's own text (§3/§4) is left as-is (an accurate record of what was true then); this document is the one that supersedes it going forward, and `verified` is the mechanism that keeps both the narrow (Gmail/Context7/Discord) and the widened (the other 7) apps honestly labeled in the same list rather than overstating confidence in the new ones.
- **The hub is a genuine replacement, not an addition alongside the old surfaces.** `ConnectionPicker.tsx`'s inline form and `SettingsPanel.tsx`'s admin connections section are removed once their functionality is confirmed working identically inside the hub's tabs — not left running in parallel as dead code.
- **`Dialog`, not another `Sheet`.** A side-anchored sheet doesn't match "comes in the middle, blurring everything else" — a centered, dimmed modal is a genuinely different pattern, worth its own primitive rather than stretching `Sheet`'s existing styling.
- **No new backend authorization model.** "Global Connections" tab visibility is the exact same `me.role === "admin"` check `SettingsPanel` already does; nothing new to reason about there.

## 5. Data model / implementation notes

- `backend/mcp/app_catalog.py`: add `verified: bool` to `AppCatalogEntry`; the 7 new entries (server URLs, credential types, and setup-instruction links below, all confirmed live/real, not guessed):
  - Linear (`https://mcp.linear.app/mcp`, `linear_oauth2`) — linear.app/docs/mcp
  - Atlassian (`https://mcp.atlassian.com/v1/sse`, `atlassian_oauth2`) — support.atlassian.com/atlassian-rovo-mcp-server
  - Stripe (`https://mcp.stripe.com`, `stripe_oauth2`) — docs.stripe.com/mcp
  - Slack (`https://mcp.slack.com/mcp`, `slack_oauth2`) — api.slack.com (app/OAuth registration)
  - Notion (`https://mcp.notion.com/mcp`, `notion_oauth2`) — developers.notion.com/guides/mcp
  - Zoom (`https://mcp.zoom.us/mcp/zoom/streamable`, `zoom_oauth2`) — developers.zoom.us/docs/mcp
  - Figma (`https://mcp.figma.com/mcp`, `figma_oauth2`) — developers.figma.com/docs/figma-mcp-server
- `backend/api/schemas.py`: `AppCatalogEntryInfo` gains `verified: bool`.
- `frontend/src/components/ui/dialog.tsx`: new, generated the same way `sheet.tsx`/`tabs.tsx` were (owned source, not an opaque dependency).
- `frontend/src/panels/ConnectionsHub.tsx`: the modal shell + tab routing + the "scoped to a field" mode (props mirroring `ConnectionPicker`'s existing `allowedTypes`/`requiredCapability`/`requiredCredentialType`/`onChange`).
- `frontend/src/panels/connections/MarketplaceTab.tsx`, `YourConnectionsTab.tsx`, `GlobalConnectionsTab.tsx`: one file per tab, each a focused extraction of logic that already exists today (`AppCatalogGallery` + creation form for Marketplace; `GET /connections` list for Your Connections; `SettingsPanel`'s admin section verbatim for Global Connections) rather than new logic.
- `ConnectionPicker.tsx` becomes a thin trigger that renders the current selection and opens `ConnectionsHub` with the field's existing filter props.
- `SettingsPanel.tsx` loses its connections section (now solely in the hub); keeps public-base-url and invite-user sections unchanged.

## 6. Acceptance criteria

- [ ] `GET /app-catalog` returns 10 entries; the original 3 keep `verified: true`; the 7 new ones return `verified: false` with the exact real URLs above.
- [ ] The hub opens centered with a dimming overlay (not a side sheet) and shows all three tabs to an admin, and only Marketplace + Your Connections to a non-admin (no Global Connections tab at all, not just disabled).
- [ ] Opening a node's connection field opens the hub pre-scoped — only type/capability/credential-appropriate connections and marketplace entries are selectable, matching today's `ConnectionPicker` filtering exactly.
- [ ] Selecting an existing connection, or completing a new one (via Marketplace pre-fill or a from-scratch custom connection), for a scoped field closes the hub and correctly sets that field's value — proven equivalent to today's `ConnectionPicker` behavior, not just visually similar.
- [ ] Every action that existed in `SettingsPanel`'s admin section (create/edit/delete/promote/bootstrap/reconnect/api-key) works identically from the Global Connections tab.
- [ ] `ConnectionPicker.tsx`'s old inline form and `SettingsPanel.tsx`'s old admin connections section are actually removed, not left in place unused.
- [ ] Full test suite passes (backend + frontend).
- [ ] Live-verified: the two already-proven catalog entries (Context7 fully, Gmail's pre-fill) still work identically through the new hub; at least one of the 7 new entries (Linear, Notion, or another — whichever the admin has real OAuth credentials for, or a no-credential reachability check otherwise) confirmed reachable through the hub's own flow.
- [ ] `git diff main -- backend/execution/engine.py` is empty.

## 7. Open questions

- **Which of the 7 newly-added entries, if any, should the admin actually complete real OAuth registration for during implementation** (to flip a second/third entry to `verified: true`, matching SPEC-030's own "at least one real, live demonstration" bar for whichever ones get fully wired) — versus leaving all 7 at `verified: false` for this pass and completing registration later as each is actually needed? Not blocking implementation of the hub itself either way.

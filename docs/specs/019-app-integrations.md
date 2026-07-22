# SPEC-019: App Integrations Framework — Dynamically-Generated MCP App Nodes (+ Manifest Fallback)

**Status:** Draft — ready for review
**Milestone:** Toward a real, self-hostable app (n8n-parity push)
**Author:** Rohan
**Depends on:** SPEC-006 (connection profiles/picker), SPEC-012 (sub-node/cluster pattern — `trigger_adapter` slot, `telegram_adapter`), SPEC-013 (visual design system / palette categories), SPEC-018 (connection picker coverage, Telegram auto-webhook registration), ADR-004 (MCP connectivity — this spec revises its stdio-only transport scope; a new ADR documenting that revision is part of this spec's Phase 0, since ADRs are never edited after merge)

## 1. Goal

Give this project "loads of apps ready to use" the way n8n's palette does — but reach that breadth the way it's actually achievable here: **dynamically generate typed, purpose-built nodes from any MCP server's own live tool discovery**, rather than hand-authoring a manifest per app. One generic execution path underneath; many distinct, browsable, properly-schema'd nodes on top, each bound to a specific (server, tool) pair. A hand-authored manifest stays available as a deliberate **fallback**, used only where no trustworthy MCP server exists for an app that still needs first-party reliability — concretely, Telegram today.

## 2. Why this, why now — and why the direction changed mid-design

This spec started as "hand-build Telegram + Slack node families" (see git history of this file / the earlier draft). Two things surfaced during design review that changed the plan:

1. **n8n's real 400+/1,000+ integration count comes from hand-authored declarative node definitions, one per app** — the same cost structure as the original manifest plan. That doesn't scale here any better than it does for n8n's own team; it only produces "loads of apps" if this project is willing to hand-build dozens of them.
2. **MCP servers are already, genuinely self-describing** — the one thing REST-API apps like Telegram/Slack aren't. `mcp_call` already discovers a server's tools live; the only thing missing is turning that live discovery into palette-visible, individually-typed nodes instead of one generic, anonymously-configured node. That's a real, buildable feature — and it gets you breadth (any MCP server, including ones neither we nor the user has to build) without per-app authoring cost.

So the framework flips: **dynamic MCP-driven generation is the default path to breadth. A hand-authored manifest is the deliberate exception**, reserved for apps where MCP coverage doesn't exist or isn't trustworthy enough for unattended production use — Telegram is that concrete case (no official MCP server exists for it as of this spec).

## 3. Scope

**In scope:**

- **Remote MCP transport.** Today's MCP client (`backend/mcp/client.py`) only speaks the local-stdio transport (confirmed by reading it directly — `stdio_client` only). Hosted/gateway MCP servers (e.g. a Slack-hosted server, or broader third-party gateways) are typically reached over the MCP remote transport (HTTP + SSE), which this codebase cannot speak today. This spec adds a remote transport client alongside the existing stdio one, behind a shared internal interface, so discovery and tool-calling code doesn't care which transport a given server uses. **This revises ADR-004's stdio-only scope decision** — a new ADR records that explicitly (Phase 0 below), per this project's "never edit an ADR after merge, supersede instead" rule.
- **A new `mcp_server` connection type.** MCP servers become real, named, saved, testable connections for the first time — closing a real gap where `mcp_call` today requires retyping the full `command`/`args` on every single node instance, with no shared, reusable, credential-store-integrated configuration. Config: `transport` (`stdio` or `remote`), transport-specific fields (`command`/`args` for stdio; `url`/auth for remote), and **`trusted: bool` (default `false`)**. Test Connection performs a real live discovery call and reports the number and names of tools found — the same "prove it actually works" bar every other connection type's test already meets.
- **Dynamic node generation from a saved `mcp_server` connection.** On connection creation (and via an explicit "Refresh capabilities" action thereafter — see design decisions on staleness), the backend performs live tool discovery and registers one generated node type per discovered tool: a real palette entry with its own name and dynamically-resolved typed schema (reusing the exact `resolve_slots` mechanism `mcp_call`/`code` already use), not a generic node the user has to hand-configure. All generated nodes for one connection execute through one shared generic executor (conceptually today's `mcp_call` logic), parameterized by `(connection_name, tool_name)` — this is the "written over a generic node" structure: many typed nodes, one execution path underneath.
- **Trusted servers skip the approval gate.** A generated node's `require_approval` behavior is driven by its source connection's `trusted` flag, not a fixed per-node default — a server explicitly marked trusted lets its generated nodes run unattended (e.g. inside an active, webhook-triggered production graph), closing the gap that would otherwise make dynamically-generated nodes useless for anything but interactive/manual runs.
- **Telegram, as the manifest fallback's proving case** (kept from the original draft, unchanged in substance): existing `telegram_adapter` (listening) stays as-is; new hand-built `telegram_messaging` (send_message, send_photo, send_document, edit_message, delete_message) and `telegram_chat_management` (get_chat, get_chat_member) nodes, manifest-driven per the original design.
- **Slack, as the dynamic path's proving case** (changed from the original draft): instead of hand-building Slack nodes, this spec verifies the dynamic-generation mechanism against a real, reachable Slack MCP server — whichever transport it actually requires (confirmed during implementation; Anthropic's Slack reference server exists, transport to be confirmed rather than assumed here).
- **Registry metadata + palette rendering for two distinct shapes**, both under a new top-level "Apps" category:
  - *Manifest apps* (curated, deep): Apps → \<App\> → \<capability group\> → node types — e.g. Apps → Telegram → Messaging → `telegram_messaging`.
  - *MCP-generated apps* (dynamic, broad): Apps → \<mcp_server connection name\> → \<tool\> — flatter, since there's no human-curated capability grouping for a generically-discovered tool.
- Refactor SPEC-018's Telegram-only activation-sync code in `app.py` behind a small integration-agnostic interface, so it isn't Telegram-specific plumbing that a future manifest app would have to copy.

**Out of scope (future specs/work):**

- Bundling/pre-shipping any MCP servers with this project's own Docker image — confirmed as bring-your-own-server; this project only needs to generically consume whatever server the user points it at.
- The remaining Telegram Bot API surface beyond the ~7 manifested methods, and building manifests for any app beyond Telegram — the dynamic path is now the intended route for further app breadth, not more manifests.
- A generic outbound HTTP-request node (n8n's actual "unlimited" escape hatch) — legitimate future work, not folded into this spec.
- Cross-run conversation memory for agents — still a separate, already-identified future gap; a generated or manifest send-node closes "can it reply," not "does it remember."
- OAuth-based install flows for any app — both connection types (manifest apps' own connection types, and `mcp_server`) use directly-supplied credentials (bot token, server URL/auth), not an OAuth redirect flow.

## 4. Design decisions (resolved)

- **Hybrid, not dynamic-only**: MCP-driven dynamic generation is the default mechanism for breadth; a hand-authored manifest remains a supported, first-class fallback for apps needing production reliability where no trustworthy MCP server exists. Both are real, permanent parts of the framework — the manifest path is not deprecated by this spec, just no longer the primary growth mechanism.
- **Bring-your-own MCP server**: this project's job is to make *any* configured MCP server (local or remote) generically useful — not to maintain a curated bundle of servers itself. Breadth is a function of what the user (or the wider MCP ecosystem) makes available, not this project's own authoring backlog.
- **Remote transport is in scope now**, specifically because "bring your own server" is a weak promise if it only covers local-subprocess servers — a meaningful share of real-world MCP servers (hosted gateways, SaaS-provided servers) use the remote transport, and excluding it would silently limit "loads of apps" to whatever's locally installable.
- **Trust is a property of the connection, not the node.** A single `trusted` flag on the `mcp_server` connection governs every node generated from it — simpler than per-generated-node trust state, and consistent with the connection being the one place credentials/config for that server already live. This is explicitly an MCP-specific concept: manifest-based nodes (Telegram) call a first-party-documented API directly and were never approval-gated in the first place, so they need no equivalent flag.
- **Discovery is refreshed explicitly, not polled.** Tools are discovered once when an `mcp_server` connection is created (as part of Test Connection) and cached as registered node types; a server's capabilities can drift over time (new tools added, old ones removed), so an explicit **"Refresh capabilities"** action re-runs discovery and updates the generated node set — no automatic background polling, to avoid repeatedly spawning subprocesses or hitting a remote endpoint on a timer for no concrete reason.
- **Generated node registrations are rebuilt on backend startup** for every saved `mcp_server` connection, mirroring SPEC-015's `_reactivate_persisted_graphs` pattern — so the palette is correct immediately after a restart, not only after each connection happens to be manually refreshed.
- **`mcp_call` (the existing raw/manual node) is kept, unchanged** — it remains the direct escape hatch for a one-off server/tool combination a user doesn't want to save as a named connection. Generated nodes are an additive, better-UX layer for saved, reusable servers, not a replacement.
- **`app.py`'s Telegram-only activation-sync code is refactored behind a small integration-agnostic interface** ("does this integration support auto webhook registration? if yes, call its hook; if no, report the derived URL for manual entry") so it's not Telegram-specific plumbing a future manifest app has to duplicate.

## 5. Acceptance criteria

**Remote MCP transport:**
- [ ] A real MCP server reachable only over the remote transport (not stdio) can be added as an `mcp_server` connection, with a real, live Test Connection success
- [ ] A real local-stdio MCP server continues to work identically through the same connection type (transport is a config choice, not two divergent code paths from the framework's perspective)

**Dynamic generation:**
- [ ] Saving an `mcp_server` connection to a real Slack MCP server results in real, individually-named, correctly-typed nodes appearing in the palette under Apps → \<that connection\>, with no manifest written for Slack
- [ ] At least one generated Slack node (a message-send capability) is verified with a real, live post into a real Slack channel
- [ ] Marking that connection `trusted` and using the generated send node inside an **active, webhook-triggered** graph runs it with no approval prompt — verified live, end to end (a real inbound trigger causes a real Slack message to be sent, unattended)
- [ ] An untrusted `mcp_server` connection's generated nodes still require approval — verified live (both behaviors demonstrably real, not just one)
- [ ] "Refresh capabilities" on an `mcp_server` connection re-runs discovery and updates the generated node set, demonstrated live against a server whose tool set actually changed (or is simulated to)

**Telegram manifest fallback:**
- [ ] `telegram_messaging` sends a real message via `send_message`, verified live in a real Telegram chat; at least one of `send_photo`/`send_document`/`edit_message`/`delete_message` also verified live
- [ ] `telegram_chat_management` supports `get_chat` and `get_chat_member`, verified live
- [ ] Existing `telegram_adapter` graphs (e.g. `examples/delivery_support_agent.json`) continue to work completely unchanged
- [ ] A `telegram_messaging` send node dropped into a `tool_group` under an `agent`, wired so the agent can reply into the chat it received a message from — real, live round trip from a real phone

**Framework:**
- [ ] Palette renders both grouping shapes correctly: Apps → Telegram → \<capability group\> → node types, and Apps → \<mcp_server connection\> → \<tool\>
- [ ] A new ADR is written documenting the revision to ADR-004's stdio-only transport scope
- [ ] Full existing test suite passes unchanged
- [ ] `git diff main -- backend/execution/engine.py` is empty

## 6. Open questions

- **Exact remote-transport API surface of the installed `mcp` Python SDK version** (the precise client class/module for the remote/streamable-HTTP transport) is not pinned down here — to be confirmed by inspecting the installed package during implementation, not guessed in advance.
- **Which concrete remote MCP server is used to verify the remote transport** — proposing whichever real, reachable Slack MCP server is actually available at implementation time (Anthropic's reference server or another verified option); not committing to a specific one here since I haven't confirmed its transport or hosting details firsthand.
- **Exact Telegram method list** (proposed: `send_message`, `send_photo`, `send_document`, `edit_message`, `delete_message`, `get_chat`, `get_chat_member`) is a reasonable starting slice, open to adjustment if one proves awkward to manifest cleanly.
- **Manifest storage location**: proposing `backend/integrations/telegram/manifest.py`, a new top-level package parallel to `backend/connections/` and `backend/nodes/`, open to relocating once real code exists.

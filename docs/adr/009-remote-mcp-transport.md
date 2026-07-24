# ADR-009: Remote (Streamable HTTP) MCP transport, alongside stdio

**Status:** Accepted
**Date:** 2026-07-22
**Related:** ADR-004 (MCP connectivity — this ADR revises its transport scope), SPEC-019

## Context

ADR-004 deliberately scoped MCP connectivity to local stdio subprocesses only, explicitly deferring a remote transport as a "no real server driving the requirement yet" future extension. SPEC-019 needs to make good on "bring your own MCP server" as a genuine breadth mechanism for dynamically-generated app nodes — a promise that's materially weaker if it only covers servers you can run as a local subprocess. A meaningful share of real, useful MCP servers (hosted/SaaS-provided ones, e.g. Slack's) are reached over HTTP, not stdio. That's now a real, driving requirement, not a guess at a future shape.

## Decision

- **A second transport, `remote`, is added alongside the existing `stdio` transport** — not a replacement. `backend/mcp/client.py`'s stdio path and every existing stdio-only caller (`mcp_call`) are unchanged.
- **Streamable HTTP (`mcp.client.streamable_http.streamablehttp_client`) is the remote transport implementation**, not SSE (`mcp.client.sse.sse_client`) — Streamable HTTP is the current MCP spec's primary remote transport; SSE is largely legacy at this point and not required by anything in this project's scope.
- **A shared `McpTransport`-shaped interface** (`backend/mcp/transport.py`) is introduced so discovery/tool-calling call sites (dynamic node generation, the `mcp_server` connection's `test_connection`) don't need to know or care which transport a given server uses — they call `list_tools(...)`/`call_tool(...)` against a transport config, not against `stdio_client` or `streamablehttp_client` directly.
- **`backend/mcp/remote_client.py`** mirrors `client.py`'s existing structure exactly: same `McpToolInfo`/`McpConnectionError` types (reused, not duplicated), same sync-wrapper-around-async-SDK pattern, same discovery-failure-caching behavior — a remote server is exercised identically to a stdio one from every caller's point of view, differing only in how the underlying session is opened.
- **Auth for a remote server is a bearer/custom header**, supplied as part of the `mcp_server` connection's config (not environment-variable inheritance, which only makes sense for a local subprocess) — the remote transport's connection attempt carries whatever header the connection specifies.

## Rationale

- **Alongside, not instead of, stdio** — the existing `mcp_call` node and every graph using it today must keep working unchanged; this is purely additive infrastructure.
- **Streamable HTTP over SSE** because it's the protocol's current primary remote transport, not a maintenance-mode one; picking it avoids building against a transport that's itself being phased out across the ecosystem.
- **A shared interface, not two divergent code paths in every caller** — SPEC-019's dynamic node generation needs to treat "this connection's tools" identically regardless of transport; branching on transport type at every call site would leak an implementation detail into code that has no reason to care.

## Consequences

- The `mcp_server` connection type (SPEC-019) can point at either a local command or a remote URL, verified live via the same `test_connection` contract every other connection type already has.
- ADR-004's consequence "A remote (HTTP/SSE) MCP server cannot be used yet" is no longer true as of this ADR — superseded for the remote-transport question specifically. ADR-004's other decisions (approval-gating by default, re-fetch-not-snapshot schema discovery, one generic `mcp_call` node for the manual/raw case) are unaffected and remain in force.
- A remote server's reachability now depends on real network conditions (auth token validity, endpoint uptime) in addition to the existing "is the subprocess launchable" failure mode stdio already had — surfaced through the same `McpConnectionError` path, no new error-handling shape needed by callers.

## Alternatives considered

- **SSE instead of/alongside Streamable HTTP**: rejected for v1 — no known server in this project's actual usage needs it; Streamable HTTP alone covers the driving requirement (a hosted Slack-style server).
- **Per-caller transport branching (no shared interface)**: rejected — would leak "which transport does this connection use" into dynamic node generation and connection testing, both of which have no real reason to know.

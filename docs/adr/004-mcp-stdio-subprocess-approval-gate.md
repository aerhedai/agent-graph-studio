# ADR-004: MCP connectivity via local stdio subprocess, approval-gated by default

**Status:** Accepted
**Date:** 2026-07-12
**Related:** SPEC-003 §3–§7

## Context

SPEC-003 needed to let a node call a real external tool via MCP (Model Context Protocol) — the point where the project stops being a self-contained sandbox. Several genuinely open questions needed resolving before implementation: how the server is actually reached (transport), whether/how tool calls need human approval before executing (since a tool can have real side effects — e.g. writing a file), how a tool's schema (needed for `resolve_slots`, per ADR-003) gets discovered and cached, and whether every tool call needs approval or only mutating ones.

## Decision

- **Transport: local stdio subprocess only for v1**, not HTTP/SSE. Confirmed via local testing that reference MCP servers (e.g. `@modelcontextprotocol/server-filesystem`) are launched as a subprocess and communicate over stdio, not reached via a URL. A remote server transport is an explicit, deliberately deferred future extension (likely a `transport: "stdio" | "http"` discriminator later), not solved now.
- **One generic `mcp_call` node type**, config-driven (`command`, `args`, `tool_name`), not a distinct registered node type per server/tool combination. Schema resolution reuses `resolve_slots` (ADR-003) exactly, resolved via a live network call to the server instead of local source parsing.
- **Approval gate: every tool call requires approval by default** (`require_approval: true`), via a blocking terminal prompt (print tool name + arguments, wait for `y`/`n` on stdin) — not just mutating ones. MCP doesn't reliably expose a read/write distinction across arbitrary servers, so narrowing this to "only mutating calls need approval" was rejected as unsafe to assume in general.
- **Schema discovery: re-fetch on every graph validation, never snapshotted into graph JSON.** The discovery cache (keyed by `(command, args)`) also memoizes failures, not just successes, scoped to one process lifetime (i.e. one CLI invocation).
- **Credentials**: `credential_ref`, a key into `ExecutionContext.resources` (ADR-003's pattern), looked up first; if absent, the subprocess inherits the full parent environment, so a credential already exported in the invoking shell reaches the server with zero extra wiring.

## Rationale

- **stdio over HTTP for v1** because that's what's actually running today (CLAUDE.md's "smallest thing that works" bias) — building a second transport for a server that doesn't exist yet in this project's usage would be speculative generality.
- **Approval-by-default over selectively-gated approval** because the cost of a false positive (an unnecessary confirmation prompt on a safe read) is low, while the cost of a false negative (a write silently executing because some server didn't mark it correctly) is a real, uncontrolled side effect. MVP correctness favors the safer default; narrowing it is explicitly left to revisit once real usage shows read-only calls are safe to skip (§3).
- **Re-fetch over snapshot** because a snapshotted schema can silently drift from what the server actually offers — a stale, wrong schema surfacing only as a confusing execution-time failure is worse than an honest, immediate "server unreachable, validation fails" outcome. The failure-inclusive cache exists purely to avoid `check_required_inputs`/`check_type_mismatches` each independently re-spawning (and re-timing-out) the same dead server multiple times within one `validate_graph()` call — not a relaxation of the re-fetch decision itself, since a fresh process still starts with an empty cache.
- **Credentials via environment inheritance, not an explicit `resources`-to-subprocess-env bridge**, because this is what the codebase already does everywhere else: neither `AnthropicLLMClient` nor `OllamaLLMClient` reads secrets from `resources` either — both read their own process environment directly. `resources` has only ever been a test-injection seam (ADR-003), never a real-secrets channel populated by `backend/cli/main.py`. Treating `mcp_call` differently would have been an inconsistent, one-off exception.

## Consequences

- A dead/unreachable MCP server is reported as **valid** by `validate_graph()` (discovery failure returns `None`, matching `resolve_slots`'s established contract), with the concrete error only surfacing at execution time — an explicit, accepted MVP failure mode, not a bug.
- `NodeResult`/`TraceRecord` each gained one field, `side_effect: bool`, and `engine.py` gained one corresponding line flowing it through — the one place this spec's own `engine.py`-diff bar wasn't literally empty, justified in-spec by the same "one-time capability-gated widening" standard ADR-003 already established for `resolve_slots`. Every successful `mcp_call`, read or write, sets `side_effect=True`, since read/write aren't distinguished anywhere else in this MVP.
- Every tool call — even a pure read — requires a human in the loop for now. This is a genuine UX cost, accepted deliberately; a richer, narrower approval UX is explicitly canvas-era territory (SPEC-005+).
- A remote (HTTP/SSE) MCP server cannot be used yet. Any future PC-hosted or team-shared server needs a new transport before `mcp_call` can reach it.

## Alternatives considered

- **Remote HTTP/SSE transport now, alongside stdio**: rejected — no real server driving the requirement yet; would be built against a guess at the eventual shape.
- **A distinct registered node type per server/tool** (e.g. auto-generating `read_text_file_node`): rejected for this spec — real discoverability win, but explicitly canvas-era (a palette can surface this visually); not worth the added registry complexity for a CLI-only MVP.
- **Narrower approval gating (mutating calls only)**: rejected for v1 — MCP doesn't reliably expose the read/write distinction across arbitrary servers; approving everything is the safe default until real usage demonstrates read-only calls are safe to skip.
- **Snapshotting tool schema into graph JSON at authoring time**: rejected — trades a clear "server unreachable" failure for a silent, harder-to-debug "schema drifted from reality" failure.

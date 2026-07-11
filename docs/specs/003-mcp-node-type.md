# SPEC-003: MCP Server as a Node Type

**Status:** Draft — ready for implementation
**Milestone:** Real-World Tool Connectivity
**Author:** Rohan
**Depends on:** SPEC-002 (pluggable node registry, dynamic slot resolution via `resolve_slots`)

## 1. Goal

Let a node call any MCP server's tools — turning the graph from "connects AI models and code" into "connects AI models, code, and real external systems" (databases, APIs, file systems, SaaS tools). This is the point where the project stops being a self-contained sandbox and starts doing things in the world.

## 2. Why this, why now

Per `CLAUDE.md`'s stated differentiation, this project's value is in orchestrating real agent work, not just chaining LLM calls. MCP is the current standard for tool connectivity (per our earlier research: adopted across Claude, and increasingly other providers, as the common interface for agent-to-tool communication). Without this, the project can only ever manipulate text/code — it can't read a real file, query a real database, or call a real API.

## 3. Scope

In scope:
- A generic `mcp_call` node type that connects to a configured MCP server and invokes one of its exposed tools
- Tool discovery: given a server, list its available tools and their schemas (this is what makes the node's inputs "dynamic" in a similar spirit to the `code` node, but schema comes from the server, not from parsing local source)
- Credential/auth handling for MCP servers that require it (at minimum, an API-key-style credential passed via `resources`, following the same pattern established for `llm_client` in SPEC-002)
- A mandatory **human-in-the-loop gate for any tool call the server itself marks as a write/mutating action** (if the MCP spec exposes this distinction) — or, if it doesn't reliably expose this, treat ALL tool calls as requiring approval for MVP, and revisit narrowing that once real usage shows read-only calls are safe to skip
- Trace/observability: tool calls logged with the same rigor as `llm_call` (inputs, outputs, latency; token cost is not applicable here, but "external side effect occurred: yes/no" should be recorded)

Out of scope (future specs):
- A visual tool-picker UI (that's canvas territory, SPEC-005+)
- Multi-server orchestration/routing logic beyond "one node = one server = one tool call"
- Full permission/governance systems beyond a basic per-call approval gate

## 4. Why this is harder than the `code` node's dynamic slots

The `code` node's `resolve_slots` works because the function signature is available locally, via `ast.parse`, before anything executes — safe and cheap. An MCP tool's schema lives on a remote server. This means:
- Discovering a tool's input schema likely requires an actual (read-only) network call to the server at graph-validation time, not just static parsing — a meaningfully different failure mode (server down = validation fails, not just "bad config")
- The schema itself needs to be cached/pinned somehow (do you re-fetch every validation, or snapshot it into the graph JSON at authoring time?) — this is a real open design question, a good candidate for the RFC above

## 5. Data model

### `mcp_call` config
```json
{
  "server_url": "string",
  "tool_name": "string",
  "credential_ref": "string, key into resources dict, following llm_client's pattern",
  "require_approval": true
}
```

### Node behavior
- Inputs/outputs: dynamic, derived from the named tool's schema on the configured server (mirrors `code`'s `resolve_slots` pattern, but resolved via a network call instead of local parsing — see §4)
- If `require_approval` is true (or unconditionally, per §3's MVP default): execution pauses, surfaces the proposed call (tool name + arguments) to the user, and only proceeds on explicit confirmation — this is the human-in-the-loop node behavior from ARCHITECTURE.md §6, but embedded as a mode of `mcp_call` rather than a separate node, for MVP simplicity

## 6. Acceptance criteria

- [ ] `mcp_call` node connects to a real MCP server and successfully invokes a read-only tool (e.g. a filesystem-read or similar low-risk tool), live-verified, non-mocked
- [ ] Tool schema discovery correctly produces per-instance input/output slots without requiring `engine.py` changes (should reuse `resolve_slots`, or prove why it can't)
- [ ] A write/mutating tool call pauses for approval and does not execute until confirmed
- [ ] Credential handling follows the `resources`-bag pattern established in SPEC-002 — no credentials hardcoded or committed
- [ ] A failed/unreachable MCP server produces a clear validation or execution error, not a silent hang or unhandled exception
- [ ] `git diff` on `engine.py`: either empty, or — if not — a written justification following the same standard set by SPEC-002's `effective_inputs`/`effective_outputs` precedent (one-time, capability-gated widening, not per-type coupling)

## 7. Design decisions (resolved)

- **Schema caching: re-fetch on every graph validation, not snapshotted.** Snapshotting into the graph JSON risks silent drift from what the server actually offers — a stale, wrong schema that only surfaces as a confusing failure at execution time. Re-fetching is simpler to implement, and "server unreachable → validation fails clearly" is an acceptable, honest MVP failure mode.
- **One generic `mcp_call` node type, config-driven** (server + tool name in config), not a distinct registered node type per server/tool combination. Auto-generating a node type per tool is a real discoverability win, but it's a canvas-era feature (SPEC-005+) — the CLI-only MVP doesn't benefit from it yet, and it's meaningfully more code to build and maintain now for no present payoff.
- **Approval UX: a blocking terminal prompt.** Print the proposed tool call (name + arguments), wait for explicit y/n on stdin before proceeding. Simplest mechanism that satisfies the human-in-the-loop requirement in §3; a richer UX is canvas territory.
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

Confirmed via local testing: reference MCP servers (e.g. `@modelcontextprotocol/server-filesystem`) communicate over **stdio**, not HTTP — the server is launched as a subprocess, not connected to via URL. Config reflects that:

```json
{
  "command": "string, e.g. \"npx\"",
  "args": ["list", "of", "strings", "e.g. [\"-y\", \"@modelcontextprotocol/server-filesystem\", \"/path/to/sandbox\"]"],
  "tool_name": "string",
  "credential_ref": "string, key into resources dict, following llm_client's pattern -- optional, only needed for servers requiring auth",
  "require_approval": true
}
```

Note: this targets **local, stdio-based MCP servers only** for this spec's MVP. A remote server (e.g. one running on a separate machine, reached over HTTP/SSE, relevant for an eventual PC-hosted setup) is a deliberate future extension — different transport, likely a distinct config shape or a `transport: "stdio" | "http"` discriminator. Not solved here; smallest thing that works for what's actually running today.

### Node behavior
- Inputs/outputs: dynamic, derived from the named tool's schema on the configured server (mirrors `code`'s `resolve_slots` pattern, but resolved via a network call instead of local parsing — see §4)
- If `require_approval` is true (or unconditionally, per §3's MVP default): execution pauses, surfaces the proposed call (tool name + arguments) to the user, and only proceeds on explicit confirmation — this is the human-in-the-loop node behavior from ARCHITECTURE.md §6, but embedded as a mode of `mcp_call` rather than a separate node, for MVP simplicity

## 6. Acceptance criteria

- [ ] `mcp_call` node launches a real local MCP server as a subprocess (confirmed working: `npx -y @modelcontextprotocol/server-filesystem <sandbox-dir>`) and successfully invokes a read-only tool (e.g. reading a known test file from the sandbox), live-verified, non-mocked
- [ ] Tool schema discovery correctly produces per-instance input/output slots without requiring `engine.py` changes (should reuse `resolve_slots`, or prove why it can't)
- [ ] A write/mutating tool call pauses for approval and does not execute until confirmed
- [ ] Credential handling follows the `resources`-bag pattern established in SPEC-002 — no credentials hardcoded or committed
- [ ] A failed/unreachable MCP server produces a clear validation or execution error, not a silent hang or unhandled exception
- [ ] `git diff` on `engine.py`: either empty, or — if not — a written justification following the same standard set by SPEC-002's `effective_inputs`/`effective_outputs` precedent (one-time, capability-gated widening, not per-type coupling)

## 7. Design decisions (resolved)

- **Schema caching: re-fetch on every graph validation, not snapshotted.** Snapshotting into the graph JSON risks silent drift from what the server actually offers — a stale, wrong schema that only surfaces as a confusing failure at execution time. Re-fetching is simpler to implement, and "server unreachable → validation fails clearly" is an acceptable, honest MVP failure mode.
- **One generic `mcp_call` node type, config-driven** (server + tool name in config), not a distinct registered node type per server/tool combination. Auto-generating a node type per tool is a real discoverability win, but it's a canvas-era feature (SPEC-005+) — the CLI-only MVP doesn't benefit from it yet, and it's meaningfully more code to build and maintain now for no present payoff.
- **Approval UX: a blocking terminal prompt.** Print the proposed tool call (name + arguments), wait for explicit y/n on stdin before proceeding. Simplest mechanism that satisfies the human-in-the-loop requirement in §3; a richer UX is canvas territory.

## 8. Implementation notes

Written after implementation, per this spec's own §6 requirement to justify any `engine.py` change rather than make it silently.

- **`resolve_slots` reused with zero contract changes.** `mcp_call`'s `_resolve_mcp_slots` returns `None` on any discovery failure (server unreachable, tool not found) — the exact same contract `code`'s `resolve_slots` already uses. No changes were needed to `resolve_slots`'s type, `effective_inputs`/`effective_outputs`, or `validation/rules.py`. Only **required** input properties from a tool's JSON schema become graph ports (discovered live against the real filesystem server: `read_text_file` has optional `tail`/`head` params alongside required `path` — these are omitted from the graph interface and the server's own defaults apply, since `engine.py`'s input-gathering loop doesn't honor `InputSlotSpec.required=False`; extending that is out of scope here).
- **One `engine.py` line, not zero.** §3 requires trace records to note "external side effect occurred: yes/no." `NodeResult` and `TraceRecord` each gained a `side_effect: bool = False` field; `engine.py`'s success-path `TraceRecord(...)` construction gained one line, `side_effect=node_result.side_effect`, mirroring exactly how `token_cost` already flows from node to trace. The failure path needed no change (defaults `False`). Every successful `mcp_call` execution — read or write — sets `side_effect=True`: since this node's whole purpose is reaching a real external system, and since read/write aren't distinguished anywhere else in this MVP (§3), treating any completed call as an "external side effect occurred" is the interpretation consistent with that. `git diff` on `engine.py` for this spec is this one line.
- **Validation-time vs. execution-time errors for a dead server.** Because discovery failures return `None` (not raise), an unreachable-server graph is reported "valid" by `validate_graph()` — the concrete error surfaces when `run_graph()` actually executes the node. This uses this spec's own §6 wording ("a clear validation **or execution** error") as license: the alternative (letting `resolve_slots` raise a distinguishable, validation-reportable error) was rejected because applied consistently it would have reintroduced the double-reporting bug SPEC-002 fixed for `code`'s malformed-source case. Live-verified: a bad `command` produces a clear, fast (<1s) error in the trace, not a hang.
- **Discovery cache includes failures, not just successes.** `check_required_inputs`/`check_type_mismatches` each independently resolve a node's schema (once per node, again per incident edge), so a naive success-only cache would re-spawn (and re-timeout) an unreachable server on every call site within one `validate_graph()`, and could even produce inconsistent results if the failure were flaky. `backend/mcp/client.py`'s module-level cache, keyed by `(command, args)`, memoizes the raised exception too. A fresh process (i.e. every CLI invocation) starts with an empty cache, so this still satisfies "re-fetch every validation" — it only removes redundant spawns *within* one invocation.
- **Credentials**: `credential_ref` is looked up in `ctx.resources` first (test/override seam, matching `llm_client`'s established pattern) and, if present, injected into the server subprocess's environment as `credential_ref.upper()`. If absent, no override is added — the subprocess still inherits the full `os.environ`, so a credential already exported in the invoking shell reaches the server for free. This mirrors the *actual* precedent already in the codebase: neither `AnthropicLLMClient` nor `OllamaLLMClient` reads secrets from `resources` either; they read their own process environment directly. `resources` has only ever been a test-injection seam, never populated with real secrets by `backend/cli/main.py` — confirmed no CLI change was needed.
- **Live verification performed** (non-mocked, against the real `npx -y @modelcontextprotocol/server-filesystem ~/mcp-test-sandbox` server): (1) real read of `sample.txt` via `read_text_file` with the terminal approval prompt genuinely answered over stdin; (2) a `write_file` call declined at the prompt — confirmed the file was **not** created on disk; (3) the same call approved — confirmed the file **was** created on disk with the exact content, and removed afterward; (4) an unreachable server (bad `command`) — confirmed a fast, clear error in the trace, not a hang. `uv run pytest tests/ -v` — 99 tests pass, fully mocked/offline (the live checks above were run separately, by design, since mocked coverage alone previously let real gaps through in SPEC-001/002).
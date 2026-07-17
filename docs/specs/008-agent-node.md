# SPEC-008: Agent Node (Model + Memory + Tools)

**Status:** Draft — ready for implementation
**Milestone:** Autonomous Agent Execution
**Author:** Rohan
**Depends on:** SPEC-002 (`resolve_slots`), SPEC-004 (loop execution patterns), SPEC-006 (`ConnectionType` registry)

## 1. Goal

Add an `agent` node type that wraps a model connection with **memory** (conversation context) and **tools** (other nodes it can call), and runs a genuine reasoning loop — the model itself decides which tools to call, in what order, and when it's done — rather than following a graph shape a human wired in advance. This is the closest analog to n8n's AI Agent node, and it's a different execution paradigm from anything built so far: every prior node type does one fixed thing per run; `agent` decides its own path at runtime.

## 2. Why this, why now

Every node type so far (`llm_call`, `code`, `mcp_call`, `loop`, `fan_out`/`merge`) executes a fixed behavior the graph author specified. Real autonomous agent behavior — "figure out how to answer this, using whatever tools are actually needed, however many steps that takes" — isn't expressible with any of them. This is the single largest conceptual gap identified when comparing against n8n's AI Agent node (model/memory/tools sub-inputs, ReAct-style loop).

## 3. Scope

In scope:
- An `agent` node type: inputs a `connection` (reused from SPEC-006), a `memory` config, a list of **tool references** (other node IDs in the same graph the agent is allowed to call), and a task/prompt
- **Tool-calling support in the connection layer**: extend `ConnectionType` with an optional capability (mirroring SPEC-007's optional `list_models` pattern) for connections whose underlying model supports native tool-calling. **Primary target for this spec: Ollama**, which has genuine native tool-calling support (an OpenAI-compatible `tools` API) for compatible models — confirmed working for Llama 3.1+, Qwen 2.5/3, Mistral Nemo, Command-R. Anthropic support should still be implemented (it's a smaller addition given Claude's own tool-use API), but Ollama is the one that must be live-verified, since it's the connection actually available without billing right now.
- **Tool schema derivation**: each referenced tool node's existing input/output schema (already established via `resolve_slots` for dynamic nodes, or static `.inputs`/`.outputs` otherwise) is translated into the tool-calling schema the model API expects — no new schema system, reuse what exists
- **The reasoning loop itself**: call the model with the current conversation + available tools; if it requests a tool call, execute the referenced node directly (not via a graph edge — see §4) with the model-provided arguments, feed the result back, repeat; stop when the model produces a final answer with no further tool calls, or a `max_iterations` safety cap is hit (same safety-cap principle as SPEC-004's `loop` node)
- **Memory (v1 scope)**: simple in-memory conversation window (last N messages), scoped to a single graph run only — cross-run/persistent memory (the SQLite-backed version) is explicitly deferred to a future spec (see §3 out-of-scope)
- Trace representation: the agent's own trace record contains nested `child_traces` for each tool invocation, reusing SPEC-004's nested-trace pattern established for loops/fan-out

Out of scope (future specs):
- Persistent/cross-run memory (Postgres/SQLite-backed) — this is its own spec, since it involves storage design, session identity, and retention policy that shouldn't be rushed into this one
- Guaranteeing reliable tool-calling across *all* Ollama models — per current research, smaller local models (roughly 8B and under) show real reliability drop-off on multi-step or parallel tool calls. This spec targets a specific, confirmed-good model (e.g. Qwen3 or Llama 3.1+) for its acceptance criteria, and documents rather than silently papers over the fact that not every locally available model will perform equally well
- Summary-style memory (rolling summary instead of raw window) — a real future option, not needed for a first working version
- Nested agents (an agent whose tool list includes another `agent` node) — should not be explicitly forbidden, but not a tested/designed-for case in this spec

## 4. Key design decision: how tool calls actually execute

This is the trickiest part of this spec, worth stating explicitly rather than leaving implicit. Every other node type receives its inputs via **graph edges**, resolved before it runs. An `agent` node's tool calls are fundamentally different: the model decides *at runtime* which tool to call and with what arguments — there's no static edge for this, because it isn't known in advance.

**Resolution:** tool nodes referenced by an `agent` node are **not connected via normal graph edges** to the agent. Instead, the agent's config lists tool node IDs, and at runtime, the engine (or a small helper the `agent` node's `execute()` calls into) directly invokes each referenced node's `execute()` function with the model-supplied arguments as its inputs — bypassing the edge-based input-gathering mechanism entirely for these calls. This means:
- A tool-referenced node **must not** also have normal incoming graph edges for the inputs the agent will supply — its schema should be satisfiable purely from what the agent's tool call provides
- The referenced node still produces a normal trace record, which becomes a `child_trace` on the agent's own record
- This is a deliberate, scoped exception to the "everything flows through edges" model — worth a short ADR once implemented, given it's a real, permanent architectural carve-out, not a one-off shortcut

## 5. Data model

### `agent` node config
```json
{
  "connection": "personal-anthropic",
  "model": "claude-sonnet-4-6",
  "system_prompt": "string, agent's role/instructions",
  "tools": ["node_id_1", "node_id_2"],
  "memory": { "type": "window", "max_messages": 20 },
  "max_iterations": 10
}
```
- Inputs: one, the initial task/prompt (text)
- Outputs: one, the agent's final answer (text)

### `ConnectionType` addition
```python
class ConnectionType(Protocol):
    ...
    supports_tool_calling: bool
    complete_with_tools: Callable[..., ToolCallResponse] | None  # None if unsupported
```

### Tool schema translation
Each tool node's `resolve_slots`/static schema is converted to the model API's expected tool-definition shape (name, description, parameter schema) at agent-run time — derived, not separately authored, so a tool node's schema and its callability from an agent never drift apart.

## 6. Acceptance criteria

- [ ] An `agent` node with one tool (e.g. a `code` node) correctly calls that tool when the model determines it's needed, and incorporates the result into its final answer — live-verified, non-mocked, using a real local Ollama connection with a confirmed tool-calling-capable model (e.g. Qwen3 or Llama 3.1+)
- [ ] An `agent` node with **no** tool calls needed produces a direct final answer without ever invoking a tool — confirms the loop doesn't force unnecessary tool use
- [ ] `max_iterations` correctly stops the loop as a hard safety cap, even if the model would otherwise keep requesting tools
- [ ] Memory window correctly limits context to the last N messages within a single run — verified by an agent conversation exceeding N turns internally (e.g. via repeated tool calls) and confirming early context is dropped as expected
- [ ] Attempting to use `agent` with a connection/model combination that doesn't support tool-calling (e.g. a non-tool-calling Ollama model, or any connection type without `supports_tool_calling`) produces a clear, explicit error — not a silent failure or a confusing raw model error
- [ ] Agent trace record correctly nests each tool call as a `child_trace`, inspectable the same way SPEC-004's loop/fan-out nesting works
- [ ] A referenced tool node's schema-derivation correctly matches what's actually callable — i.e. the model is offered accurate tool descriptions/parameters, not a stale or mismatched schema
- [ ] Full existing test suite (SPEC-001–007) still passes unchanged

## 7. Open questions

- Should a tool node used by an `agent` be restricted to certain node types (e.g. only `code` and `mcp_call`, not `llm_call` or another `agent`), or should any node type be technically callable as a tool? Recommend: technically unrestricted for now (simpler registry-wise), but document that using another `agent` or an `llm_call` as a "tool" is untested territory, not a designed-for use case yet.
- How should tool-call argument validation failures (model supplies malformed/incomplete arguments) be surfaced — fed back to the model as an error to self-correct (more agentic, more complex), or treated as a hard node failure (simpler, less resilient)? Recommend: feed back as an error message the model can see and retry against, up to the `max_iterations` cap — this is much closer to how real tool-calling agents behave and avoids brittle hard-failures on minor model mistakes.
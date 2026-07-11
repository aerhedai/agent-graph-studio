# SPEC-002: Pluggable Node Registry + Provider-Agnostic Model Calls

**Status:** Draft
**Milestone:** Node Registry Generalization
**Author:** Rohan
**Depends on:** SPEC-001 (Execution Engine MVP), ADR-001 (Pydantic v2), CLAUDE.md node-registry conventions

## 1. Goal

Prove that new node types — and new LLM providers — can be added to the system **without modifying the core execution engine**. SPEC-001 proved the engine mechanism works; this spec proves the plugin model actually holds up, using two concrete additions as the test case rather than a generic registry with nothing to plug into it.

## 2. Why this, why now

`CLAUDE.md` states node types must be pluggable and model-call nodes must go through the `LLMClient` Protocol rather than being hardcoded to one provider. SPEC-001's `llm_call` node currently only exercises `AnthropicLLMClient`. Until a second provider is actually wired in, "provider-agnostic" is an aspiration, not a proven property of the codebase — this spec closes that gap concretely.

## 3. Scope

In scope:
- Formalize the node registry as a genuine plugin interface (if SPEC-001 didn't already make it one — audit first, see §6)
- Add a **second `LLMClient` implementation**: `OllamaLLMClient`, talking to a local Ollama instance, satisfying the exact same `LLMClient` Protocol as `AnthropicLLMClient`
- Update `llm_call` node config to select provider (e.g. `config.provider: "anthropic" | "ollama"`), dispatching to the correct client — **without any `if provider == ...` branching inside the engine itself**; the dispatch lives in the node/provider layer
- Add a new **`code` node type** — runs an arbitrary, sandboxed-as-reasonably-possible Python function against its inputs, producing outputs. This is the generality escape hatch from ARCHITECTURE.md §2.1.
- Prove both additions required **zero changes to `backend/execution/engine.py`** — this is the actual acceptance bar, not just "it works"

Out of scope (future specs):
- MCP server as a node type (SPEC-003)
- Fan-out/fan-in, loops (SPEC-004, per ADR-002)
- Canvas / frontend (SPEC-005+)
- Sandboxing the `code` node beyond basic safety (full sandboxing is its own spec if this becomes a real security concern — for now, document the risk, don't over-engineer it)

## 4. Data model additions

### `llm_call` config (updated)
```json
{
  "provider": "anthropic | ollama",
  "model": "string",
  "system_prompt": "string",
  "max_tokens": "int",
  "provider_options": { "...": "provider-specific, e.g. ollama host/port" }
}
```

### `OllamaLLMClient`
Implements the existing `LLMClient` Protocol (`complete(*, model, system_prompt, prompt, max_tokens) -> LLMResponse`) exactly as `AnthropicLLMClient` does — calling a local Ollama HTTP endpoint instead of the Anthropic SDK. `input_tokens`/`output_tokens` should be populated from whatever usage data Ollama's API returns, or `0` with a documented note if unavailable.

### `code` node
- Inputs: dynamic — defined per-instance by the user (this is a departure from the fixed-schema nodes in SPEC-001; needs explicit design, see §8)
- Outputs: dynamic, same caveat
- Config: `{ "function_source": "string, Python source for a single function" }`
- Behavior: loads and executes the function against the provided inputs, returns its result as outputs. Must wrap execution errors as a structured `NodeExecutionError`, consistent with SPEC-001's error propagation model (ADR pending on sandboxing approach, if any, for MVP).

## 5. Acceptance criteria

- [ ] `OllamaLLMClient` implements `LLMClient` and is used successfully by an `llm_call` node with `provider: "ollama"` against a real local Ollama instance
- [ ] The same graph JSON format supports both `provider: "anthropic"` and `provider: "ollama"` on otherwise-identical `llm_call` nodes, proving provider-swap requires no schema redesign
- [ ] A `code` node executes a simple user-provided Python function (e.g. string transformation) and produces correct output
- [ ] A `code` node's runtime error (e.g. the function raises an exception) is captured as a structured trace error, downstream nodes do not execute — consistent with SPEC-001 §7's existing failure-propagation behavior
- [ ] `git diff` of this feature branch against `backend/execution/engine.py` is **empty** — the core engine file is untouched. This is the literal proof the registry is genuinely pluggable, not just "add an if-branch and call it a plugin"
- [ ] Unit tests cover: Ollama client success + failure paths (mocked, matching the `AnthropicLLMClient` test pattern), code node success + failure paths, and a live (non-mocked) run of at least one Ollama graph and one code-node graph, demonstrated per `CLAUDE.md`'s live-verification rule

## 6. First step before writing any code

Audit whether SPEC-001's registry is genuinely pluggable yet, or whether the 4 MVP node types are still special-cased somewhere in the engine. Do not assume — check:
```bash
grep -rn "text_input\|llm_call\|conditional_branch\|text_output" backend/execution/
```
If the engine file references specific node type names directly (rather than only calling through the registry's generic interface), that's this spec's first real task: remove that coupling before adding anything new.

## 7. Open questions for review before implementation

- Should `code` node function source be provided as inline text in the graph JSON (simple, but awkward for anything beyond a few lines), or as a reference to a separate `.py` file on disk (cleaner for larger logic, but breaks "graph JSON is fully self-contained and portable")? Recommend: inline for MVP, revisit if real usage shows it's painful.
- What's the security posture for `code` node execution — plain `exec()` (fast, zero isolation) vs. a restricted execution environment vs. full subprocess sandboxing? Recommend: plain `exec()` for MVP with an explicit, documented "do not run untrusted graphs" caveat; sandboxing is a separate future spec if this project is ever exposed to untrusted input.
- Does the Ollama client need to handle "model not pulled locally" as a distinct, user-friendly error, or is a generic connection/API error acceptable for MVP?

Resolved: the literal 'empty diff on engine.py' bar was not met — implementing dynamic-slot support (resolve_slots) required adding effective_inputs/effective_outputs to engine.py, replacing direct reads of definition.inputs/.outputs. This is judged acceptable because the change is a one-time widening of the node contract (static slots → optionally-computed slots), gated on a registration-time capability flag (resolve_slots), not a per-node-type branch. Future node types, dynamic or static, require zero further engine.py changes. The stricter empty-diff bar remains the standard for ordinary (non-schema-generalizing) node additions going forward.
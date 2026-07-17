# ADR-003: Engine/node-type decoupling via a generic resources bag + result_slot, not type-name branching

**Status:** Accepted
**Date:** 2026-07-11
**Related:** SPEC-002 §3, §5, §6

## Context

SPEC-001 proved the engine mechanism (topological execution, validation, tracing) against four fixed node types. SPEC-002's actual goal was narrower and harder: add a second `LLMClient` implementation (`OllamaLLMClient`) and a wholly new `code` node type **without `backend/execution/engine.py` branching on node type names to do it** — CLAUDE.md's explicit pluggability bar. SPEC-002 §6 mandated auditing this first rather than assuming SPEC-001 already delivered it: `grep -rn "text_input\|llm_call\|conditional_branch\|text_output" backend/execution/` to check whether the 4 MVP types were still special-cased anywhere in the engine.

Two concrete problems needed a generic (not per-type) answer:
1. How does a node's `execute()` body get the collaborators it needs (an LLM client, eventually credentials, approval callbacks) without the engine constructing or knowing about them?
2. How does the engine know which node's output becomes the graph's final result, without hardcoding `text_output` (or any other type name) as "the" output node?

## Decision

- **`ExecutionContext` gains a generic `resources: dict[str, Any]` bag**, passed unchanged from `run_graph`'s caller through to every node's `execute()`. The engine never inspects or populates it — it's purely an opaque pass-through. Each node's `execute()` looks up what it needs by a self-chosen string key (e.g. `"llm_client"`) and falls back to its own default construction if absent.
- **`NodeDefinition` gains an optional `result_slot: str | None`**, naming one of that node type's own input slots whose value the engine captures into the graph-level result when the node executes. Any node type can opt into being a graph output by declaring this at registration time — the engine has no `if node.type == "text_output"` anywhere.
- **A later, closely related widening**: `resolve_slots`, an optional per-instance schema resolver on `NodeDefinition`, for node types (starting with `code`) whose actual input/output slots depend on their own config rather than being fixed for the whole type. `effective_inputs`/`effective_outputs` helper functions were added to `backend/registry/base.py`, and `engine.py`/`validation/rules.py` call these instead of reading `.inputs`/`.outputs` directly.

## Rationale

- **`resources` as an opaque bag, not a typed dependency-injection container**, keeps the engine genuinely ignorant of what any node type needs. A typed container (e.g. `resources.llm_client: LLMClient | None`) would have required the engine's own code to know "LLM client" is a concept that exists — exactly the coupling this spec exists to eliminate. The cost is weaker compile-time safety (a node could look up the wrong key and silently get `None`), accepted because every node already handles a missing resource explicitly (build-a-default or raise a clear error), and this exact pattern needed to extend cleanly to entirely unrelated future needs (MCP credentials in SPEC-003, named LLM connections in SPEC-006) without further engine changes — which it did, in both cases, with zero `engine.py` diff.
- **`result_slot` over a hardcoded output-node-type check** because the alternative (`if node.type == "text_output": result[node.id] = ...`) is precisely the kind of type-name branching CLAUDE.md prohibits, and would need a new `elif` for every future type that might terminate a graph.
- **`resolve_slots` was the one place this spec's own literal "empty diff on `engine.py`" bar (§5) was knowingly not met.** Supporting `code`'s dynamic per-instance ports required `effective_inputs`/`effective_outputs` to replace direct `.inputs`/`.outputs` reads in both `engine.py` and `validation/rules.py`. This is judged acceptable and recorded here rather than silently absorbed: it's a one-time widening of the node contract (static slots → optionally-computed slots), gated on a registration-time capability flag, not a per-node-type branch — every node type since (`mcp_call`, `fan_out`, `merge`) required zero further `engine.py` changes to also become dynamic-schema. The stricter empty-diff bar remains the standard for ordinary node additions.

## Consequences

- Every future cross-cutting need that a node's `execute()` requires (credentials, clients, callbacks) flows through `resources`, by convention, not by engine change. This held for SPEC-003 (MCP credentials, approval callback), SPEC-005 (unrelated: engine gained `on_round_start`/`on_trace_record` callbacks, but still no node-type coupling), and SPEC-006 (named connection clients) — all landed with an empty `engine.py` diff.
- `resources` being a plain, unvalidated `dict[str, Any]` means a node with a typo'd lookup key fails silently into its "resource absent" branch rather than a type error at call time. No test regression has surfaced from this in practice; revisit only if it does.
- `resolve_slots`'s `None`-on-failure contract (rather than raising) means a node whose dynamic schema can't currently be resolved (e.g. malformed `code` source) is treated as "unresolvable, skip this node" by validation, deferring to `check_config_schema` to report the real error — a convention every later dynamic-schema type (`mcp_call`, `fan_out`, `merge`) had to follow to avoid double-reporting the same problem.

## Alternatives considered

- **A typed `resources` container, or constructor-injected dependencies per node type**: rejected — reintroduces the engine needing to know what a "kind" of resource is, defeating the purpose.
- **Auto-detecting an output node** (e.g. "whichever node has no outgoing edges"): rejected — ambiguous for graphs with multiple terminal nodes or a terminal node that isn't meant to be a result (a side-effecting `mcp_call` with no downstream edge), and implicit in a way `result_slot`'s explicit opt-in isn't.

# SPEC-004: Loops and Fan-Out/Fan-In

**Status:** Draft — ready for implementation
**Milestone:** Agentic Execution Patterns
**Author:** Rohan
**Depends on:** SPEC-001 (execution engine), SPEC-002 (pluggable registry, `resolve_slots`), ADR-002 (loop-as-subgraph decision)

## 1. Goal

Add the two execution patterns deliberately deferred out of every prior spec: **loops** ("repeat until done") and **fan-out/fan-in** ("run several things in parallel, then combine results" — the planner/worker/critic pattern). Together these are what let a graph express genuinely agentic behavior, not just a fixed-length pipeline.

## 2. Why this, why now

Every node type built so far (`llm_call`, `conditional_branch`, `code`, `mcp_call`) executes exactly once per graph run, in a strict topological order. Real agent workflows need: retrying/iterating until a condition is met, and running independent branches concurrently rather than one at a time. Per ADR-002, this was explicitly deferred until the core engine was proven — SPEC-001 through SPEC-003 have now exercised the engine against four genuinely different node types (fixed-schema, dynamic-schema via local parsing, dynamic-schema via remote discovery), so it's reasonable to now stress it against a fundamentally different execution shape.

## 3. Scope

In scope:
- A **`loop` node type** wrapping a sub-graph, per ADR-002's design: from the outer graph's perspective it has exactly one input and one output (still a true DAG node), but internally re-invokes its sub-graph repeatedly, re-injecting its own output as the next input, until a stop condition is met
- A **fan-out node** and a **merge/wait-all node**, per ARCHITECTURE.md §5 — one input splits into N parallel branches; a merge node blocks until all N have completed, then produces one combined output
- **Concurrent execution** of independent branches in the engine — this is a real change to the execution algorithm (§6 of SPEC-001 assumed strict sequential topological order); branches with no data dependency on each other must be able to run at the same time, not just be *orderable* independently
- Trace representation for nested/repeated execution (a loop's internal iterations, a fan-out's parallel branches) — needs its own shape, since the flat list-of-records trace from SPEC-001 assumed one execution per node per run

Out of scope (future specs):
- Visual representation of loops/fan-out on the canvas (SPEC-005+)
- Dynamic/runtime-determined fan-out width driven by data (e.g. "one worker per item in a list of unknown length") — MVP assumes a fixed, config-specified worker count
- Cross-iteration state beyond the loop's own re-injected output (e.g. accumulating a running list across iterations) — a real future need, not solved here

## 4. Design decisions (resolved)

- **Loop stop condition**: an explicit iteration cap (`max_iterations` in config) combined with an optional condition-check on the sub-graph's output (reusing the existing `conditional_branch` node type inside the sub-graph — no new condition-evaluation logic needed). If `max_iterations` is hit first, the loop stops regardless of condition — prevents infinite loops from ever being possible even from a badly authored graph.
- **Fan-out width**: fixed at config time (`worker_count: int`), not computed from data at runtime. This is a deliberate MVP simplification (per §3's out-of-scope) — dynamic width requires a different data-flow model (mapping over a list) that's a bigger, separate design problem.
- **Concurrency mechanism**: use Python's `asyncio` for concurrent branch execution, since node bodies calling out to LLMs/MCP servers are I/O-bound — matches the async recommendation already surfaced as current best practice for this kind of workload. This is a real, non-trivial change to `run_graph`'s internals (moving from a synchronous for-loop over topological order to scheduling concurrent tasks for independent branches) — expect this to touch `engine.py` substantially. Unlike SPEC-002/003's node-type additions, this spec's core deliverable *is* an engine change, not a node type working around one; frame the resulting diff accordingly rather than trying to hold it to the "empty diff" bar.
- **Trace shape for loops/fan-out**: nest child trace records under a parent record (e.g. the `loop` node's own trace entry contains a `child_traces: list[TraceRecord]` field representing each iteration's full execution, and similarly for fan-out's parallel branches) rather than flattening everything into one top-level list. Keeps "what happened inside this one node" inspectable as a unit — relevant later for the canvas's per-node inspection feature (ARCHITECTURE.md §8).

## 5. Data model

### `loop` node config
```json
{
  "sub_graph": { "...": "a nested GraphSpec, same schema as the top-level graph" },
  "max_iterations": "int",
  "stop_condition_slot": "string, optional -- name of a boolean-typed output slot inside the sub-graph; if present and true, loop stops before max_iterations"
}
```
- Inputs: one, matching the sub-graph's expected entry input
- Outputs: one, the sub-graph's final output after the loop stops

### `fan_out` / `merge` node configs
```json
// fan_out
{ "worker_count": "int" }
// merge
{ "expected_input_count": "int" }
```
- `fan_out`: one input, N outputs (`branch_1` ... `branch_N`), each receiving a copy of the same input
- `merge`: N inputs (`input_1` ... `input_N`), one output — a list/collection of all N results, in completion order or index order (resolve during implementation; index order is simpler and more predictable, prefer it)

## 6. Acceptance criteria

- [ ] A `loop` node correctly re-invokes its sub-graph, re-injecting output as next input, stopping at `max_iterations`
- [ ] A `loop` node stops early when `stop_condition_slot` evaluates true, without waiting for `max_iterations`
- [ ] A `fan_out` → N parallel workers → `merge` graph executes all N branches concurrently (not sequentially) — verify via timing (N branches with an artificial delay should complete in roughly one delay's worth of wall-clock time, not N times that)
- [ ] `merge` correctly waits for all N branches before producing output, and fails clearly if any branch errors (does not silently produce a partial result)
- [ ] Trace output correctly nests loop iterations and fan-out branches as child records, not flattened
- [ ] Full existing test suite (SPEC-001 through 003) still passes unchanged — this refactor must not break prior sequential-execution behavior for graphs that don't use loop/fan_out/merge
- [ ] At least one live, non-mocked run demonstrating a real loop (e.g. iterating a `code` node transformation 3 times) and one real fan-out/merge (e.g. two parallel `code` node branches merged) — both are free to test, no external provider needed

## 7. Open questions

- Should a `loop` node's internal sub-graph be allowed to contain its own nested `loop`/`fan_out` nodes (true recursion), or is one level of nesting sufficient for MVP? Recommend: allow it structurally (don't special-case against it), but don't write dedicated tests for deep nesting until a real use case demands it.
- Error handling inside a loop: does one failed iteration stop the whole loop (fail-fast) or get recorded and skipped (continue with next iteration)? Recommend: fail-fast for MVP, consistent with SPEC-001's existing "failed node stops downstream execution" behavior — revisit only if a real workflow needs resilience over strictness.
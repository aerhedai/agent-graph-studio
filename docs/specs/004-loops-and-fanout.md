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

## 8. Implementation notes

Written after implementation, following the SPEC-003 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **`engine.py` diff is substantial, as this spec anticipated** (§4: "expect this to touch `engine.py` substantially... frame the resulting diff accordingly rather than trying to hold it to the 'empty diff' bar"). `run_graph`'s single sequential `for node_id in order:` loop over one fixed `kahn_order()` result was replaced with a layered/wavefront concurrent scheduler: each round, every still-pending node is sorted into `ready` (ships to `asyncio.gather` this round — the actual concurrency), `blocked` (an input can never arrive; permanently skipped, no trace record, identical in effect to the old one-shot check), or left pending to re-check next round. Node bodies (`NodeDefinition.execute`) are unchanged, still plain synchronous functions — the engine runs each one via `asyncio.to_thread`, which is what actually delivers concurrency for I/O-bound bodies (blocking HTTP/subprocess calls release the GIL during the wait). `run_graph`'s public signature is byte-for-byte unchanged; it's now a thin `asyncio.run(...)` wrapper around the async scheduler, so every existing call site (CLI, all tests, `loop`'s own recursive call) needed zero changes. `kahn_order`/`schema/topo.py` is no longer used by the engine (still used by `validate_graph`'s cycle check) — the scheduler discovers execution order dynamically instead of computing one upfront.
- **Full existing test suite passes with zero modifications** — all 99 pre-existing tests (SPEC-001 through SPEC-003) pass unchanged against the new scheduler. Verified both by hand-tracing the one existing test with genuinely-independent concurrent-eligible branches (`test_independent_branch_continues_after_sibling_failure`) through the new algorithm before writing any code, and by actually running the full suite (`uv run pytest tests/ -v`) after.
- **Fan-out branches do *not* get `child_traces`, a disclosed deviation from this spec's own §6 checklist line** ("nests... fan-out branches as child records, not flattened"). A `fan_out`'s branches are ordinary sibling nodes in the same top-level graph, reached via ordinary typed edges and executed by the same scheduler — unlike `loop`'s genuinely separate nested `run_graph()` invocation, there's no non-arbitrary boundary for what a fan-out branch's "child trace" would even contain (just the immediate branch node? everything up to `merge`? branches of different lengths?). Concurrency is instead evidenced by branches' overlapping `started_at`/`finished_at` timestamps in the ordinary flat trace — confirmed live (two branches starting within 50µs of each other, each taking the same ~1.5s, total wall-clock ~2.4s rather than ~3s+ sequential). Discussed and confirmed with the spec's author before implementation.
- **`loop`'s sub-graph entry/exit conventions** (not fully specified by §5's literal data model): the sub-graph must contain exactly one `text_input`-type node, whose `config.value` is overwritten with the current loop value each iteration (the only zero-input node type, the natural unambiguous "seed a value" convention); the sub-graph's `RunResult.result` must have exactly one entry, used as that iteration's output and the next iteration's input. Both are enforced with a clear `NodeExecutionError` if violated (zero or multiple candidates). `child_traces` is `list[list[TraceRecord]]` (one inner list per iteration) rather than a single flat list, since spec's literal wording didn't resolve how multiple iterations' trace lists compose.
- **`merge`'s "fails clearly, no silent partial result" requirement** relies entirely on the engine's existing generic skip mechanism, unchanged since SPEC-001 — no merge-specific error-detection code. If a branch fails, `merge`'s corresponding input never arrives, so `merge` is never invoked at all (no trace record, never a partial/wrong result) while the actual failure is clearly visible on the failing branch's own trace record. Consistent with every other failure-propagation case in this system.
- **Live verification performed** (non-mocked): a `code` node transformation looped 3 times via `max_iterations`, run through the real CLI (`a → a! → a!! → a!!!`, with `child_traces` correctly containing one full 3-node sub-graph trace per iteration); two `code` node branches (`time.sleep(1.5)` each) fanned out and merged, run through the real CLI, with the trace's own timestamps showing both branches starting within 50 microseconds of each other and total wall-clock time (~2.4s) far below the ~3s+ sequential execution would have taken. `uv run pytest tests/ -v` — 118 tests pass (99 pre-existing unchanged + 19 new).
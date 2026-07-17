# ADR-005: Concurrent round-based async scheduler, replacing sequential topological execution

**Status:** Accepted
**Date:** 2026-07-12
**Related:** ARCHITECTURE.md §5, SPEC-001 §6, SPEC-004 §3–§4, §8

## Context

SPEC-001's `run_graph` executed nodes via a single upfront `kahn_order()` topological sort, then a plain sequential `for node_id in order:` loop — correct, but strictly one-node-at-a-time regardless of whether nodes were actually data-dependent on each other. SPEC-004 needed genuine concurrent execution to make `fan_out`/`merge` (ARCHITECTURE.md §5's planner/worker/critic pattern) meaningful: running N independent branches sequentially would defeat the point of fan-out, and §6's own acceptance criterion required proving concurrency via wall-clock timing, not just structural correctness. This is explicitly flagged in SPEC-004 §4 as a real engine change, not a node type working around one — unlike SPEC-002/003, this spec's core deliverable *is* the engine change.

## Decision

Replace `run_graph`'s single sequential pass with a **layered/wavefront concurrent scheduler**: each round, every still-pending node is classified as `ready` (all inputs satisfied — dispatched to `asyncio.gather` this round, the actual concurrency), `blocked` (an input can never arrive, e.g. upstream failure or an unfired conditional branch — permanently skipped, no trace record), or left pending to re-check next round. Node bodies remain plain synchronous functions (`NodeDefinition.execute` is unchanged); the engine runs each one via `asyncio.to_thread`, which delivers real concurrency for I/O-bound bodies (blocking HTTP/subprocess calls release the GIL while waiting). `run_graph`'s public signature stays byte-for-byte synchronous — it becomes a thin `asyncio.run(...)` wrapper around the new async scheduler, so every existing call site (CLI, all tests, `loop`'s own recursive `run_graph` call) needed zero changes.

`kahn_order`/`schema/topo.py` is no longer used for execution ordering — the scheduler discovers ready nodes dynamically each round instead of computing one fixed order upfront. It's retained and still used by `validate_graph`'s cycle-detection check, which is a structural property independent of execution order.

## Rationale

- **`asyncio` over threads/multiprocessing** because the workload is I/O-bound (LLM calls, MCP subprocess calls), not CPU-bound — `asyncio.to_thread` gives the concurrency benefit without the overhead or complexity of process-level parallelism, and matches the standard recommendation for this shape of Python workload.
- **Round-based/wavefront over a fully event-driven task graph** (e.g. spawning a task per node immediately when its inputs become available, no explicit rounds) because it's simpler to reason about and trace: a round boundary is a natural, inspectable checkpoint (this is also what SPEC-005 later hooked `on_round_start`/`on_trace_record` into for live per-node status, with a documented caveat that nodes within the same round only become individually observable once the round's `gather` completes — an accepted consequence of this shape, not a gap in those callbacks).
- **Keeping `run_graph`'s public signature synchronous** rather than making it `async def` (and pushing async-ness onto every caller) avoids a breaking change rippling through the CLI, the entire test suite, and `loop`'s recursive self-call, for a benefit (avoiding one `asyncio.run()` wrapper) that doesn't materialize until SPEC-005's API layer — and even there, `asyncio.run()`'s "no running event loop on this thread" requirement was solved by keeping every FastAPI route a plain `def` (ADR-006), not by making `run_graph` itself async.
- **Node bodies staying synchronous** (not becoming `async def`) was a deliberate scope boundary: converting every node type's `execute()` to async would have been a much larger, cross-cutting rewrite (every LLM/MCP client call site) to capture a concurrency benefit `asyncio.to_thread` already delivers for free at the scheduler level.

## Consequences

- `engine.py`'s diff for this spec is substantial, not the near-empty diff SPEC-002/003 held to — explicitly anticipated and accepted in SPEC-004 §4 rather than treated as a violation of that prior precedent.
- All 99 pre-existing tests (SPEC-001 through SPEC-003) pass unchanged against the new scheduler, verified both by hand-tracing the one existing test with genuinely-independent concurrent-eligible branches through the new algorithm before writing code, and by running the full suite after.
- Fan-out branches do **not** get nested `child_traces` the way `loop` iterations do — a disclosed deviation from this spec's own original data-model expectation. A `fan_out`'s branches are ordinary sibling nodes reached via ordinary edges in the same top-level graph, executed by the same scheduler; unlike `loop`'s genuinely separate nested `run_graph()` invocation, there's no non-arbitrary boundary for what a fan-out branch's "child trace" would contain. Concurrency is instead evidenced by branches' overlapping `started_at`/`finished_at` timestamps in the ordinary flat trace.
- A node's status only becomes individually observable at round boundaries (SPEC-005's live-status callbacks inherit this), not the instant it individually finishes within a round of several.

## Alternatives considered

- **A different execution model entirely (actor/message-passing) to tolerate literal graph cycles**: already rejected in ADR-002 for the loop case; not revisited here, since fan-out/merge don't need cycles either (they're a fan-out DAG shape, not a cyclic one).
- **Threads or multiprocessing instead of `asyncio`**: rejected — no CPU-bound workload in any current node type to justify the overhead, and I/O-bound concurrency is exactly what `asyncio.to_thread` already provides.
- **A fully event-driven (non-round-based) scheduler**: rejected for MVP — real concurrency gain is the same for the workloads this project has today, but loses the simple, inspectable round boundary that later proved useful for live status reporting.

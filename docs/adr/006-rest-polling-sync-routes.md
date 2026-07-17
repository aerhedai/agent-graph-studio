# ADR-006: REST + polling API transport; every route handler stays synchronous

**Status:** Accepted
**Date:** 2026-07-16
**Related:** SPEC-005 §4, §8

## Context

SPEC-005 added the first HTTP layer over the engine (FastAPI, per CLAUDE.md's existing tech-stack note), needing to: submit a graph for execution, and report live per-node status (pending → running → success/error) back to the canvas as it runs, without holding the HTTP request open for a potentially long-running graph (loops, per ADR-005/SPEC-004, could run for a while). Separately, a real correctness constraint surfaced during design review: `validate_graph()` and the `resolve-slots` logic both transitively call `resolve_slots` for `mcp_call`, which internally does its own `asyncio.run(...)` (ADR-004's stdio-subprocess wrapper, `backend/mcp/client.py`). Calling that from a coroutine already running on an event loop — which any `async def` FastAPI route runs on — raises `RuntimeError: asyncio.run() cannot be called from a running event loop`.

## Decision

- **API transport: plain REST + polling, not WebSockets/SSE, for MVP.** `POST /runs` validates and returns a `run_id` immediately; the run itself executes via FastAPI's `BackgroundTasks` (dispatched through Starlette's `run_in_threadpool`, a genuine worker thread). `GET /runs/{run_id}` is polled by the frontend (~500ms interval) for current status + trace-so-far.
- **Every FastAPI route is declared as a plain `def`, never `async def` — a blanket policy, not decided per-route.** Starlette dispatches synchronous route functions through a worker thread automatically, giving each request its own thread with no event loop of its own — the same "no event loop on this thread" precondition already relied on for `loop` node's own recursive `run_graph()` call (ADR-005). This makes `resolve_slots`'s internal `asyncio.run()` safe to call from any route, unconditionally, without having to reason about which specific routes happen to touch `mcp_call` today or might in the future.
- `run_graph`/`_run_graph_async` (ADR-005) gained two optional callback parameters, `on_round_start` and `on_trace_record`, both defaulting to `None` and used by the background task to update an in-memory run record incrementally — the mechanism that makes live polling meaningful rather than just "done or not."

## Rationale

- **Polling over WebSockets/SSE** because it's simpler, has no new failure modes (dropped connections, reconnection/backoff logic) to build and debug, and is a completely reasonable choice for a single-user local tool where sub-second latency to a status update isn't a real requirement. WebSockets would give smoother updates, but that's a UX polish concern, not a correctness one — revisit only if polling latency genuinely becomes a problem once the tool is in real use.
- **Blanket plain-`def`-routes policy over case-by-case async** because the failure mode (a route silently working today, then breaking the moment someone adds an `mcp_call`-touching code path to it) is exactly the kind of coupling-by-omission this project's conventions (CLAUDE.md, ADR-003's decoupling precedent) try to avoid elsewhere. A blanket rule removes the need to audit every route's transitive call graph for hidden `asyncio.run()` calls, now or later.
- **Background-task dispatch over holding the request open** because SPEC-004 already established that a graph can legitimately run for a while (loops with many iterations); a `POST /runs` that blocked until completion would tie up an HTTP connection for an unbounded duration.

## Consequences

- Real-time updates are polling-interval-grained (~500ms), not push-based. Acceptable for the current single-user, local-dev-tool scope; would need revisiting for a hosted, multi-user, or lower-latency use case.
- The plain-`def` constraint applies to *all* current and future routes, including ones that will never touch MCP — a small, deliberate over-restriction traded for never having to reason about it per-route.
- `on_round_start`/`on_trace_record`'s status granularity inherits ADR-005's round-boundary limitation: nodes within the same concurrent scheduling round (e.g. two `fan_out` branches) still transition together as a batch, since they only become individually observable once that round's `asyncio.gather` returns. Documented as inherent to the scheduler shape, not a gap in the callbacks themselves.
- The in-memory run store (`backend/api/runs.py`) has no persistence — a restarted API process loses all run history. Acceptable for MVP; would need a real store for anything beyond local development use.

## Alternatives considered

- **WebSockets or Server-Sent Events for live status**: rejected for MVP — real UX improvement, but adds real complexity (connection lifecycle, reconnection) with no concrete problem it solves yet for a single local user.
- **Making `resolve_slots`/`mcp_call`'s MCP client genuinely async** (removing the internal `asyncio.run()`) instead of constraining routes to plain `def`: rejected as a much larger refactor (the whole MCP client layer, `ADR-004`) to solve a problem the plain-`def`-routes policy already solves cleanly and completely.
- **Per-route decision on `async def` vs. plain `def`, based on whether that route currently touches MCP**: rejected — fragile, since a future route could gain an MCP-touching code path without anyone noticing the constraint had silently started applying to it.

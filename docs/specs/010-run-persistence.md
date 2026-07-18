# SPEC-010: Execution History & Run Persistence

**Status:** Draft — ready for implementation
**Milestone:** Platform Observability
**Author:** Rohan
**Depends on:** SPEC-005 (API layer, `GET /runs/{run_id}`), SPEC-009 (triggers — the actual motivating gap)

## 1. Goal

Persist every graph run (manual, triggered, or agent-invoked) to durable local storage, and expose an endpoint to list/browse past runs. Right now, a run's trace only exists in the single API response returned at the time it happened — once a `schedule_trigger` fires in the background, that result is effectively gone unless you happened to be watching. This is the last piece needed before triggers (SPEC-009) are genuinely usable unattended.

## 2. Why this, why now

SPEC-009 made graphs capable of running themselves, unattended, on a schedule or via webhook. Without this spec, there is no way to answer "what happened while I wasn't looking" — the single biggest practical gap once self-starting workflows exist. n8n's own execution history view exists for exactly this reason. This is also foundational groundwork for later error-handling workflows (needing to look back at what failed) and eval/regression work (comparing runs over time).

## 3. Scope

In scope:
- A local, durable store for run records — **SQLite**, matching the project's local-first philosophy already established for connection profiles (SPEC-006) rather than requiring a separate database service
- Every `run_graph` invocation — manual (`POST /runs`), triggered (SPEC-009), or nested (agent tool calls, loop iterations) — gets its full result (trace + status + timing) written to this store, not just returned and discarded
- `GET /runs` — list past runs, paginated, filterable by graph ID, status (success/failed/running), and trigger source (manual/schedule/webhook)
- `GET /runs/{run_id}` — updated to read from persistent storage rather than only in-memory state, so a run's result remains queryable long after the process that ran it
- Basic retention: no automatic deletion for v1 (explicitly deferred, see out-of-scope) — everything is kept

Out of scope (future specs):
- Retention/pruning policy (auto-delete runs older than N days, cap total storage) — real, deferred; note as a known simplification, not silently ignored, since an unattended trigger firing every minute will accumulate rows indefinitely
- A dedicated history browser UI in the canvas (SPEC-005's frontend) — this spec is backend-only; a future spec can build the visual browser once this data actually exists to browse
- Full-text search across run history — basic filters (§3) are sufficient for v1

## 4. Design decisions (resolved)

- **Storage: SQLite**, not Postgres/Redis. Consistent with the project's stated local-first, single-user philosophy (same reasoning applied when choosing SQLite over Postgres/Redis for the deferred agent-memory persistence work) — no new service to run, no new connection type to manage, a single file the API server reads/writes directly.
- **Write point: inside the API/CLI layer, immediately after `run_graph` returns** — not inside `engine.py` itself. This preserves the same boundary established since SPEC-001: the engine executes and returns a result; everything about what happens to that result afterward (persistence, now; connection resolution, since SPEC-006) is a caller-side concern.
- **Schema**: one `runs` table (run_id, graph_id, status, started_at, finished_at, trigger_source) plus the full trace/result JSON blob per run — no need to normalize trace records into their own relational schema for v1; a JSON column is sufficient and much simpler to implement correctly.
- **Nested runs (agent tool calls, loop iterations)**: per SPEC-004/008's existing `child_traces` nesting, these remain nested within their parent run's single stored record — not written as separate top-level rows. A `GET /runs` listing shows top-level runs only; drilling into nested detail happens via the existing trace structure within that one record.

## 5. Data model

### SQLite schema (illustrative)
```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    status TEXT NOT NULL,               -- "running" | "completed" | "failed"
    trigger_source TEXT NOT NULL,       -- "manual" | "schedule" | "webhook"
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,                   -- full RunResult, serialized
    error TEXT
);
```

### Updated/new endpoints
```
GET /runs?graph_id=&status=&trigger_source=&limit=&offset=
  -> paginated list of run summaries (not full trace -- keep list responses light)

GET /runs/{run_id}
  -> full run record including complete trace, read from SQLite
```

## 6. Acceptance criteria

- [ ] Every manual run (`POST /runs`) is persisted and retrievable via `GET /runs/{run_id}` even after the API server process restarts
- [ ] Every triggered run (schedule or webhook, per SPEC-009) is persisted identically to a manual run, correctly tagged with its `trigger_source`
- [ ] `GET /runs` correctly filters by `graph_id`, `status`, and `trigger_source`, and paginates rather than returning unbounded results
- [ ] A run's nested child traces (from an `agent` node or `loop`/`fan_out`) are preserved intact within its single stored record — verified by persisting and re-reading a run that actually used one of these node types
- [ ] SQLite write failures (e.g. disk full, locked file) do not crash or silently swallow a graph run's actual execution — the run completes and returns its result to the caller regardless; persistence failure is logged/surfaced separately, not allowed to break the run itself
- [ ] Full existing test suite (SPEC-001–009) still passes unchanged
- [ ] At least one live demonstration: activate a schedule-triggered graph (from SPEC-009), let it fire twice unattended, restart the API server, then call `GET /runs` and confirm both prior firings are still there with correct data

## 7. Open questions

- Should `result_json` be stored compressed, given trace records (especially nested ones from agents/loops) could get large over time? Recommend: no compression for v1 — premature optimization without evidence it's actually a problem yet; revisit if real usage shows the SQLite file growing unreasonably fast.
- Should there be a hard cap on trace size stored per run (e.g. truncate extremely long agent conversations)? Recommend: not for v1 — store everything; a retention/pruning spec (already deferred, §3) is the right place to address unbounded growth, not ad-hoc truncation here.
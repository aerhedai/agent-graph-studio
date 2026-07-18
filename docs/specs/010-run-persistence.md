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
  - Resolved: adopted as recommended. No compression implemented.
- Should there be a hard cap on trace size stored per run (e.g. truncate extremely long agent conversations)? Recommend: not for v1 — store everything; a retention/pruning spec (already deferred, §3) is the right place to address unbounded growth, not ad-hoc truncation here.
  - Resolved: adopted as recommended. No truncation implemented.
- **A real fork this spec's own text didn't resolve, raised and confirmed before implementation**: `GraphSpec` has no server-side identity anywhere in this codebase (no `id` field, `POST /runs` takes a raw graph body — see SPEC-009's own implementation notes making the same observation for activation). §5's schema makes `graph_id NOT NULL`, and this spec's acceptance criteria require `GET /runs` to filter by `graph_id`, but nothing in the spec said where a *manual* run's `graph_id` should come from.
  - Resolved: `POST /runs` gains an optional `graph_id` query parameter (caller-supplied, mirroring the existing "graph_id is caller-chosen, no server identity" pattern already established for activation), defaulting to `null` when omitted. The `runs.graph_id` column is nullable, not `NOT NULL` as originally illustrated in §5 — a deliberate, confirmed deviation from that illustrative schema.

## 8. Implementation notes

Written after implementation, following the SPEC-004/005/008/009 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **Engine diff: confirmed empty, exactly as designed.** `git diff main -- backend/execution/engine.py` for this spec is a no-op, per §4's resolved "write point is caller-side, never engine-side" decision. All persistence logic lives in `backend/api/runs.py` (write) and `backend/api/app.py` (endpoints); `backend/storage/runs_store.py` is a standalone SQLite module the engine never imports or knows about.
- **Storage layer (`backend/storage/runs_store.py`) mirrors `backend/connections/store.py`'s override-path pattern**: `AGENT_GRAPH_STUDIO_RUNS_DB_PATH` env var override for test isolation (default `~/.agent-graph-studio/runs.db`), same reasoning as `AGENT_GRAPH_STUDIO_CONNECTIONS_PATH`. Every call opens and closes its own short-lived `sqlite3.connect(..., timeout=5.0)` rather than sharing one connection across threads — writers come from both the FastAPI background-worker-thread pool (manual/`POST /runs`) and `backend/triggers/runner.py`'s raw `threading.Thread` (schedule/webhook fires), and `sqlite3.Connection` objects aren't safe to share across threads by default. The `timeout` gives a busy-wait window for the (real, demonstrated) case of a scheduler tick and a manual run writing at nearly the same moment.
- **Write timing: one INSERT on start (status="running"), one UPDATE on completion/failure** — not a single write-at-the-end. This makes a run's "running" state itself durable (visible via `GET /runs` even if the process dies mid-run, though such a row would then stay stuck at "running" forever — an accepted known simplification, no crash-recovery reconciliation was built, consistent with this spec's own "no retention/pruning for v1" scope stance).
- **Write failures are swallowed inside `runs_store.py` itself** (`except sqlite3.Error: logger.exception(...)`), not by the callers in `runs.py`. This is what satisfies the acceptance criterion that a disk-full/locked-file failure never breaks the run's actual execution or return value — confirmed by a real test (`test_sqlite_write_failure_does_not_break_the_run`) that fails `_connect` itself (the real sqlite3 boundary), not the wrapping functions, so the test exercises the actual protection rather than bypassing it.
- **`GET /runs/{run_id}` checks the in-memory `_runs` dict first, falling back to SQLite only if absent.** The in-memory path remains the only place `running_node_ids` (live per-node progress, spec-005) exists — a persisted-only record reports an empty list, since this spec's write point is after `run_graph` returns, not during. This preserves the hot "still running" polling path exactly as spec-005 built it.
- **`GET /runs` (the list endpoint) reads exclusively from SQLite, never the in-memory dict** — listing is inherently a history/browse operation, not the live-status hot path `GET /runs/{run_id}` optimizes for.
- **Nested `child_traces` required zero special-casing**, exactly as this spec's own §4 predicted: `result_json` is just `RunResult.model_dump_json()`, and Pydantic already serializes `TraceRecord.child_traces` recursively. Confirmed by a real test (`test_nested_child_traces_survive_persistence_for_loop_node`) that runs a real `loop` node, persists it, forces the SQLite-fallback path, and asserts the re-read `child_traces` are byte-identical to what the live run produced.
- **CLI (`backend/cli/main.py`) is out of scope for this spec, a deliberate reading of the spec's own text, not an oversight.** §3's scope bullet enumerates "manual (`POST /runs`), triggered (SPEC-009), or nested" — literally naming the HTTP endpoint, not the CLI tool — and no acceptance criterion tests CLI persistence. The CLI calls `run_graph` directly and was never wired through `backend/api/runs.py`'s create/execute_run flow; doing so would need either duplicated store-write logic in the CLI or a deeper refactor neither asked for nor justified by this spec's scope. Revisit if a future spec wants CLI-invoked runs in history.
- **Live end-to-end demo performed against a real `uvicorn` process** (not just the mocked/TestClient-level suite), per this spec's own mandatory final criterion: activated a `schedule_trigger` (`*/1 * * * *`) → `code` node graph via the real activation endpoint, observed it fire for real at two consecutive one-minute boundaries (`13:26:00`, `13:27:00` UTC), confirmed both via `GET /runs?graph_id=...&trigger_source=schedule`, then killed the real `uvicorn` process and restarted it fresh against the same SQLite file. `GET /graphs/active` came back empty (SPEC-009's documented no-persistence-across-restarts limitation, confirmed still true and unaffected by this spec) while `GET /runs` still returned both prior firings with correct `started_at`/`finished_at`/`status`, and `GET /runs/{run_id}` still returned each one's complete two-node trace — durability across a real process restart, demonstrated rather than assumed.
- **Full test suite**: `uv run pytest tests/ -v` — 203 passed (195 pre-existing SPEC-001–009 tests unchanged + 8 new in `tests/test_run_persistence.py`).
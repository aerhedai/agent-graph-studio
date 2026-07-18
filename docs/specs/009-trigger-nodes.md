# SPEC-009: Trigger Nodes (Schedule + Webhook)

**Status:** Draft — ready for implementation
**Milestone:** Self-Starting Workflows
**Author:** Rohan
**Depends on:** SPEC-005 (API layer), SPEC-006 (connection profiles, for reference on the "separate persisted config, not in graph JSON" pattern)

## 1. Goal

Let a graph start itself — on a schedule, or when an external system calls a webhook — instead of only ever running when a human or script explicitly calls it. This is the second-biggest gap identified versus n8n (after the Agent node from SPEC-008): every node type built so far assumes a graph is invoked on demand; nothing lets a graph be a standing, self-starting automation.

## 2. Why this, why now

Per the n8n comparison: trigger nodes (schedule, webhook, event-based) are what make a workflow tool feel like a real automation platform rather than an on-demand utility. Without this, "build a graph that watches for something and reacts" isn't possible — every use case discussed (inbox triage, scheduled briefings, log monitoring) requires this.

## 3. Scope

In scope:
- A `schedule_trigger` node type: fires its containing graph on a cron-style interval
- A `webhook_trigger` node type: exposes an HTTP endpoint that, when called, fires its containing graph with the request payload as input
- A graph **activation** concept: a graph with trigger nodes must be explicitly "activated" before its triggers are armed — an inactive graph with a schedule/webhook trigger node does nothing, exactly like n8n's active/inactive workflow toggle
- New API endpoints to activate/deactivate a graph, and list currently active graphs
- The scheduler and webhook router live in-process with the API server (per §4) — **not** persisted across server restarts for this MVP; re-activation after a restart is a manual step, explicitly documented as a known limitation rather than silently assumed away

Out of scope (future specs):
- Event-based triggers tied to specific external services (e.g. "new email arrives") — these would layer on top of `mcp_call`/connections, and are a distinct, later spec
- Persisting active-graph state across server restarts (auto-reactivation) — real, deferred complexity (needs a small local store, startup reconciliation logic)
- Trigger-specific retry/backoff policies — a failed triggered run behaves exactly like a failed manually-run graph for now (per existing trace/error handling), no special retry logic

## 4. Design decisions (resolved)

- **Where triggers live**: as ordinary node types inside the graph JSON, with zero inputs — a `schedule_trigger` or `webhook_trigger` node is the graph's entry point, replacing (for that graph) the need for someone to `POST /runs` manually. A graph can have more than one trigger node; each fires the same graph independently when its own condition is met.
- **Activation is separate from the graph definition itself**: saving/editing a graph does not automatically arm its triggers — `POST /graphs/{id}/activate` does. This mirrors n8n's own explicit active/inactive distinction and avoids surprising behavior (e.g. opening a saved graph in the canvas accidentally starting a live cron job).
- **Scheduler**: in-process, using a standard async-friendly scheduling library (e.g. APScheduler) rather than hand-rolling cron parsing — reuse a solved problem, consistent with the project's general "don't reinvent infrastructure" pattern (same reasoning as choosing Pydantic in ADR-001).
- **Webhook routing**: each activated `webhook_trigger` node gets a stable, unique URL path (e.g. `/webhooks/{graph_id}/{node_id}`), registered dynamically with the API server while the graph is active, removed on deactivation.
- **No persistence across restarts for v1**: explicitly accepted as a known limitation. Re-running `POST /graphs/{id}/activate` after a server restart is a manual step for now; auto-reactivation is real, deferred work (§3).

## 5. Data model

### `schedule_trigger` node config
```json
{ "cron": "*/5 * * * *" }
```
- Inputs: none
- Outputs: one, `fired_at` (text, ISO timestamp) — minimal payload, since a schedule trigger has no external data to pass beyond "it's time"

### `webhook_trigger` node config
```json
{}
```
(no config needed beyond its existence in the graph — the URL is derived from graph/node IDs, per §4)
- Inputs: none
- Outputs: one, `payload` (json) — the raw body of whatever was POSTed to its webhook URL

### New API endpoints
```
POST   /graphs/{graph_id}/activate
  -> registers all trigger nodes in the graph (cron jobs + webhook routes)
  -> { "status": "active", "triggers": [{"node_id": ..., "type": ..., "endpoint_or_schedule": ...}] }

POST   /graphs/{graph_id}/deactivate
  -> unregisters all of the graph's triggers

GET    /graphs/active
  -> lists currently active graphs and their trigger details

POST   /webhooks/{graph_id}/{node_id}
  -> the actual dynamically-registered webhook endpoint; body becomes the trigger's `payload` output
```

## 6. Acceptance criteria

- [ ] Activating a graph with a `schedule_trigger` causes it to run automatically at the configured interval, live-verified over at least 2-3 real fire cycles (use a short interval like every 1-2 minutes for the test, not a real production interval)
- [ ] Activating a graph with a `webhook_trigger` causes a real `curl POST` to the derived endpoint to correctly fire the graph, with the POST body available to downstream nodes as `payload`
- [ ] Deactivating a graph stops all further scheduled/webhook firing — verified by deactivating and confirming a subsequent webhook POST or schedule tick does *not* trigger a run
- [ ] A graph can be activated, manually run via the existing `POST /runs` at the same time, and both paths produce correct, non-conflicting results — trigger-based and manual invocation must coexist cleanly
- [ ] Restarting the API server causes previously active graphs to become inactive (confirming the documented limitation is real and behaves as described, not silently different)
- [ ] `GET /graphs/active` accurately reflects current activation state at all times
- [ ] Full existing test suite (SPEC-001–008) still passes unchanged
- [ ] At least one live end-to-end demonstration: a graph with a `schedule_trigger` feeding into a `code` node, activated, observed firing correctly on its own at least twice, then deactivated and confirmed to stop

## 7. Open questions

- Should a webhook trigger's endpoint require any authentication (e.g. a shared secret in the URL or header), or is an unguessable path (`graph_id`/`node_id` as UUIDs) sufficient for a local, single-user MVP? Recommend: unguessable path only for now — this is a local dev tool, not something exposed to the public internet; revisit if/when this is ever deployed somewhere reachable externally.
  - Resolved: adopted as recommended. No auth on webhook routes for v1; `graph_id`/`node_id` are caller-chosen strings, not enforced-unguessable UUIDs (the caller is free to use one).
- Should multiple trigger nodes in the same graph be allowed to fire concurrently (e.g. a schedule tick and a webhook POST arriving at the same moment)? Recommend: yes, treat each firing as an independent `run_graph` invocation — the existing engine already supports concurrent execution (SPEC-004), so this should require no special handling beyond confirming it via a real test.
  - Resolved: adopted as recommended. Every firing (`backend/triggers/runner.py::fire`) starts its own independent `run_graph()` call on its own background thread; no shared state between firings beyond the read-only cached `GraphSpec`. Confirmed live: a manually-submitted `POST /runs` and a real webhook `curl` against the same activated graph completed independently with no crosstalk (§8).

## 8. Implementation notes

Written after implementation, following the SPEC-003/004/005/006/008 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **`graph_id` has no persisted identity anywhere else in this codebase, and this spec doesn't invent one.** Neither `GraphSpec` nor any prior spec ever gave a graph a server-side identity: there's no `id` field on the model, `POST /runs` takes a full graph body with no id, and the canvas's own "save" (SPEC-005) is a local file download, never sent to the server. Rather than build a new `/graphs` CRUD resource (a real, bigger addition this spec's scope never asked for), `POST /graphs/{graph_id}/activate` carries the full `GraphSpec` as its own request body — `graph_id` is caller-chosen, and the graph is cached in `backend/triggers/registry.py` purely in-memory, for exactly as long as it's active. This was raised explicitly and confirmed before implementation, rather than assumed silently, given it's a real architectural fork the spec's own §5 endpoint list presupposes without resolving.
- **`webhook_trigger`'s `payload` output is TEXT (a JSON-serialized string), not a strict JSON-typed slot — a deviation from §5's literal wording, forced by an existing constraint.** Every node type registered so far (`code`, `llm_call`, ...) is TEXT-only; `SlotType.JSON` exists in the enum but had zero real usage anywhere, and slot-type compatibility is exact-match with no coercion (`backend/schema/types.py` explicitly defers json→text coercion to "a future spec"). A strictly JSON-typed `payload` could never connect to any node type that exists today, which would make this very spec's own "confirm the POST body reaches a downstream node" acceptance criterion undemonstrable. `payload` carries the same information as a TEXT slot instead (`json.dumps(body)`), connectable to a `code` node today (`json.loads()` inside the function), and should be revisited once a real JSON-consuming node type exists.
- **Scheduler: APScheduler's `BackgroundScheduler` (thread-based), not `AsyncIOScheduler`.** Consistent with this codebase's existing "plain sync callables in worker threads, never nested inside an already-running event loop" rule (`backend/api/app.py`'s own module docstring, and `run_graph`'s internal `asyncio.run()`). A cron tick fires in one of the scheduler's own worker threads, with no event loop of its own to conflict with — the same reasoning that already governs every FastAPI route in this project.
- **Engine diff: confirmed empty, exactly as designed.** `git diff main -- backend/execution/engine.py` for this spec is a no-op. Trigger firing reuses the exact same `resources` opaque-bag mechanism already established for named connections (SPEC-006) and `nodes_by_id` (SPEC-008/ADR-008): a webhook's POST body is threaded in as `resources["trigger_payloads"] = {node_id: body}`, populated by the caller (`backend/triggers/runner.py::fire`) before calling `run_graph()`. Every trigger node is a completely ordinary zero-required-input node type; the existing round-based scheduler (SPEC-004) already treats it as "ready" in the very first round with no engine-side special-casing at all — unlike ADR-008's tool-call carve-out, no exception was needed here.
- **Re-activating an already-active `graph_id` replaces the prior registration outright** (deactivate-then-activate) rather than erroring 409 — a deliberate small choice for activation to be idempotent from the caller's perspective, verified by a real test (`test_reactivating_an_already_active_graph_id_replaces_cleanly`).
- **Live end-to-end demos performed against a real `uvicorn` process**, not just the mocked/TestClient-level suite: (1) a `schedule_trigger` (`*/1 * * * *`) → `code` node graph, activated, observed firing for real at 3 consecutive one-minute boundaries (`09:53:00`, `09:54:00`, `09:55:00`) via a side-effect file the code node appended to, then deactivated and confirmed no further fires past the next boundary; (2) a `webhook_trigger` → `code` node graph, activated, fired via a real `curl POST` with a JSON body, confirmed the body reached the downstream node as `payload` via `GET /runs/{run_id}`'s trace, then deactivated and confirmed a subsequent `curl` 404s; (3) the same activated graph invoked both via a real webhook `curl` and manually via `POST /runs` at the same time, both completing independently and correctly with no crosstalk; (4) the real `uvicorn` process killed and restarted, confirming `GET /graphs/active` came back empty and the previously-live webhook URL now 404s — the documented "no persistence across restarts" limitation (§3), demonstrated as real rather than assumed.

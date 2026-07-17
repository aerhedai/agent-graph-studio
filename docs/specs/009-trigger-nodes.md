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
- Should multiple trigger nodes in the same graph be allowed to fire concurrently (e.g. a schedule tick and a webhook POST arriving at the same moment)? Recommend: yes, treat each firing as an independent `run_graph` invocation — the existing engine already supports concurrent execution (SPEC-004), so this should require no special handling beyond confirming it via a real test.

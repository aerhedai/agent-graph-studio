# SPEC-015: Graph Persistence & Durable Trigger Activation

**Status:** Draft — ready for implementation
**Milestone:** Toward a real, self-hostable app (n8n-parity push)
**Author:** Rohan
**Depends on:** SPEC-009 (trigger activation), SPEC-010 (run persistence — same SQLite-local-store precedent)

## 1. Goal

Give a graph a real, stable, server-side identity, and make trigger activation survive a backend restart. Today, `GraphSpec` has no id anywhere (SPEC-009/010 both note this explicitly), and the canvas generates a fresh client-side UUID every time it loads — meaning a graph's webhook URL changes every time the browser tab reloads, and all active triggers vanish the moment the backend process restarts (confirmed live, twice, this week: a real Telegram bot's webhook silently pointed at a URL that no longer existed after both a canvas reload and — separately — an apparent backend restart). This spec fixes both, together, because they're the same underlying problem: nothing about "this graph" persists anywhere durable.

## 2. Why this, why now

This is the direct, repeated pain from actually using triggers this week, not a hypothetical. It's also the foundation SPEC-016 (deployment) needs — you can't meaningfully deploy something whose active-webhook state resets on every restart. And it's the single biggest structural difference from n8n today: n8n workflows are named, saved, listed, and stay activated across restarts; this project's canvas is still closer to a stateless scratchpad than an app.

## 3. Scope

In scope:
- A durable **graphs** store (SQLite, same local-first pattern as SPEC-010's runs store): each saved graph gets a real, server-assigned, stable id, a name, its current `GraphSpec`, and an `is_active` flag.
- `POST /graphs` (create, server assigns id), `PUT /graphs/{id}` (update name/spec), `GET /graphs` (list), `GET /graphs/{id}` (load one), `DELETE /graphs/{id}`.
- `POST /graphs/{id}/activate` / `/deactivate` (SPEC-009's existing endpoints) additionally persist `is_active` + the activated spec to this store, instead of only the in-memory `trigger_registry`.
- **Startup re-activation**: on backend process start, read every `is_active=true` row and re-register its triggers (schedule jobs, webhook routes) automatically — the actual fix for "restart the backend, triggers are gone."
- Canvas: Save/Load work against this server-side list (name a graph, save it, reopen it later) instead of only local file download/upload. Local file export/import stays too, additively — useful for the existing `examples/*.json` workflow and portability — it's just no longer the *only* way to persist a graph.
- The canvas's client-generated `graphId` (built in SPEC's-worth-of-work last week for live trigger activation) is replaced by the server-assigned id from this store once a graph has been saved at least once.

Out of scope (future specs):
- Multi-user sharing/permissions on saved graphs — this project remains single-user/local-first per CLAUDE.md; no auth model changes here (that's SPEC-017).
- Graph versioning/history (n8n's "workflow history" snapshots) — a real, separate feature; this spec only needs the *current* spec per graph, not a history of past versions.
- Migrating existing in-flight activations — since trigger state today is in-memory-only and this spec is what introduces persistence at all, there's nothing pre-existing to migrate.

## 4. Design decisions (resolved)

- **Storage: SQLite**, new `backend/storage/graphs_store.py`, mirroring `backend/storage/runs_store.py`'s exact pattern (own DB file `~/.agent-graph-studio/graphs.db`, `AGENT_GRAPH_STUDIO_GRAPHS_DB_PATH` env override for test isolation, short-lived per-call connections, swallow-and-log write failures so a storage hiccup never breaks activation itself).
- **Id generation: server-side, on `POST /graphs`.** This is a deliberate reversal of SPEC-009/010's "graph_id is always caller-chosen" convention — that convention existed *because* there was no persistence layer to own id generation. Now that one exists, the store is the natural, single owner of identity, same as `run_id` already is in SPEC-010. `POST /graphs/{id}/activate` keeps accepting a path-param id unchanged (SPEC-009's contract), it's just that the frontend now always passes a real, saved graph's id rather than a session-local UUID.
- **`is_active` lives on the same row as the spec, not a separate table.** One graph is either currently activated or not; no need to normalize this out — matches SPEC-010's "a JSON blob column is sufficient, don't over-normalize for v1" precedent.
- **Startup re-activation reuses the exact same registration code `POST /graphs/{id}/activate` already runs** (`add_schedule_job`, `app.add_api_route`) — refactored into one shared internal function both the endpoint and a FastAPI startup event call, so there is exactly one code path that ever registers a trigger, never two to keep in sync.
- **A startup re-activation failure for one graph (e.g. its spec no longer validates against a since-changed node registry) must not block every other graph's re-activation** — each graph's re-activation is independently try/excepted and logged, matching this project's "never silently swallow node execution errors" principle applied to startup instead.

## 5. Data model

```sql
CREATE TABLE graphs (
    graph_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

```
POST   /graphs                 {name, spec} -> {graph_id, name, spec, is_active}
GET    /graphs                 -> [{graph_id, name, is_active, updated_at}, ...]
GET    /graphs/{graph_id}       -> {graph_id, name, spec, is_active}
PUT    /graphs/{graph_id}       {name?, spec?} -> updated record
DELETE /graphs/{graph_id}       -> 204 (also deactivates first if currently active)

POST   /graphs/{graph_id}/activate    (SPEC-009, existing path) -- now also persists is_active=true + spec_json
POST   /graphs/{graph_id}/deactivate  (SPEC-009, existing path) -- now also persists is_active=false
```

## 6. Acceptance criteria

- [ ] `POST /graphs` creates a graph with a real server-assigned id; `GET /graphs/{id}` retrieves it later, including after a backend restart
- [ ] `GET /graphs` lists saved graphs without requiring their full spec in the response (summary only, matching SPEC-010's "keep list responses light" precedent)
- [ ] Activating a saved graph, then **restarting the real backend process**, results in its trigger(s) automatically re-registered — verified live: activate a webhook-triggered graph, restart `uvicorn` for real, POST directly to the webhook path with no prior re-activation call, and confirm it still fires a real run
- [ ] Deactivating persists correctly — after deactivate + restart, the graph does NOT get automatically re-activated
- [ ] A startup re-activation failure for one broken graph does not prevent other valid graphs from re-activating (tested with one intentionally invalid saved spec alongside one valid one)
- [ ] Canvas can save a graph with a name, reload the page, and re-open that exact same graph via its stable id — the webhook path shown by the existing trigger chip (SPEC from last week) stays the same across the reload, not a fresh UUID
- [ ] Local file export/import (existing Save/Load-to-file) still works unchanged, additively alongside the new server-side save/load
- [ ] Full existing test suite passes unchanged; `git diff main -- backend/execution/engine.py` stays empty (this is entirely an API/storage/frontend concern, same as SPEC-010)

## 7. Open questions

- Should deleting a saved graph that's currently active auto-deactivate it first, or reject the delete? Recommend: auto-deactivate then delete — matches DELETE's usual "just make it gone" semantics, and there's no reason to force a separate manual deactivate step first.
- Does the frontend's existing local-file Save/Load UI need to change at all, or just gain new server-side siblings? Recommend: keep both as clearly separate actions (e.g. "Save" = server-side by name, "Export"/"Import" = the existing local file JSON download/upload, renamed for clarity) rather than merging them into one control.

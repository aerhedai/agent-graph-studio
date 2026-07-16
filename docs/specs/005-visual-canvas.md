# SPEC-005: Visual Canvas (Frontend + API Layer)

**Status:** Draft — ready for implementation
**Milestone:** Visual Graph Builder
**Author:** Rohan
**Depends on:** SPEC-001–004 (execution engine, pluggable registry, MCP node, loops/fan-out)

## 1. Goal

Build the first visual, drag-and-drop interface for constructing and running graphs — the actual "ComfyUI for agents" experience the whole project has been building toward. Everything before this spec has been backend-only, run via the CLI on hand-written JSON. This is the point where the project becomes visually demoable.

## 2. Why this, why now

Per `CLAUDE.md`'s stated differentiators and the original project vision, the canvas is the centerpiece, not an add-on. The backend (SPEC-001–004) is now mature enough — pluggable node types, provider-agnostic model calls, real tool access via MCP, loops and concurrent branches — that a canvas built on top of it has something genuinely substantial to visualize, rather than a toy 3-node demo.

## 3. Scope

In scope:
- A minimal **API layer** (FastAPI, per `CLAUDE.md`'s existing tech stack note) exposing the engine over HTTP: submit a graph, run it, stream/poll results and trace data. The CLI and API should both call into the same `run_graph` — neither becomes the "real" entry point at the expense of the other.
- A **React Flow canvas** (per ARCHITECTURE.md §8): drag nodes from a palette, connect typed edges, configure node settings inline or in a side panel
- **Node palette** populated dynamically from the backend's node registry (via a new `GET /node-types` endpoint) — the canvas must not hardcode the list of available node types; this is the frontend's own version of the pluggability bar the backend has held throughout
- **Typed edge validation in the UI itself** — attempting to connect incompatible slot types should be rejected at connection time in the canvas, not just at backend validation time (mirrors the backend's own "validate at connection time, not just runtime" principle from `CLAUDE.md`)
- **Run + live trace display** — trigger a run from the canvas, see per-node status (pending/running/success/error) update as it executes, and click any node after a run to inspect its trace record (inputs/outputs/cost/error) — this is ARCHITECTURE.md §8's explicit "inspect intermediate state" differentiator
- **Save/load graphs** as the existing JSON format — the canvas is a visual editor for the same file format the CLI already consumes, not a separate representation

Out of scope (future specs):
- Real-time collaborative editing (multi-user) — explicitly out of scope per ARCHITECTURE.md §9
- Visual representation of `loop`/`fan_out` sub-graphs as expandable/collapsible groups (nice-to-have, defer — MVP can show them as a single node with a config panel, sub-graph edited via a nested/modal view rather than inline on the main canvas)
- Full eval/regression suite UI (that's SPEC-006)
- Authentication/multi-tenancy for the API layer (fine for a local single-user tool; revisit only if this ever needs to be hosted for others)

## 4. Design decisions (resolved)

- **API transport: plain REST + polling, not WebSockets, for MVP.** A `POST /runs` kicks off execution, `GET /runs/{run_id}` returns current status + trace-so-far, polled by the frontend every ~500ms during a run. WebSockets/SSE would give smoother real-time updates, but polling is simpler, has no new failure modes (dropped connections, reconnection logic), and is a completely reasonable MVP choice for a single-user local tool. Revisit only if polling latency genuinely becomes a UX problem once this is being used.
- **Execution stays synchronous from the API's perspective per request, but the run itself happens in a background task** — `POST /runs` returns immediately with a `run_id`, actual execution happens via FastAPI's background task mechanism (or a simple in-process thread), so the HTTP request isn't held open for the full run duration. Necessary given SPEC-004 introduced loops that could run for a while.
- **Canvas state lives in React state during editing, persisted to the JSON file format on explicit save** — no separate canvas-native format. This directly matters for portfolio value: the graph JSON stays the portable, inspectable artifact regardless of which interface (CLI or canvas) produced it.
- **Node config editing: a side panel, not inline on the node itself** — clicking a node opens a config form (fields generated from the node type's config schema, reusing the same Pydantic models the backend already validates against — this is the "one source of truth" payoff flagged back in ADR-001). Inline editing on tiny node bodies gets cramped fast; a side panel scales better as node configs grow (e.g. `mcp_call`'s command/args/tool_name, or `code`'s multi-line function source).

## 5. Data model / API surface

### New endpoints
```
GET  /node-types
  -> [{ "type": "llm_call", "input_schema": {...}, "output_schema": {...}, "config_schema": {...} }, ...]
  (schemas derived from the same Pydantic models the backend validates against -- see §4)

POST /runs
  body: GraphSpec (the existing graph JSON format)
  -> { "run_id": "uuid", "status": "running" }

GET  /runs/{run_id}
  -> { "run_id": "uuid", "status": "running" | "completed" | "failed",
       "trace": [...], "result": {...} | null }
```

### Frontend structure
```
/frontend
  /src
    /canvas          # React Flow setup, custom node components
    /panels           # config side panel, trace inspector
    /api              # thin client for the endpoints above
```

## 6. Acceptance criteria

- [ ] `GET /node-types` returns all currently-registered node types (including `mcp_call`, `code`, `loop`, `fan_out`, `merge`) with their schemas, with zero hardcoded list in either the endpoint or the frontend — adding a new backend node type must make it appear in the palette automatically
- [ ] A user can drag out at least the 4 SPEC-001 node types plus `code`, connect them into a valid linear graph, and run it from the canvas
- [ ] Attempting an incompatible-type edge connection is rejected in the UI itself, before hitting the backend
- [ ] Triggering a run shows live per-node status updates (pending → running → success/error) without a full page reload
- [ ] Clicking any node after a run displays its actual trace record (inputs, outputs, token cost / side effect, error) — verified against a real backend run, not mocked data
- [ ] A graph built on the canvas can be saved, and the resulting JSON is byte-compatible with what the CLI already accepts — verify by saving a canvas-built graph and running it via `agent-graph-studio <file>.json` directly
- [ ] A graph authored via the CLI/hand-written JSON can be loaded into the canvas and displays correctly (round-trip compatibility, not just canvas → CLI)
- [ ] At least one live end-to-end run demonstrated: build a graph on the canvas (including at least one node type beyond the original 4), run it, inspect a node's trace, save it, then run the same file via the CLI and confirm identical results

## 7. Open questions

- Should the config side panel auto-generate purely from JSON Schema (fully generic, zero per-node-type frontend code, but potentially awkward UX for things like `code`'s multi-line Python source), or should certain node types (at minimum `code`) get a hand-built config UI (e.g. a real code editor widget) while everything else stays auto-generated? Recommend: auto-generate by default, special-case `code` with a proper text-area/editor component — the awkwardness of a single-line JSON-schema-generated text input for a multi-line function is a real, foreseeable UX problem worth solving directly rather than deferring.
- Trace inspection for nested execution (a `loop`'s iterations, a `fan_out`'s branches, per SPEC-004's `child_traces`) — does the MVP canvas need a dedicated UI for drilling into nested traces, or is a flattened/JSON-dump view acceptable for a first pass? Recommend: flattened/raw view acceptable for MVP; a proper nested trace explorer is real, deferrable polish.

## 8. Implementation notes

Written after implementation, following the SPEC-003/004 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **One spec, five checkpointed implementation phases, not two specs.** Considered splitting the API layer and the frontend into separate specs/branches (raised explicitly before implementation started). Decided against: the acceptance criteria are fundamentally coupled — the zero-hardcoded-node-list requirement, live trace display, and the mandated end-to-end demo all require the API and frontend proven *together*, not as separately-"done" halves. A single spec with checkpointed phases (API standalone-tested → canvas scaffold → run/trace → save/load → full sweep + demo, each with its own live verification and git commit) gets the same incremental de-risking without that gap. All five phases landed as separate commits on `feature/visual-canvas`.
- **`POST /node-types/{type}/resolve-slots`, an endpoint beyond §5's literal list.** `GET /node-types` can only report a fixed schema for static node types; `code`/`mcp_call`/`fan_out`/`merge`'s real ports depend on per-instance config (SPEC-002's `resolve_slots`). There's no way to satisfy "drag out `code`, connect it into a valid graph" without a way to ask the backend "what are this specific node's ports right now" — this endpoint reuses the exact existing `effective_inputs`/`effective_outputs` logic against a throwaway probe `NodeSpec`, not a new resolution mechanism.
- **Two optional callbacks added to `run_graph()`/`_run_graph_async()`: `on_round_start` and `on_trace_record`.** Needed for genuine incremental `pending → running → success/error` status (§6's explicit criterion), since `run_graph()` previously only returned a result once, fully, at the end. Both default to `None`; every pre-existing call site (CLI, all 118 SPEC-001–004 tests, `loop`'s recursive call) is unaffected. **Caveat, confirmed by design review before writing code**: nodes within the same concurrent scheduling round (e.g. two `fan_out` branches) still transition together as a batch, since they only become individually observable once `asyncio.gather` returns for the whole round — inherent to the round-based scheduler (SPEC-004), not a gap in the callbacks themselves.
- **Every FastAPI route is a plain `def`, never `async def` — load-bearing, not a style choice.** `validate_graph()` (via `POST /runs`) and the resolve-slots logic both transitively call `resolve_slots` for `mcp_call`, which internally does its own `asyncio.run(...)` (`backend/mcp/client.py`). Calling that from inside an already-running event loop (which any `async def` route runs on) raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. FastAPI/Starlette dispatches plain `def` routes through a worker thread automatically (`run_in_threadpool`, confirmed by reading Starlette's source directly during design review) — the same "no event loop on this thread" pattern already relied on for `loop` node's recursive `run_graph()` call. Applied as a blanket policy so it never has to be reasoned about per-route.
- **A real product bug was found and fixed during Phase 2 live verification, not papered over for a screenshot.** `@xyflow/react` caches each node's handle positions internally and doesn't auto-detect newly-added `<Handle>` DOM elements when a dynamic-schema node's ports change *after* mount — exactly what happens when `code`/`mcp_call`/`fan_out`/`merge` resolve their real ports over HTTP, well after the node's first render. Without `useUpdateNodeInternals()`, an edge into such a node's newly-resolved port existed correctly in React state but silently never rendered. This would have bitten a real user connecting a `code` node, not just the test script — confirmed by direct DOM inspection, fixed properly, called out in the Phase 2 commit.
- **Client-side type-mismatch rejection is implemented and unit-tested, but was never demonstrated live against real node types.** Every node type registered through SPEC-004 is TEXT-only, so no real pair of nodes today can produce a genuine type incompatibility to screenshot. The actual comparison logic (`slotTypesCompatible`, mirroring the backend's exact-match `SlotTypeSpec` semantics) is proven instead via 5 Vitest cases against synthetic non-text type shapes matching the real wire format — confirmed correct, just not yet exercised by a real graph. Revisit with a live screenshot once a second `SlotType` is actually used by some node type.
- **`GET /node-types` — confirmed zero-hardcoded-list, the way the spec's hard requirement demands.** The endpoint (`backend/api/app.py::list_node_types`) calls `default_registry.all_types()` directly and nothing else; `default_registry` (`backend/registry/base.py`) is populated entirely by `@register_node(...)` decorator side effects across `backend/nodes/*.py`. A repo-wide grep of `frontend/src` confirms no node-type-name list exists anywhere in the frontend either — the only string-matched name in any frontend file is a config *field* name (`"function_source"`, for the `code` editor special-case per this spec's own §7 resolution), not a node *type* name. Adding a future node type requires only the existing two-step pattern (new file + one import line in `backend/nodes/__init__.py`) — nothing in `backend/api/` or `/frontend` changes.
- **Live end-to-end demo performed** (spec §6's final, mandatory criterion): built a 4-node graph on the canvas (`text_input → code → uppercase_text → text_output`, two node types beyond the original four), ran it from the canvas, inspected `text_output`'s real trace (`inputs: {"text": "DEMO FINAL STUDIO GRAPH AGENT"}`), saved it via the real browser download, ran that exact saved file through `python -m backend.cli.main <file>` from the terminal, and confirmed an identical result (`"text_output_4": "DEMO FINAL STUDIO GRAPH AGENT"`). Screenshots and terminal output captured at every step.
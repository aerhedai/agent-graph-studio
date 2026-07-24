# Agent Graph Studio — Complete Reference Guide

A visual, node-graph-based environment for building and running AI agent workflows: LLM calls (any provider, cloud or local), tools/MCP servers, arbitrary code, and control flow, wired together on a canvas — in the spirit of ComfyUI, but for agent/tool orchestration instead of diffusion pipelines.

This guide documents **everything the software can currently do** — every node type, every API endpoint, every UI control, every connection type, the CLI, the execution model, and the security/deployment model — for a user who wants to make full use of it, not just the basics.

---

## Table of contents

1. [Core concepts](#1-core-concepts)
2. [Getting started / deployment](#2-getting-started--deployment)
3. [Security model](#3-security-model)
4. [The canvas — full UI reference](#4-the-canvas--full-ui-reference)
5. [Node type reference](#5-node-type-reference)
6. [Connections & LLM providers](#6-connections--llm-providers)
7. [REST API reference](#7-rest-api-reference)
8. [CLI reference](#8-cli-reference)
9. [Execution engine internals](#9-execution-engine-internals)
10. [Feature history (what's been built, spec by spec)](#10-feature-history)
11. [Known limitations / scope boundaries](#11-known-limitations--scope-boundaries)

---

## 1. Core concepts

- **Node** — a unit of work: an `id`, a `type` (looked up in a pluggable registry), typed `inputs`/`outputs`, static `config`, and a body (LLM call, tool/MCP call, control flow, arbitrary code, or agent). Every node type declares its config and port schema explicitly as Pydantic models, validated **at connection time**, not just at run time — you cannot wire two incompatible ports together in the canvas, let alone run the graph.
- **Edge** — a typed connection between two nodes. Two kinds exist: a `data` edge (ordinary producer → consumer, same as ComfyUI) and a `sub_node` edge (a structural "this node plugs into that node as a component" connection — e.g. wiring a `model` node into an `agent`'s `model` slot). Sub-node edges don't participate in data-flow ordering.
- **Graph** — the full assembly of nodes + edges, serialized as one portable JSON document (`{"version", "nodes": [...], "edges": [...]}`). This is the save/load/version unit — the canvas, the CLI, and the API all read and write exactly this same format, there is no separate canvas-only representation.
- **Execution engine** — a strict DAG executor. It validates the graph, then executes it in concurrent rounds (independent nodes run in parallel automatically), logging every node's inputs, outputs, token cost, timing, and any error as a structured trace record. **No literal cycles are ever allowed** — iteration is achieved via a `loop` node that wraps a sub-graph and re-invokes it internally, not via cyclic edges (see [§9](#9-execution-engine-internals)).
- **Trace / node-level inspection** — every run produces a full trace, and clicking any node after a run shows exactly what it received and produced, including token cost and latency. This is a first-class, load-bearing feature of the whole system, not a debug afterthought — it's what makes the canvas genuinely useful for iterating on a workflow.
- **Connections** — named, reusable, saved-once credential/endpoint profiles (e.g. `my-ollama`, `prod-anthropic`) that graph nodes reference **by name** rather than embedding secrets inline. Any config field named `connection` or ending in `_connection` is a connection reference.
- **Provider-agnostic model calls** — model-call nodes never talk to a provider SDK directly; they go through an `LLMClient` protocol. Swapping Claude for a local Ollama model (or adding a third provider later) requires zero engine changes.
- **Triggers / activation** — a graph can be "activated" to turn its `schedule_trigger`/`webhook_trigger` nodes into standing, always-on listeners (cron jobs, live HTTP routes) — the n8n-style active/inactive toggle. Activation state persists across backend restarts.

---

## 2. Getting started / deployment

### Required secrets

The backend refuses to start without both of these — no silent fallback:

| Env var | Purpose | How to generate |
|---|---|---|
| `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` | Fernet key encrypting all connection secrets (API keys, bot tokens) at rest | `uv run python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `AGENT_GRAPH_STUDIO_API_KEY` | The one shared credential every API request needs | any string you choose |

### Local development (no Docker)

```bash
export AGENT_GRAPH_STUDIO_ENCRYPTION_KEY="<generated>"
export AGENT_GRAPH_STUDIO_API_KEY="<your choice>"
uv run uvicorn backend.api.app:app --reload
```

The canvas prompts for the API key on first load and remembers it in `localStorage`. Any pre-existing plaintext `connections.json` from before encryption was added is migrated to encrypted storage automatically on first read.

### Docker (recommended for anything beyond your own laptop)

```bash
export AGENT_GRAPH_STUDIO_ENCRYPTION_KEY="<generated>"
export AGENT_GRAPH_STUDIO_API_KEY="<your choice>"
docker compose up --build
```

- **Frontend (the app itself):** `http://localhost:8081`
- **Backend API directly:** `http://localhost:8000`

Nginx (in the frontend container) serves the built SPA and reverse-proxies everything else to the backend, so the whole app is reachable from one origin with zero CORS configuration.

`docker compose down` stops containers without deleting data; `docker compose down -v` wipes everything. All durable state (connections, run history, saved graphs) lives in one named volume, `agent-graph-studio-data`, mounted at `/data`.

### Full environment variable reference

| Variable | Required? | Purpose |
|---|---|---|
| `AGENT_GRAPH_STUDIO_API_KEY` | **Yes** | Shared API credential; backend won't boot without it |
| `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` | **Yes** | Fernet key for connections-at-rest encryption; backend won't boot without it |
| `AGENT_GRAPH_STUDIO_CONNECTIONS_PATH` | No (default `~/.agent-graph-studio/connections.json`) | Override where the connections store lives |
| `AGENT_GRAPH_STUDIO_GRAPHS_DB_PATH` | No (default `~/.agent-graph-studio/graphs.db`) | Override where saved graphs + activation state live |
| `AGENT_GRAPH_STUDIO_RUNS_DB_PATH` | No (default `~/.agent-graph-studio/runs.db`) | Override where run history lives |
| `AGENT_GRAPH_STUDIO_SETTINGS_PATH` | No (default `~/.agent-graph-studio/settings.json`) | Override where the public-base-URL setting lives |
| `VITE_API_BASE` | No | Frontend **build-time** ARG for the API base URL — baked in at build, not runtime; changing it requires a frontend rebuild |
| `ANTHROPIC_API_KEY` | Only if used outside a saved connection | The Anthropic connection type stores its own key per-connection; this env var isn't required for normal use |

### Connecting to an LLM

Entirely your own choice, configured as a **connection** inside the running app (not in the deployment config): a cloud provider (Anthropic) works from anywhere; a local model (Ollama) is reached via `http://host.docker.internal:11434`, `network_mode: host`, or a private network (e.g. Tailscale) if the model server is on a different machine.

### TLS

Docker Compose serves plain HTTP only. Any public deployment needs a TLS-terminating reverse proxy in front (Caddy, nginx+Let's Encrypt, a Cloudflare Tunnel, Tailscale Funnel, etc.) — deliberately out of scope of the compose file itself.

---

## 3. Security model

Two independent secrets protect two independent things:

| | API key | Encryption key |
|---|---|---|
| Env var | `AGENT_GRAPH_STUDIO_API_KEY` | `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` |
| Protects | Every API route (network access) | `connections.json` contents (data at rest) |
| Format | Any string | Must be a valid Fernet key |
| Missing → | Backend refuses to start | Backend refuses to start |
| Presented how | `Authorization: Bearer <key>` header, or `?key=<key>` query param | Never presented by callers — internal only |

**Auth coverage:** every route requires the API key — including dynamically-registered webhook routes added after a graph is activated — except `GET /health`, `/docs`, `/redoc`, and `/openapi.json`. This is enforced at the app level (`Depends(require_api_key)` on the whole app), specifically so a route added later can never accidentally end up unprotected.

**The two presentation methods exist because some external callers can't set custom headers** — e.g. Telegram calling your webhook URL directly. The webhook endpoint URL shown after activating a graph already has `?key=...` baked in, ready to hand to an external service. This is a disclosed tradeoff: the same shared key that grants full API access ends up embedded in every webhook URL you register externally.

**Encryption at rest:** every connection's config (API keys, bot tokens) is encrypted with Fernet symmetric encryption before being written to `connections.json`, uniformly for every connection type (not type-specific). A pre-existing plaintext file is auto-migrated to encrypted form on first read. **There is no key-rotation feature** — losing or changing the encryption key after connections already exist will make them undecryptable; treat it as a secret you must not lose.

---

## 4. The canvas — full UI reference

### 4.1 Unlock gate

On first load (or whenever any request comes back `401`), the app is replaced by a centered form asking for the API key. Typing it and clicking **Unlock** (or Enter) stores it in `localStorage` and reveals the app; an invalid key shows an inline error. There's no visible "log out" control in the UI.

### 4.2 Node palette (left sidebar)

- Every registered node type, grouped into collapsible categories (Triggers, Core, AI, Data, Connectivity, Tools — plus any new backend category, sorted after known ones), each with a colored dot and a live match count.
- A **filter box** at the top searches by type name across all categories; while filtering, matching sections force-open and empty ones disappear.
- A **"dynamic"** badge marks node types whose ports are only known once configured (`code`, `mcp_call`, `fan_out`, `merge`, `webhook_trigger`).
- **Adding a node is drag-and-drop only** — drag from the palette, drop on the canvas.

### 4.3 Placing and wiring nodes

- **Drop-to-contain**: dropping a node directly onto a "hybrid" group node's card (e.g. `tool_group`) wires it in immediately as a contained tool — no manual wire-drag needed.
- **Drag an existing node onto a group** to contain it the same way.
- **Remove from a group**: an "×" on each contained tool row pulls it back out as an independent node.
- **Expand/collapse a group card** by clicking its header.
- **Click a row inside a group** to select and inspect it directly.
- **Wire two nodes**: drag from an output port (right edge) to a compatible input port (left edge). Rejected client-side if types don't match, the target input already has an edge, or (for sub-node slots on a root's bottom edge) the source's role doesn't match what the slot accepts or a single-cardinality slot is already filled.
- **Sub-node connectors** render as a compact card with one top-edge connector dot instead of a full port card; root nodes render each declared slot as a separate bottom-edge handle, labeled, with a red asterisk on required (cardinality "one") slots.
- **Live port re-resolution**: wiring a trigger adapter into `webhook_trigger` immediately updates its visible output ports — no save/reload needed.
- **Node shape communicates role**: zero-input nodes (e.g. `text_input`, triggers) render as a rounded "start" shape; zero-output nodes (`text_output`) render as a "terminator" shape.
- **Badges**: a resolvable connection's provider type (e.g. "ollama"), or a "cluster" badge for nodes with sub-node slots.
- Standard React Flow pan/zoom, a **Controls** zoom/fit-view widget, and a **MiniMap**.

### 4.4 Run bar (top of canvas)

| Control | Behavior |
|---|---|
| **Run** | Submits the current canvas and executes it; disables to "Running..." mid-run |
| **Graph name** field | Editable name, used when saving server-side (default "Untitled") |
| **Save** | First save creates a server-side graph record with a stable ID; later saves update it |
| **Load saved graph…** | Dropdown of every server-saved graph, tagged "(active)" if activated. Reopening an already-active graph restores active/trigger UI state immediately |
| **Export** | Downloads the canvas as `graph.json` — the same portable format the CLI/API use |
| **Import** | Loads a `.json` file, replacing the canvas. **Always resets to a fresh, never-saved state** — clears the saved-graph ID, resets the name, forces activation state to inactive |
| **Activate / Deactivate** | Turns triggers into standing listeners. Auto-saves first if unsaved (a stable ID is required to activate) |
| **Update** | Appears only while active — re-pushes canvas edits to the live trigger registration without a deactivate→activate cycle |
| **● active badge + trigger chips** | Shown while active, listing each trigger's live endpoint/schedule as inline code pills |
| **Status pill** | Current run's status once a run exists |
| **History** | Opens the Execution History overlay |
| **Settings** | Opens the Settings overlay |

**Live trigger watching**: while a graph is active, the canvas silently polls for any new run under that graph's ID (e.g. a real incoming webhook) and automatically attaches to it — same live node-by-node animation as a manual Run, no click required.

### 4.5 Node Inspector (right sidebar)

Two tabs once a node is selected — **Config** and **Trace**. Trace is disabled until a run exists, and auto-selects itself the moment a trace record appears for the selected node.

**Config tab:**
- Auto-generated form from the node type's own config schema; every field the backend actually validates, nothing hand-maintained separately.
- Special field rendering: `function_source` → a real CodeMirror Python editor; `connection`/`*_connection` → the Connection Picker (never a plain text box, to prevent secrets being typed in directly); `model` → a live dropdown when the connection supports model discovery, else plain text; booleans → toggle switch; numbers → number input; objects/arrays → raw-JSON textarea parsed on blur.
- **Save** re-resolves ports live for dynamic-schema nodes (code, mcp_call, fan_out, merge).
- **Connected sub-nodes** section (read-only) lists whatever's wired into the node's slots — you must click the sub-node itself on canvas to edit it.

**Trace tab** (the node-level debugging feature):
- Status, started/finished timestamps, token cost (input/output), whether it had a side effect, full **Inputs** and **Outputs** as pretty-printed JSON, and a dedicated **Error** block on failure.
- **Child traces** (loop iterations, agent tool calls) shown as a flattened nested section.
- Live-updates ~every 500ms during an in-progress run; the same rendering path is reused for a historical run loaded from History.

### 4.6 Connection Picker (embedded in Config wherever a connection field appears)

- Dropdown of every saved connection as `name (type)`.
- **"+ New connection"** → category tabs generated from the backend's connection types (e.g. Local/Cloud), a name field, and type-specific fields.
- **Test Connection** — a real round-trip check; editing any field afterward invalidates the test result.
- **Save** is disabled until a successful test has run and a name is entered — you cannot save an untested connection.
- **Delete** — removes the currently-selected connection, behind a native confirm dialog.

### 4.7 Model field

For any `model` config field: shows a live dropdown of real models if the connection's type supports discovery (Ollama); otherwise falls back to plain text (Anthropic). A previously-saved value not in the fetched list is kept as an extra option rather than dropped.

### 4.8 Execution History panel

- **Status filter** (All/Running/Completed/Failed) and **Source filter** (All/Manual/Schedule/Webhook).
- Manual **Refresh** — this is a look-backward view, not live-updating.
- Up to 50 rows: status pill, graph ID, trigger source, start time.
- Clicking a row loads that run's full trace into the same inspector used for live runs, and stops any in-flight live polling so it can't overwrite the historical view.

### 4.9 Settings panel

- One field: **Public base URL** (e.g. a Tailscale Funnel/ngrok URL, or your real domain) — used to auto-register external webhooks (Telegram's `setWebhook`) on activation.
- **Save** triggers a non-blocking reachability check against `{url}/health`; a failed check shows a warning but does **not** block the save.

### 4.10 Visual language (cluster/sub-node system)

- **Root nodes** (e.g. `agent`) show ordinary data ports plus a distinct row of bottom-edge sub-node slot handles.
- **Sub-node types** (`model`, `memory`, trigger adapters) render as compact cards with a one-line meta summary and a single top connector — never wired via ordinary data edges.
- **Hybrid group nodes** (`tool_group`) are simultaneously a root and a sub-node: a collapsible card with a live tool count, each contained tool row lighting up in real time while genuinely mid-call.
- **Edges**: sub-node wiring always renders dashed/violet (structural, not per-run data); ordinary data edges are colored/animated by their target node's live run status.
- Per-node subtitles give at-a-glance context without opening the panel (e.g. `llm_call` shows its model name, `code` shows its function's first line, `conditional_branch` shows its condition expression).

---

## 5. Node type reference

All slot types in the shipped node set are `text` today (the schema also defines `json`, `file_ref`, `embedding`, `image`, `boolean`, `list` for future use).

### Triggers

| Type | What it does | Config | Notes |
|---|---|---|---|
| `webhook_trigger` | Entry point for graphs run by an external HTTP POST | none | Cluster root; requires exactly one `trigger_adapter` sub-node. Zero inputs; outputs mirror whichever adapter is connected |
| `generic_adapter` (sub-node) | Passes the raw webhook body through as JSON text, uninterpreted | none | Default adapter; never scheduled directly, only invoked by its parent |
| `telegram_adapter` (sub-node) | Parses a real Telegram Bot API update into clean fields | `bot_token_connection` (required) | Outputs `message_text`, `sender_id`, `chat_id`. Raises a clear error on a non-message-shaped payload |
| `schedule_trigger` | Fires its graph on a cron schedule | `cron` (required) | Zero inputs; output `fired_at` (ISO timestamp). Works identically whether fired by the real scheduler or a manual run |

### Core (flow control & utility)

| Type | What it does | Config | Ports |
|---|---|---|---|
| `text_input` | A fixed, author-supplied text value | `value` (required) | 0 in → `text` out. The required, unambiguous entry point for a `loop` sub-graph |
| `text_output` | Marks a value as one of the graph's final results | none | `text` in → 0 out. A graph can have several |
| `uppercase_text` | Upper-cases text | none | `text` in → `text` out |
| `code` | Arbitrary Python function as a node | `function_source` (required, one top-level `def`, no decorators/`*args`/`**kwargs`/classes) | Fully dynamic — one input per function parameter (defaults become optional), one fixed `result` output. Runs via plain `exec()`, **no sandboxing** — do not use with untrusted graphs |
| `conditional_branch` | If/else routing | `condition` (`contains('x')` or `equals('x')`) | `value` in → `true_branch`/`false_branch` out; only the firing branch actually produces output, so the other side is silently skipped downstream |
| `fan_out` | Splits one value into N identical parallel branches | `worker_count` (default 2) | `value` in → `branch_1..branch_N` out, all in one round → genuine concurrency |
| `merge` | Waits for N branches, combines into an ordered list | `expected_input_count` (default 2) | `input_1..input_N` in → `result` out (JSON array, index order not completion order) |
| `loop` | Repeats a sub-graph until a stop condition or iteration cap | `sub_graph` (required), `max_iterations` (default 10), `stop_condition_slot` (optional) | `value` in → `value` out. Internally re-invokes the full engine per iteration; each iteration's trace is a nested child trace |

### AI (models, agents, memory, retrieval)

| Type | What it does | Config | Ports |
|---|---|---|---|
| `llm_call` | One-shot prompt → completion | `connection` (required), `model` (required), `system_prompt` (default `""`), `max_tokens` (default 1024) | `prompt` in → `response` out. Records real token usage |
| `agent` | A genuine reasoning loop — the model decides which tools to call, in what order, until done | `max_iterations` (default 10) | `task` in → `answer` out. Sub-node slots: `model` (required, exactly one), `memory` (optional), `tools` (optional, must be a `tool_group`). Tool calls bypass normal edges entirely — the target node's `execute()` is invoked directly with model-supplied arguments; a failed tool call is reported back to the model as an error string so it can self-correct |
| `model` (sub-node) | The connection/model settings an agent reasons with | `connection`, `model`, `system_prompt`, `max_tokens` | No ports — pure config, plugged into an agent's `model` slot |
| `memory` (sub-node) | Bounds an agent's conversation history | `type` ("window"), `max_messages` (default 20) | No ports — a sliding window over the running conversation |
| `tool_group` (hybrid) | Bundles tool nodes into one unit an agent connects to | none | Root AND sub-node simultaneously; `tools` slot (cardinality "many", any node type) |
| `ingest_document` | Chunk + embed + store text for later retrieval | `connection` (vector store, required), `embedding_model_connection` (required), `embedding_model` (required), `chunk_size` (default 500), `chunk_overlap` (default 50), `document_name` (optional) | `text` in → `chunks_stored` out |
| `vector_search` | Embed a query and retrieve the most similar stored chunks | `connection` (required), `embedding_model_connection` (required), `embedding_model` (required), `top_k` (default 5) | `query` in → `results` out (numbered list with source labels) |

### Connectivity

| Type | What it does | Config | Ports |
|---|---|---|---|
| `mcp_call` | Calls one tool on an external MCP server (local subprocess) | `command` (required), `args`, `tool_name` (required), `credential_ref` (optional), `require_approval` (default `true`) | Fully dynamic — discovered live from the server's own tool schema; one fixed `result` output. Gated behind an interactive approval prompt by default; flags `side_effect=true` on its trace |

### Tools

Any registered node type — not just a dedicated "tool" category — can be dropped into a `tool_group` and used as an agent tool. Its exposed name is its graph node id, its description is auto-generated, and its parameter schema is taken directly from its own input slots, so a tool's shape can never drift from what the node actually accepts.

---

## 6. Connections & LLM providers

### LLM providers (implement the `LLMClient` protocol)

| Provider | Connection type | Config | Plain completion | Tool-calling | Embeddings | Model discovery |
|---|---|---|---|---|---|---|
| Anthropic / Claude | `anthropic` | `api_key` | Real Messages API call | Yes (native tool_use) | No | No — type model names manually |
| Ollama (local) | `ollama` | `host` (default `localhost`), `port` (default `11434`) | Real `/api/generate` call | Yes (`/api/chat`, forces `temperature: 0` for reliability) | Yes (`/api/embeddings`) — the only provider backing RAG nodes today | Yes (`/api/tags`) |

An `agent` node's `model` sub-node can point at either provider, as long as it supports `complete_with_tools` (both do). Only Ollama can currently back `ingest_document`/`vector_search`.

### Other connection types

| Type | Connects to | Config | Test does |
|---|---|---|---|
| `vector_store` | A local `sqlite-vec`-backed file (not a hosted service) | `path` | Opens/creates the file, runs `SELECT 1`. Embeddings are L2-normalized so plain distance search behaves like cosine similarity |
| `telegram` | A Telegram bot | `bot_token` | Only checks the token string is non-empty — does **not** verify it against Telegram's real API. The real `setWebhook`/`deleteWebhook` calls happen separately, on graph activate/deactivate |

### Storage of connections

All connection configs (including secrets) live encrypted in one file, uniformly across types — see [§3](#3-security-model). `DELETE /connections/{name}/vectors` clears a vector store's contents without deleting the connection profile itself.

---

## 7. REST API reference

FastAPI app; interactive docs are always available at `GET /docs` (Swagger) and `GET /redoc`, plus the raw schema at `GET /openapi.json` — all three, plus `GET /health`, are the only routes exempt from the API key.

### Health

- **`GET /health`** — liveness check, no auth. `{"status": "ok"}`.

### Node types

- **`GET /node-types`** — the full palette: every registered type's category, config schema, dynamic-schema flag, ports, sub-node slots.
- **`POST /node-types/{type_name}/resolve-slots`** — resolves actual ports for a dynamic-schema type given a specific config. Body: `{"config": {...}}`. `404` unknown type, `422` unresolvable.

### Runs

- **`POST /runs`** (202) — submit a full `GraphSpec` for execution (optional `graph_id` query param for history tagging). Validates, resolves connections, executes in the background, returns immediately: `{"run_id", "status": "running"}`. `422` with `[{rule, node_id, message}]` on validation failure.
- **`GET /runs`** — history list. Filters: `graph_id`, `status`, `trigger_source`, `limit` (default 50), `offset` (default 0).
- **`GET /runs/{run_id}`** — full status: `run_id`, `status`, `graph_id`, `trigger_source`, `running_node_ids`, `active_sub_node_ids`, `trace`, `result`, `error`. `404` if unknown.

### Connections

- **`GET /connection-types`** — every registered connection type, its config schema, and capability flags (`supports_model_listing`, `supports_tool_calling`, `supports_embedding`).
- **`GET /connections`** — `[{name, type}]`, never returns config/secrets.
- **`GET /connections/{name}/models`** — live model list from the backend. `404` unknown, `422` unsupported, `502` live call failed.
- **`POST /connections`** (201) — create. `{"name", "type", "config"}`. `422` invalid type/config, `409` duplicate name.
- **`POST /connections/{name}/test`** — real connectivity check; can test an unsaved draft config by passing `type`+`config` in the body. Always `200` with `{"success", "message"}` — a failed check is a normal, non-error outcome.
- **`DELETE /connections/{name}`** (204) — `404` if unknown.
- **`DELETE /connections/{name}/vectors`** (204) — clears a vector store's contents. `422` if not a `vector_store` connection.

### Settings

- **`GET /settings`** — `{"public_base_url": str | null}`.
- **`PUT /settings`** — `{"public_base_url": str}` → `{"public_base_url", "warning": str | null}` (a failed `/health` reachability check produces a warning, not a rejection).

### Triggers / activation

- **`POST /graphs/{graph_id}/activate`** — body: full `GraphSpec`. Registers real cron jobs and dynamic webhook routes; if the graph has a `telegram_adapter`, calls Telegram's real `setWebhook` (requires `public_base_url` set, or `422`). Idempotent — re-activating replaces the prior registration. Any Telegram-sync failure rolls back the entire activation. Response: `{"status": "active", "triggers": [{node_id, type, endpoint_or_schedule}]}`.
- **`POST /graphs/{graph_id}/deactivate`** — removes cron jobs and webhook routes; best-effort Telegram `deleteWebhook` (never blocks deactivation). `404` if never activated.
- **`GET /graphs/active`** — `[{graph_id, triggers: [...]}]`.
- **`POST /webhooks/{graph_id}/{node_id}`** (dynamic, only exists while active) — arbitrary JSON body, triggers a real run with `trigger_source="webhook"`. Still requires the API key (typically `?key=`). `404` if not currently active.

### Graphs (saved graph CRUD)

- **`POST /graphs`** (201) — `{"name", "spec"}` → generated `graph_id`.
- **`GET /graphs`** — `[{graph_id, name, is_active, updated_at}]`.
- **`GET /graphs/{graph_id}`** — full detail incl. spec. `404` if unknown.
- **`PUT /graphs/{graph_id}`** — partial update (`name` and/or `spec`). Note: does **not** auto-re-register triggers on an active graph — use the run-bar's Update button / re-activate for that.
- **`DELETE /graphs/{graph_id}`** (204) — deactivates first if currently active, then deletes.

---

## 8. CLI reference

One command, registered as `agent-graph-studio` (or `python -m backend.cli.main`):

```bash
agent-graph-studio <graph.json>
```

Flow: parse → full `validate_graph()` → resolve every referenced connection → `run_graph()` (the exact same engine as the API) → pretty-printed JSON (`result` + full `trace`) to stdout.

| Exit code | Meaning |
|---|---|
| `0` | Run dispatched successfully (individual node failures still show up as `error` fields inside the printed trace — that's not a nonzero exit) |
| `1` | Validation failed, or a referenced connection couldn't be resolved |
| `2` | Usage error or malformed JSON |

No flags, subcommands, or output-file options — deliberately minimal.

---

## 9. Execution engine internals

For understanding exactly what will and won't be accepted, and why a run behaves the way it does.

### Validation rules (run before anything executes; every issue is aggregated, not fail-fast)

| Rule | Rejects |
|---|---|
| `structural` | An edge referencing a node id that doesn't exist |
| `unregistered_type` | A node `type` not in the registry |
| `missing_required_input` | A required input slot with no incoming data edge (sub-node inputs are exempt — they come from direct invocation) |
| `type_mismatch` | An edge between incompatible slot types, or referencing a slot that doesn't exist |
| `cycle` | Any literal cycle among `data` edges (sub-node edges excluded from this check) |
| `invalid_config` | A node's `config` failing its own Pydantic model |
| `missing_connection` | A `connection`/`*_connection` field naming a connection that doesn't exist in the store |
| `unknown_sub_node_slot` / `incompatible_sub_node_type` / `sub_node_has_conflicting_edges` / `sub_node_cardinality` | Sub-node wiring rule violations — wrong slot name, wrong role, a sub-node with both a sub-node edge and a normal data edge, or a slot filled with the wrong count (e.g. an agent with zero or two `model` sub-nodes) |

Sub-node cardinalities today: `agent.model` = exactly one (role `model`); `agent.memory` = zero-or-one (role `memory`); `agent.tools` = zero-or-one (role `tool_group`); `tool_group.tools` = many, any role; `webhook_trigger.trigger_adapter` = exactly one (role `trigger_adapter`).

### The scheduler

Nodes execute in concurrent rounds, not one at a time: each round, every node with all required inputs resolved is dispatched together via `asyncio.gather`; a node that can never get a required input (an unfired conditional branch, a failed upstream) is permanently and silently skipped — no error, no trace record. This single mechanism implements both conditional-branch pruning and failure propagation with zero node-type-specific engine code. `fan_out`'s branches, or any two independent parts of a graph, genuinely run in parallel as a result.

### Failure isolation

A node raising any exception never crashes the run — it's caught, recorded on that node's trace with `error` set and no outputs, and every downstream consumer of it is skipped in a later round.

### Loops (the "no cycles" rule, made possible)

A `loop` node is an ordinary one-in/one-out DAG node from the outer graph's perspective. Internally, it re-invokes the entire engine on a fresh copy of its embedded `sub_graph` every iteration, feeding the previous iteration's single result back in as the next input, until `stop_condition_slot` fires or `max_iterations` is hit. The sub-graph must contain exactly one `text_input` node (the loop's entry point) and produce exactly one result. Each iteration's full trace nests under the loop node's `child_traces`.

### Agent tool calls (the one deliberate exception to "everything flows through edges")

When an `agent`'s model requests a tool, the target node's `execute()` is invoked **directly** with the model-supplied arguments — bypassing the normal edge-based input-gathering entirely, because there's no fixed edge to resolve (the model decides at runtime, not at graph-authoring time). A tool failure is caught and fed back to the model as an error string rather than aborting the agent. This is documented as a permanent, narrow, disclosed exception — not a precedent for other nodes reaching into siblings.

### Triggers, end to end

Activating a graph registers real cron jobs (APScheduler) and/or dynamic HTTP routes on the live FastAPI app. Both a real cron tick and a real webhook POST go through the same firing path: resolve connections fresh, start an independent run on its own thread, return immediately. Multiple triggers in the same graph, or a trigger firing concurrently with a manual run, share no mutable state. Active state is persisted, so a backend restart automatically re-arms every graph that was active when it stopped.

---

## 10. Feature history

Chronological summary of every spec that built this system (see `docs/specs/001`–`018` for full detail):

1. **Execution Engine MVP** — graph schema, validation, topological executor, four node types, full tracing, CLI.
2. **Pluggable Node Registry + Provider-Agnostic Calls** — second LLM provider (Ollama) with zero engine changes; the `code` node.
3. **MCP Server as a Node Type** — `mcp_call`, live tool discovery, approval-gated by default.
4. **Loops and Fan-Out/Fan-In** — `loop`, `fan_out`, `merge`; the engine became a concurrent round-based scheduler.
5. **Visual Canvas** — the first drag-and-drop UI, FastAPI layer, trace inspector.
6. **Connection Profiles** — named, reusable, encrypted-later credential storage replacing inline provider config.
7. *(ADR-007 — connection profile architecture; no numbered spec)*
8. **Agent Node** — model-driven tool selection, conversation memory, `max_iterations` safety cap.
9. **Trigger Nodes** — `schedule_trigger`, `webhook_trigger`, and the activate/deactivate concept.
10. **Execution History & Run Persistence** — durable SQLite run history, `GET /runs`.
11. **RAG / Vector Retrieval** — `vector_store` connections, `ingest_document`, `vector_search`.
12. **Sub-Node Connectors & Cluster Nodes** — generalized agent's tool-bypass trick into the reusable `sub_node` edge/slot pattern; trigger adapters (`generic_adapter`, `telegram_adapter`).
13. **Visual Design System** — node anatomy, live execution-state color/animation, categorized palette, design tokens.
14. **Tool Group Node** — `tool_group` as a dedicated container, replacing "any node can be a bare tool wire."
15. **Graph Persistence & Durable Trigger Activation** — real server-side graph identity, `is_active` flag, auto re-activation on restart.
16. **Docker Deployment Packaging** — backend + frontend images, compose file, persistent volume.
17. **Production Hardening** — connections-at-rest encryption, the shared API key on every route, the Execution History UI.
18. **Canvas UX Parity** — connection-picker coverage for every `*_connection` field, connection deletion from the UI, and automatic Telegram `setWebhook`/`deleteWebhook` registration via the new Settings panel.

---

## 11. Known limitations / scope boundaries

- **`code` nodes run with no sandboxing.** Do not run graphs from untrusted sources.
- **No encryption-key rotation.** Changing `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` after connections exist makes them undecryptable — there's no built-in re-encryption command.
- **Slot type compatibility is exact-match only** — no coercion (e.g. json → text) yet; in practice, nearly every shipped node type is `text`-only today.
- **`telegram` connections' "test" only checks the token string is non-empty** — it does not verify against Telegram's real API.
- **Editing an active graph's spec via `PUT /graphs/{id}` does not auto-re-register its live triggers** — use the canvas's Update button, or re-activate.
- **Single-process deployment only** — no gunicorn/multi-worker, no Kubernetes/Helm, no rolling deploys; in-memory trigger state assumes one process.
- **No TLS in the shipped Docker Compose setup** — put a reverse proxy in front for any public deployment.
- **`conditional_branch` is two-way only** (true/false) — an N-way switch would be a new node type, not a generalization of this one.
- **Agent memory is in-process only** — a simple sliding window, no cross-run persistence.
- **`VITE_API_BASE` is baked in at frontend build time** — changing it requires a rebuild, not just an env var flip at runtime.

# SPEC-006: Connection Profiles for Local and Cloud Providers

**Status:** Draft — ready for implementation
**Milestone:** Multi-Provider Connectivity
**Author:** Rohan
**Depends on:** SPEC-002 (pluggable node registry, `LLMClient` Protocol), SPEC-005 (canvas + API layer, config side panel)
**Supersedes:** The deferred "Ollama provider" follow-up from SPEC-002 — that work is now scoped as one instance of this more general system, not a one-off provider addition.

## 1. Goal

Let any node that needs a model/provider (starting with `llm_call`) be configured against **either a local or a cloud backend, chosen and configured by the user at the UI level, with zero hardcoded assumptions about anyone's specific hardware.** Concretely: clicking an `llm_call` node's config should offer tabs for "Local" and "Cloud" providers, each with its own setup flow, and the actual connection details (API keys, local endpoints) are saved once as named, reusable profiles — never baked into a graph JSON.

## 2. Why this, why now

This directly replaces and generalizes the Ollama work deferred in SPEC-002. Rather than hardcoding a second `OllamaLLMClient` with its own config shape, this spec builds the general mechanism the project actually needs: a **connection profile system**, so any current or future node type needing external access (LLMs now; image/embedding models, or other local services, later) plugs into the same pattern. This is also the first spec explicitly designed around portability — a graph built on your machine must remain meaningful (with a clear, friendly failure) when opened on someone else's, rather than silently referencing your specific IP or key.

## 3. Scope

In scope:
- A **connection profile** concept: a named, typed configuration (e.g. `"my-pc-ollama"`, `"personal-anthropic"`) describing how to reach a specific provider instance, stored **outside** any graph JSON
- A small **local connection store** — e.g. `~/.agent-graph-studio/connections.json` (or OS-appropriate config directory), never committed to any repo, holding all configured profiles for the current machine/user
- Backend support for **connection types**: at minimum `anthropic` (API key) and `ollama` (host/port endpoint) for this spec — designed so adding a third type later doesn't require touching the engine or the generic connection-resolution logic
- Nodes reference a connection **by name** in their config (e.g. `llm_call.config.connection: "my-pc-ollama"`), resolved to actual credentials/endpoint at run time via the connection store — never resolved into the graph JSON itself
- **API endpoints** for managing connections: list, create, test (verify it actually works before saving), delete
- **Canvas UX**: clicking a node needing a connection shows a picker (choose an existing named connection) plus a way to add a new one inline, with tabs for provider type (Local / Cloud) and type-appropriate fields (endpoint for local, API key for cloud) — manual entry only for v1, with a "Test Connection" button that performs a real, lightweight round-trip check before allowing save
- **Clear, friendly failure when a graph references a missing connection** — e.g. opening someone else's graph shows "This graph needs a connection named 'their-pc-ollama' which isn't configured on this machine" with a direct path to configure one, rather than a raw error

Out of scope (future specs):
- Network auto-discovery of local Ollama-like servers (per your decision: manual-only for v1, discovery is a deliberate future enhancement)
- Team/multi-user shared connection profiles (this is a single-user local tool for now)
- Connection types beyond `anthropic` and `ollama` (e.g. OpenAI, Bedrock, other local runtimes) — the system must be designed so adding these later is cheap, but they aren't built in this spec
- Encrypting the local connection store at rest (acceptable risk for a local single-user dev tool for now; flag as a known simplification, not silently ignored)

## 4. Design decisions (resolved)

- **Storage: named profiles in a separate local connection store, not inline in graph JSON.** Per the tradeoff discussion: graphs stay portable and shareable, secrets never get committed by accident, and updating a key/endpoint doesn't require editing every graph that uses it.
- **Discovery: manual entry + test-connection button only for v1.** No network scanning yet. The connection form design should leave room for an eventual "Discover" button (e.g. next to the manual endpoint field) without needing a redesign later — but nothing is built for it now.
- **Genericity: build the connection system itself as provider-type-agnostic from the start.** The engine/backend should have a `ConnectionType` concept (each type declaring its own config schema and a `test()` capability), not a hardcoded `if type == "anthropic"` / `if type == "ollama"` split baked into the API or UI layer. This mirrors the same registry pattern already used for node types (SPEC-002) — reuse that precedent rather than inventing a new one.
- **Resolution point: connections are resolved to real clients inside the CLI/API layer, before `run_graph` is called** — not inside `engine.py`, and not inside the node's own `execute()` either (a departure from how `AnthropicLLMClient` was constructed in SPEC-001/002). This keeps `engine.py` and node bodies completely unaware that named connections exist at all; they just receive an already-resolved client via the existing `resources` bag, exactly as before. The connection *name* lives in node config; the connection *resolution* is a caller-side concern.

## 5. Data model

### Connection store (`~/.agent-graph-studio/connections.json`, illustrative shape)
```json
{
  "connections": [
    {
      "name": "personal-anthropic",
      "type": "anthropic",
      "config": { "api_key": "sk-ant-..." }
    },
    {
      "name": "my-pc-ollama",
      "type": "ollama",
      "config": { "host": "100.x.x.x", "port": 11434 }
    }
  ]
}
```

### `llm_call` node config (updated from SPEC-002's `provider` field)
```json
{
  "connection": "my-pc-ollama",
  "model": "llama3",
  "system_prompt": "string",
  "max_tokens": 100
}
```
Note: the `provider` field from SPEC-002's draft is superseded — the connection's own `type` (looked up from its name) determines which client implementation is used; the node no longer specifies provider directly.

### New API endpoints
```
GET    /connections            -> list of {name, type} (never returns secrets)
POST   /connections            -> create a new named connection
POST   /connections/{name}/test -> attempt a real, lightweight round-trip; returns success/failure + message
DELETE /connections/{name}
```

### `ConnectionType` registry (backend, mirrors the node registry pattern)
Each registered connection type declares:
- its config schema (Pydantic model, consistent with ADR-001)
- a `build_client()` function returning something satisfying `LLMClient` (or a future protocol, for non-LLM connection types)
- a `test()` function performing a minimal real check (e.g. list models for Ollama, a trivial low-token completion for Anthropic)

## 6. Acceptance criteria

- [ ] A connection can be created via the API for both `anthropic` (API key) and `ollama` (host/port) types, and is persisted in the local store, never inside any graph JSON
- [ ] `POST /connections/{name}/test` performs a real check and correctly reports success/failure — verified live for both an Ollama connection (against the already-running local server) and, if available, an Anthropic connection
- [ ] An `llm_call` node referencing a connection by name resolves and executes correctly against that connection's real backend — live-verified for Ollama at minimum
- [ ] Opening/running a graph that references a connection name not present in the local store produces a clear, specific error (naming the missing connection), not a raw exception or silent failure
- [ ] Adding a hypothetical third connection type requires no changes to `engine.py`, the API endpoint handlers' core logic, or the canvas's generic connection-picker component — only a new registered `ConnectionType`
- [ ] The canvas's node config panel shows a connection picker with Local/Cloud-style tabs, lets a user add a new connection inline without leaving the panel, and reflects a newly tested-and-saved connection immediately
- [ ] Full existing test suite (SPEC-001–005) still passes unchanged
- [ ] At least one full live round-trip: create an Ollama connection via the canvas UI, build a graph using an `llm_call` node referencing it, run the graph, see a real model response

## 7. Open questions

- Should `test()` failures distinguish between "unreachable" (network/connection problem) vs "reachable but misconfigured" (e.g. wrong model name, bad API key)? Recommend: yes if cheap to determine, since it directly affects what the UI should tell the user to fix — but don't over-engineer this for v1 if the underlying client libraries don't make the distinction easy to surface.
  - Resolved: not distinguished for v1. Both connection types' `test_connection()` return a single `success: bool` + free-text `message`; the message itself is descriptive enough (e.g. Ollama's "Could not reach Ollama at ...: <urllib error>" vs. a real Anthropic auth error) without a separate status enum. Revisit only if the UI ever needs to branch on failure *kind*, not just show the message.
- Should deleting a connection that's still referenced by an existing graph be blocked, warned, or silently allowed (with the graph simply failing to run afterward, per this spec's "clear error on missing connection" behavior)? Recommend: allow deletion, rely on the existing missing-connection error path — building reference-tracking across all saved graphs is real complexity with limited payoff for a single-user local tool.
  - Resolved: allowed unconditionally, exactly as recommended — `DELETE /connections/{name}` never checks any saved graph. `check_missing_connections` (below) is what a subsequently-run graph hits.

## 8. Implementation notes

Written after implementation, following the SPEC-003/004/005 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **`GET /connection-types`, one necessary addition beyond §5's literal endpoint list.** Same shape as SPEC-005's `resolve-slots` addition: the canvas's connection picker needs each type's `config_schema` (and its `category`, for the Local/Cloud tabs) to render fields generically — there's no way to build a zero-hardcoded-type-list picker without a way to ask the backend "what connection types exist and what do their forms look like." Mirrors `GET /node-types` exactly, down to being the *only* place `default_connection_registry.all_types()` is enumerated (confirmed via grep: no connection-type name is hardcoded anywhere in `backend/api/` or `frontend/src/`, aside from the `CATEGORY_LABELS` display-string map in `ConnectionPicker.tsx`, which maps categories — `"local"`/`"cloud"` — to tab labels, not connection type names).
- **"Missing connection" is a validation *rule*, not a bespoke exception path.** `check_missing_connections` (`backend/validation/rules.py`) is wired into the existing `validate_graph()` aggregation exactly like every other §5 rule, rather than introducing a separate `try`/`except ConnectionNotFoundError` flow for this one case. This means a graph with both, say, a `missing_required_input` problem *and* a missing connection reports both in one pass (`GraphValidationError.issues`), and the CLI/API's existing error-rendering code needed zero new branches — the "clear, specific, pre-execution error naming the missing connection" acceptance criterion (§6) falls out of a rule function, not new plumbing. `resolve_connections()` (`backend/connections/resolver.py`) still independently raises `ConnectionNotFoundError` as a defensive fallback for the theoretical TOCTOU race between validation and resolution (store edited concurrently) — in practice unreachable in the single-user local-tool context this targets.
- **`backend/llm/providers.py` deleted outright, not left alongside the new mechanism.** Per explicit instruction: the old `provider`/`provider_options` `llm_call` fields and their dispatch registry are fully superseded, not dual-maintained. `AnthropicLLMClient.__init__` gained an optional `api_key` param (env-var fallback preserved) so `anthropic_connection.py` can inject a stored key without reintroducing a second construction path.
- **Engine diff: confirmed empty, exactly as designed.** `git diff backend/execution/engine.py` across all of SPEC-006 is a no-op — `ExecutionContext.resources` (added in SPEC-002) already generically supports "hand a node's `execute()` something it looks up by key," and connection resolution is just a new key (`resources["connections"]`) populated by the CLI/API caller before `run_graph` runs, exactly per §4's resolution-point decision. Verified live, not just asserted, during the Phase 1 checkpoint.
- **Test isolation seam: `AGENT_GRAPH_STUDIO_CONNECTIONS_PATH` env var.** `backend/connections/store.py::connections_path()` reads this override before falling back to `~/.agent-graph-studio/connections.json`. `tests/conftest.py` sets it via an autouse fixture (`isolated_connections_store`, one fresh `tmp_path` per test) so the full suite (149 tests, including the two new connections-specific files) never reads or writes the real store — necessary once `validate_graph()` started consulting the store directly, since test-time `resources=` injection alone no longer bypasses that check.
- **Live end-to-end demos performed at both checkpoints, against a real remote Ollama.** Phase 1: API-level only (`POST /connections` → `POST /connections/{name}/test` → `POST /runs` → polled to completion), against `curl`. Phase 2: the full canvas UI flow (drag `llm_call` onto canvas → open its config panel → connection picker's "+ New connection" → Local tab → fill host/port → **Test Connection** against the real server → **Save**, enabled only after a successful test → picker's dropdown immediately reflects the new connection → wire it into a 3-node graph → **Run** → real model response visible in the trace inspector), driven by Playwright against real `uvicorn`/`vite` dev servers, screenshots viewed directly. One incidental finding during Phase 1's live run: the first model tried (`gemma4:latest`) is a "thinking" model that silently burned its entire token budget on hidden reasoning, returning an empty response string — confirmed via a raw `curl` to Ollama's own `/api/generate` reproducing the same behavior, i.e. not a bug in this project's code. Switched to a non-thinking model (`qwen2.5:14b`) for both demos.
- **`fieldRenderers.tsx`, a small extraction to avoid a circular import.** `ConfigPanel.tsx` special-cases `config.connection` into a `<ConnectionPicker>`; `ConnectionPicker.tsx` in turn needs the same generic boolean/number/string/JSON-fallback field rendering for *its own* inline connection-type form (e.g. Ollama's `host`/`port`). Rather than have the two components import each other, the shared renderer moved to its own module (`renderPrimitiveField`), imported by both.
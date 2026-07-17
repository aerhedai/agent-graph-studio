# ADR-007: Named connection profiles, stored outside graph JSON, resolved outside the engine

**Status:** Accepted
**Date:** 2026-07-17
**Related:** SPEC-002 §4 (superseded), SPEC-006 §3–§4

## Context

SPEC-002 gave `llm_call` a `provider: "anthropic" | "ollama"` config field plus a free-form `provider_options` dict, with the actual client resolved lazily *inside* the node's own `execute()` (`backend/llm/providers.py`, a small dispatch registry) if none was pre-injected via `resources` (ADR-003). This worked but had two real problems once used beyond the CLI: credentials/endpoints were tied to individual graphs rather than being reusable, named, and portable, and a graph authored on one machine would either fail confusingly or silently rely on machine-specific environment variables when opened on another. SPEC-006 needed a general **connection profile** system — named, typed, reusable configuration for reaching a specific provider instance — generalizing the deferred "add Ollama" follow-up into the actual mechanism the project needed, for any current or future node type needing external access, not just `llm_call`.

## Decision

- **Named connection profiles live in a local file** (`~/.agent-graph-studio/connections.json`), never inline in graph JSON. A node references a connection **by name** (`llm_call.config.connection: "my-pc-ollama"`), superseding the `provider`/`provider_options` fields entirely (not left alongside them).
- **A `ConnectionType` registry mirrors the node-type registry (ADR-003) exactly**: each registered type declares its own config schema, a `build_client()`, and a `test()` — no hardcoded `if type == "anthropic"` split anywhere in the API, engine, or frontend.
- **Connections are resolved to real, built clients by the CLI/API layer — the caller — *before* `run_graph` is invoked, not inside `engine.py` and not inside the node's own `execute()` either.** This is a deliberate departure from how `AnthropicLLMClient` was constructed pre-SPEC-006 (lazily, inside the node body). The resolved clients are handed to the run via the same generic `resources` bag (ADR-003) — `resources["connections"]`, a dict of connection-name → built client — so `engine.py` and node bodies stay completely unaware that named connections exist as a concept at all; a node just looks up an already-built client by the name in its own config.
- **"Missing connection" is a `validate_graph()` rule** (`check_missing_connections`), not a bespoke exception path — reusing the exact same issue-aggregation mechanism every other validation rule already has, so a graph referencing an unconfigured connection gets the same clear, pre-execution, specific error as any other validation failure.

## Rationale

- **Separate local file over inline graph JSON** because graphs need to stay portable and shareable — a graph JSON committed to a repo or sent to a teammate must never risk carrying a secret, and updating a key/endpoint must not require editing every graph that references it. This is the same portability concern ADR-004 already accepted a cost for (approval-gate UX) in service of; here it shapes the storage boundary instead.
- **Resolution point at the caller, not inside `execute()`**, unlike SPEC-002's original `AnthropicLLMClient` pattern, because a *named* connection is caller-scoped state (which local store, which machine) in a way a hardcoded default construction never was — resolving it inside the engine or node body would require either of those to become aware of the connection store's existence, breaking the exact decoupling ADR-003 established. Keeping resolution entirely upstream means `engine.py`'s diff for this whole spec is empty, verified directly (`git diff backend/execution/engine.py`), the same genericity proof SPEC-002 first established as the bar to clear.
- **Registry mirroring the node-type pattern** rather than inventing a new plugin shape, because the node registry (ADR-003) had already solved "add a new kind of pluggable thing without engine coupling" — reusing that shape means a third connection type (e.g. a hypothetical `openai`) requires only a new registered `ConnectionDefinition`, nothing else, exactly like a new node type does.
- **A validation rule instead of a separate error-handling path for missing connections** because it was cheaper and more consistent to extend the mechanism that already aggregates and reports every other kind of graph problem (missing inputs, type mismatches, cycles, bad config) than to introduce a second, differently-shaped error-reporting flow for one specific new failure kind.

## Consequences

- `backend/llm/providers.py` (SPEC-002's dispatch registry) is deleted outright, not kept alongside the new mechanism — the old `provider`/`provider_options` fields no longer exist on `llm_call`, and `connection` is required with no default, since there's no longer a sensible implicit choice.
- Every test that previously injected `resources={"llm_client": fake}` directly needed updating to also register a matching entry in an isolated connection store (`AGENT_GRAPH_STUDIO_CONNECTIONS_PATH` env-var override, one fresh `tmp_path` per test) — a direct consequence of `validate_graph()` now independently consulting the store, which test-time resource injection alone no longer bypasses.
- A local connection store is unencrypted at rest — an explicitly accepted MVP simplification (per SPEC-006 §3), not silently ignored, on the reasoning that this targets a single-user local dev tool for now.
- Deleting a connection that's still referenced by a saved graph is allowed unconditionally; the graph simply hits the same missing-connection validation error on its next run, rather than the system tracking references across every saved graph file.

## Alternatives considered

- **Credentials/endpoints inline in graph JSON** (SPEC-002's original shape, generalized rather than replaced): rejected — the portability and accidental-secret-commit risks are exactly what this spec exists to close.
- **Resolving connections inside `engine.py`** (the engine looks up the store itself): rejected — reintroduces engine awareness of a concept (named connections) that has no business being engine-level, symmetric with why node-type dispatch was pulled out of the engine in ADR-003.
- **Resolving connections inside each node's own `execute()`** (continuing SPEC-002's `AnthropicLLMClient` pattern, just swapping in a named lookup): rejected — would require every node type needing external access to independently know about the connection store and resolution logic, rather than that logic living once, upstream, at the caller boundary.
- **A distinct exception type/handling path for "connection not found," separate from `GraphValidationError`**: rejected in favor of one more validation rule — no new plumbing needed in either the CLI's or the API's existing error-rendering code.

# SPEC-012: Sub-Node Connectors, Cluster Nodes, and Trigger Adapters

**Status:** Draft — ready for implementation
**Milestone:** Cluster Node Architecture
**Author:** Rohan
**Depends on:** SPEC-002 (registry), SPEC-005 (canvas), SPEC-008 (agent node, tool-call bypass exception), SPEC-009 (webhook trigger)
**Supersedes (partially):** SPEC-008's config-based `tools`/`connection` fields on `agent`, and SPEC-009's plain `webhook_trigger`

## 1. Goal

Generalize the ad-hoc "agent bypasses edges to call tools" mechanism (SPEC-008 §4) into a proper, reusable **cluster node** pattern — a root node with visually distinct **sub-node slots**, matching n8n's Model/Memory/Tool sub-connector pattern — and apply that same pattern to a second case: a `webhook_trigger` cluster with pluggable **trigger adapter** sub-nodes for specific external services (starting with Telegram).

## 2. Why this, why now

SPEC-008 solved the *execution* problem (agent calls tools directly, bypassing edges) but left the *authoring* experience as raw JSON config (a list of node IDs, a connection string typed by hand). This spec makes that pattern visual, general, and reusable — so any future root node type (not just `agent`) can declare sub-node slots without reinventing the mechanism, and so config for a sub-node (like a model's system prompt) lives and is edited in one place, not duplicated.

**Important scoping clarification, resolved before design below**: MCP and trigger adapters solve *different* halves of the "400+ integrations" gap. MCP (`mcp_call`, SPEC-003) is a pull/call-based protocol — it lets your graph call out to a tool. It does not solve incoming, push-based events (a Telegram message arriving, an email received). Trigger adapters, introduced in this spec, solve the push-based half specifically for `webhook_trigger` — they are **not** MCP-based, and this spec does not attempt to unify them with MCP.

## 3. Scope

In scope:
- A new edge kind, `sub_node`, distinct from normal typed data edges — validated differently (see §4), not part of topological data-flow ordering
- Root nodes declare named **sub-node slots** in their registry definition (e.g. `agent` declares `model: single`, `memory: single`, `tools: multiple`)
- A `model` node type: holds `connection`, `model` name, `system_prompt`, `max_tokens` — pulled out of `agent`'s own config (superseding SPEC-008's inline fields) into its own pluggable node, connectable to any future root node type needing a model
- Canvas UX: root nodes render distinct sub-node connector sockets (visually different from normal data ports); clicking a root node's config panel shows its own settings plus a **read-only summary** of each connected sub-node's settings; editing a sub-node's settings only happens by clicking directly into that sub-node
- `webhook_trigger` becomes a cluster root node with a `trigger_adapter` sub-node slot (single)
- Two `trigger_adapter` sub-node types, proving the pattern generalizes:
  - `generic_adapter`: today's raw passthrough behavior (the POST body becomes `payload`, unchanged from SPEC-009)
  - `telegram_adapter`: parses a real Telegram Bot API webhook payload into clean, structured outputs (`message_text`, `sender_id`, `chat_id`) instead of raw JSON

Out of scope (future specs):
- Email/WhatsApp trigger adapters — explicitly deferred. Email is typically poll-based (IMAP), not webhook-based, so it needs a different trigger *shape* entirely (closer to `schedule_trigger`'s polling model than `webhook_trigger`'s push model) — forcing it into this cluster would be a bad fit, not a missing feature of this spec
- Migrating `llm_call` to use a `model` sub-node instead of its own inline config — a reasonable future consistency improvement, not required now; `llm_call` keeps its existing SPEC-002/006 shape unchanged in this spec
- A general marketplace/package system for community-contributed sub-node or adapter types — registry-based extensibility already exists (SPEC-002); packaging/distribution is a distinct, larger concern

## 4. Design decisions (resolved)

- **`sub_node` edges are not part of topological/data-flow ordering.** They're resolved once, at graph-validation time, into a reference the root node's `execute()` can use directly — mechanically identical to how SPEC-008's agent already calls tool nodes directly, just now declared and wired visually instead of listed in JSON config. This keeps the core engine's execution model (SPEC-001, SPEC-004) completely unaffected — `sub_node` edges are a schema/registry/canvas concern, not an engine concern.
- **A root node validates its own sub-node slots** (e.g. `agent` requires exactly one `model`, zero-or-one `memory`, zero-or-more `tools`) — this validation logic lives with the root node type's registration, not the engine, following the same "capability declared at registration, engine stays generic" pattern established since SPEC-002's `effective_inputs`.
- **Read-only display of sub-node settings on the root node is a canvas-only concern** — the backend doesn't need new API surface for this; a sub-node's config is already part of the same graph JSON the canvas has loaded, so displaying it read-only elsewhere is purely a frontend rendering decision, not new backend logic.
- **Trigger adapters are explicitly not MCP-based** — per §2's clarification. A `trigger_adapter` sub-node's job is parsing a specific *incoming* payload shape into clean outputs; this is closer in spirit to `resolve_slots`-style schema work than to `mcp_call`'s tool-calling mechanism.

## 5. Data model

### Graph JSON — sub_node edges
```json
{
  "edges": [
    { "kind": "sub_node", "slot": "model", "from": {"node": "model_1"}, "to": {"node": "agent_1"} },
    { "kind": "sub_node", "slot": "tools", "from": {"node": "code_1"}, "to": {"node": "agent_1"} },
    { "kind": "data", "from": {"node": "agent_1", "slot": "answer"}, "to": {"node": "text_output_1", "slot": "text"} }
  ]
}
```
Note: normal data edges gain an explicit `"kind": "data"` for symmetry/clarity, defaulting to `"data"` if omitted for backward compatibility with existing graphs from SPEC-001–011.

### `model` node config
```json
{
  "connection": "personal-anthropic",
  "model": "claude-sonnet-4-6",
  "system_prompt": "string",
  "max_tokens": 1024
}
```

### `agent` node config (updated — model/tools removed, now via sub_node edges)
```json
{
  "memory": { "type": "window", "max_messages": 20 },
  "max_iterations": 10
}
```

### `webhook_trigger` node config (updated — now a cluster root)
```json
{}
```
(the adapter sub-node connected via a `sub_node` edge, slot `trigger_adapter`, determines parsing behavior)

### `telegram_adapter` node config
```json
{ "bot_token_connection": "my-telegram-bot" }
```
- Outputs: `message_text`, `sender_id`, `chat_id` (all text)

## 6. Acceptance criteria

- [ ] An `agent` node with a `model` sub-node connected via a `sub_node` edge correctly resolves and uses that model at runtime — live-verified, non-mocked
- [ ] Canvas renders `sub_node` connectors visually distinct from normal data edges, and the root node's config panel shows the connected `model`'s settings as read-only, with editing only possible by clicking the `model` node directly
- [ ] Attempting to connect an incompatible sub-node type to a slot (e.g. wiring a `code` node into the `model` slot) is rejected, either at canvas connection time or at graph validation — not silently accepted
- [ ] A `webhook_trigger` with a `generic_adapter` behaves identically to SPEC-009's original webhook trigger (regression check — this must not break existing behavior)
- [ ] A `webhook_trigger` with a `telegram_adapter` correctly parses a real (or realistically-shaped test) Telegram webhook payload into `message_text`/`sender_id`/`chat_id` — live-verified with a real sample payload
- [ ] `git diff` on `engine.py`: empty or minimal — this spec's changes belong in schema/validation/registry/canvas, not core execution, consistent with the standard held since SPEC-002
- [ ] Full existing test suite (SPEC-001–011) still passes unchanged, including existing `agent` and `webhook_trigger` tests updated to the new shape where necessary (call out explicitly what changed and why, per this project's existing convention)

## 7. Open questions

- Should `sub_node` slot validation happen at canvas connection time (immediate UI feedback), at graph save time, or both? Recommend: both — canvas-time for immediate feedback (consistent with SPEC-005's existing "reject incompatible edges in the UI itself" principle), graph-validation-time as the authoritative backstop (consistent with every prior spec's validation layer being the real source of truth, not the UI).
- Should a `model` sub-node be reusable across multiple root nodes simultaneously (e.g. one `model` node feeding two different `agent` nodes), or must each root node have its own dedicated `model` instance? Recommend: allow sharing — it's a natural, low-cost win (avoid re-entering the same system prompt/model twice) and fits naturally since `sub_node` edges are just edges; don't add artificial restriction without a reason to.
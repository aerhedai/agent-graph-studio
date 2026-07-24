# SPEC-022: Unified App Nodes — Operation Dropdown + Formalized Trigger Adapters

**Status:** Draft — ready for review
**Milestone:** Toward a real, self-hostable app (n8n-parity push), continued
**Author:** Rohan
**Depends on:** SPEC-019 (app integrations framework — dynamic MCP node generation, this spec restructures how that generation presents itself), SPEC-021 (per-user app connections — this spec changes node *structure/count*, not connection/credential resolution, which stays exactly as SPEC-021 built it), SPEC-012 (sub-node/cluster pattern — `trigger_adapter` slot, the existing pattern this spec formalizes), SPEC-002/004 (dynamic-schema `resolve_slots` mechanism, reused here one level further)

## 1. Goal

Replace SPEC-019's "one generated node type per `(connection, tool)`" model with **one node type per connection**, where the specific tool ("Operation") is chosen via a real dropdown inside the node's own config, with dynamically-resolved typed inputs/outputs matching whichever operation is currently selected. Also **formalize** the existing Telegram-style manifest/trigger-adapter pattern (SPEC-012) into an explicit, documented, reusable contract for adding a new app's trigger — without claiming triggers can be derived automatically from MCP, which they genuinely cannot be.

## 2. Why this, why now

Live-verified during SPEC-021's own proving: connecting Gmail's real MCP server generated **13 separate flat node types** (`create_draft`, `list_drafts`, `get_thread`, `get_message`, `search_threads`, `label_thread`, `unlabel_thread`, `apply_sensitive_thread_label`, `list_labels`, `label_message`, `unlabel_message`, `apply_sensitive_message_label`, `create_label`) cluttering the palette under one connection. This only gets worse for apps with more tools (Discord, Slack, Notion-scale servers can expose dozens). n8n's own established UX — one node per app/service, an Operation (here, deliberately no separate Resource level — see §4) dropdown inside it — is the proven answer to exactly this problem, and this project already has the underlying mechanism to build it: SPEC-002/004's dynamic-schema `resolve_slots` contract, already used by `code`, `mcp_call`, `fan_out`, and `merge`. This spec applies that same, already-validated mechanism one level further — a natural generalization, not a new invention.

## 3. Scope

**In scope:**

- **Node generation collapses from one-per-tool to one-per-connection.** `backend/mcp/generated_nodes.py`'s `generate_node_types_for_connection` registers exactly one node type per saved `mcp_server` connection (e.g. `mcp__u_<owner>__my-gmail`, or `mcp__my-gmail` for a global connection — the same owner-scoping SPEC-021 already built, unchanged) instead of one per discovered tool.
- **A real `operation` config field**, rendered as a dropdown (not free text) — populated via a new endpoint enumerating the connection's currently-discovered tool names, mirroring SPEC-006 Addendum's `GET /connections/{name}/models` pattern exactly (the same "live dropdown, not a guess" precedent already proven for Ollama's model field).
- **Dynamic schema resolution** via the *existing* `POST /node-types/{type}/resolve-slots` mechanism (SPEC-002/004, already used by `code`/`mcp_call`/`fan_out`/`merge`) — given `config.operation`, returns the real typed inputs/outputs for that specific tool, reusing the exact per-tool parameter schema already discovered today (no new discovery mechanism; just resolved per-request instead of baked into a separate node type at generation time).
- **Execution unchanged in substance**: `execute()` reads `config.operation` to determine which underlying tool to call, then proceeds exactly as today's generated nodes do — same connection resolution, same SPEC-021 per-user OAuth / per-run shared-graph slot mapping, same trust/approval gating. This spec changes node *structure and selection*, not credential or connection resolution.
- **A formalized trigger-adapter contract**: the existing `telegram_adapter`'s shape (a `trigger_adapter`-role sub-node, SPEC-012 §4, paired with `webhook_trigger`) is extracted into an explicit, documented interface every future app-trigger adapter implements — so adding e.g. "Discord: on message" later is a matter of implementing that contract, not re-deriving the pattern from scratch by reading `telegram_adapter`'s source. The existing `telegram_adapter` is refactored to explicitly implement this contract, with zero behavior change (regression-tested) — proving the contract is a real abstraction of what already works, not a speculative one.

**Out of scope:**

- **Deriving trigger capability automatically from any MCP server.** Confirmed: MCP has no generic, reliably-implemented "notify me when X happens" primitive across real servers. Every app trigger remains hand-built, just against a clearer, reusable contract instead of an implicit pattern.
- **A "Resource" grouping level above Operation.** Confirmed decision: a flat Operation dropdown only (e.g. `create_draft`, `list_labels`, ...), not a two-level Resource→Operation hierarchy. MCP tool names have no inherent resource taxonomy, and heuristic name-parsing to invent one was explicitly rejected as unreliable across arbitrary third-party servers.
- **Any change to SPEC-021's per-user OAuth or connection-resolution mechanism.** This spec is entirely about node *type* structure and *operation* selection.
- **Backward compatibility for already-saved graphs referencing the old per-tool node types.** Confirmed decision: accept this as a breaking change (§4) rather than build and maintain a compatibility shim.

## 4. Design decisions (resolved)

- **Flat Operation dropdown, no Resource level** — MCP tool names have no reliable inherent grouping; a flat list of real, live tool names is simpler and never guesses wrong, at the cost of being less visually organized than n8n's own two-level UX for apps with many tools.
- **Triggers are formalized, not automated.** This spec's job for triggers is making the *pattern* easier to extend to a new app — a documented contract — not deriving trigger capability from MCP discovery, which isn't reliably possible.
- **Breaking change accepted for old per-tool node types.** This project has very few real graphs at this stage; a clean removal (old type names produce a normal "unknown node type" validation error, same as any other removed type) is simpler than building and maintaining a compatibility shim for a userbase this small. Revisit if this project reaches a stage where breaking saved graphs has real cost.
- **Reuses SPEC-002/004's `resolve_slots` mechanism unchanged**, rather than inventing a second dynamic-schema pathway — the exact same `dynamic_schema: true` / `POST /node-types/{type}/resolve-slots` contract `code`/`mcp_call`/`fan_out`/`merge` already implement.

## 5. Data model (illustrative)

```
GET /connections/{name}/mcp-operations
  -> ["create_draft", "list_drafts", "get_thread", ...]
  404 if the connection name isn't in the store
  422 if the connection isn't an mcp_server type
  502 if live discovery itself fails (server unreachable, etc.)

Generated node type (one per connection, not per tool):
  type_name: mcp__u_<owner>__<connection_name>  (or mcp__<connection_name> for a global connection)
  dynamic_schema: true
  config_model: { operation: str }

POST /node-types/{type}/resolve-slots
  body: { config: { operation: "create_draft" } }
  -> { inputs: [...typed slots for create_draft...], outputs: [...] }
```

## 6. Acceptance criteria

- [ ] Connecting Gmail's real MCP server produces exactly **one** node type in the palette (not 13) — live-verified against the real server already proven reachable in SPEC-021.
- [ ] That node's config panel shows a real **Operation** dropdown populated with live, currently-discovered tool names (not free text, not a stale/cached list).
- [ ] Selecting different operations changes the node's displayed input/output slots to match that operation's real schema — verified live for at least two materially different operations (e.g. `create_draft` vs `list_labels`).
- [ ] Executing the node with a chosen operation performs the real underlying tool call — live-verified, non-mocked, same real behavior as today's per-tool nodes, just reached via config instead of node-type identity.
- [ ] `telegram_adapter` is refactored to explicitly implement the new formalized trigger-adapter contract with **zero behavior change** — existing Telegram trigger graphs (e.g. any graph using `examples/telegram_activate_test.json`) continue to work completely unchanged, regression-checked live.
- [ ] A graph referencing an old, now-removed per-tool node type produces a clear, specific "unknown node type" validation error (the existing `check_unregistered_types` rule, unchanged) — not a silent failure or crash.
- [ ] Full existing test suite passes, with SPEC-019's own per-tool-generation tests explicitly updated (not silently left broken) to reflect the new one-node-per-connection model — this is an expected, disclosed set of test changes, not a regression.
- [ ] `git diff main -- backend/execution/engine.py` is empty.

## 7. Open questions

- **Exact shape/name of the new dropdown-population endpoint** (`GET /connections/{name}/mcp-operations` proposed here) — naming/placement to be finalized during implementation, not a design-blocking decision.
- **Whether a second, real trigger-adapter (beyond the refactored Telegram one) should be built as proof the formalized contract actually generalizes**, or whether refactoring Telegram alone (with zero behavior change) is sufficient proof for this spec's acceptance bar. Proposing: refactoring Telegram alone is sufficient for this spec; a genuinely new second trigger adapter is real, additional scope better suited to a future spec once a concrete second app/trigger candidate exists.

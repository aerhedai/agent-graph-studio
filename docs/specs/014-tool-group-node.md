# SPEC-014: Tool Group Node

**Status:** Draft — ready for implementation
**Milestone:** Cluster Node Architecture (follow-on to SPEC-012)
**Author:** Rohan
**Depends on:** SPEC-008 (agent node, tool-call bypass), SPEC-012 (sub-node connectors, cluster nodes), SPEC-013 (visual design system)
**Supersedes (partially):** SPEC-008/012's "any node type can be wired directly into `agent`'s `tools` slot" behavior

## 1. Goal

Replace the direct "any node can be wired straight into an agent's `tools` slot" mechanism with a new **`tool_group`** container node: a single sub-node an agent connects to, which itself holds any number of tool nodes dropped onto it. This turns N loose per-tool wires into one organized, collapsible connection.

## 2. Why this, why now

SPEC-012 generalized agent's tool-calling into a `tools` sub-node slot with `cardinality="many", accepts_role=None` — any node type, wired directly to the agent, one edge per tool. Mechanically sound, but every single node on the canvas renders an unlabeled output connector so it *could* be used this way, whether or not it ever is (SPEC-013 visual audit flagged this as a stray, purposeless-looking dot on ordinary nodes like `text_input` or `llm_call`). A multi-tool agent also ends up with several individual wires converging on it, which doesn't organize or collapse.

`tool_group` fixes both: the per-node connector is removed from ordinary nodes entirely (agent no longer accepts bare tools directly, so it stops serving a purpose for them), and multiple tools collapse into one visual, collapsible unit.

## 3. Scope

In scope:
- A new `tool_group` node type: a pure structural container, no config, no execution behavior of its own
- `tool_group` declares its own `tools` sub-node slot (`cardinality="many", accepts_role=None`) — the same permissive "any node type" behavior `agent.tools` used to have, just one level removed
- `tool_group` itself declares `sub_node_role="tool_group"`, making it pluggable into `agent`'s `tools` slot
- `agent`'s `tools` slot becomes `cardinality="one", accepts_role="tool_group"` — a breaking change from SPEC-012, intentional (see §4)
- `agent`'s tool-gathering logic adds one level of indirection: agent's own `tools` slot resolves to a `tool_group`, whose own `tools` slot resolves to the real tool node ids
- Canvas: a new collapsible group card (collapsed: icon + count; expanded: a compact row per contained tool), populated by **dropping a node directly onto the group's card** — not manual wire-dragging
- Removing the generic `SUB_NODE_HANDLE_ID` output connector from ordinary (non-sub-node-role) nodes, since it no longer serves any purpose once tool-wiring goes through `tool_group`
- A new `tools` palette category (icon: wrench, its own accent color), following the same category-presentation mechanism every other category already uses

Out of scope (future work, not required now):
- Nesting a `tool_group` inside another `tool_group` — technically not blocked by the role/cardinality rules (a `tool_group` could satisfy its own `accepts_role=None` "tools" slot), but no special UI polish is built for it
- Any migration path for graphs authored under SPEC-012's old direct-tool-wiring shape — this project has no production data at stake; existing test fixtures are updated directly, not shimmed
- Persisting collapsed/expanded state in the graph JSON — pure canvas presentation state, same treatment as node `status`

## 4. Design decisions (resolved)

- **The interaction is drop-to-contain, not wire-dragging.** Confirmed directly with the user (over the alternative of keeping a manual connector, just retargeted at the group): dragging a tool node onto the group's rendered card automatically creates the underlying `sub_node` edge. This is a canvas/interaction-layer decision only — the data model underneath is still an ordinary `sub_node` edge (`kind: "sub_node", slot: "tools"`), so no new graph-schema field is introduced for containment.
- **`agent.tools` becomes role-gated and single-cardinality, breaking direct tool wiring.** Accepted as an intentional breaking change, not a silently-made one: `resources["sub_nodes"]` (`backend/execution/engine.py`) and `check_sub_node_edges` (`backend/validation/rules.py`) are both already fully generic over arbitrary root/slot nesting — verified by reading both directly before writing any code — so this needed zero engine or validation-logic changes, only registry data (new `tool_group` type, changed `agent` slot spec) and `agent.py`'s own two-line gathering-logic update.
- **`tool_group` is the first "hybrid" node**: simultaneously a root (`sub_node_slots` non-empty) and a sub-node (`sub_node_role` set). The canvas detects this generically (`subNodeRole && subNodeSlots present`) rather than hardcoding the type name `"tool_group"` — consistent with this project's standing "never hardcode node type names in the canvas" principle — so any future hybrid cluster type gets the same collapsible-group treatment automatically.
- **Contained tool nodes stop rendering as separate free-floating canvas cards.** Once contained, a tool is represented only by its compact row inside the group card. The underlying node still exists in canvas state (needed for its own config/execution) — only the *rendered, visible* node/edge lists passed to React Flow are filtered; the full state remains the save/load source of truth.
- **Collapse/expand state is not persisted.** Matches how `status`/`errorMessage` (SPEC-013) also aren't serialized — `nodesAndEdgesToGraphSpec` already only extracts `id/type/config` per node, so this needed no new exclusion logic.

## 5. Data model

### `tool_group` node config
```json
{}
```
No config fields — a pure structural container.

### Graph JSON — nesting example
```json
{
  "edges": [
    { "kind": "sub_node", "slot": "tools", "from": {"node": "code_1"}, "to": {"node": "tool_group_1"} },
    { "kind": "sub_node", "slot": "tools", "from": {"node": "mcp_call_1"}, "to": {"node": "tool_group_1"} },
    { "kind": "sub_node", "slot": "tools", "from": {"node": "tool_group_1"}, "to": {"node": "agent_1"} },
    { "kind": "sub_node", "slot": "model", "from": {"node": "model_1"}, "to": {"node": "agent_1"} }
  ]
}
```
`agent_1`'s `tools` slot resolves to exactly one node (`tool_group_1`); `tool_group_1`'s own `tools` slot resolves to the real tool ids (`code_1`, `mcp_call_1`) that `agent.py` actually invokes directly (ADR-008-style bypass, unchanged).

## 6. Acceptance criteria

- [ ] A `tool_group` containing two or more real tool nodes, connected to an `agent`, correctly resolves and calls those tools at runtime — live-verified, non-mocked
- [ ] Wiring a bare node (no `tool_group` in between) directly into an agent's `tools` slot is rejected by validation — regression-proves the role-gating actually took effect
- [ ] Dropping a node (from the palette, or an existing canvas node) onto a `tool_group` card visually contains it — represented as a compact row, no longer a separate free-floating card
- [ ] Removing a tool from a group re-materializes it as an ordinary, independently positioned canvas node
- [ ] The group card collapses/expands, with contents visible (as rows) only when expanded
- [ ] Ordinary nodes (not sub-node-role types) no longer render the generic sub-node output connector
- [ ] `git diff main -- backend/execution/engine.py`: empty
- [ ] Full existing test suite passes, with every changed pre-existing test (agent/validation fixtures updated to route through `tool_group`) called out explicitly, not silently modified

## 7. Open questions

None — the one real ambiguity (drop-to-contain vs. manual wire-dragging) was resolved directly with the user before this spec was written; see §4.

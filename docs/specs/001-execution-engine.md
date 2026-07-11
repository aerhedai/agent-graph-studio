# SPEC-001: Execution Engine MVP

**Status:** Draft
**Milestone:** Execution Engine MVP
**Author:** Rohan
**Depends on:** ARCHITECTURE.md §2–5

## 1. Goal

Build the minimum backend execution engine capable of running a simple linear-to-branching graph of nodes, with full trace logging, without any UI yet. This is the foundation everything else (canvas, node library, evals) builds on.

## 2. Scope (this spec only)

In scope:
- Graph JSON schema (nodes + edges, per ARCHITECTURE.md §2.3)
- Graph validation: required inputs connected, no type mismatches, no illegal cycles
- Topological sort for execution order
- Execution of four node types only, to prove the model end to end:
  1. `text_input` (I/O)
  2. `llm_call` (model call — Claude API)
  3. `conditional_branch` (control flow)
  4. `text_output` (I/O)
- Structured trace logging per node execution (per ARCHITECTURE.md §7)
- A CLI entry point that takes a graph JSON file and runs it, printing the trace

Out of scope (future specs):
- Loops / fan-out-fan-in (needs its own spec once linear + branch is solid)
- Frontend / canvas
- MCP tool nodes
- Human-in-the-loop node
- Local model routing

## 3. Data model

### Graph schema
```json
{
  "version": "0.1",
  "nodes": [
    {
      "id": "string, unique in graph",
      "type": "one of: text_input | llm_call | conditional_branch | text_output",
      "config": { "...": "type-specific, see §4" }
    }
  ],
  "edges": [
    {
      "from": { "node": "node id", "slot": "output slot name" },
      "to": { "node": "node id", "slot": "input slot name" }
    }
  ]
}
```

### Trace record (per node execution)
```json
{
  "run_id": "uuid",
  "node_id": "string",
  "node_type": "string",
  "started_at": "ISO timestamp",
  "finished_at": "ISO timestamp",
  "inputs": { "slot_name": "value" },
  "outputs": { "slot_name": "value" },
  "token_cost": { "input_tokens": 0, "output_tokens": 0 },
  "error": null
}
```

## 4. Node type definitions (MVP set)

### `text_input`
- Inputs: none
- Outputs: `text` (string)
- Config: `{ "value": "string, provided at graph-run time or hardcoded" }`

### `llm_call`
- Inputs: `prompt` (text)
- Outputs: `response` (text)
- Config: `{ "model": "string", "system_prompt": "string", "max_tokens": "int" }`
- Behavior: calls the Claude API with `system_prompt` + `prompt`, returns `response`. Must record token cost in the trace.

### `conditional_branch`
- Inputs: `value` (text), evaluated against `config.condition`
- Outputs: `true_branch` (passthrough of `value`), `false_branch` (passthrough of `value`) — only one fires per execution
- Config: `{ "condition": "string expression, e.g. contains('yes')" }`

### `text_output`
- Inputs: `text`
- Outputs: none
- Config: none
- Behavior: captures final value for the run result

## 5. Validation rules

A graph is invalid (must be rejected before execution) if:
- Any node's required input slot has no incoming edge and no default in config
- Any edge connects mismatched types
- The graph contains a cycle (MVP does not support loops — see §2 out-of-scope)
- Any node references an unregistered `type`

## 6. Execution algorithm

1. Parse graph JSON, validate (§5).
2. Build dependency graph from edges; topologically sort.
3. Walk nodes in sorted order:
   - Gather inputs from upstream outputs (or config defaults for `text_input`)
   - Execute the node's body
   - Record a trace entry (§3)
   - For `conditional_branch`, only propagate the value along the fired branch; the other branch's downstream nodes do not execute
4. Collect final `text_output` node value(s) as the run result.
5. Return `{ "result": {...}, "trace": [...] }`.

## 7. Acceptance criteria

- [ ] A 4-node linear graph (`text_input → llm_call → text_output`) executes correctly and returns the LLM's response
- [ ] A graph with a `conditional_branch` correctly executes only the fired branch and skips the other
- [ ] An invalid graph (missing required input) is rejected with a clear error before any node executes
- [ ] Every node execution produces a complete trace record, including token cost for `llm_call`
- [ ] A cyclic graph is rejected at validation with a clear error, not a runtime hang
- [ ] Unit tests cover: valid linear graph, valid branching graph, missing-input rejection, cyclic rejection, a node execution failure mid-graph (verify downstream nodes do not execute and the error is captured in the trace)

## 8. Open questions for review before implementation

- Should `conditional_branch` support more than two branches in a later version, or is true/false sufficient for MVP? (Recommend: two branches only for MVP, revisit in a future spec.)
- Where does the CLI read graph JSON from — local file path only for MVP, or should it accept stdin too?
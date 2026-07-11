# Architecture — Agent Graph Studio

## 1. Overview

Agent Graph Studio is a visual dataflow programming environment for AI agent workflows. The graph the user builds on the canvas **is** the program — there is no separate "compile to code" step for it to run; the graph itself is executed directly by the backend engine.

This mirrors the ComfyUI model: a node = an operation, an edge = data passed between operations, and a saved graph = a portable, versionable workflow definition (JSON).

## 2. Core abstractions

### 2.1 Node

A node is the atomic unit of work. Every node has:

- `id`: unique identifier within the graph
- `type`: which node type this is (see registry, §3)
- `inputs`: named, typed input slots
- `outputs`: named, typed output slots
- `config`: static configuration for this instance (model name, temperature, system prompt, retry policy, etc.)
- `body`: the actual behavior — one of:
  - **Model call** (LLM API — Claude, local Ollama model, etc.)
  - **Tool/action call** (HTTP request, MCP server call, DB query, file read)
  - **Control flow** (conditional branch, loop, parallel fan-out, merge/wait-all)
  - **Code** (arbitrary Python function — the escape hatch, equivalent to ComfyUI custom nodes)
  - **Human-in-the-loop** (pause execution until a person approves/edits)
  - **Memory/state** (store/retrieve from session or vector store)
  - **I/O** (graph-level input or output)

### 2.2 Edge

An edge connects one node's output slot to another node's input slot. Edges are **typed** — the output type of the source slot must match (or be explicitly coercible to) the input type of the destination slot. Types include: `text`, `json`, `file_ref`, `embedding`, `image`, `boolean`, `list<T>`.

Type validation happens **at connection time in the UI**, not just at execution time — this is a deliberate differentiator (see CLAUDE.md).

### 2.3 Graph

The full set of nodes + edges, serialized as JSON:

```json
{
  "version": "0.1",
  "nodes": [
    { "id": "n1", "type": "llm_call", "config": {...}, "position": {...} },
    { "id": "n2", "type": "conditional_branch", "config": {...}, "position": {...} }
  ],
  "edges": [
    { "from": {"node": "n1", "slot": "output"}, "to": {"node": "n2", "slot": "input"} }
  ]
}
```

This format is the save/load/version unit — directly analogous to a ComfyUI `workflow.json`.

### 2.4 Execution engine

Given a graph, the engine:

1. **Validates** the graph (all required inputs connected, no type mismatches, no illegal cycles).
2. **Topologically sorts** the DAG to determine valid execution order.
3. **Resolves control flow**: conditional branches select which downstream edge fires; loops are handled as a special subgraph construct (see §4), not as literal cycles.
4. **Executes nodes**, respecting dependencies — independent branches may run in parallel (fan-out), with explicit merge/wait-all nodes to rejoin.
5. **Logs every node execution**: inputs received, outputs produced, latency, token cost (where applicable), and any error — this is the tracing/eval layer, and it is not optional or deferred.
6. **Propagates errors** as structured data to the graph-level trace; a failed node does not silently continue.

## 3. Node registry

Node types are registered in a plugin-style registry so new types can be added without modifying the core engine. Each registered node type declares:
- its input/output schema
- its config schema
- its execution function

This keeps the engine core stable while the node library grows — same design principle as ComfyUI's custom node ecosystem.

## 4. Handling loops (the hard part)

DAGs cannot natively express "repeat until condition met." The approach:
- A **loop node** wraps a sub-graph.
- The engine treats the sub-graph as a black box that it invokes repeatedly, re-injecting the loop's own output as its next input, until a stop condition (explicit iteration cap, or a condition-check node inside the sub-graph) is met.
- This keeps the outer graph a true DAG (loop node has one input, one output) while still allowing iterative logic inside.

## 5. Fan-out / fan-in (multi-agent pattern)

To express planner → parallel workers → critic:
- A **fan-out node** takes one input and one config (e.g. "N workers") and produces N parallel execution branches.
- A **merge/wait-all node** takes N inputs and blocks until all have arrived, then produces a single combined output.
- The engine must run independent branches concurrently (async execution), not just sequentially in submission order.

## 6. Human-in-the-loop

A dedicated node type halts graph execution and surfaces the current state to the user via the UI. Execution resumes only on explicit approval (optionally with edits to the data passed through). This is mandatory for any node type that writes or deletes data in an external system.

## 7. Observability / evals layer

Every node execution emits a structured trace record:

```json
{
  "run_id": "...",
  "node_id": "n1",
  "started_at": "...",
  "finished_at": "...",
  "inputs": {...},
  "outputs": {...},
  "token_cost": {...},
  "error": null
}
```

These records are the foundation for: debugging (inspect any node's exact input/output after a run), cost tracking, and — longer term — a regression-eval suite (define expected outputs for a set of test graphs, run automatically, flag drift).

## 8. Frontend

- Canvas built on **React Flow**.
- Each node type has a corresponding visual component (inputs/outputs as connectable ports, config as an inline or side-panel form).
- Clicking a node after a run surfaces its trace record (inputs/outputs/cost/error) inline — this is the "inspect intermediate state" differentiator.

## 9. Open questions / future ADRs

- Sub-graph versioning and reuse (equivalent to ComfyUI node groups) — needs its own ADR once the base engine is stable.
- Multi-user / collaboration — out of scope for MVP.
- Local model backend abstraction (Ollama vs ComfyUI-style vs cloud API) — needs its own ADR before the "model call" node type is finalized.
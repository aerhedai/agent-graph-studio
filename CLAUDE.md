# CLAUDE.md — Agent Graph Studio

This file gives Claude Code (or any agent working in this repo) the context it needs before touching any code. Read this in full before starting a task.

## What this project is

A visual, node-graph-based development environment for building and running AI agent workflows — connecting LLM calls, tools/MCP servers, local models, and arbitrary code as nodes on a canvas, wired together with typed edges, similar in spirit to ComfyUI's node graph but for agent/tool orchestration instead of diffusion pipelines.

Existing prior art (Flowise, Langflow, n8n, Dify) already covers this category. Our differentiation, in priority order:
1. **Local/hybrid execution as first-class** — nodes can route to a local model (Ollama, ComfyUI-style backend) or a cloud API, with tiered cost/model routing expressed directly in the graph, not bolted on.
2. **Node-level evaluation and tracing built in from day one** — every node run is logged with inputs, outputs, token cost, and latency; this is a first-class part of the data model, not an afterthought.
3. **Intermediate state inspection** — a user can click any node after a run and see exactly what it received and produced, borrowed from what makes ComfyUI genuinely good for debugging.

## Core abstractions (see ARCHITECTURE.md for full detail)

- **Node**: a unit of work with typed inputs, typed outputs, a body (LLM call / tool call / MCP call / code), and config.
- **Edge**: a typed connection from one node's output to another's input.
- **Graph**: the full assembly of nodes + edges, serialized as JSON (analogous to a ComfyUI workflow.json).
- **Execution engine**: topologically sorts the graph, resolves branches/loops, executes nodes, and passes data downstream while logging every step.

## Repo structure

```
/backend              # execution engine, node registry, API server
/frontend             # canvas UI (React Flow based)
/docs
  /adr                # Architecture Decision Records — one file per significant decision, numbered, never edited after merge (superseded instead)
  /specs               # SPEC.md per feature, written BEFORE implementation
/tests
```

## Conventions

- **Spec-first, always.** No feature gets implemented without a corresponding `docs/specs/NNN-name.md` written and reviewed first. If you (the agent) are asked to implement something without a spec, stop and ask for one, or draft one before proceeding.
- **Every node type must define its input/output schema explicitly** and validate at connection time, not just at run time. This is a deliberate differentiator from Flowise's weaker type-checking — do not weaken this.
- **Every PR corresponds to one GitHub issue**, which corresponds to one milestone/epic. Reference the issue number in the PR description.
- **Tests are part of the same PR as the feature**, not a follow-up. Node execution logic especially needs tests for: normal path, missing input, malformed input, downstream failure propagation.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- **Never silently swallow node execution errors** — a failed node must propagate a structured error to the graph-level trace, not just log-and-continue.

## Tech stack (initial)

- Backend: Python (execution engine, node registry, FastAPI for the API layer)
- Frontend: React + React Flow for the canvas
- Node registry: plugin-style — new node types should be addable without touching the core engine
- Tracing/logging: structured JSON logs per node run, designed to be queryable later (this is the "evals" layer)

## What NOT to do

- Do not add a new node type without an entry in the node schema registry and a corresponding spec.
- Do not implement cycles/loops as literal graph cycles — see ARCHITECTURE.md for the loop-as-subgraph pattern; naive cycles will break the topological sort.
- Do not bypass the typed input/output validation "to make something work quickly" — flag it in the PR instead.

## Current milestone

See the GitHub Project board. First milestone: **Execution Engine MVP** — see `docs/specs/001-execution-engine.md`.
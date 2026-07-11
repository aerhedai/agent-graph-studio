# CLAUDE.md — Agent Graph Studio

This file gives Claude Code (or any agent working in this repo) the context it needs before touching any code. Read this in full before starting a task.

## What this project is

A visual, node-graph-based development environment for building and running AI agent workflows — connecting LLM calls (any provider, cloud or local), tools/MCP servers, and arbitrary code as nodes on a canvas, wired together with typed edges. Similar in spirit to ComfyUI's node graph, but for agent/tool orchestration instead of diffusion pipelines.

Existing prior art (Flowise, Langflow, n8n, Dify) already covers this category. Our differentiation, in priority order:
1. **Provider-agnostic, local/hybrid execution as first-class** — nodes can route to a local model (Ollama, ComfyUI-style backend) or any cloud API, with tiered cost/model routing expressed directly in the graph, not bolted on. Model-call nodes must not be hardcoded to a single provider — see the node registry section below.
2. **Node-level evaluation and tracing built in from day one** — every node run is logged with inputs, outputs, token cost, and latency; this is a first-class part of the data model, not an afterthought.
3. **Intermediate state inspection** — a user can click any node after a run and see exactly what it received and produced, borrowed from what makes ComfyUI genuinely good for debugging.
4. **Arbitrary code and agents as nodes** — a `code` node type (arbitrary Python function) is the generality escape hatch, equivalent to ComfyUI custom nodes. An "agent" node (a stateful, possibly-looping unit of work) is a harder future case — see ARCHITECTURE.md §4 and ADR-002.

## Core abstractions (see ARCHITECTURE.md for full detail)

- **Node**: a unit of work with typed inputs, typed outputs, a body (LLM call / tool call / MCP call / code), and config.
- **Edge**: a typed connection from one node's output to another's input.
- **Graph**: the full assembly of nodes + edges, serialized as JSON (analogous to a ComfyUI workflow.json).
- **Execution engine**: topologically sorts the graph, resolves branches, executes nodes, and passes data downstream while logging every step. The engine is a strict DAG executor — no literal cycles. Iteration is handled via a loop node wrapping a sub-graph (see ADR-002), not via cyclic edges.

## Repo structure

```
/backend
  /cli                 # entry point: backend/cli/main.py, registered as `agent-graph-studio` script
  /execution           # the DAG engine (run_graph, topological sort, tracing)
  /llm                 # LLMClient Protocol + provider implementations (currently: AnthropicLLMClient)
  /nodes               # node type implementations + registry
  /schema              # Pydantic models + graph JSON loader/parser
  /validation          # graph validation rules and error types
/tests
  /fixtures/graphs      # example graph JSON files used across tests (valid_linear.json, cyclic.json, missing_input.json, etc.)
  conftest.py
/docs
  /adr                 # Architecture Decision Records — numbered, never edited after merge (superseded instead)
  /specs               # SPEC.md per feature, written BEFORE implementation
```

## Conventions

- **Spec-first, always.** No feature gets implemented without a corresponding `docs/specs/NNN-name.md` written and reviewed first. If asked to implement something without a spec, stop and ask for one, or draft one before proceeding.
- **If a spec has an unresolved open question relevant to the current task, ask before proceeding rather than assuming.** Do not silently pick an answer to an explicitly flagged open question (e.g. branch arity, CLI input source, schema library choice) — surface it and wait for a decision. Once resolved, the answer gets written back into the spec itself (see below).
- **Resolved open questions get written back into the spec.** When an open question from a spec's "Open questions" section is decided, add a one-line resolution directly under it (e.g. "Resolved: two branches only for MVP — see ADR-002") so the spec stays an accurate record, not a stale question.
- **Every node type must define its input/output schema explicitly** as a Pydantic model and validate at connection time, not just at run time. This is a deliberate differentiator from Flowise's weaker type-checking — do not weaken this.
- **Model-call nodes must go through the `LLMClient` Protocol** (see `backend/llm/client.py`), never call a provider SDK directly from node execution code. This is what makes provider-agnostic swapping (Claude, local Ollama, others) possible without touching the engine. New providers are new classes implementing `LLMClient`, not new branches inside an existing node.
- **Every PR corresponds to one GitHub issue**, which corresponds to one milestone/epic. Reference the issue number in the PR description, with the spec's acceptance criteria pasted in as a checklist.
- **Tests are part of the same PR as the feature**, not a follow-up.
- **Any feature that adds tests must also ensure `uv run pytest` succeeds from a clean `uv sync`, and — if it touches the CLI or an execution/integration path — must include at least one real, non-mocked invocation demonstrated to the user before being reported as complete.** Mocked unit tests passing is not sufficient evidence that a feature works end to end; this was a real gap found during SPEC-001 review (an entire execution/tracing layer was initially unverified because only schema/validation/registry/topo-sort tests existed, with no execution tests, and no live run was surfaced until explicitly asked for).
- **Never silently swallow node execution errors** — a failed node must propagate a structured error to the graph-level trace, not just log-and-continue.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

## Dependency management (uv)

- This project uses **uv**, not pip/venv directly. `pyproject.toml` + `uv.lock` are the source of truth.
- Dev dependencies (pytest, etc.) live under **`[dependency-groups]`**, not `[project.optional-dependencies]`. This matters: `uv sync` alone installs everything needed to run tests — no `--extra dev` flag required. Do not move dev tooling back into optional-dependencies; it silently breaks the "clean `uv sync` → tests pass" guarantee above.
- Run tests with `uv run pytest tests/ -v`. Run the CLI with the registered script: `uv run agent-graph-studio <graph.json>`, or via module path `uv run python -m backend.cli.main <graph.json>`.
- Real LLM calls require `ANTHROPIC_API_KEY` (or the relevant provider's key) set in the environment — this is a **separate credential from any claude.ai/Claude Code subscription**, obtained from console.anthropic.com. The `AnthropicLLMClient` is constructed lazily, only when a graph actually contains an `llm_call` node and no client was injected, so graphs without one never require a key.

## What NOT to do

- Do not add a new node type without an entry in the node schema registry and a corresponding spec.
- Do not hardcode a node type to a single LLM provider — go through the `LLMClient` Protocol.
- Do not implement cycles/loops as literal graph cycles — see ARCHITECTURE.md §4 and ADR-002 for the loop-as-subgraph pattern; naive cycles will break the topological sort and validation.
- Do not bypass the typed input/output validation "to make something work quickly" — flag it in the PR instead.
- Do not report a feature as "done" on the basis of mocked tests alone if it touches an execution or integration path — see the testing convention above.

## Current milestone

See the GitHub Project board. SPEC-001 (Execution Engine MVP) is complete pending final PR review. Next: **SPEC-002 — pluggable node registry**, scoped to prove genuine plugin extensibility by adding a second LLM provider (local, via Ollama) alongside Claude, and a `code` node type for arbitrary Python execution.
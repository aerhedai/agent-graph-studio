# ADR-001: Pydantic v2 for schema representation

**Status:** Accepted
**Date:** 2026-07-11
**Related:** SPEC-001 §3, §8

## Context

SPEC-001 required a representation technology for the Graph, Node, Edge, and TraceRecord data models. The node registry design (per CLAUDE.md) requires heterogeneous, per-node-type config schemas — `llm_call` config looks nothing like `conditional_branch` config — which is a discriminated-union / tagged-union problem. The choice was between:

1. Stdlib `dataclasses` + hand-rolled validation logic
2. Pydantic v2

## Decision

Use Pydantic v2 for all core data models (`Graph`, `Node`, `Edge`, `TraceRecord`), with a discriminated union on the node's `type` field to validate per-node-type `config` shapes.

## Rationale

- **Discriminated unions are a solved problem in Pydantic v2**, handled natively via a `type`-tagged union. Hand-rolling this ourselves means writing our own tagged-union dispatch and validation logic — solvable, but it doesn't differentiate this project from anything. Effort is better spent on the actual differentiators (local/hybrid model routing, the evals/tracing layer), not on reimplementing validation infrastructure.
- **Validation error quality matters for an explicit acceptance criterion.** SPEC-001 §7 requires that an invalid graph be "rejected with a clear error before any node executes." Pydantic's validation errors identify the exact field, node, and type mismatch by default. Hand-rolled validation tends to degrade into vague `ValueError`s unless deliberately invested in — again, effort better spent elsewhere.
- **Low marginal cost.** FastAPI (already scoped in CLAUDE.md for the future API layer) is itself built on Pydantic. Adopting it now means one validation model used consistently across the CLI, engine, and future API layer, instead of two different systems living side by side.
- **Free downstream value.** Pydantic models generate JSON Schema automatically. This will matter once the frontend canvas needs to auto-generate per-node-type config forms from the same schema the backend validates against — one source of truth instead of two independently maintained ones.

## Consequences

- Adds `pydantic>=2.7` as a hard dependency (already reflected in `pyproject.toml`).
- All future node type config schemas must be defined as Pydantic models registered into the discriminated union — this is now a repo convention, not just this decision's footprint (see CLAUDE.md).
- We do not get to claim "zero dependencies" as a portfolio talking point — an accepted tradeoff, since raw validation-logic implementation was never the point of this project.

## Alternatives considered

- **Dataclasses + hand-rolled validation**: rejected. Would have been defensible only if minimizing dependencies were itself a goal, or if demonstrating raw validation-logic skill were the point of the exercise — neither applies here.
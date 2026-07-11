# ADR-002: DAG execution model — loops as sub-graphs, conditional branches fixed at two

**Status:** Accepted
**Date:** 2026-07-11
**Related:** ARCHITECTURE.md §4–5, SPEC-001 §2, §7, §8

## Context

Two related design questions came up during SPEC-001 implementation:

1. Should `conditional_branch` support N branches now, or two (true/false) for MVP?
2. The engine executes via topological sort, which requires the graph to be a true DAG (no cycles). But real agent workflows need iteration ("repeat until condition met"), which is naturally cyclic. How should looping be supported without breaking the DAG execution model?

## Decision

- **`conditional_branch` supports exactly two branches (`true_branch` / `false_branch`) for MVP.** No N-way switch node exists yet.
- **Loops are not implemented as literal graph cycles.** Iteration is deferred to a future spec, and will be implemented as a dedicated **loop node** that wraps a sub-graph as a black box: the engine invokes the sub-graph repeatedly, re-injecting its own output as the next input, until a stop condition is met (explicit iteration cap, or an internal condition-check node). From the outer graph's perspective, the loop node has exactly one input and one output — it remains a true DAG node, not a cycle.

## Rationale

**On branch arity:** Two-branch conditionals are sufficient to prove the mechanism SPEC-001 needed to prove — that the engine correctly fires one branch and skips the other during execution (§7's explicit acceptance criterion). N-way doesn't test anything qualitatively different, it's the same mechanism repeated. Designing the N-way version now would mean guessing at requirements not yet known (should it be a `switch`-style value match? weighted routing? multiple simultaneous truthy branches?) — better resolved once real graphs are being built with the finished engine and an actual need surfaces. If a multi-way switch is needed later, it will be a **new node type** (e.g. `switch_branch`) added via the registry, not a generalization of `conditional_branch` itself — this keeps existing graphs and tests stable.

**On loops:** A topological sort is only well-defined on a DAG. Allowing literal cycles in the graph would break validation (§5 explicitly requires cycle detection and rejection) and the execution ordering algorithm (§6) at the same time. Rather than redesigning the core engine to support a different execution model (e.g., an actor/message-passing model that tolerates cycles), we keep the outer graph strictly acyclic and push iteration inside a single node's black-box behavior. This:

- Keeps the engine's mental model simple and matches how ComfyUI-style tools already work (no native cycles; iteration lives inside custom nodes or externally scripted loops).
- Keeps validation and topological sort unchanged and already-tested — no rework of SPEC-001's core logic.
- Localizes the hard part (state management across iterations, termination conditions) to one well-scoped future node type rather than spreading it through the general execution engine.

## Consequences

- Loops are explicitly **out of scope** for SPEC-001 (already noted in its §2) and require a dedicated future spec before any agent workflow needing genuine iteration can be built.
- The loop node's internal sub-graph execution will need its own trace-record handling (nested traces, or a flattened representation) — an open design question for that future spec, not resolved here.
- Fan-out/fan-in (parallel branches, per ARCHITECTURE.md §5) is a related but separate concern, also deferred, and should be designed alongside the loop node since both stress the same "sub-graph as a black-box unit" pattern.

## Alternatives considered

- **N-way branch node now**: rejected as premature generalization without a concrete driving use case.
- **Literal cycle support in the core engine** (e.g. via a different execution model such as message-passing actors): rejected for MVP — significantly higher implementation cost, and would have delayed proving the core engine mechanism SPEC-001 exists to prove. May be revisited in a future ADR if the loop-as-subgraph pattern proves insufficient for real workflows.
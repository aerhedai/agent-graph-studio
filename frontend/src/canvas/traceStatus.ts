import type { NodeStatus } from "./GenericNode";
import type { RunStatusResponse, TraceRecord } from "../api/types";

// A tool node invoked through an agent's tool_group (ADR-008's direct-
// execution bypass) never gets its own top-level entry in a run's trace --
// its real TraceRecord only ever lands nested inside its agent's own
// `child_traces` (one inner list per iteration/tool-call). Searching only
// the flat top-level list, as this used to, made those nodes look like
// they never ran at all: no status color, no click-to-inspect trace. This
// recurses through every level of nesting (not hardcoded to one), since
// `child_traces` is itself recursively typed and any future node type that
// nests further gets the same treatment for free.
export function findTraceRecord(records: TraceRecord[], nodeId: string): TraceRecord | null {
  for (const record of records) {
    if (record.node_id === nodeId) return record;
    for (const inner of record.child_traces ?? []) {
      const found = findTraceRecord(inner, nodeId);
      if (found) return found;
    }
  }
  return null;
}

export function statusForNode(nodeId: string, run: RunStatusResponse | null): NodeStatus {
  if (!run) return "pending";
  const record = findTraceRecord(run.trace, nodeId);
  if (record) return record.error ? "error" : "success";
  if (run.running_node_ids.includes(nodeId)) return "running";
  if (run.active_sub_node_ids.includes(nodeId)) return "running";
  return "pending";
}

// spec-013 §7 (resolved open question, adopted its own "yes" recommendation):
// a failed node shows its error message via a short inline hover tooltip for
// immediate visibility, in addition to the full detail already available in
// the trace inspector panel -- real trace data, not a placeholder string.
export function errorMessageForNode(nodeId: string, run: RunStatusResponse | null): string | null {
  if (!run) return null;
  return findTraceRecord(run.trace, nodeId)?.error ?? null;
}

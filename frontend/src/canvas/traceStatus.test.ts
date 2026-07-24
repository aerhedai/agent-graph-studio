import { describe, expect, it } from "vitest";
import type { RunStatusResponse, TraceRecord } from "../api/types";
import { errorMessageForNode, findTraceRecord, statusForNode } from "./traceStatus";

function record(overrides: Partial<TraceRecord> & { node_id: string }): TraceRecord {
  return {
    run_id: "run-1",
    node_type: "code",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    inputs: {},
    outputs: {},
    token_cost: { input_tokens: 0, output_tokens: 0 },
    side_effect: false,
    child_traces: null,
    error: null,
    ...overrides,
  };
}

function run(overrides: Partial<RunStatusResponse> = {}): RunStatusResponse {
  return {
    run_id: "run-1",
    status: "running",
    running_node_ids: [],
    active_sub_node_ids: [],
    pending_approvals: [],
    trace: [],
    result: null,
    error: null,
    ...overrides,
  };
}

describe("findTraceRecord", () => {
  it("finds a top-level record", () => {
    const target = record({ node_id: "text_input_1" });
    expect(findTraceRecord([target], "text_input_1")).toBe(target);
  });

  it("returns null for a node absent everywhere", () => {
    expect(findTraceRecord([record({ node_id: "text_input_1" })], "does_not_exist")).toBeNull();
  });

  // The actual bug: a tool node invoked via an agent's ADR-008 direct-
  // execution bypass never gets its own top-level trace entry -- its real
  // TraceRecord only ever lands nested inside the agent's own
  // child_traces (one inner list per tool call).
  it("finds a record nested inside child_traces", () => {
    const toolRecord = record({ node_id: "add_1", node_type: "code" });
    const agentRecord = record({ node_id: "agent_1", node_type: "agent", child_traces: [[toolRecord]] });
    expect(findTraceRecord([agentRecord], "add_1")).toBe(toolRecord);
  });

  it("finds a record nested arbitrarily deep (child_traces of a child_traces entry)", () => {
    const deepest = record({ node_id: "deepest" });
    const middle = record({ node_id: "middle", child_traces: [[deepest]] });
    const top = record({ node_id: "top", child_traces: [[middle]] });
    expect(findTraceRecord([top], "deepest")).toBe(deepest);
  });

  it("searches every entry across multiple tool calls, not just the first", () => {
    const first = record({ node_id: "tool_a" });
    const second = record({ node_id: "tool_b" });
    const agentRecord = record({ node_id: "agent_1", child_traces: [[first], [second]] });
    expect(findTraceRecord([agentRecord], "tool_b")).toBe(second);
  });
});

describe("statusForNode", () => {
  it("returns pending when there is no run yet", () => {
    expect(statusForNode("agent_1", null)).toBe("pending");
  });

  it("returns success for a completed top-level node", () => {
    const r = run({ trace: [record({ node_id: "agent_1" })] });
    expect(statusForNode("agent_1", r)).toBe("success");
  });

  it("returns error for a failed top-level node", () => {
    const r = run({ trace: [record({ node_id: "agent_1", error: "boom" })] });
    expect(statusForNode("agent_1", r)).toBe("error");
  });

  // The actual bug fix: a tool node only present nested inside child_traces
  // must resolve to its real status, not fall through to "pending".
  it("resolves a node only present nested inside child_traces", () => {
    const toolRecord = record({ node_id: "add_1" });
    const agentRecord = record({ node_id: "agent_1", child_traces: [[toolRecord]] });
    const r = run({ trace: [agentRecord] });
    expect(statusForNode("add_1", r)).toBe("success");
  });

  it("resolves a failed nested tool call to error", () => {
    const toolRecord = record({ node_id: "add_1", error: "division by zero" });
    const agentRecord = record({ node_id: "agent_1", child_traces: [[toolRecord]] });
    const r = run({ trace: [agentRecord] });
    expect(statusForNode("add_1", r)).toBe("error");
  });

  it("returns running for a top-level node the scheduler is currently executing", () => {
    const r = run({ running_node_ids: ["agent_1"] });
    expect(statusForNode("agent_1", r)).toBe("running");
  });

  it("returns running for a sub-node currently active via the live activity signal", () => {
    const r = run({ active_sub_node_ids: ["model_1"] });
    expect(statusForNode("model_1", r)).toBe("running");
  });

  it("falls back to pending when a node is in neither trace nor either running set", () => {
    const r = run();
    expect(statusForNode("text_input_1", r)).toBe("pending");
  });
});

describe("errorMessageForNode", () => {
  it("returns null when there is no run yet", () => {
    expect(errorMessageForNode("agent_1", null)).toBeNull();
  });

  it("returns the real error message for a top-level failed node", () => {
    const r = run({ trace: [record({ node_id: "code_1", error: "division by zero" })] });
    expect(errorMessageForNode("code_1", r)).toBe("division by zero");
  });

  it("recurses into child_traces for a nested tool's error", () => {
    const toolRecord = record({ node_id: "add_1", error: "bad input" });
    const agentRecord = record({ node_id: "agent_1", child_traces: [[toolRecord]] });
    const r = run({ trace: [agentRecord] });
    expect(errorMessageForNode("add_1", r)).toBe("bad input");
  });

  it("returns null for a node with no error", () => {
    const r = run({ trace: [record({ node_id: "code_1" })] });
    expect(errorMessageForNode("code_1", r)).toBeNull();
  });
});

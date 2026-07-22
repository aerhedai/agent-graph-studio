import type {
  ActivateGraphResponse,
  ConnectionInfo,
  ConnectionTypeInfo,
  GraphSpec,
  NodeTypeInfo,
  ResolveSlotsResponse,
  RunListResponse,
  RunStatusResponse,
  RunSubmitResponse,
  TestConnectionResponse,
} from "./types";

// No axios -- this project's convention (backend and frontend alike) is to
// keep dependencies minimal; a thin fetch wrapper is enough for 4 endpoints.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${init?.method ?? "GET"} ${path} failed (${response.status}): ${body}`);
  }
  return response.json() as Promise<T>;
}

export function fetchNodeTypes(): Promise<NodeTypeInfo[]> {
  return request<NodeTypeInfo[]>("/node-types");
}

export function resolveSlots(
  type: string,
  config: Record<string, unknown>,
): Promise<ResolveSlotsResponse> {
  return request<ResolveSlotsResponse>(`/node-types/${encodeURIComponent(type)}/resolve-slots`, {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

// `graphId` is optional and caller-chosen (backend/api/app.py's POST /runs
// docstring, spec-010 §8) -- omitted for an ordinary manual run, passed by
// Canvas.tsx so a Run-button submission and a trigger-fired run both land
// under the same graph_id for the watch poll (listRuns) to find uniformly.
export function submitRun(graph: GraphSpec, graphId?: string): Promise<RunSubmitResponse> {
  const query = graphId ? `?graph_id=${encodeURIComponent(graphId)}` : "";
  return request<RunSubmitResponse>(`/runs${query}`, {
    method: "POST",
    body: JSON.stringify(graph),
  });
}

export function pollRun(runId: string): Promise<RunStatusResponse> {
  return request<RunStatusResponse>(`/runs/${encodeURIComponent(runId)}`);
}

// --- spec-009: trigger activation --------------------------------------

export function activateGraph(graphId: string, graph: GraphSpec): Promise<ActivateGraphResponse> {
  return request<ActivateGraphResponse>(`/graphs/${encodeURIComponent(graphId)}/activate`, {
    method: "POST",
    body: JSON.stringify(graph),
  });
}

export function deactivateGraph(graphId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/graphs/${encodeURIComponent(graphId)}/deactivate`, {
    method: "POST",
  });
}

// --- spec-010: run history (Canvas.tsx's watch poll) --------------------

export function listRuns(params: { graph_id?: string; limit?: number }): Promise<RunListResponse> {
  const qs = new URLSearchParams();
  if (params.graph_id) qs.set("graph_id", params.graph_id);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  return request<RunListResponse>(`/runs?${qs.toString()}`);
}

// --- spec-006: named connection profiles -----------------------------

export function fetchConnectionTypes(): Promise<ConnectionTypeInfo[]> {
  return request<ConnectionTypeInfo[]>("/connection-types");
}

export function fetchConnections(): Promise<ConnectionInfo[]> {
  return request<ConnectionInfo[]>("/connections");
}

export function createConnection(
  name: string,
  type: string,
  config: Record<string, unknown>,
): Promise<ConnectionInfo> {
  return request<ConnectionInfo>("/connections", {
    method: "POST",
    body: JSON.stringify({ name, type, config }),
  });
}

// Omit type/config to re-test an already-saved connection by name; pass
// both to test a draft configuration before it's been saved at all (the
// picker's "Test Connection" button, pre-Save).
export function testConnection(
  name: string,
  draft?: { type: string; config: Record<string, unknown> },
): Promise<TestConnectionResponse> {
  return request<TestConnectionResponse>(`/connections/${encodeURIComponent(name)}/test`, {
    method: "POST",
    body: JSON.stringify(draft ?? {}),
  });
}

export function fetchConnectionModels(name: string): Promise<string[]> {
  return request<string[]>(`/connections/${encodeURIComponent(name)}/models`);
}

export async function deleteConnection(name: string): Promise<void> {
  const response = await fetch(`${API_BASE}/connections/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`DELETE /connections/${name} failed (${response.status})`);
  }
}

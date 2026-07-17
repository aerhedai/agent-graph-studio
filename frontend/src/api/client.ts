import type {
  ConnectionInfo,
  ConnectionTypeInfo,
  GraphSpec,
  NodeTypeInfo,
  ResolveSlotsResponse,
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

export function submitRun(graph: GraphSpec): Promise<RunSubmitResponse> {
  return request<RunSubmitResponse>("/runs", {
    method: "POST",
    body: JSON.stringify(graph),
  });
}

export function pollRun(runId: string): Promise<RunStatusResponse> {
  return request<RunStatusResponse>(`/runs/${encodeURIComponent(runId)}`);
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

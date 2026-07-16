import type {
  GraphSpec,
  NodeTypeInfo,
  ResolveSlotsResponse,
  RunStatusResponse,
  RunSubmitResponse,
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

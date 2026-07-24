import type {
  ActivateGraphResponse,
  ActiveGraphInfo,
  ConnectionInfo,
  ConnectionTypeInfo,
  GraphDetail,
  GraphSpec,
  GraphSummary,
  NodeTypeInfo,
  ResolveSlotsResponse,
  RunListResponse,
  RunStatusResponse,
  RunSubmitResponse,
  SettingsResponse,
  TestConnectionResponse,
  UpdateSettingsResponse,
} from "./types";

// No axios -- this project's convention (backend and frontend alike) is to
// keep dependencies minimal; a thin fetch wrapper is enough for 4 endpoints.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

// spec-017: the one shared credential every request needs. Held in
// localStorage -- a deliberate, justified exception to "nothing persists
// across a refresh" (every other piece of canvas state is ephemeral by
// design), since re-entering a login credential every reload would be
// genuinely painful UX, unlike ephemeral canvas state.
const API_KEY_STORAGE_KEY = "agent-graph-studio-api-key";

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE_KEY);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
}

// Thrown on a 401 specifically (missing/wrong credential) -- distinct from
// a generic failure, so Canvas.tsx can catch this one case and show the
// unlock prompt instead of a plain error message.
export class UnauthorizedError extends Error {
  constructor() {
    super("Missing or invalid API key");
  }
}

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders() },
    ...init,
  });
  if (response.status === 401) throw new UnauthorizedError();
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

// spec-019: answers a pending approval-gated tool call from the canvas
// instead of a terminal input() prompt -- see backend/execution/approvals.py.
// `remember`: don't ask again for this same tool for the rest of this run
// (distinct from an mcp_server connection's `trusted` flag, which skips
// asking across every run).
export function resolveApproval(
  runId: string,
  approvalId: string,
  approved: boolean,
  remember = false,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`,
    { method: "POST", body: JSON.stringify({ approved, remember }) },
  );
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

// spec-015: used to check whether a just-reopened saved graph is already
// active, so the canvas can restore that UI state immediately.
export function listActiveGraphs(): Promise<ActiveGraphInfo[]> {
  return request<ActiveGraphInfo[]>("/graphs/active");
}

// --- spec-010: run history (Canvas.tsx's watch poll) --------------------

export function listRuns(params: {
  graph_id?: string;
  status?: string;
  trigger_source?: string;
  limit?: number;
}): Promise<RunListResponse> {
  const qs = new URLSearchParams();
  if (params.graph_id) qs.set("graph_id", params.graph_id);
  if (params.status) qs.set("status", params.status);
  if (params.trigger_source) qs.set("trigger_source", params.trigger_source);
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
    headers: authHeaders(),
  });
  if (response.status === 401) throw new UnauthorizedError();
  if (!response.ok) {
    throw new Error(`DELETE /connections/${name} failed (${response.status})`);
  }
}

// --- spec-015: saved graphs, real server-side graph identity -----------

export function createGraph(name: string, spec: GraphSpec): Promise<GraphDetail> {
  return request<GraphDetail>("/graphs", {
    method: "POST",
    body: JSON.stringify({ name, spec }),
  });
}

export function listGraphs(): Promise<GraphSummary[]> {
  return request<GraphSummary[]>("/graphs");
}

export function getGraph(graphId: string): Promise<GraphDetail> {
  return request<GraphDetail>(`/graphs/${encodeURIComponent(graphId)}`);
}

export function updateGraph(
  graphId: string,
  update: { name?: string; spec?: GraphSpec },
): Promise<GraphDetail> {
  return request<GraphDetail>(`/graphs/${encodeURIComponent(graphId)}`, {
    method: "PUT",
    body: JSON.stringify(update),
  });
}

export async function deleteGraph(graphId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/graphs/${encodeURIComponent(graphId)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (response.status === 401) throw new UnauthorizedError();
  if (!response.ok) {
    throw new Error(`DELETE /graphs/${graphId} failed (${response.status})`);
  }
}

// --- spec-018: the public base URL setting (auto-registered webhooks) ---

export function getSettings(): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings");
}

export function updateSettings(publicBaseUrl: string): Promise<UpdateSettingsResponse> {
  return request<UpdateSettingsResponse>("/settings", {
    method: "PUT",
    body: JSON.stringify({ public_base_url: publicBaseUrl }),
  });
}

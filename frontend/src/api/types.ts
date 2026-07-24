// Mirrors backend/api/schemas.py and backend/execution/trace.py exactly --
// this is the frontend half of the "one schema, no duplicated validation
// logic" payoff (ADR-001, spec-005 §4). Keep in sync by hand for now; there
// is no codegen step in this MVP.

export interface SlotInfo {
  name: string;
  type: Record<string, unknown>; // SlotTypeSpec.model_dump(), e.g. {base: "text", element_type: null}
  required: boolean;
}

export interface SubNodeSlotInfo {
  cardinality: "one" | "zero_or_one" | "many";
  accepts_role: string | null; // null means any node type is accepted (e.g. `tools`)
}

export interface NodeTypeInfo {
  type: string;
  category: string;
  config_schema: JsonSchema;
  dynamic_schema: boolean;
  inputs: SlotInfo[];
  outputs: SlotInfo[];
  // spec-012: cluster-node metadata. `sub_node_slots` is set for root types
  // (e.g. agent's model/memory/tools); `sub_node_role` is set for
  // sub-node-eligible types (e.g. model's "model", the adapters'
  // "trigger_adapter"). A type can have neither (ordinary node types).
  sub_node_slots?: Record<string, SubNodeSlotInfo> | null;
  sub_node_role?: string | null;
  resolve_slots_from_sub_node?: string | null;
  // spec-019: "apps" category grouping. `integration` is the app name
  // (manifest-backed, e.g. "telegram") or an mcp_server connection's own
  // name (dynamically generated). `capability_group` is a curated
  // sub-grouping (e.g. "Messaging") for manifest apps only -- null for
  // generated nodes, which render a flatter Apps -> connection -> tool
  // hierarchy instead of the 3-level Apps -> App -> capability_group one.
  integration?: string | null;
  capability_group?: string | null;
}

export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  default?: unknown;
  $ref?: string;
}

export interface JsonSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  title?: string;
  type?: string;
}

export interface ResolveSlotsResponse {
  inputs: SlotInfo[];
  outputs: SlotInfo[];
}

export interface TokenCost {
  input_tokens: number;
  output_tokens: number;
}

export interface TraceRecord {
  run_id: string;
  node_id: string;
  node_type: string;
  started_at: string;
  finished_at: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  token_cost: TokenCost;
  side_effect: boolean;
  child_traces: TraceRecord[][] | null;
  error: string | null;
}

export interface RunSubmitResponse {
  run_id: string;
  status: string;
}

export interface PendingApprovalInfo {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface RunStatusResponse {
  run_id: string;
  status: "running" | "completed" | "failed";
  running_node_ids: string[];
  active_sub_node_ids: string[];
  // spec-019: any approval-gated tool call currently blocked waiting for a
  // decision -- resolveApproval() answers it. Always empty for a
  // historical run or one that never hit an approval gate.
  pending_approvals: PendingApprovalInfo[];
  trace: TraceRecord[];
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface GraphNodeSpec {
  id: string;
  type: string;
  config: Record<string, unknown>;
}

export interface EdgeEndpoint {
  node: string;
  slot?: string; // absent only on a sub_node-kind edge
}

export interface GraphEdgeSpec {
  kind?: "data" | "sub_node"; // defaults to "data" server-side if omitted
  from: EdgeEndpoint;
  to: EdgeEndpoint;
  slot?: string; // sub_node edges only: which of `to`'s sub-node slots this fills
}

export interface GraphSpec {
  version: string;
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
}

// spec-006: named connection profiles, mirroring backend/api/schemas.py's
// ConnectionTypeInfo/ConnectionInfo/TestConnectionResponse exactly.

export interface ConnectionTypeInfo {
  type: string;
  category: "local" | "cloud";
  config_schema: JsonSchema;
  supports_model_listing: boolean;
}

export interface ConnectionInfo {
  name: string;
  type: string;
}

export interface TestConnectionResponse {
  success: boolean;
  message: string;
}

// spec-015: saved graphs, mirroring backend/api/schemas.py's
// GraphSummary/GraphDetail exactly.

export interface GraphSummary {
  graph_id: string;
  name: string;
  is_active: boolean;
  updated_at: string;
}

export interface GraphDetail {
  graph_id: string;
  name: string;
  spec: GraphSpec;
  is_active: boolean;
}

// spec-018: the one app-level setting needed to auto-register external
// webhooks (Telegram).

export interface SettingsResponse {
  public_base_url: string | null;
}

export interface UpdateSettingsResponse {
  public_base_url: string;
  warning: string | null;
}

// spec-009: trigger activation, mirroring backend/api/schemas.py exactly.

export interface TriggerInfo {
  node_id: string;
  type: "schedule_trigger" | "webhook_trigger";
  endpoint_or_schedule: string;
}

export interface ActivateGraphResponse {
  status: string;
  triggers: TriggerInfo[];
}

// spec-015: used when reopening a saved graph to know if it's already
// active (so the trigger chip/badge reflects that immediately, not just
// after the user clicks Activate again).
export interface ActiveGraphInfo {
  graph_id: string;
  triggers: TriggerInfo[];
}

// spec-010: run history, used by the canvas's background "detect a new run
// for this graph_id" watch poll (Canvas.tsx's watch useEffect).

export interface RunSummary {
  run_id: string;
  graph_id: string | null;
  status: string;
  trigger_source: string;
  started_at: string;
  finished_at: string | null;
}

export interface RunListResponse {
  runs: RunSummary[];
  total: number;
  limit: number;
  offset: number;
}

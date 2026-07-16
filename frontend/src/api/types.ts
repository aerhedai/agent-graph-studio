// Mirrors backend/api/schemas.py and backend/execution/trace.py exactly --
// this is the frontend half of the "one schema, no duplicated validation
// logic" payoff (ADR-001, spec-005 §4). Keep in sync by hand for now; there
// is no codegen step in this MVP.

export interface SlotInfo {
  name: string;
  type: Record<string, unknown>; // SlotTypeSpec.model_dump(), e.g. {base: "text", element_type: null}
  required: boolean;
}

export interface NodeTypeInfo {
  type: string;
  config_schema: JsonSchema;
  dynamic_schema: boolean;
  inputs: SlotInfo[];
  outputs: SlotInfo[];
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

export interface RunStatusResponse {
  run_id: string;
  status: "running" | "completed" | "failed";
  running_node_ids: string[];
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
  slot: string;
}

export interface GraphEdgeSpec {
  from: EdgeEndpoint;
  to: EdgeEndpoint;
}

export interface GraphSpec {
  version: string;
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
}

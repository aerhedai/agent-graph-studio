import type { Edge } from "@xyflow/react";
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec } from "../api/types";
import type { GenericFlowNode } from "../canvas/GenericNode";

// Canvas state -> the exact existing graph JSON format (backend/schema/models.py's
// GraphSpec) -- no separate canvas-native format (spec-005 §4). Canvas-only UI
// state (position, resolved port lists, run status) is intentionally dropped;
// only `id`/`type`/`config` per node and `from`/`to` per edge survive, matching
// what the CLI and API both already accept.
export function nodesAndEdgesToGraphSpec(
  nodes: GenericFlowNode[],
  edges: Edge[],
): GraphSpec {
  const graphNodes: GraphNodeSpec[] = nodes.map((n) => ({
    id: n.id,
    type: n.data.nodeType,
    config: n.data.config,
  }));

  const graphEdges: GraphEdgeSpec[] = edges.map((e) => ({
    from: { node: e.source, slot: e.sourceHandle ?? "" },
    to: { node: e.target, slot: e.targetHandle ?? "" },
  }));

  return { version: "0.1", nodes: graphNodes, edges: graphEdges };
}

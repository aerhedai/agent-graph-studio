import type { Edge } from "@xyflow/react";
import { resolveSlots } from "../api/client";
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, NodeTypeInfo } from "../api/types";
import type { GenericFlowNode, GenericNodeData } from "../canvas/GenericNode";
import { computeLayout } from "./layout";

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

// The reverse direction: a graph JSON file (hand-written or CLI-authored,
// same format either way -- spec-005 §6's round-trip-both-directions
// criterion) -> canvas nodes/edges. Dynamic-schema node types (code,
// mcp_call, fan_out, merge) need a real POST /resolve-slots call per node to
// recover their actual ports, since the file itself doesn't carry them.
export async function graphSpecToNodesAndEdges(
  graph: GraphSpec,
  nodeTypesByName: Record<string, NodeTypeInfo>,
): Promise<{ nodes: GenericFlowNode[]; edges: Edge[] }> {
  const positions = computeLayout(graph);

  const nodes: GenericFlowNode[] = await Promise.all(
    graph.nodes.map(async (n) => {
      const typeInfo = nodeTypesByName[n.type];
      if (!typeInfo) {
        throw new Error(`Unknown node type in loaded graph: '${n.type}'`);
      }

      let inputs = typeInfo.inputs;
      let outputs = typeInfo.outputs;
      if (typeInfo.dynamic_schema) {
        try {
          const resolved = await resolveSlots(n.type, n.config);
          inputs = resolved.inputs;
          outputs = resolved.outputs;
        } catch {
          // Malformed config in the loaded file -- leave ports empty rather
          // than fail the whole load; the config panel still shows the raw
          // config so the user can see/fix it.
          inputs = [];
          outputs = [];
        }
      }

      const data: GenericNodeData = {
        nodeType: n.type,
        config: n.config,
        configSchema: typeInfo.config_schema,
        inputs,
        outputs,
        dynamicSchema: typeInfo.dynamic_schema,
        status: "pending",
      };
      return {
        id: n.id,
        type: "generic",
        position: positions[n.id] ?? { x: 0, y: 0 },
        data,
      } satisfies GenericFlowNode;
    }),
  );

  const edges: Edge[] = graph.edges.map((e, i) => ({
    id: `loaded-${i}-${e.from.node}.${e.from.slot}-${e.to.node}.${e.to.slot}`,
    source: e.from.node,
    sourceHandle: e.from.slot,
    target: e.to.node,
    targetHandle: e.to.slot,
  }));

  return { nodes, edges };
}

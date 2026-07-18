import type { Edge } from "@xyflow/react";
import { resolveSlots } from "../api/client";
import type { GraphEdgeSpec, GraphNodeSpec, GraphSpec, NodeTypeInfo } from "../api/types";
import { SUB_NODE_HANDLE_ID, type GenericFlowNode, type GenericNodeData } from "../canvas/GenericNode";
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

  // spec-012: an edge whose source is the reserved sub-node handle is a
  // `sub_node`-kind edge -- its top-level `slot` is the target root's slot
  // name (the target handle id), and neither endpoint sets its own `slot`
  // (that's only meaningful for ordinary data ports). Every other edge is
  // an ordinary `data` edge, unchanged from before this spec.
  const graphEdges: GraphEdgeSpec[] = edges.map((e) => {
    if (e.sourceHandle === SUB_NODE_HANDLE_ID) {
      return {
        kind: "sub_node",
        slot: e.targetHandle ?? "",
        from: { node: e.source },
        to: { node: e.target },
      };
    }
    return {
      from: { node: e.source, slot: e.sourceHandle ?? "" },
      to: { node: e.target, slot: e.targetHandle ?? "" },
    };
  });

  return { version: "0.1", nodes: graphNodes, edges: graphEdges };
}

// The reverse direction: a graph JSON file (hand-written or CLI-authored,
// same format either way -- spec-005 §6's round-trip-both-directions
// criterion) -> canvas nodes/edges. Dynamic-schema node types (code,
// mcp_call, fan_out, merge) need a real POST /resolve-slots call per node to
// recover their actual ports, since the file itself doesn't carry them.
//
// spec-012: a *cluster root* whose ports mirror a connected sub-node
// (resolve_slots_from_sub_node, e.g. webhook_trigger) is dynamic in the
// same sense, but resolved entirely client-side instead -- the connected
// sub-node's type is always an ordinary static-schema type, so its real
// outputs are already sitting in `nodeTypesByName`, no HTTP round-trip
// needed (unlike code/mcp_call's genuinely config-dependent resolution).
export async function graphSpecToNodesAndEdges(
  graph: GraphSpec,
  nodeTypesByName: Record<string, NodeTypeInfo>,
): Promise<{ nodes: GenericFlowNode[]; edges: Edge[] }> {
  const positions = computeLayout(graph);

  function mirroredOutputsFor(rootId: string, slotName: string) {
    const subEdge = graph.edges.find(
      (e) => e.kind === "sub_node" && e.slot === slotName && e.to.node === rootId,
    );
    if (!subEdge) return [];
    const subNode = graph.nodes.find((n) => n.id === subEdge.from.node);
    if (!subNode) return [];
    return nodeTypesByName[subNode.type]?.outputs ?? [];
  }

  const nodes: GenericFlowNode[] = await Promise.all(
    graph.nodes.map(async (n) => {
      const typeInfo = nodeTypesByName[n.type];
      if (!typeInfo) {
        throw new Error(`Unknown node type in loaded graph: '${n.type}'`);
      }

      let inputs = typeInfo.inputs;
      let outputs = typeInfo.outputs;
      if (typeInfo.resolve_slots_from_sub_node) {
        outputs = mirroredOutputsFor(n.id, typeInfo.resolve_slots_from_sub_node);
      } else if (typeInfo.dynamic_schema) {
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
        subNodeSlots: typeInfo.sub_node_slots ?? null,
        subNodeRole: typeInfo.sub_node_role ?? null,
        resolveSlotsFromSubNode: typeInfo.resolve_slots_from_sub_node ?? null,
      };
      return {
        id: n.id,
        type: "generic",
        position: positions[n.id] ?? { x: 0, y: 0 },
        data,
      } satisfies GenericFlowNode;
    }),
  );

  const edges: Edge[] = graph.edges.map((e, i) => {
    if (e.kind === "sub_node") {
      return {
        id: `loaded-${i}-${e.from.node}.sub_node-${e.to.node}.${e.slot}`,
        source: e.from.node,
        sourceHandle: SUB_NODE_HANDLE_ID,
        target: e.to.node,
        targetHandle: e.slot,
      };
    }
    return {
      id: `loaded-${i}-${e.from.node}.${e.from.slot}-${e.to.node}.${e.to.slot}`,
      source: e.from.node,
      sourceHandle: e.from.slot,
      target: e.to.node,
      targetHandle: e.to.slot,
    };
  });

  return { nodes, edges };
}

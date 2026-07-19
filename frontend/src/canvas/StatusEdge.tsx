import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";
import { SUB_NODE_HANDLE_ID, type NodeStatus } from "./GenericNode";

// spec-013 §5: an edge's visual language is driven by two real facts, never
// a decorative loop -- (1) its *kind*, sub_node edges (sourceHandleId ===
// the reserved SUB_NODE_HANDLE_ID) are always dashed/violet regardless of
// run state, since they're structural/config wiring, not per-run data
// flow; (2) for ordinary data edges, its *target node's* current status
// from the same real polling data GenericNode's own pulse/settle animation
// already uses (Canvas.tsx's statusForNode) -- flowing while the
// downstream node runs, settled to green/red once it finishes.
export type StatusEdgeData = {
  targetStatus: NodeStatus;
};

export type StatusFlowEdge = Edge<StatusEdgeData, "status">;

export function StatusEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  data,
  markerEnd,
}: EdgeProps<StatusFlowEdge>) {
  const [edgePath] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const isSubNode = sourceHandleId === SUB_NODE_HANDLE_ID;
  const targetStatus = data?.targetStatus ?? "pending";
  const className = isSubNode ? "edge-sub-node" : `edge-data edge-data--${targetStatus}`;

  return <BaseEdge id={id} path={edgePath} className={className} markerEnd={markerEnd} />;
}

import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
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

  const className = isSubNode
    ? "stroke-[var(--cat-ai)] [stroke-dasharray:3_5] [stroke-linecap:round]"
    : cn(
        "[transition:stroke_200ms_var(--ease-standard)]",
        targetStatus === "pending" && "stroke-[var(--color-border-strong)]",
        targetStatus === "running" &&
          "animate-edge-flow stroke-[var(--status-running)] [stroke-dasharray:6_6]",
        targetStatus === "success" && "stroke-[var(--status-success)]",
        targetStatus === "error" && "stroke-[var(--status-error)]",
      );
  const strokeWidth = isSubNode ? 2.5 : targetStatus === "running" ? 2 : 1.5;

  return (
    <BaseEdge id={id} path={edgePath} className={className} style={{ strokeWidth }} markerEnd={markerEnd} />
  );
}

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { JsonSchema, SlotInfo } from "../api/types";

// One generic component for every node type -- ports are rendered from
// whatever the /node-types or /resolve-slots response says exist, never
// from a per-type hardcoded component. This is the frontend's own version
// of the pluggability bar the backend registry holds (spec-005 §3).
export type NodeStatus = "pending" | "running" | "success" | "error";

export type GenericNodeData = {
  nodeType: string;
  config: Record<string, unknown>;
  configSchema: JsonSchema;
  inputs: SlotInfo[];
  outputs: SlotInfo[];
  dynamicSchema: boolean;
  status?: NodeStatus;
};

export type GenericFlowNode = Node<GenericNodeData, "generic">;

function slotTop(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`;
}

const PORT_ROW_HEIGHT = 22;

export function GenericNode({ data, selected }: NodeProps<GenericFlowNode>) {
  const { nodeType, inputs, outputs, dynamicSchema, status } = data;
  const portRows = Math.max(inputs.length, outputs.length, 1);
  const bodyHeight = portRows * PORT_ROW_HEIGHT + 8;

  return (
    <div className={`generic-node status-${status ?? "pending"}${selected ? " selected" : ""}`}>
      <div className="generic-node__header">{nodeType}</div>

      <div className="generic-node__body" style={{ height: `${bodyHeight}px` }}>
        {inputs.map((slot, i) => (
          <div
            key={`in-${slot.name}`}
            className="generic-node__port generic-node__port--in"
            style={{ top: slotTop(i, inputs.length) }}
          >
            <Handle id={slot.name} type="target" position={Position.Left} />
            <span className="generic-node__slot-label">{slot.name}</span>
          </div>
        ))}

        {outputs.map((slot, i) => (
          <div
            key={`out-${slot.name}`}
            className="generic-node__port generic-node__port--out"
            style={{ top: slotTop(i, outputs.length) }}
          >
            <span className="generic-node__slot-label">{slot.name}</span>
            <Handle id={slot.name} type="source" position={Position.Right} />
          </div>
        ))}

        {dynamicSchema && inputs.length === 0 && outputs.length === 0 && (
          <div className="generic-node__hint">configure to resolve ports</div>
        )}
      </div>
    </div>
  );
}

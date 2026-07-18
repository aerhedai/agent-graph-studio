import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { JsonSchema, SlotInfo, SubNodeSlotInfo } from "../api/types";

// One generic component for every node type -- ports are rendered from
// whatever the /node-types or /resolve-slots response says exist, never
// from a per-type hardcoded component. This is the frontend's own version
// of the pluggability bar the backend registry holds (spec-005 §3).
export type NodeStatus = "pending" | "running" | "success" | "error";

// spec-012: the reserved handle id for a node's single "usable as a
// sub-node" connector. Every node renders this handle unconditionally
// (any node type can fill a `tools`-shaped slot, whose accepts_role is
// None -- "any node type accepted"); role/cardinality compatibility is
// enforced at connection time (Canvas.tsx's isValidConnection) and at
// graph-validation time (check_sub_node_edges), not by hiding the handle
// for types that wouldn't fit every slot. It plugs upward into whichever
// root it's wired to (n8n's own sub-node convention: children sit below
// their root, connecting into its bottom edge) -- so this handle sits on
// this node's own TOP edge, while a root's own per-slot handles
// (rendered below) sit on ITS bottom edge.
export const SUB_NODE_HANDLE_ID = "__sub_node__";

export type GenericNodeData = {
  nodeType: string;
  config: Record<string, unknown>;
  configSchema: JsonSchema;
  inputs: SlotInfo[];
  outputs: SlotInfo[];
  dynamicSchema: boolean;
  status?: NodeStatus;
  subNodeSlots?: Record<string, SubNodeSlotInfo> | null;
  subNodeRole?: string | null;
  resolveSlotsFromSubNode?: string | null;
};

export type GenericFlowNode = Node<GenericNodeData, "generic">;

function slotTop(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`;
}

function slotLeft(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`;
}

const PORT_ROW_HEIGHT = 22;
const SUB_NODE_ROW_HEIGHT = 20;

export function GenericNode({ data, selected }: NodeProps<GenericFlowNode>) {
  const { nodeType, inputs, outputs, dynamicSchema, status, subNodeSlots } = data;
  const portRows = Math.max(inputs.length, outputs.length, 1);
  const subNodeSlotNames = subNodeSlots ? Object.keys(subNodeSlots) : [];
  const bodyHeight = portRows * PORT_ROW_HEIGHT + 8 + (subNodeSlotNames.length > 0 ? SUB_NODE_ROW_HEIGHT : 0);

  // A "start" node (no data inputs at all -- text_input, schedule_trigger,
  // webhook_trigger) and a "terminator" node (no data outputs at all --
  // text_output) get distinct flowchart-style semicircle-ended shapes.
  // Derived purely from each node's already-known inputs/outputs length,
  // never a hardcoded type-name list, consistent with this project's
  // "palette/canvas never hardcodes node type names" principle. A node
  // with *both* empty (model, memory -- pure sub-node config carriers,
  // no data ports on either side) falls through to the ordinary shape.
  const isStart = inputs.length === 0 && outputs.length > 0;
  const isTerminator = outputs.length === 0 && inputs.length > 0;
  const shapeClass = isStart ? " generic-node--start" : isTerminator ? " generic-node--terminator" : "";

  return (
    <div className={`generic-node status-${status ?? "pending"}${shapeClass}${selected ? " selected" : ""}`}>
      <div className="generic-node__title">{nodeType}</div>

      <div className="generic-node__body" style={{ height: `${bodyHeight}px` }}>
        {/* spec-012: a root's own declared sub-node slots -- visually
            distinct (bottom edge, accent color) from normal left/right
            data ports, and from a sub-node's own connector (top edge,
            below). One target handle per slot, id = the slot name itself,
            which is exactly what a sub_node edge's own top-level `slot`
            field records. */}
        {subNodeSlotNames.map((slotName, i) => (
          <div
            key={`sub-in-${slotName}`}
            className="generic-node__port generic-node__port--sub-node-in"
            style={{ left: slotLeft(i, subNodeSlotNames.length) }}
          >
            <Handle id={slotName} type="target" position={Position.Bottom} />
            <span className="generic-node__slot-label">{slotName}</span>
          </div>
        ))}

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

        {dynamicSchema && inputs.length === 0 && outputs.length === 0 && subNodeSlotNames.length === 0 && (
          <div className="generic-node__hint">configure to resolve ports</div>
        )}

        {/* spec-012: every node's single "plug me into a sub-node slot"
            connector -- top edge, same accent styling as the slot handles
            above, source-typed since a sub-node edge always flows from
            the sub-node into a root's named slot (which now sits on the
            root's bottom edge, directly below where this node would be
            positioned on canvas -- n8n's own child-below-root convention). */}
        <div className="generic-node__port generic-node__port--sub-node-out">
          <Handle id={SUB_NODE_HANDLE_ID} type="source" position={Position.Top} />
        </div>
      </div>
    </div>
  );
}

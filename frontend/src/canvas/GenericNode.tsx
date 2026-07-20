import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Box,
  ChevronDown,
  ChevronRight,
  Database,
  Plug,
  Sparkles,
  Waypoints,
  Wrench,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { createContext, useContext, useState, type CSSProperties } from "react";
import type { JsonSchema, SlotInfo, SubNodeSlotInfo } from "../api/types";

// One generic component for every node type -- ports are rendered from
// whatever the /node-types or /resolve-slots response says exist, never
// from a per-type hardcoded component. This is the frontend's own version
// of the pluggability bar the backend registry holds (spec-005 §3).
export type NodeStatus = "pending" | "running" | "success" | "error";

// spec-012/spec-014: the reserved handle id for a node's single "usable as
// a sub-node" connector. Originally rendered unconditionally on every node
// type (any node could be dragged into a `tools`-shaped slot); spec-014
// removed it from ordinary nodes -- it now renders only on sub-node-role
// types (model/memory/trigger adapters, and the `tool_group` hybrid
// below), since tool-wiring goes through a `tool_group` container instead
// of a bare per-node connector. It plugs upward into whichever root it's
// wired to (n8n's own sub-node convention: children sit below their root,
// connecting into its bottom edge) -- so this handle sits on this node's
// own TOP edge, while a root's own per-slot handles (rendered below) sit
// on ITS bottom edge.
export const SUB_NODE_HANDLE_ID = "__sub_node__";

export type GenericNodeData = {
  nodeType: string;
  category: string;
  config: Record<string, unknown>;
  configSchema: JsonSchema;
  inputs: SlotInfo[];
  outputs: SlotInfo[];
  dynamicSchema: boolean;
  status?: NodeStatus;
  errorMessage?: string | null;
  subNodeSlots?: Record<string, SubNodeSlotInfo> | null;
  subNodeRole?: string | null;
  resolveSlotsFromSubNode?: string | null;
  // spec-014: populated by Canvas.tsx's containment tracking for a
  // `tool_group` (or any future hybrid) node only -- the real, full-state
  // nodes currently contained by this group, for the compact row list.
  // Undefined/empty for every non-group node type. `active` is the live
  // per-call signal (Canvas.tsx's groupContents, driven by
  // run.active_sub_node_ids) -- true only while this specific tool is
  // genuinely mid-call, not just while its parent agent is running.
  containedNodes?: { id: string; nodeType: string; category: string; active: boolean }[];
};

export type GenericFlowNode = Node<GenericNodeData, "generic">;

// spec-013 §5: connection-name -> connection-type lookup, so a node's
// badge can show "which provider" (e.g. "ollama") without GenericNode
// itself fetching /connections -- Canvas.tsx fetches once and provides it,
// the same "fetch at the top, denormalize for presentation" shape already
// used for nodeTypesByName. Defaults to {} so GenericNode never needs a
// null-check at every call site.
export const ConnectionTypeContext = createContext<Record<string, string>>({});

// spec-014: group-card interactions (selecting a contained node so its
// config still opens in the side panel, and removing it back out to a
// free-floating canvas node) live in Canvas.tsx, which owns node/edge
// state -- GenericNode only needs to call back into it. Defaults to no-ops
// so GenericNode never needs a null-check at every call site.
export const GroupActionsContext = createContext<{
  selectNode: (id: string) => void;
  removeFromGroup: (nodeId: string) => void;
}>({ selectNode: () => {}, removeFromGroup: () => {} });

// Presentation-only category -> {icon, css-token} map. This is distinct
// from the "palette derives its section list from the registry's category
// field, never a hardcoded list" decision (spec-013 §4) -- that's about
// which *sections exist*, not which icon/color represents a known section
// once it does exist. An unrecognized future category still renders (Box
// icon, neutral border-strong token) rather than crashing.
export const CATEGORY_PRESENTATION: Record<string, { icon: LucideIcon; colorVar: string; label: string }> = {
  triggers: { icon: Zap, colorVar: "--cat-triggers", label: "Triggers" },
  core: { icon: Waypoints, colorVar: "--cat-core", label: "Core" },
  ai: { icon: Sparkles, colorVar: "--cat-ai", label: "AI" },
  data: { icon: Database, colorVar: "--cat-data", label: "Data" },
  connectivity: { icon: Plug, colorVar: "--cat-connectivity", label: "Connectivity" },
  tools: { icon: Wrench, colorVar: "--cat-tools", label: "Tools" },
};

export function categoryPresentation(category: string) {
  return (
    CATEGORY_PRESENTATION[category] ?? {
      icon: Box,
      colorVar: "--color-border-strong",
      label: category,
    }
  );
}

// Per-type "operation subtitle" deriver -- a client-side presentation
// helper only (not new registry data, per the plan): most types get a
// generic fallback (first string config value), a handful of types with an
// obviously-more-useful single field get a bespoke line instead.
function deriveSubtitle(nodeType: string, config: Record<string, unknown>): string | null {
  switch (nodeType) {
    case "llm_call":
    case "model":
      return typeof config.model === "string" && config.model ? config.model : null;
    case "memory":
      return typeof config.max_messages === "number" ? `last ${config.max_messages} messages` : null;
    case "code": {
      const source = config.function_source;
      if (typeof source !== "string" || !source.trim()) return null;
      return source.trim().split("\n")[0];
    }
    case "conditional_branch":
      return typeof config.condition === "string" && config.condition ? config.condition : null;
    case "mcp_call":
      return typeof config.tool_name === "string" && config.tool_name ? config.tool_name : null;
    default: {
      const firstString = Object.values(config).find(
        (v): v is string => typeof v === "string" && v.trim().length > 0,
      );
      return firstString ?? null;
    }
  }
}

// Sub-node cards (model/memory/trigger adapters -- anything with its own
// sub_node_role) get a single combined meta line instead of the root
// anatomy's separate badge+subtitle, since the compact card (see
// generic-node--subnode below) has no room for a badge: "ollama ·
// qwen2.5:14b" rather than a connection-type pill plus a model-name line.
function deriveSubNodeMeta(
  nodeType: string,
  config: Record<string, unknown>,
  connectionTypeByName: Record<string, string>,
): string | null {
  const connectionName = typeof config.connection === "string" ? config.connection : null;
  const connectionType = connectionName ? connectionTypeByName[connectionName] : undefined;
  const rest = deriveSubtitle(nodeType, config);
  if (connectionType && rest) return `${connectionType} · ${rest}`;
  return connectionType ?? rest;
}

type Badge = { text: string; kind: "connection" | "cluster" };

// Badge priority order (spec-013 §5, resolved): (1) a resolvable
// connection's type -- "which provider"; (2) a cluster-root marker; (3, no
// longer reachable here) a bare sub_node_role -- that case now gets the
// compact subnode card above instead of a badge on the full anatomy.
function deriveBadge(
  config: Record<string, unknown>,
  subNodeSlots: Record<string, SubNodeSlotInfo> | null | undefined,
  connectionTypeByName: Record<string, string>,
): Badge | null {
  const connectionName = typeof config.connection === "string" ? config.connection : null;
  const connectionType = connectionName ? connectionTypeByName[connectionName] : undefined;
  if (connectionType) return { text: connectionType, kind: "connection" };
  if (subNodeSlots && Object.keys(subNodeSlots).length > 0) return { text: "cluster", kind: "cluster" };
  return null;
}

function slotTop(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`;
}

function slotLeft(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`;
}

const PORT_ROW_HEIGHT = 22;
const SUB_NODE_ROW_HEIGHT = 20;

export function GenericNode({ data, selected }: NodeProps<GenericFlowNode>) {
  const {
    nodeType,
    category,
    config,
    inputs,
    outputs,
    dynamicSchema,
    status,
    subNodeSlots,
    subNodeRole,
    errorMessage,
    containedNodes,
  } = data;
  const connectionTypeByName = useContext(ConnectionTypeContext);
  const groupActions = useContext(GroupActionsContext);
  // spec-014: collapsed by default -- a freshly dropped group starts as
  // just an icon + connector until its first tool is dropped onto it.
  // Canvas/presentation-only state, deliberately not persisted (spec-014
  // §4, same treatment as `status`).
  const [collapsed, setCollapsed] = useState(true);

  // spec-014: `tool_group` (and any future container type built the same
  // way) is a "hybrid" node -- simultaneously a root (declares its own
  // sub_node_slots) AND a sub-node (declares its own subNodeRole). Detected
  // generically from those two already-known facts, never a hardcoded
  // `nodeType === "tool_group"` check, so any future hybrid type gets the
  // same collapsible-group treatment automatically.
  const isHybridGroup = Boolean(subNodeRole) && Boolean(subNodeSlots && Object.keys(subNodeSlots).length > 0);

  if (isHybridGroup) {
    const { icon: CategoryIcon, colorVar } = categoryPresentation(category);
    const contents = containedNodes ?? [];
    return (
      <div
        className={`generic-node generic-node--group${collapsed ? " generic-node--group-collapsed" : ""} status-${status ?? "pending"}${selected ? " selected" : ""}`}
        style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
      >
        <Handle id={SUB_NODE_HANDLE_ID} type="source" position={Position.Top} className="generic-node__subnode-pin" />
        <div className="generic-node__group-header" onClick={() => setCollapsed((c) => !c)}>
          <div className="generic-node__icon-chip">
            <CategoryIcon />
          </div>
          <div className="generic-node__titles">
            <div className="generic-node__title">{nodeType}</div>
            <div className="generic-node__subtitle">
              {contents.length} tool{contents.length === 1 ? "" : "s"}
            </div>
          </div>
          <button type="button" className="generic-node__group-toggle" aria-label={collapsed ? "Expand" : "Collapse"}>
            {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
        {!collapsed && (
          <div className="generic-node__group-contents">
            {contents.length === 0 && (
              <div className="generic-node__hint">drop a node here to add it as a tool</div>
            )}
            {contents.map((n) => {
              const { icon: RowIcon, colorVar: rowColorVar } = categoryPresentation(n.category);
              return (
                <div
                  key={n.id}
                  className={`generic-node__group-row${n.active ? " generic-node__group-row--active" : ""}`}
                  style={{ "--node-accent": `var(${rowColorVar})` } as CSSProperties}
                  onClick={(e) => {
                    e.stopPropagation();
                    groupActions.selectNode(n.id);
                  }}
                >
                  <span className="generic-node__group-row-icon">
                    <RowIcon size={14} />
                  </span>
                  <span className="generic-node__group-row-label">{n.nodeType}</span>
                  <button
                    type="button"
                    className="generic-node__group-row-remove"
                    aria-label={`Remove ${n.nodeType} from group`}
                    onClick={(e) => {
                      e.stopPropagation();
                      groupActions.removeFromGroup(n.id);
                    }}
                  >
                    <X size={12} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // Sub-node-role types (model, memory, the trigger adapters) are never
  // wired via ordinary data edges in this canvas -- a sub_node edge only
  // ever uses the single reserved SUB_NODE_HANDLE_ID, and their declared
  // inputs/outputs exist purely so resolve_slots_from_sub_node can read a
  // type-level schema (see webhook_trigger's docstring), not to be drawn as
  // connectable ports here. They get the compact "plugs into a root" card
  // from the approved design mockup instead of the full root-node anatomy.
  if (subNodeRole) {
    const { colorVar } = categoryPresentation(category);
    const meta = deriveSubNodeMeta(nodeType, config, connectionTypeByName);
    return (
      <div
        className={`generic-node generic-node--subnode status-${status ?? "pending"}${selected ? " selected" : ""}`}
        style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
      >
        <Handle id={SUB_NODE_HANDLE_ID} type="source" position={Position.Top} className="generic-node__subnode-pin" />
        <div className="generic-node__title">{nodeType}</div>
        {meta && <div className="generic-node__subtitle">{meta}</div>}
      </div>
    );
  }

  const portRows = Math.max(inputs.length, outputs.length, 1);
  const subNodeSlotNames = subNodeSlots ? Object.keys(subNodeSlots) : [];
  const bodyHeight = portRows * PORT_ROW_HEIGHT + 8 + (subNodeSlotNames.length > 0 ? SUB_NODE_ROW_HEIGHT : 0);

  const { icon: CategoryIcon, colorVar } = categoryPresentation(category);
  const subtitle = deriveSubtitle(nodeType, config);
  const badge = deriveBadge(config, subNodeSlots, connectionTypeByName);

  // A "start" node (no data inputs at all -- text_input, schedule_trigger,
  // webhook_trigger) and a "terminator" node (no data outputs at all --
  // text_output) get distinct flowchart-style semicircle-ended shapes.
  // Derived purely from each node's already-known inputs/outputs length,
  // never a hardcoded type-name list, consistent with this project's
  // "palette/canvas never hardcodes node type names" principle. A node
  // with both empty falls through to the ordinary shape (model/memory
  // never reach this branch at all -- they're handled by the compact
  // subnode card above).
  const isStart = inputs.length === 0 && outputs.length > 0;
  const isTerminator = outputs.length === 0 && inputs.length > 0;
  const shapeClass = isStart ? " generic-node--start" : isTerminator ? " generic-node--terminator" : "";

  return (
    <div
      className={`generic-node status-${status ?? "pending"}${shapeClass}${selected ? " selected" : ""}`}
      style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
    >
      <div className="generic-node__header">
        <div className="generic-node__icon-chip">
          <CategoryIcon />
        </div>
        <div className="generic-node__titles">
          <div className="generic-node__title">{nodeType}</div>
          {subtitle && <div className="generic-node__subtitle">{subtitle}</div>}
        </div>
        {badge && <div className={`generic-node__badge generic-node__badge--${badge.kind}`}>{badge.text}</div>}
      </div>

      {/* spec-013 §7 (resolved open question): a failed node's error
          shows via a short inline hover tooltip for immediate visibility
          -- the full message still lives in the trace inspector panel;
          this is real trace data (Canvas.tsx's errorMessageForNode), not a
          placeholder. */}
      {status === "error" && errorMessage && (
        <div className="generic-node__error-tooltip">{errorMessage}</div>
      )}

      <div className="generic-node__body" style={{ height: `${bodyHeight}px` }}>
        {/* spec-012: a root's own declared sub-node slots -- visually
            distinct (bottom edge, accent color) from normal left/right
            data ports, and from a sub-node's own connector (top edge,
            below). One target handle per slot, id = the slot name itself,
            which is exactly what a sub_node edge's own top-level `slot`
            field records. spec-014: a `cardinality="one"` slot (currently
            only agent's `model`) gets a red required-asterisk next to its
            label, mirroring the same treatment data ports get below. */}
        {subNodeSlotNames.map((slotName, i) => {
          const isRequiredSlot = subNodeSlots?.[slotName]?.cardinality === "one";
          return (
            <div
              key={`sub-in-${slotName}`}
              className="generic-node__port generic-node__port--sub-node-in"
              style={{ left: slotLeft(i, subNodeSlotNames.length) }}
            >
              <Handle id={slotName} type="target" position={Position.Bottom} />
              <span className="generic-node__slot-label">
                {slotName}
                {isRequiredSlot && <span className="generic-node__slot-required-asterisk">*</span>}
              </span>
            </div>
          );
        })}

        {inputs.map((slot, i) => (
          <div
            key={`in-${slot.name}`}
            className={`generic-node__port generic-node__port--in${slot.required ? "" : " generic-node__port--optional"}`}
            style={{ top: slotTop(i, inputs.length) }}
          >
            <Handle id={slot.name} type="target" position={Position.Left} />
            <span className="generic-node__slot-label">
              {slot.name}
              {!slot.required && <span className="generic-node__slot-optional-tag">optional</span>}
            </span>
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
      </div>
    </div>
  );
}

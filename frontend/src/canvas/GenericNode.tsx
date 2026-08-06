import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Blocks,
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
import { cn } from "@/lib/utils";
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
  containedNodes?: { id: string; nodeType: string; category: string; active: boolean; integration?: string | null }[];
  // spec-025: literal values for input slots with no incoming edge, keyed
  // by slot name -- edited in the side ConfigPanel (mirrors how `config`
  // itself is edited, not a per-card inline widget; see ConfigPanel.tsx).
  inputValues?: Record<string, unknown>;
  // spec-025 Phase 5: which of this type's own input slot names have a
  // live-fetched dropdown available (NodeTypeInfo.dynamic_option_slots) --
  // and, for a generated MCP node, which connection to fetch them against
  // (NodeTypeInfo.integration, already the connection's own name for a
  // dynamically-generated type -- see backend/api/schemas.py).
  dynamicOptionSlots?: string[];
  integration?: string | null;
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
  apps: { icon: Blocks, colorVar: "--cat-apps", label: "Apps" },
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

// A node type's `integration` field (set for both manifest-backed app
// types, e.g. "telegram", and dynamically-generated MCP types, e.g.
// "kpidepot" -- see NodeTypeInfo's own docstring) is the connection/app's
// real display name; the raw type string (`mcp__kpidepot__get_kpi`,
// `telegram_chat_management`) is an implementation detail no user should
// ever have to read. Falls back to the raw type for every non-app node,
// completely unaffected.
export function displayName(nodeType: string, integration?: string | null): string {
  return integration ?? nodeType;
}

// The operation displayName() throws away (which specific tool/action this
// is, within its app) -- humanized back into a subtitle. Handles both
// shapes: a generated MCP type's `mcp__<connection>__<tool>` prefix is
// stripped to just the tool name; a manifest type's own semantic type name
// (no such prefix) is humanized as-is. Either way: underscores -> spaces.
export function humanizeOperation(nodeType: string, integration: string): string {
  const mcpPrefix = `mcp__${integration}__`;
  const raw = nodeType.startsWith(mcpPrefix) ? nodeType.slice(mcpPrefix.length) : nodeType;
  return raw.replace(/_/g, " ");
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
  integration?: string | null,
): string | null {
  // Only a *generated* per-tool MCP type (mcp__<connection>__<tool>) needs
  // the operation recovered this way -- its title now shows just the
  // connection name, so the tool it calls would otherwise vanish entirely.
  // A manifest-backed type (e.g. telegram_adapter) already has a real,
  // useful per-instance subtitle below (its own connection field) --
  // overriding that with a humanized restatement of the type name itself
  // ("telegram adapter") would be a strict downgrade, not an improvement.
  if (integration && nodeType.startsWith(`mcp__${integration}__`)) {
    return humanizeOperation(nodeType, integration);
  }
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

// Regular data-port handles: react-flow's own default (a near-black dot
// with a stark white ring) reads as an un-themed foreign element against
// this palette -- overridden via a descendant arbitrary-variant to the same
// muted-dot language as the rest of the anatomy (react-flow injects this
// element itself, so it can't carry a className of our own).
const DATA_HANDLE_CLASSES =
  "[&_.react-flow__handle]:h-[7px] [&_.react-flow__handle]:w-[7px] [&_.react-flow__handle]:border [&_.react-flow__handle]:border-background [&_.react-flow__handle]:bg-border";

// spec-014: an optional data port (InputSlotSpec.required=False) gets a
// visually distinct dashed/hollow ring instead of the solid dot every
// required port uses -- "you can leave this unwired" at a glance.
const OPTIONAL_HANDLE_CLASSES =
  "[&_.react-flow__handle]:border-dashed [&_.react-flow__handle]:bg-transparent";

const SUB_NODE_HANDLE_CLASSES =
  "[&_.react-flow__handle]:h-2.5 [&_.react-flow__handle]:w-2.5 [&_.react-flow__handle]:rounded-full [&_.react-flow__handle]:border-2 [&_.react-flow__handle]:border-[var(--cat-ai)] [&_.react-flow__handle]:bg-card";

// Multi-property transitions with different easing curves per property
// (transform uses --ease-spring, color/shadow use --ease-standard) aren't
// expressible via Tailwind's single-timing-function `transition-*`
// utilities -- kept as one arbitrary CSS-property declaration to preserve
// that distinction exactly, per tokens.css's own "deliberately different
// curves" rationale.
const NODE_TRANSITION =
  "[transition:transform_150ms_var(--ease-spring),box-shadow_150ms_var(--ease-standard),border-color_150ms_var(--ease-standard)]";

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
    integration,
  } = data;
  const title = displayName(nodeType, integration);
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

  const statusRingClass =
    status === "running"
      ? "border-[3px] border-[var(--status-running)] animate-node-breathe"
      : status === "success"
        ? "border-[3px] border-[var(--status-success)] animate-node-settle"
        : status === "error"
          ? "border-[3px] border-[var(--status-error)] animate-node-settle"
          : "";

  if (isHybridGroup) {
    const { icon: CategoryIcon, colorVar } = categoryPresentation(category);
    const contents = containedNodes ?? [];
    return (
      <div
        className={cn(
          "relative overflow-hidden rounded-[var(--radius-md)] border-2 border-border bg-card text-xs shadow-[var(--shadow-md)]",
          collapsed ? "w-[130px] min-w-[130px]" : "w-[150px] min-w-[150px]",
          selected && "border-primary shadow-[0_0_0_3px_color-mix(in_srgb,var(--primary)_25%,transparent)]",
          statusRingClass,
          NODE_TRANSITION,
        )}
        style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
      >
        <Handle
          id={SUB_NODE_HANDLE_ID}
          type="source"
          position={Position.Top}
          className={cn("!top-[-6px] !left-1/2 !-translate-x-1/2", SUB_NODE_HANDLE_CLASSES)}
        />
        <div
          className="flex cursor-pointer items-center gap-2 p-2 select-none"
          onClick={() => setCollapsed((c) => !c)}
        >
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] [&_svg]:h-[15px] [&_svg]:w-[15px]"
            style={{
              background: "color-mix(in srgb, var(--node-accent) 18%, var(--card))",
              color: "var(--node-accent)",
            }}
          >
            <CategoryIcon />
          </div>
          <div className="min-w-0 flex-1">
            <div className="overflow-hidden text-[12.5px] font-semibold text-ellipsis whitespace-nowrap tracking-[-0.01em]">
              {title}
            </div>
            <div className="mt-px text-[10.5px] text-muted-foreground">
              {contents.length} tool{contents.length === 1 ? "" : "s"}
            </div>
          </div>
          <button
            type="button"
            className="flex shrink-0 items-center border-none bg-transparent p-0 text-muted-foreground"
            aria-label={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
        {!collapsed && (
          <div className="flex flex-col gap-[3px] border-t border-border p-2 pt-2">
            {contents.length === 0 && (
              <div className="px-2.5 py-1.5 text-[11px] italic opacity-60">drop a node here to add it as a tool</div>
            )}
            {contents.map((n) => {
              const { icon: RowIcon, colorVar: rowColorVar } = categoryPresentation(n.category);
              return (
                <div
                  key={n.id}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 rounded-[var(--radius-sm)] border border-transparent px-1.5 py-[3px] transition-colors duration-150",
                    n.active && "border-[var(--status-running)] animate-group-row-pulse",
                  )}
                  style={{
                    "--node-accent": `var(${rowColorVar})`,
                    background: n.active
                      ? "color-mix(in srgb, var(--status-running) 18%, var(--popover))"
                      : "color-mix(in srgb, var(--node-accent) 10%, var(--popover))",
                  } as CSSProperties}
                  onClick={(e) => {
                    e.stopPropagation();
                    groupActions.selectNode(n.id);
                  }}
                >
                  <span className="flex shrink-0 items-center" style={{ color: "var(--node-accent)" }}>
                    <RowIcon size={14} />
                  </span>
                  <span className="flex-1 overflow-hidden text-[10.5px] text-ellipsis whitespace-nowrap">
                    {displayName(n.nodeType, n.integration)}
                  </span>
                  <button
                    type="button"
                    className="flex shrink-0 items-center rounded-[var(--radius-sm)] border-none bg-transparent p-0.5 text-muted-foreground transition-colors duration-150 hover:bg-destructive/15 hover:text-destructive"
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
    const meta = deriveSubNodeMeta(nodeType, config, connectionTypeByName, integration);
    return (
      <div
        className={cn(
          "relative min-w-[130px] w-[130px] rounded-[var(--radius-md)] border-2 border-border bg-card p-2 px-3 text-center text-xs shadow-[var(--shadow-md)]",
          selected && "border-primary shadow-[0_0_0_3px_color-mix(in_srgb,var(--primary)_25%,transparent)]",
          statusRingClass,
          NODE_TRANSITION,
        )}
        style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
      >
        <Handle
          id={SUB_NODE_HANDLE_ID}
          type="source"
          position={Position.Top}
          className={cn("!top-[-6px] !left-1/2 !-translate-x-1/2", SUB_NODE_HANDLE_CLASSES)}
        />
        <div className="overflow-hidden text-[11px] font-semibold text-ellipsis whitespace-normal">{title}</div>
        {meta && <div className="mt-0.5 text-[9.5px] whitespace-normal text-muted-foreground">{meta}</div>}
      </div>
    );
  }

  const portRows = Math.max(inputs.length, outputs.length, 1);
  const subNodeSlotNames = subNodeSlots ? Object.keys(subNodeSlots) : [];
  const bodyHeight = portRows * PORT_ROW_HEIGHT + 8 + (subNodeSlotNames.length > 0 ? SUB_NODE_ROW_HEIGHT : 0);

  const { icon: CategoryIcon, colorVar } = categoryPresentation(category);
  // See deriveSubNodeMeta's comment: only a generated mcp__ type needs its
  // operation recovered this way -- a manifest-backed type's own
  // deriveSubtitle fallback (e.g. showing which bot-token connection it
  // uses) is already more useful than restating its own type name.
  const subtitle =
    integration && nodeType.startsWith(`mcp__${integration}__`)
      ? humanizeOperation(nodeType, integration)
      : deriveSubtitle(nodeType, config);
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
  const shapeClass = isStart
    ? "rounded-l-[var(--radius-pill)] rounded-r-[var(--radius-md)]"
    : isTerminator
      ? "rounded-l-[var(--radius-md)] rounded-r-[var(--radius-pill)]"
      : "rounded-[var(--radius-lg)]";

  return (
    <div
      className={cn(
        "group relative w-[220px] min-w-[220px] max-w-[220px] border-2 border-border bg-card text-xs shadow-[var(--shadow-md)] hover:-translate-y-0.5 hover:shadow-[var(--shadow-lg)]",
        shapeClass,
        selected && "border-primary shadow-[0_0_0_3px_color-mix(in_srgb,var(--primary)_25%,transparent)]",
        statusRingClass,
        NODE_TRANSITION,
      )}
      style={{ "--node-accent": `var(${colorVar})` } as CSSProperties}
    >
      <div className="flex items-start gap-2 p-2 px-3">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] [&_svg]:h-[15px] [&_svg]:w-[15px]"
          style={{
            background: "color-mix(in srgb, var(--node-accent) 18%, var(--card))",
            color: "var(--node-accent)",
          }}
        >
          <CategoryIcon />
        </div>
        <div className="min-w-0 flex-1">
          <div className="overflow-hidden text-[12.5px] font-semibold text-ellipsis whitespace-nowrap tracking-[-0.01em]">
            {title}
          </div>
          {subtitle && (
            <div className="mt-px overflow-hidden text-[10.5px] text-ellipsis whitespace-nowrap text-muted-foreground">
              {subtitle}
            </div>
          )}
        </div>
        {badge && (
          <div
            className={cn(
              "max-w-[72px] shrink-0 overflow-hidden rounded-[var(--radius-pill)] px-1.5 py-0.5 text-[9px] font-bold text-ellipsis whitespace-nowrap uppercase tracking-[0.03em]",
              badge.kind === "cluster" &&
                "border border-[var(--color-border-strong)] bg-foreground/12 text-muted-foreground",
            )}
            style={
              badge.kind === "connection"
                ? {
                    background: "color-mix(in srgb, var(--node-accent) 20%, var(--card))",
                    color: "var(--node-accent)",
                    border: "1px solid color-mix(in srgb, var(--node-accent) 45%, transparent)",
                  }
                : undefined
            }
          >
            {badge.text}
          </div>
        )}
      </div>

      {/* spec-013 §7 (resolved open question): a failed node's error shows
          via a short inline hover tooltip for immediate visibility -- the
          full message still lives in the trace inspector panel; this is
          real trace data (Canvas.tsx's errorMessageForNode), not a
          placeholder. `group`/`group-hover` replaces the old
          `.generic-node:hover .generic-node__error-tooltip` cascade. */}
      {status === "error" && errorMessage && (
        <div
          className={cn(
            "pointer-events-none absolute bottom-full left-1/2 z-10 max-w-[260px] -translate-x-1/2 translate-y-1 rounded-[var(--radius-sm)] border bg-popover px-3 py-2 text-[11px] leading-[1.4] whitespace-normal text-[var(--status-error)] opacity-0 transition-[opacity,transform] duration-150 group-hover:translate-y-0 group-hover:opacity-100",
          )}
          style={{ marginBottom: "8px", borderColor: "color-mix(in srgb, var(--status-error) 45%, transparent)" }}
        >
          {errorMessage}
        </div>
      )}

      <div className="relative" style={{ height: `${bodyHeight}px` }}>
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
              className={cn(
                "absolute bottom-[-20px] flex -translate-x-1/2 flex-col-reverse items-center whitespace-nowrap",
                SUB_NODE_HANDLE_CLASSES,
              )}
              style={{ left: slotLeft(i, subNodeSlotNames.length) }}
            >
              <Handle id={slotName} type="target" position={Position.Bottom} />
              <span className="mt-0.5 text-[9px] text-muted-foreground">
                {slotName}
                {isRequiredSlot && <span className="ml-0.5 font-bold text-[var(--status-error)]">*</span>}
              </span>
            </div>
          );
        })}

        {inputs.map((slot, i) => (
          <div
            key={`in-${slot.name}`}
            className={cn(
              "absolute left-[-4px] flex -translate-y-1/2 items-center whitespace-nowrap",
              DATA_HANDLE_CLASSES,
              !slot.required && OPTIONAL_HANDLE_CLASSES,
            )}
            style={{ top: slotTop(i, inputs.length) }}
          >
            <Handle id={slot.name} type="target" position={Position.Left} />
            <span className="mx-1.5 text-[10.5px] text-muted-foreground">
              {slot.name}
              {!slot.required && <span className="ml-[3px] text-[9px] italic opacity-60">optional</span>}
            </span>
          </div>
        ))}

        {outputs.map((slot, i) => (
          <div
            key={`out-${slot.name}`}
            className={cn(
              "absolute right-[-4px] flex flex-row-reverse -translate-y-1/2 items-center whitespace-nowrap",
              DATA_HANDLE_CLASSES,
            )}
            style={{ top: slotTop(i, outputs.length) }}
          >
            <span className="mx-1.5 text-[10.5px] text-muted-foreground">{slot.name}</span>
            <Handle id={slot.name} type="source" position={Position.Right} />
          </div>
        ))}

        {dynamicSchema && inputs.length === 0 && outputs.length === 0 && subNodeSlotNames.length === 0 && (
          <div className="px-2.5 py-1.5 text-[11px] italic opacity-60">configure to resolve ports</div>
        )}
      </div>
    </div>
  );
}

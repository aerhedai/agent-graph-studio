import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useUpdateNodeInternals,
  type Connection,
  type Edge,
  type IsValidConnection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Download, History as HistoryIcon, KeyRound, MoreHorizontal, Play, Settings as SettingsIcon, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import {
  activateGraph,
  createGraph,
  deactivateGraph,
  fetchConnections,
  fetchNodeTypes,
  getApiKey,
  getGraph,
  googleLoginUrl,
  listActiveGraphs,
  listGraphs,
  listRuns,
  NeedsConnectionMappingError,
  pollRun,
  resolveApproval,
  setApiKey,
  setConnectionMapping,
  submitRun,
  UnauthorizedError,
  updateGraph,
} from "../api/client";
import type {
  ConnectionSlotSpec,
  GraphSpec,
  GraphSummary,
  MissingSlotInfo,
  NodeTypeInfo,
  RunStatusResponse,
  TriggerInfo,
} from "../api/types";
import { graphSpecToNodesAndEdges, nodesAndEdgesToGraphSpec } from "../graph/serialize";
import { HistoryPanel } from "../panels/HistoryPanel";
import { InvokeKeysPanel } from "../panels/InvokeKeysPanel";
import { SettingsPanel } from "../panels/SettingsPanel";
import { NodeInspectorPanel } from "../panels/NodeInspectorPanel";
import {
  ConnectionTypeContext,
  GenericNode,
  GroupActionsContext,
  SUB_NODE_HANDLE_ID,
  type GenericFlowNode,
  type GenericNodeData,
} from "./GenericNode";
import { Palette } from "./Palette";
import { QuickAddSearch } from "./QuickAddSearch";
import { StatusEdge } from "./StatusEdge";
import { errorMessageForNode, findTraceRecord, statusForNode } from "./traceStatus";
import { slotTypesCompatible } from "./typeCompat";

const nodeTypes = { generic: GenericNode };
const edgeTypes = { status: StatusEdge };
const POLL_INTERVAL_MS = 500;
// Lighter than POLL_INTERVAL_MS -- this is a passive background check for
// "has a new run appeared for this graph_id", not a foreground wait.
const WATCH_INTERVAL_MS = 1750;

// Node types and connection badges change rarely (a human took some action
// in a connections panel), not continuously -- a much longer interval than
// WATCH_INTERVAL_MS is deliberate, trading a little staleness for not
// hammering the backend on every tick for data that's usually unchanged.
const CAPABILITIES_REFRESH_INTERVAL_MS = 20000;

let idCounter = 0;
function nextNodeId(typeName: string): string {
  idCounter += 1;
  return `${typeName}_${idCounter}`;
}

// spec-014: a "hybrid" node (e.g. `tool_group`) is simultaneously a root
// (declares sub_node_slots) and a sub-node (declares subNodeRole) --
// detected generically from those two already-known facts, never a
// hardcoded `nodeType === "tool_group"` check, so any future hybrid
// container type gets the same drop-to-contain treatment automatically.
function isHybridNode(node: GenericFlowNode): boolean {
  return Boolean(node.data.subNodeRole) && Boolean(node.data.subNodeSlots && Object.keys(node.data.subNodeSlots).length > 0);
}

// Real rendered dimensions once @xyflow/react has measured the node
// (`node.measured`, populated after first paint); a sensible fallback
// before that first measurement lands, matching this card's own CSS
// (`.generic-node` min-width: 220px).
function hybridNodeBounds(node: GenericFlowNode) {
  return {
    x: node.position.x,
    y: node.position.y,
    width: node.measured?.width ?? 220,
    height: node.measured?.height ?? 60,
  };
}

function nodeCenter(node: GenericFlowNode): { x: number; y: number } {
  const width = node.measured?.width ?? 220;
  const height = node.measured?.height ?? 60;
  return { x: node.position.x + width / 2, y: node.position.y + height / 2 };
}

function pointInHybridBounds(point: { x: number; y: number }, node: GenericFlowNode): boolean {
  const b = hybridNodeBounds(node);
  return point.x >= b.x && point.x <= b.x + b.width && point.y >= b.y && point.y <= b.y + b.height;
}

function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function CanvasInner() {
  const [nodes, setNodes, onNodesChange] = useNodesState<GenericFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [run, setRun] = useState<RunStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  // spec-019: "don't ask again this run" checkbox state, per pending
  // approval_id -- purely local UI state read at click-time by
  // handleResolveApproval, not persisted anywhere itself.
  const [rememberApproval, setRememberApproval] = useState<Record<string, boolean>>({});
  const [nodeTypesByName, setNodeTypesByName] = useState<Record<string, NodeTypeInfo>>({});
  // spec follow-up ("100s of nodes" discoverability): the ComfyUI-style
  // canvas-native quick-add -- screen-space anchor for the popover's own
  // position, flow-space position for where the chosen node actually gets
  // created (both captured at the same double-click, see
  // handlePaneClickForQuickAdd below). Null anchor means closed.
  const [quickAddAnchor, setQuickAddAnchor] = useState<{ x: number; y: number } | null>(null);
  const [quickAddFlowPosition, setQuickAddFlowPosition] = useState<{ x: number; y: number } | null>(null);
  const lastPaneClickRef = useRef<{ time: number; x: number; y: number } | null>(null);
  const [connectionTypeByName, setConnectionTypeByName] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  // spec-017/020: gate the whole canvas behind a sign-in prompt until a
  // credential is present -- starts true if nothing's stored yet; also
  // flips back to true if any request comes back 401 (expired/revoked
  // session). spec-020 replaced the password-style key entry with a real
  // Google sign-in redirect; the shared API key still works as a fallback
  // credential server-side (webhooks), it's just no longer typed in here.
  const [needsUnlock, setNeedsUnlock] = useState<boolean>(() => getApiKey() === null);
  const [unlockError, setUnlockError] = useState<string | null>(null);
  // spec-015: a graph's real identity now lives server-side (graphs_store),
  // not a client-only session UUID (that was spec-009's original
  // stopgap -- GraphSpec had no server identity anywhere at the time).
  // `savedGraphId` is null until the graph has been saved at least once
  // (explicitly via Save, or implicitly the first time Activate is
  // clicked); once set, it's stable across a reload as long as the same
  // saved graph is reopened via the Load-by-name UI, unlike the old
  // fresh-UUID-per-session scheme.
  const [savedGraphId, setSavedGraphId] = useState<string | null>(null);
  const [graphName, setGraphName] = useState<string>("Untitled");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedGraphs, setSavedGraphs] = useState<GraphSummary[]>([]);
  // spec-021: "shared" lets a non-author user run this graph with their own
  // connections instead of the author's, once they've mapped each declared
  // slot -- see connectionSlots/missingSlots below.
  const [sharing, setSharing] = useState<"private" | "shared">("private");
  const [connectionSlots, setConnectionSlots] = useState<ConnectionSlotSpec[]>([]);
  const [showSharingPanel, setShowSharingPanel] = useState(false);
  // Populated when submitRun comes back 409 -- the exact slots this caller
  // still needs to map before this shared graph will run for them.
  const [missingSlots, setMissingSlots] = useState<MissingSlotInfo[]>([]);
  const [slotMappingDrafts, setSlotMappingDrafts] = useState<Record<string, string>>({});
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [mappingInProgress, setMappingInProgress] = useState(false);
  // spec-021: result of an mcp_server connection's own OAuth connect flow,
  // read from the return redirect's URL fragment (see the effect below).
  const [mcpOAuthMessage, setMcpOAuthMessage] = useState<string | null>(null);
  const [activation, setActivation] = useState<"inactive" | "activating" | "active" | "deactivating">(
    "inactive",
  );
  const [activationError, setActivationError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showInvokeKeys, setShowInvokeKeys] = useState(false);
  const [triggers, setTriggers] = useState<TriggerInfo[] | null>(null);
  const { screenToFlowPosition } = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const pollTimeoutRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Dedupe guards shared by handleRun and the watch loop below --
  // activeRunIdRef is whatever run_id is currently attached/polling;
  // lastSeenRunIdRef is whatever run_id either path has already reacted to
  // (stamped before the activeRunIdRef check so a same-tick race between
  // the two entry points never double-attaches).
  const activeRunIdRef = useRef<string | null>(null);
  const lastSeenRunIdRef = useRef<string | null>(null);

  // spec-020: the Google OAuth callback redirects back here carrying the
  // freshly-issued session JWT (or a rejection reason) in the URL fragment,
  // never a query param -- fragments never reach any server/proxy, so the
  // token can't end up in an access log. Runs once on mount, independent of
  // needsUnlock (it's what flips needsUnlock false in the first place).
  useEffect(() => {
    if (!window.location.hash) return;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("token");
    const authError = params.get("auth_error");
    // spec-021: an mcp_server connection's own OAuth connect flow
    // (ConnectionPicker's "Connect" button) returns here the same way --
    // a real browser redirect, fragment-delivered, never a query param.
    const mcpOAuthConnected = params.get("mcp_oauth_connected");
    const mcpOAuthError = params.get("mcp_oauth_error");
    if (token) {
      setApiKey(token);
      setUnlockError(null);
      setNeedsUnlock(false);
    } else if (authError) {
      setUnlockError(
        authError === "not_invited"
          ? "That Google account hasn't been invited to Agent Graph Studio."
          : `Sign-in failed: ${authError}`,
      );
    } else if (mcpOAuthConnected) {
      setMcpOAuthMessage(`"${mcpOAuthConnected}" connected -- its tools are ready to use.`);
    } else if (mcpOAuthError) {
      setMcpOAuthMessage(`Connecting failed: ${mcpOAuthError}`);
    }
    if (token || authError || mcpOAuthConnected || mcpOAuthError) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, []);

  // Bug fix: both of these used to fetch exactly once, on mount, with
  // nothing anywhere ever refetching them -- so anything that changes
  // what node types or connections exist (bootstrapping catalog nodes,
  // connecting/reconnecting an app, promoting a connection to global, or
  // even just a change made from a *different* browser tab/session) never
  // showed up here without a full page reload. This is the generic
  // "whenever I do anything, the site needs refreshing" report: unlike
  // run status (which already polls, see WATCH_INTERVAL_MS below), no
  // other piece of app-wide state had any refresh mechanism at all.
  // Polled on a much longer interval than run status -- this data changes
  // rarely (a human took some action in a connections panel somewhere),
  // not continuously -- rather than trying to thread an explicit
  // "something changed" callback through every single mutating action
  // across ConnectionPicker/SettingsPanel/InvokeKeysPanel, which is easy
  // to miss a spot on (this bug is exactly that: several places already
  // correctly refresh their own local list, e.g. ConnectionPicker's own
  // connections dropdown, but nothing told Canvas about it too).
  useEffect(() => {
    if (needsUnlock) return;

    function loadNodeTypes() {
      fetchNodeTypes()
        .then((types) => setNodeTypesByName(Object.fromEntries(types.map((t) => [t.type, t]))))
        .catch((e: unknown) => {
          if (e instanceof UnauthorizedError) {
            setUnlockError("Your session expired -- please sign in again.");
            setNeedsUnlock(true);
          } else {
            setLoadError(String(e));
          }
        });
    }

    function loadConnectionBadges() {
      // spec-013 §5: a node's badge shows its connection's *type* (e.g.
      // "ollama"), not just its name -- presentation-only lookup, so a
      // fetch failure here shouldn't block the canvas from working; nodes
      // simply render without a connection badge.
      fetchConnections()
        .then((connections) =>
          setConnectionTypeByName(Object.fromEntries(connections.map((c) => [c.name, c.type]))),
        )
        .catch((e: unknown) => {
          if (e instanceof UnauthorizedError) {
            setUnlockError("Your session expired -- please sign in again.");
            setNeedsUnlock(true);
          } else {
            console.error("Failed to load connections for node badges:", e);
          }
        });
    }

    loadNodeTypes();
    loadConnectionBadges();
    const id = window.setInterval(() => {
      loadNodeTypes();
      loadConnectionBadges();
    }, CAPABILITIES_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [needsUnlock]);

  useEffect(() => {
    return () => {
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (needsUnlock) return;
    listGraphs()
      .then(setSavedGraphs)
      .catch((e: unknown) => {
        if (e instanceof UnauthorizedError) {
          setUnlockError("Your session expired -- please sign in again.");
          setNeedsUnlock(true);
        } else {
          console.error("Failed to load saved graphs list:", e);
        }
      });
  }, [needsUnlock]);

  // Shared by both drop-from-palette (onDrop below) and the quick-add
  // search (QuickAddSearch.tsx, spec follow-up to the "100s of nodes"
  // discoverability request) -- the only difference between the two entry
  // points is how `position` was computed, everything about actually
  // creating and drop-to-containing the node is identical.
  const addNodeAtPosition = useCallback(
    (nodeTypeInfo: NodeTypeInfo, position: { x: number; y: number }) => {
      const id = nextNodeId(nodeTypeInfo.type);
      const data: GenericNodeData = {
        nodeType: nodeTypeInfo.type,
        category: nodeTypeInfo.category,
        config: {},
        configSchema: nodeTypeInfo.config_schema,
        inputs: nodeTypeInfo.dynamic_schema ? [] : nodeTypeInfo.inputs,
        outputs: nodeTypeInfo.dynamic_schema ? [] : nodeTypeInfo.outputs,
        dynamicSchema: nodeTypeInfo.dynamic_schema,
        status: "pending",
        subNodeSlots: nodeTypeInfo.sub_node_slots ?? null,
        subNodeRole: nodeTypeInfo.sub_node_role ?? null,
        resolveSlotsFromSubNode: nodeTypeInfo.resolve_slots_from_sub_node ?? null,
        inputValues: {},
        dynamicOptionSlots: nodeTypeInfo.dynamic_option_slots ?? [],
        integration: nodeTypeInfo.integration ?? null,
      };
      const newNode: GenericFlowNode = { id, type: "generic", position, data };
      setNodes((nds) => [...nds, newNode]);

      // spec-014 §4: the interaction is drop-to-contain, not manual
      // wire-dragging -- dropping a node straight onto a hybrid group's
      // card (tool_group) immediately wires it in as that group's
      // sub-node, via an ordinary sub_node edge (kind: "sub_node", slot:
      // "tools") under the hood. Hit-tested against every currently
      // rendered hybrid node's real bounds; the first slot whose
      // accepts_role matches (or accepts any role) is used.
      const targetGroup = nodes.find((n) => isHybridNode(n) && pointInHybridBounds(position, n));
      if (targetGroup) {
        const slotName = Object.entries(targetGroup.data.subNodeSlots ?? {}).find(
          ([, slot]) => slot.accepts_role === null || slot.accepts_role === (nodeTypeInfo.sub_node_role ?? null),
        )?.[0];
        if (slotName) {
          setEdges((eds) =>
            addEdge(
              {
                source: id,
                sourceHandle: SUB_NODE_HANDLE_ID,
                target: targetGroup.id,
                targetHandle: slotName,
                type: "status",
                data: { targetStatus: "pending" },
              } as Connection,
              eds,
            ),
          );
        }
      }
    },
    [setNodes, setEdges, nodes],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const raw = event.dataTransfer.getData("application/x-node-type");
      if (!raw) return;
      const nodeTypeInfo = JSON.parse(raw) as NodeTypeInfo;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addNodeAtPosition(nodeTypeInfo, position);
    },
    [screenToFlowPosition, addNodeAtPosition],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  // ComfyUI-style canvas-native quick-add: double-click empty canvas opens
  // a floating fuzzy search right at the cursor (QuickAddSearch.tsx).
  // React Flow has no onPaneDoubleClick of its own, so double-click is
  // detected manually here -- two onPaneClick firings within 350ms at
  // essentially the same screen position. Deliberately reuses onPaneClick
  // rather than a separate native onDoubleClick listener on the wrapper
  // div, since onPaneClick already only fires for genuine empty-canvas
  // clicks (never node/edge clicks), which is exactly the "empty canvas"
  // gate this needs.
  const DOUBLE_CLICK_MS = 350;
  const DOUBLE_CLICK_SLOP_PX = 6;
  const handlePaneClick = useCallback(
    (event: React.MouseEvent) => {
      setSelectedNodeId(null);
      const now = Date.now();
      const prev = lastPaneClickRef.current;
      lastPaneClickRef.current = { time: now, x: event.clientX, y: event.clientY };
      if (
        prev &&
        now - prev.time < DOUBLE_CLICK_MS &&
        Math.abs(event.clientX - prev.x) < DOUBLE_CLICK_SLOP_PX &&
        Math.abs(event.clientY - prev.y) < DOUBLE_CLICK_SLOP_PX
      ) {
        lastPaneClickRef.current = null;
        setQuickAddFlowPosition(screenToFlowPosition({ x: event.clientX, y: event.clientY }));
        setQuickAddAnchor({ x: event.clientX, y: event.clientY });
      }
    },
    [screenToFlowPosition],
  );

  // Client-side typed edge validation (spec-005 §3): reject an incompatible
  // connection at connection time, in the UI itself, mirroring the backend's
  // own "validate at connection time, not just runtime" principle
  // (CLAUDE.md). Also enforces the data model's "one edge per input slot"
  // invariant, which the backend's own graph schema assumes.
  //
  // spec-012: a connection whose source is the reserved sub-node handle is
  // a `sub_node`-kind attempt (e.g. wiring a `model` node into an `agent`'s
  // `model` slot) -- validated against the target's declared sub_node_slots
  // (slot exists, role compatible, cardinality not yet exceeded) instead of
  // slotTypesCompatible, which only makes sense for typed data ports.
  // Backstopped server-side by check_sub_node_edges either way (per this
  // spec's own resolved open question: both, not either/or).
  const isValidConnection: IsValidConnection = useCallback(
    (connection) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);
      if (!sourceNode || !targetNode) return false;

      if (connection.sourceHandle === SUB_NODE_HANDLE_ID) {
        const slot = targetNode.data.subNodeSlots?.[connection.targetHandle ?? ""];
        if (!slot) return false;
        if (slot.accepts_role !== null && sourceNode.data.subNodeRole !== slot.accepts_role) {
          return false;
        }
        if (slot.cardinality !== "many") {
          const alreadyFilled = edges.some(
            (e) =>
              e.target === connection.target &&
              e.targetHandle === connection.targetHandle &&
              e.sourceHandle === SUB_NODE_HANDLE_ID,
          );
          if (alreadyFilled) return false;
        }
        return true;
      }

      const alreadyConnected = edges.some(
        (e) => e.target === connection.target && e.targetHandle === connection.targetHandle,
      );
      if (alreadyConnected) return false;

      const outputSlot = sourceNode.data.outputs.find((s) => s.name === connection.sourceHandle);
      const inputSlot = targetNode.data.inputs.find((s) => s.name === connection.targetHandle);
      if (!outputSlot || !inputSlot) return false;

      return slotTypesCompatible(outputSlot.type, inputSlot.type);
    },
    [nodes, edges],
  );

  // spec-012: connecting a sub-node into a root whose outputs mirror that
  // slot (resolve_slots_from_sub_node, e.g. webhook_trigger + an adapter)
  // must update the root's rendered output ports immediately, not only on
  // the next full graph load -- otherwise a freshly-wired adapter's real
  // ports (payload, or message_text/sender_id/chat_id) never appear until
  // you save and reload. updateNodeInternals() is the same "tell
  // @xyflow/react a node's handles changed after mount" call already used
  // by onConfigChange below, for the same underlying reason (Phase 2's
  // stale-handle finding from spec-005).
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, type: "status", data: { targetStatus: "pending" } }, eds));

      if (connection.sourceHandle === SUB_NODE_HANDLE_ID) {
        const targetNode = nodes.find((n) => n.id === connection.target);
        const sourceNode = nodes.find((n) => n.id === connection.source);
        const typeInfo = targetNode ? nodeTypesByName[targetNode.data.nodeType] : undefined;
        if (targetNode && sourceNode && typeInfo?.resolve_slots_from_sub_node === connection.targetHandle) {
          setNodes((nds) =>
            nds.map((n) =>
              n.id === targetNode.id ? { ...n, data: { ...n.data, outputs: sourceNode.data.outputs } } : n,
            ),
          );
          window.setTimeout(() => updateNodeInternals(targetNode.id), 0);
        }
      }
    },
    [setEdges, setNodes, nodes, nodeTypesByName, updateNodeInternals],
  );

  // spec-014 §4: dragging an already-on-canvas free node onto a hybrid
  // group's card contains it exactly the same way a fresh palette drop
  // does (see onDrop above) -- both are the same "drop-to-contain"
  // interaction, just for a node that already exists vs. one just created.
  const onNodeDragStop = useCallback(
    (_event: MouseEvent | TouchEvent, draggedNode: GenericFlowNode) => {
      if (isHybridNode(draggedNode)) return; // no nested groups in this pass (spec-014 §3)
      const alreadyContained = edges.some(
        (e) => e.source === draggedNode.id && e.sourceHandle === SUB_NODE_HANDLE_ID,
      );
      if (alreadyContained) return;
      const center = nodeCenter(draggedNode);
      const targetGroup = nodes.find(
        (n) => n.id !== draggedNode.id && isHybridNode(n) && pointInHybridBounds(center, n),
      );
      if (!targetGroup) return;
      const slotName = Object.entries(targetGroup.data.subNodeSlots ?? {}).find(
        ([, slot]) => slot.accepts_role === null || slot.accepts_role === (draggedNode.data.subNodeRole ?? null),
      )?.[0];
      if (!slotName) return;
      setEdges((eds) =>
        addEdge(
          {
            source: draggedNode.id,
            sourceHandle: SUB_NODE_HANDLE_ID,
            target: targetGroup.id,
            targetHandle: slotName,
            type: "status",
            data: { targetStatus: "pending" },
          } as Connection,
          eds,
        ),
      );
    },
    [nodes, edges, setEdges],
  );

  // spec-014: removes a tool from its group, re-materializing it as an
  // ordinary, independently positioned canvas node (the underlying node
  // was never actually deleted from state -- only its containing
  // sub_node edge is). Nudged clear of the group card so it doesn't land
  // invisibly stacked underneath it.
  const removeFromGroup = useCallback(
    (nodeId: string) => {
      const groupEdge = edges.find((e) => e.source === nodeId && e.sourceHandle === SUB_NODE_HANDLE_ID);
      const group = groupEdge ? nodes.find((n) => n.id === groupEdge.target) : undefined;
      setEdges((eds) => eds.filter((e) => !(e.source === nodeId && e.sourceHandle === SUB_NODE_HANDLE_ID)));
      if (group) {
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId ? { ...n, position: { x: group.position.x + 260, y: group.position.y } } : n,
          ),
        );
      }
    },
    [edges, nodes, setEdges, setNodes],
  );

  // spec-014 §4: containment is derived entirely from the graph's own
  // sub_node edges (source = the contained tool, sourceHandle =
  // SUB_NODE_HANDLE_ID, target = a hybrid group node) -- never a separate
  // "which group am I in" field on node state, so save/load and the
  // rendered canvas can never drift out of sync with each other.
  const containedBy = useMemo(() => {
    const map: Record<string, string> = {};
    for (const e of edges) {
      if (e.sourceHandle !== SUB_NODE_HANDLE_ID) continue;
      const target = nodes.find((n) => n.id === e.target);
      if (target && isHybridNode(target)) map[e.source] = e.target;
    }
    return map;
  }, [edges, nodes]);

  // spec-013/014 + live sub-node activity: a contained tool's row lights
  // up while it's genuinely mid-call (`run.active_sub_node_ids`, set by
  // agent.py's _notify_sub_node_activity), re-derived every poll tick
  // (~500ms) so this is the actual "live" cadence, not a one-shot
  // snapshot -- see traceStatus.ts's statusForNode for the same signal
  // applied to a root/sub-node card.
  const groupContents = useMemo(() => {
    const activeIds = new Set(run?.active_sub_node_ids ?? []);
    const map: Record<
      string,
      { id: string; nodeType: string; category: string; active: boolean; integration?: string | null }[]
    > = {};
    for (const [childId, groupId] of Object.entries(containedBy)) {
      const child = nodes.find((n) => n.id === childId);
      if (!child) continue;
      (map[groupId] ??= []).push({
        id: child.id,
        nodeType: child.data.nodeType,
        category: child.data.category,
        active: activeIds.has(child.id),
        integration: child.data.integration,
      });
    }
    return map;
  }, [containedBy, nodes, run]);

  // The rendered node/edge lists React Flow actually draws: a contained
  // tool is hidden entirely (represented only by its row inside the
  // group's card instead), and a hybrid node's data is enriched with its
  // real current contents. Full `nodes`/`edges` state remains the
  // save/load source of truth untouched (spec-014 §4).
  const visibleNodes = useMemo(
    () =>
      nodes
        .filter((n) => !(n.id in containedBy))
        .map((n) =>
          isHybridNode(n) ? { ...n, data: { ...n.data, containedNodes: groupContents[n.id] ?? [] } } : n,
        ),
    [nodes, containedBy, groupContents],
  );

  const visibleEdges = useMemo(
    () => edges.filter((e) => !(e.source in containedBy) && !(e.target in containedBy)),
    [edges, containedBy],
  );

  // --- run + live trace polling (spec-005 §4/§6) -----------------------
  const applyRunToNodes = useCallback(
    (nextRun: RunStatusResponse) => {
      setNodes((nds) =>
        nds.map((n) => ({
          ...n,
          data: {
            ...n.data,
            status: statusForNode(n.id, nextRun),
            errorMessage: errorMessageForNode(n.id, nextRun),
          },
        })),
      );
    },
    [setNodes],
  );

  // spec-013 §5: a data edge's color/animation mirrors its *target* node's
  // real current status -- the exact same statusForNode fact GenericNode's
  // own pulse/settle animation is driven by, never a separate/decorative
  // signal. sub_node edges ignore this entirely (StatusEdge always renders
  // them dashed/violet regardless of targetStatus).
  const applyRunToEdges = useCallback(
    (nextRun: RunStatusResponse) => {
      setEdges((eds) =>
        eds.map((e) => ({ ...e, data: { ...e.data, targetStatus: statusForNode(e.target, nextRun) } })),
      );
    },
    [setEdges],
  );

  const pollUntilDone = useCallback(
    (runId: string) => {
      pollRun(runId)
        .then((status) => {
          setRun(status);
          applyRunToNodes(status);
          applyRunToEdges(status);
          if (status.status === "running") {
            pollTimeoutRef.current = window.setTimeout(() => pollUntilDone(runId), POLL_INTERVAL_MS);
          }
        })
        .catch((e: unknown) => setRunError(String(e)));
    },
    [applyRunToNodes, applyRunToEdges],
  );

  // spec-019: answers a pending approval-gated tool call from the canvas
  // instead of a terminal input() prompt -- immediately re-polls afterward
  // for snappier feedback rather than waiting up to POLL_INTERVAL_MS for
  // the banner to clear. `remember`: don't ask again for this tool for the
  // rest of this run (per-approval checkbox state, keyed by approval_id
  // since a run can have more than one pending approval at once).
  async function handleResolveApproval(approvalId: string, approved: boolean, remember: boolean) {
    if (!run) return;
    try {
      await resolveApproval(run.run_id, approvalId, approved, remember);
      const status = await pollRun(run.run_id);
      setRun(status);
      applyRunToNodes(status);
      applyRunToEdges(status);
    } catch (e) {
      setRunError(String(e));
    }
  }

  // spec-009: the one shared entry point into the live-polling pipeline --
  // used by both a manual Run click and the watch loop below noticing an
  // externally-triggered run, so the two are visually indistinguishable.
  // lastSeenRunIdRef is stamped unconditionally (even on a no-op) so a
  // same-tick race between the two callers never double-attaches; the
  // activeRunIdRef check is what actually guards against resetting/
  // re-polling a run that's already attached.
  const attachToRun = useCallback(
    (runId: string) => {
      lastSeenRunIdRef.current = runId;
      if (activeRunIdRef.current === runId) return;
      activeRunIdRef.current = runId;
      setRun(null);
      setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: "pending", errorMessage: null } })));
      setEdges((eds) => eds.map((e) => ({ ...e, data: { ...e.data, targetStatus: "pending" } })));
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
      pollUntilDone(runId);
    },
    [pollUntilDone, setNodes, setEdges],
  );

  // spec-017: load a past run (selected in HistoryPanel) into the exact
  // same `run` state the live-run view renders from -- a one-shot fetch,
  // not the polling loop (a historical run is already finished). Stops
  // any in-flight live poll first so it can't clobber this a moment later.
  async function handleSelectHistoricalRun(runId: string) {
    if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
    activeRunIdRef.current = runId;
    lastSeenRunIdRef.current = runId;
    setShowHistory(false);
    try {
      const status = await pollRun(runId);
      setRun(status);
      applyRunToNodes(status);
      applyRunToEdges(status);
    } catch (e) {
      setRunError(String(e));
    }
  }

  // spec-009: while this graph is active, keep checking for a new run
  // under its graph_id (a real trigger firing, e.g. a Telegram webhook) and
  // attach to it the moment it appears -- exactly the live rendering a
  // manual Run click gets, no click required. Cleanup via the effect's own
  // return covers both deactivation (activation leaves "active") and
  // unmount.
  useEffect(() => {
    if (activation !== "active" || savedGraphId === null) return;
    const id = window.setInterval(() => {
      listRuns({ graph_id: savedGraphId, limit: 1 })
        .then((res) => {
          const latest = res.runs[0];
          if (latest && latest.run_id !== lastSeenRunIdRef.current) {
            attachToRun(latest.run_id);
          }
        })
        .catch((e: unknown) => {
          // Passive background poll -- a transient blip shouldn't flip
          // activationError on every tick while still genuinely active.
          console.error("Trigger watch poll failed:", e);
        });
    }, WATCH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [activation, savedGraphId, attachToRun]);

  async function handleRun() {
    setIsSubmitting(true);
    setRunError(null);
    setMissingSlots([]);
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      const submitted = await submitRun(graph, savedGraphId ?? undefined);
      attachToRun(submitted.run_id);
    } catch (e) {
      if (e instanceof NeedsConnectionMappingError) {
        // spec-021: a shared graph this caller hasn't fully mapped yet --
        // show the mapping prompt instead of a plain error.
        setMissingSlots(e.missingSlots);
        setSlotMappingDrafts({});
        setMappingError(null);
      } else {
        setRunError(String(e));
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // spec-021: submits this caller's picks for every currently-missing slot,
  // then retries the run once -- a successful mapping is remembered
  // server-side, so every run after this one just works.
  async function handleMapSlotsAndRun() {
    if (savedGraphId === null) return;
    setMappingInProgress(true);
    setMappingError(null);
    try {
      for (const slot of missingSlots) {
        const connectionName = slotMappingDrafts[slot.slot_name];
        if (!connectionName) {
          setMappingError(`Pick a connection for '${slot.slot_name}' first.`);
          return;
        }
        await setConnectionMapping(savedGraphId, slot.slot_name, connectionName);
      }
      setMissingSlots([]);
      await handleRun();
    } catch (e) {
      setMappingError(String(e));
    } finally {
      setMappingInProgress(false);
    }
  }

  // spec-015: Save persists to the server, giving the graph a real, stable
  // id -- create the first time, update (by id) every time after. Returns
  // the id, since handleActivate needs to auto-save first when the graph
  // was never explicitly saved (a graph_id is required to activate at all).
  async function handleSave(): Promise<string> {
    setSaving(true);
    setSaveError(null);
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      if (savedGraphId === null) {
        const created = await createGraph(graphName, graph, sharing, connectionSlots);
        setSavedGraphId(created.graph_id);
        return created.graph_id;
      }
      await updateGraph(savedGraphId, { name: graphName, spec: graph, sharing, connectionSlots });
      return savedGraphId;
    } catch (e) {
      setSaveError(String(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }

  async function refreshSavedGraphsList() {
    try {
      setSavedGraphs(await listGraphs());
    } catch (e) {
      setLoadError(String(e));
    }
  }

  async function handleLoadFromServer(graphId: string) {
    setLoadError(null);
    try {
      const detail = await getGraph(graphId);
      const { nodes: loadedNodes, edges: loadedEdges } = await graphSpecToNodesAndEdges(
        detail.spec,
        nodeTypesByName,
      );
      setNodes(loadedNodes);
      setEdges(loadedEdges);
      setSelectedNodeId(null);
      setRun(null);
      setRunError(null);
      setSavedGraphId(detail.graph_id);
      setGraphName(detail.name);
      setSharing(detail.sharing);
      setConnectionSlots(detail.connection_slots);
      setMissingSlots([]);
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
      window.setTimeout(() => loadedNodes.forEach((n) => updateNodeInternals(n.id)), 0);

      // spec-015: this graph might already be active (activated in a prior
      // session, or before this reload) -- restore that UI state
      // immediately rather than showing "Activate" as if it were inactive.
      if (detail.is_active) {
        const active = await listActiveGraphs();
        const match = active.find((a) => a.graph_id === detail.graph_id);
        setActivation("active");
        setTriggers(match?.triggers ?? []);
      } else {
        setActivation("inactive");
        setTriggers(null);
      }
    } catch (e) {
      setLoadError(String(e));
    }
  }

  async function handleActivate() {
    setActivation("activating");
    setActivationError(null);
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      // A graph_id is required to activate at all -- if this graph was
      // never explicitly saved, auto-save it once now so activation always
      // operates on a stable, persisted id (the actual fix for "the
      // webhook path changes every time I reload the canvas").
      const activeGraphId = savedGraphId ?? (await handleSave());
      const response = await activateGraph(activeGraphId, graph);
      setTriggers(response.triggers);
      setActivation("active");
    } catch (e) {
      setActivationError(String(e));
      setActivation("inactive");
    }
  }

  async function handleDeactivate() {
    if (savedGraphId === null) return; // can't have activated without one
    setActivation("deactivating");
    setActivationError(null);
    try {
      await deactivateGraph(savedGraphId);
    } catch (e) {
      setActivationError(String(e));
    } finally {
      setActivation("inactive");
      setTriggers(null);
    }
  }

  // --- export / import (spec-005 §4/§6: canvas <-> a local CLI graph.json,
  // kept as a clearly separate action from the server-backed Save/Load
  // above per spec-015 §7's resolved open question) --------------------
  function handleExport() {
    const graph = nodesAndEdgesToGraphSpec(nodes, edges);
    downloadJson(graph, "graph.json");
  }

  async function handleImportFile(file: File) {
    setLoadError(null);
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as GraphSpec;
      const { nodes: loadedNodes, edges: loadedEdges } = await graphSpecToNodesAndEdges(
        parsed,
        nodeTypesByName,
      );
      setNodes(loadedNodes);
      setEdges(loadedEdges);
      setSelectedNodeId(null);
      setRun(null);
      setRunError(null);
      // Importing a local file is a distinct graph from whatever was
      // previously saved/loaded -- it has no server identity until
      // explicitly Saved, and whatever activation state was showing
      // belonged to the *previous* graph, not this newly-imported one.
      setSavedGraphId(null);
      setGraphName("Untitled");
      setActivation("inactive");
      setTriggers(null);
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
      // Give freshly-loaded dynamic-schema nodes' handles (resolved above,
      // present from their very first render) a measurement pass too --
      // cheap, and removes any residual risk of the Phase 2 stale-handle
      // issue recurring for a load-then-immediately-connect-more flow.
      window.setTimeout(() => loadedNodes.forEach((n) => updateNodeInternals(n.id)), 0);
    } catch (e) {
      setLoadError(String(e));
    }
  }

  function handleGoogleSignIn() {
    // A real top-level navigation, not a fetch() -- Google's consent screen
    // has to render in the actual tab. redirect_to is where the backend
    // sends the browser back once the round trip with Google completes.
    window.location.href = googleLoginUrl(window.location.origin + window.location.pathname);
  }

  // spec-017/020: a minimal login gate -- the whole canvas is meaningless
  // without a credential, so this is an early return, not a modal layered
  // over a half-loaded app. spec-020: sign-in is now a real Google OAuth
  // redirect rather than typing in the shared key directly.
  if (needsUnlock) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="flex w-[280px] flex-col gap-2.5 rounded-[var(--radius-sm)] border border-border bg-card p-6">
          <h1 className="text-lg font-semibold">Agent Graph Studio</h1>
          <p className="text-xs text-muted-foreground">Sign in with an invited Google account to continue.</p>
          <Button type="button" onClick={handleGoogleSignIn}>
            Sign in with Google
          </Button>
          {unlockError && <div className="text-xs text-[var(--status-error)]">{unlockError}</div>}
        </div>
      </div>
    );
  }

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;
  const selectedTraceRecord = run && selectedNodeId ? findTraceRecord(run.trace, selectedNodeId) : null;

  // spec-012: every sub-node currently wired into the selected node's own
  // slots, for ConfigPanel's read-only summary -- derived from `edges`
  // (sub_node-kind, targeting the selected node) + `nodes`, not stored
  // separately.
  const connectedSubNodes = selectedNode
    ? edges
        .filter((e) => e.target === selectedNode.id && e.sourceHandle === SUB_NODE_HANDLE_ID)
        .map((e) => ({ slot: e.targetHandle ?? "", node: nodes.find((n) => n.id === e.source) }))
        .filter((entry): entry is { slot: string; node: GenericFlowNode } => entry.node !== undefined)
    : [];

  // spec-025: which of the selected node's own data input slots currently
  // have an incoming edge -- ConfigPanel only offers a literal-value field
  // for the ones that don't (an edge always wins if both exist). Excludes
  // sub_node edges (SUB_NODE_HANDLE_ID source), which also set targetHandle
  // to a slot-shaped name but aren't ordinary data ports.
  const wiredInputSlotNames = selectedNode
    ? new Set(
        edges
          .filter((e) => e.target === selectedNode.id && e.sourceHandle !== SUB_NODE_HANDLE_ID)
          .map((e) => e.targetHandle)
          .filter((h): h is string => h !== null && h !== undefined),
      )
    : new Set<string>();

  return (
    <ConnectionTypeContext.Provider value={connectionTypeByName}>
      <GroupActionsContext.Provider
        value={{ selectNode: setSelectedNodeId, removeFromGroup }}
      >
      <div className="app-layout">
        <Palette />
        <div className="canvas-column">
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card/60 px-3 py-2 shadow-[var(--shadow-sm)]">
            {/* Run cluster -- the one action that gets real visual weight;
                everything else in this toolbar is deliberately quieter. */}
            <Button
              type="button"
              className="gap-1.5 font-semibold"
              onClick={() => void handleRun()}
              disabled={isSubmitting || run?.status === "running"}
            >
              <Play className="size-3.5 fill-current" />
              {run?.status === "running" ? "Running..." : "Run"}
            </Button>
            {run && (
              <Badge
                variant="outline"
                className={cn(
                  "border-none text-xs font-semibold tracking-[0.03em] uppercase",
                  run.status === "running" &&
                    "text-[var(--status-running)] bg-[color-mix(in_srgb,var(--status-running)_15%,transparent)]",
                  run.status === "completed" &&
                    "text-[var(--status-success)] bg-[color-mix(in_srgb,var(--status-success)_15%,transparent)]",
                  run.status === "failed" &&
                    "text-[var(--status-error)] bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)]",
                )}
              >
                {run.status}
              </Badge>
            )}

            <Separator orientation="vertical" className="h-5" />

            {/* Graph identity cluster */}
            <Input
              type="text"
              className="h-8 w-[140px]"
              value={graphName}
              onChange={(e) => setGraphName(e.target.value)}
              placeholder="Graph name"
              title="This graph's name -- used when Saved to the server"
            />
            <Button
              type="button"
              variant="ghost"
              onClick={() => void handleSave().then(() => refreshSavedGraphsList())}
              disabled={saving}
            >
              {saving ? "Saving..." : "Save"}
            </Button>
            <Select
              value=""
              onValueChange={(v) => {
                if (v) void handleLoadFromServer(v);
              }}
              onOpenChange={(open) => open && void refreshSavedGraphsList()}
            >
              <SelectTrigger className="w-[170px]">
                <SelectValue placeholder="Load saved graph..." />
              </SelectTrigger>
              <SelectContent>
                {savedGraphs.map((g) => (
                  <SelectItem key={g.graph_id} value={g.graph_id}>
                    {g.name}
                    {g.is_active ? " (active)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Separator orientation="vertical" className="h-5" />

            {/* Sharing + activation cluster */}
            <Button
              type="button"
              variant={sharing === "shared" ? "secondary" : "ghost"}
              onClick={() => setShowSharingPanel((v) => !v)}
              title="Let other users run this graph with their own connections"
            >
              {sharing === "shared" ? "Shared" : "Private"}
            </Button>
            <Button
              type="button"
              variant={activation === "active" ? "secondary" : "outline"}
              className={cn(
                activation === "active" && "text-[var(--status-running)]",
              )}
              onClick={() => void (activation === "active" ? handleDeactivate() : handleActivate())}
              disabled={activation === "activating" || activation === "deactivating"}
            >
              {activation === "activating"
                ? "Activating..."
                : activation === "deactivating"
                  ? "Deactivating..."
                  : activation === "active"
                    ? "Deactivate"
                    : "Activate"}
            </Button>
            {activation === "active" && (
              // Re-push the current canvas graph without a deactivate round-
              // trip -- POST /graphs/{id}/activate is already idempotent
              // server-side (replaces the prior registration outright), so
              // this reuses handleActivate completely unchanged. Without
              // this, an edit made after activating (e.g. removing an edge)
              // silently has no effect until Deactivate+Activate, which is
              // exactly the confusion that surfaced this gap.
              <Button
                type="button"
                variant="ghost"
                onClick={() => void handleActivate()}
                title="Push the current canvas graph to the already-active webhook/schedule"
              >
                Update
              </Button>
            )}
            {activation === "active" && (
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold tracking-[0.03em] text-[var(--status-running)] uppercase animate-group-row-pulse">
                ● active
              </span>
            )}
            {activation === "active" && triggers && triggers.length > 0 && (
              <span className="flex flex-wrap gap-1.5">
                {triggers.map((t) => (
                  <code
                    key={t.node_id}
                    className="rounded-[var(--radius-sm)] border border-[color-mix(in_srgb,var(--status-running)_30%,transparent)] bg-[color-mix(in_srgb,var(--status-running)_12%,var(--card))] px-1.5 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {t.type === "webhook_trigger" ? `POST ${t.endpoint_or_schedule}` : `cron ${t.endpoint_or_schedule}`}
                  </code>
                ))}
              </span>
            )}

            {mcpOAuthMessage && (
              <span
                className="cursor-pointer text-xs text-[var(--status-error)]"
                onClick={() => setMcpOAuthMessage(null)}
                title="Dismiss"
              >
                {mcpOAuthMessage}
              </span>
            )}
            {runError && <span className="text-xs text-[var(--status-error)]">{runError}</span>}
            {loadError && <span className="text-xs text-[var(--status-error)]">{loadError}</span>}
            {saveError && <span className="text-xs text-[var(--status-error)]">{saveError}</span>}
            {activationError && <span className="text-xs text-[var(--status-error)]">{activationError}</span>}

            {/* Utility cluster -- rarely-touched actions tucked into one
                overflow menu instead of competing for row space with the
                controls actually used every session. */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="icon" className="ml-auto" title="More actions">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleExport}>
                  <Download className="size-4" />
                  Export
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                  <Upload className="size-4" />
                  Import
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setShowHistory(true)}>
                  <HistoryIcon className="size-4" />
                  History
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={savedGraphId === null}
                  title={savedGraphId === null ? "Save the graph first" : undefined}
                  onClick={() => setShowInvokeKeys(true)}
                >
                  <KeyRound className="size-4" />
                  Invoke keys
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setShowSettings(true)}>
                  <SettingsIcon className="size-4" />
                  Settings
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleImportFile(file);
                e.target.value = "";
              }}
            />
          </div>
          {showSharingPanel && (
            // spec-021: declaring a graph shared lets a non-author user run
            // it with their own connections -- each slot names a connection
            // reference literally used in this graph's node config (e.g.
            // "my-gmail") plus its connection type, so a runner's picker
            // can filter to compatible connections.
            <div className="flex flex-col gap-2 border-b border-border bg-[color-mix(in_srgb,var(--status-running)_10%,var(--background))] px-3.5 py-2.5">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
                <input
                  type="checkbox"
                  checked={sharing === "shared"}
                  onChange={(e) => setSharing(e.target.checked ? "shared" : "private")}
                />
                Shared -- other users can run this graph with their own connections
              </label>
              {sharing === "shared" && (
                <>
                  {connectionSlots.map((slot, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2.5">
                      <Input
                        type="text"
                        placeholder="Slot name (e.g. my-gmail, matches a node's connection field)"
                        value={slot.slot_name}
                        onChange={(e) =>
                          setConnectionSlots((prev) =>
                            prev.map((s, j) => (j === i ? { ...s, slot_name: e.target.value } : s)),
                          )
                        }
                      />
                      <Input
                        type="text"
                        placeholder="Connection type (e.g. anthropic, mcp_server)"
                        value={slot.connection_type}
                        onChange={(e) =>
                          setConnectionSlots((prev) =>
                            prev.map((s, j) => (j === i ? { ...s, connection_type: e.target.value } : s)),
                          )
                        }
                      />
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setConnectionSlots((prev) => prev.filter((_, j) => j !== i))}
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    className="self-start"
                    onClick={() => setConnectionSlots((prev) => [...prev, { slot_name: "", connection_type: "" }])}
                  >
                    + Add slot
                  </Button>
                </>
              )}
            </div>
          )}
          {missingSlots.length > 0 && (
            // spec-021: POST /runs came back 409 -- this caller hasn't
            // mapped one or more of this shared graph's declared slots yet.
            // Picking one of their own connections here, once, is
            // remembered server-side for every future run.
            <div className="flex flex-col gap-2 border-b border-border bg-[color-mix(in_srgb,var(--status-running)_10%,var(--background))] px-3.5 py-2.5">
              <span className="min-w-[200px] flex-1 text-[13px] text-foreground">
                This shared graph needs your own connections mapped before it can run:
              </span>
              {missingSlots.map((slot) => (
                <div key={slot.slot_name} className="flex flex-wrap items-center gap-2.5">
                  <span className="min-w-[200px] flex-1 text-[13px] text-foreground">
                    {slot.slot_name} ({slot.connection_type})
                  </span>
                  <Select
                    value={slotMappingDrafts[slot.slot_name] ?? ""}
                    onValueChange={(v) => setSlotMappingDrafts((prev) => ({ ...prev, [slot.slot_name]: v }))}
                  >
                    <SelectTrigger className="w-[220px]">
                      <SelectValue placeholder="Choose your connection..." />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(connectionTypeByName)
                        .filter(([, type]) => type === slot.connection_type)
                        .map(([name]) => (
                          <SelectItem key={name} value={name}>
                            {name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
              <Button type="button" className="self-start" onClick={() => void handleMapSlotsAndRun()} disabled={mappingInProgress}>
                {mappingInProgress ? "Mapping..." : "Map & Run"}
              </Button>
              {mappingError && <span className="text-xs text-[var(--status-error)]">{mappingError}</span>}
            </div>
          )}
          {run && run.pending_approvals.length > 0 && (
            // spec-019: an approval-gated tool call is blocked mid-run,
            // waiting on a decision that used to only be answerable via a
            // terminal input() prompt -- see backend/execution/approvals.py.
            <div className="flex flex-col gap-2 border-b border-border bg-[color-mix(in_srgb,var(--status-running)_10%,var(--background))] px-3.5 py-2.5">
              {run.pending_approvals.map((p) => (
                <div key={p.approval_id} className="flex flex-wrap items-center gap-2.5">
                  <span className="min-w-[200px] flex-1 text-[13px] text-foreground">
                    Approve tool call <code className="rounded-[var(--radius-sm)] bg-card px-1.5 py-px text-xs">{p.tool_name}</code>(
                    {JSON.stringify(p.arguments)})?
                  </span>
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={rememberApproval[p.approval_id] ?? false}
                      onChange={(e) =>
                        setRememberApproval((prev) => ({ ...prev, [p.approval_id]: e.target.checked }))
                      }
                    />
                    Don't ask again this run
                  </label>
                  <Button
                    type="button"
                    className="bg-[var(--status-running)] text-background hover:bg-[var(--status-running)]/90"
                    onClick={() =>
                      void handleResolveApproval(p.approval_id, true, rememberApproval[p.approval_id] ?? false)
                    }
                  >
                    Approve
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      void handleResolveApproval(p.approval_id, false, rememberApproval[p.approval_id] ?? false)
                    }
                  >
                    Reject
                  </Button>
                </div>
              ))}
            </div>
          )}
          <div className="canvas-wrapper" onDrop={onDrop} onDragOver={onDragOver}>
            <ReactFlow
              nodes={visibleNodes}
              edges={visibleEdges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeDragStop={onNodeDragStop}
              isValidConnection={isValidConnection}
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              onPaneClick={handlePaneClick}
              defaultViewport={{ x: 0, y: 0, zoom: 1 }}
            >
              <Background />
              <Controls />
              <MiniMap />
            </ReactFlow>
            <QuickAddSearch
              anchor={quickAddAnchor}
              nodeTypes={Object.values(nodeTypesByName)}
              onClose={() => setQuickAddAnchor(null)}
              onSelect={(nt) => {
                if (quickAddFlowPosition) addNodeAtPosition(nt, quickAddFlowPosition);
                setQuickAddAnchor(null);
              }}
            />
          </div>
        </div>
        <NodeInspectorPanel
          node={selectedNode}
          traceRecord={selectedTraceRecord}
          hasRun={run !== null}
          connectedSubNodes={connectedSubNodes}
          wiredInputSlotNames={wiredInputSlotNames}
          onConfigChange={(nodeId, config, inputs, outputs, inputValues) => {
            setNodes((nds) =>
              nds.map((n) =>
                n.id === nodeId ? { ...n, data: { ...n.data, config, inputs, outputs, inputValues } } : n,
              ),
            );
            // @xyflow/react caches each node's Handle positions internally and
            // doesn't auto-detect newly-added/removed <Handle> DOM elements
            // when a dynamic-schema node's ports change after mount (code,
            // mcp_call, fan_out, merge -- SPEC-002's resolve_slots resolved
            // over HTTP, here, well after initial render). Without this call,
            // edges connected to a handle that didn't exist at mount time
            // silently fail to render (they DO exist in state, just not
            // drawn) -- confirmed by direct inspection during Phase 2
            // verification, not a hypothetical.
            updateNodeInternals(nodeId);
          }}
        />
      </div>
      {showHistory && (
        <HistoryPanel onClose={() => setShowHistory(false)} onSelectRun={(id) => void handleSelectHistoricalRun(id)} />
      )}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}
      {showInvokeKeys && savedGraphId !== null && (
        <InvokeKeysPanel graphId={savedGraphId} onClose={() => setShowInvokeKeys(false)} />
      )}
      </GroupActionsContext.Provider>
    </ConnectionTypeContext.Provider>
  );
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}

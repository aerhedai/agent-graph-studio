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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  activateGraph,
  deactivateGraph,
  fetchConnections,
  fetchNodeTypes,
  listRuns,
  pollRun,
  submitRun,
} from "../api/client";
import type { GraphSpec, NodeTypeInfo, RunStatusResponse, TriggerInfo } from "../api/types";
import { graphSpecToNodesAndEdges, nodesAndEdgesToGraphSpec } from "../graph/serialize";
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
import { StatusEdge } from "./StatusEdge";
import { errorMessageForNode, findTraceRecord, statusForNode } from "./traceStatus";
import { slotTypesCompatible } from "./typeCompat";

const nodeTypes = { generic: GenericNode };
const edgeTypes = { status: StatusEdge };
const POLL_INTERVAL_MS = 500;
// Lighter than POLL_INTERVAL_MS -- this is a passive background check for
// "has a new run appeared for this graph_id", not a foreground wait.
const WATCH_INTERVAL_MS = 1750;

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
  const [nodeTypesByName, setNodeTypesByName] = useState<Record<string, NodeTypeInfo>>({});
  const [connectionTypeByName, setConnectionTypeByName] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  // spec-009: a fresh id per canvas session -- never persisted, never
  // embedded in saved/loaded graph JSON (GraphSpec deliberately has no id
  // field, backend/api/app.py's POST /runs docstring). Lazy useState
  // initializer so crypto.randomUUID() runs exactly once, on mount, not
  // every render. Loading a different file mid-session does NOT reset
  // this -- re-activating under the same id after an edit/load is correct,
  // idempotent behavior (the activate endpoint already replaces the prior
  // registration outright).
  const [graphId] = useState<string>(() => crypto.randomUUID());
  const [activation, setActivation] = useState<"inactive" | "activating" | "active" | "deactivating">(
    "inactive",
  );
  const [activationError, setActivationError] = useState<string | null>(null);
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

  useEffect(() => {
    fetchNodeTypes()
      .then((types) => setNodeTypesByName(Object.fromEntries(types.map((t) => [t.type, t]))))
      .catch((e: unknown) => setLoadError(String(e)));
  }, []);

  useEffect(() => {
    // spec-013 §5: a node's badge shows its connection's *type* (e.g.
    // "ollama"), not just its name -- presentation-only lookup, so a
    // fetch failure here shouldn't block the canvas from working; nodes
    // simply render without a connection badge.
    fetchConnections()
      .then((connections) =>
        setConnectionTypeByName(Object.fromEntries(connections.map((c) => [c.name, c.type]))),
      )
      .catch((e: unknown) => console.error("Failed to load connections for node badges:", e));
  }, []);

  useEffect(() => {
    return () => {
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
    };
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const raw = event.dataTransfer.getData("application/x-node-type");
      if (!raw) return;
      const nodeTypeInfo = JSON.parse(raw) as NodeTypeInfo;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
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
    [screenToFlowPosition, setNodes, setEdges, nodes],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

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
    const map: Record<string, { id: string; nodeType: string; category: string; active: boolean }[]> = {};
    for (const [childId, groupId] of Object.entries(containedBy)) {
      const child = nodes.find((n) => n.id === childId);
      if (!child) continue;
      (map[groupId] ??= []).push({
        id: child.id,
        nodeType: child.data.nodeType,
        category: child.data.category,
        active: activeIds.has(child.id),
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

  // spec-009: while this graph is active, keep checking for a new run
  // under its graph_id (a real trigger firing, e.g. a Telegram webhook) and
  // attach to it the moment it appears -- exactly the live rendering a
  // manual Run click gets, no click required. Cleanup via the effect's own
  // return covers both deactivation (activation leaves "active") and
  // unmount.
  useEffect(() => {
    if (activation !== "active") return;
    const id = window.setInterval(() => {
      listRuns({ graph_id: graphId, limit: 1 })
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
  }, [activation, graphId, attachToRun]);

  async function handleRun() {
    setIsSubmitting(true);
    setRunError(null);
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      const submitted = await submitRun(graph, graphId);
      attachToRun(submitted.run_id);
    } catch (e) {
      setRunError(String(e));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleActivate() {
    setActivation("activating");
    setActivationError(null);
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      const response = await activateGraph(graphId, graph);
      setTriggers(response.triggers);
      setActivation("active");
    } catch (e) {
      setActivationError(String(e));
      setActivation("inactive");
    }
  }

  async function handleDeactivate() {
    setActivation("deactivating");
    setActivationError(null);
    try {
      await deactivateGraph(graphId);
    } catch (e) {
      setActivationError(String(e));
    } finally {
      setActivation("inactive");
      setTriggers(null);
    }
  }

  // --- save / load (spec-005 §4/§6: canvas <-> the exact CLI graph JSON) --
  function handleSave() {
    const graph = nodesAndEdgesToGraphSpec(nodes, edges);
    downloadJson(graph, "graph.json");
  }

  async function handleLoadFile(file: File) {
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

  return (
    <ConnectionTypeContext.Provider value={connectionTypeByName}>
      <GroupActionsContext.Provider
        value={{ selectNode: setSelectedNodeId, removeFromGroup }}
      >
      <div className="app-layout">
        <Palette />
        <div className="canvas-column">
          <div className="run-bar">
            <button type="button" onClick={() => void handleRun()} disabled={isSubmitting || run?.status === "running"}>
              {run?.status === "running" ? "Running..." : "Run"}
            </button>
            <button type="button" onClick={handleSave} className="run-bar__secondary">
              Save
            </button>
            <button
              type="button"
              className="run-bar__secondary"
              onClick={() => fileInputRef.current?.click()}
            >
              Load
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleLoadFile(file);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className={`run-bar__secondary run-bar__activate-btn${
                activation === "active" ? " run-bar__activate-btn--active" : ""
              }`}
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
            </button>
            {activation === "active" && (
              // Re-push the current canvas graph without a deactivate round-
              // trip -- POST /graphs/{id}/activate is already idempotent
              // server-side (replaces the prior registration outright), so
              // this reuses handleActivate completely unchanged. Without
              // this, an edit made after activating (e.g. removing an edge)
              // silently has no effect until Deactivate+Activate, which is
              // exactly the confusion that surfaced this gap.
              <button
                type="button"
                className="run-bar__secondary"
                onClick={() => void handleActivate()}
                title="Push the current canvas graph to the already-active webhook/schedule"
              >
                Update
              </button>
            )}
            {activation === "active" && <span className="run-bar__trigger-badge">● active</span>}
            {activation === "active" && triggers && triggers.length > 0 && (
              <span className="run-bar__triggers">
                {triggers.map((t) => (
                  <code key={t.node_id} className="run-bar__trigger-chip">
                    {t.type === "webhook_trigger" ? `POST ${t.endpoint_or_schedule}` : `cron ${t.endpoint_or_schedule}`}
                  </code>
                ))}
              </span>
            )}
            {run && <span className={`run-bar__status status-${run.status}`}>{run.status}</span>}
            {runError && <span className="run-bar__error">{runError}</span>}
            {loadError && <span className="run-bar__error">{loadError}</span>}
            {activationError && <span className="run-bar__error">{activationError}</span>}
          </div>
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
              onPaneClick={() => setSelectedNodeId(null)}
              defaultViewport={{ x: 0, y: 0, zoom: 1 }}
            >
              <Background />
              <Controls />
              <MiniMap />
            </ReactFlow>
          </div>
        </div>
        <NodeInspectorPanel
          node={selectedNode}
          traceRecord={selectedTraceRecord}
          hasRun={run !== null}
          connectedSubNodes={connectedSubNodes}
          onConfigChange={(nodeId, config, inputs, outputs) => {
            setNodes((nds) =>
              nds.map((n) =>
                n.id === nodeId ? { ...n, data: { ...n.data, config, inputs, outputs } } : n,
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

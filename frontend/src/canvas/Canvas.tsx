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
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchConnections, fetchNodeTypes, pollRun, submitRun } from "../api/client";
import type { GraphSpec, NodeTypeInfo, RunStatusResponse } from "../api/types";
import { graphSpecToNodesAndEdges, nodesAndEdgesToGraphSpec } from "../graph/serialize";
import { NodeInspectorPanel } from "../panels/NodeInspectorPanel";
import {
  ConnectionTypeContext,
  GenericNode,
  SUB_NODE_HANDLE_ID,
  type GenericFlowNode,
  type GenericNodeData,
  type NodeStatus,
} from "./GenericNode";
import { Palette } from "./Palette";
import { StatusEdge } from "./StatusEdge";
import { slotTypesCompatible } from "./typeCompat";

const nodeTypes = { generic: GenericNode };
const edgeTypes = { status: StatusEdge };
const POLL_INTERVAL_MS = 500;

let idCounter = 0;
function nextNodeId(typeName: string): string {
  idCounter += 1;
  return `${typeName}_${idCounter}`;
}

function statusForNode(nodeId: string, run: RunStatusResponse | null): NodeStatus {
  if (!run) return "pending";
  const record = run.trace.find((t) => t.node_id === nodeId);
  if (record) return record.error ? "error" : "success";
  if (run.running_node_ids.includes(nodeId)) return "running";
  return "pending";
}

// spec-013 §7 (resolved open question, adopted its own "yes" recommendation):
// a failed node shows its error message via a short inline hover tooltip for
// immediate visibility, in addition to the full detail already available in
// the trace inspector panel -- real trace data, not a placeholder string.
function errorMessageForNode(nodeId: string, run: RunStatusResponse | null): string | null {
  if (!run) return null;
  return run.trace.find((t) => t.node_id === nodeId)?.error ?? null;
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
  const { screenToFlowPosition } = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const pollTimeoutRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
    },
    [screenToFlowPosition, setNodes],
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

  async function handleRun() {
    setIsSubmitting(true);
    setRunError(null);
    setRun(null);
    setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: "pending", errorMessage: null } })));
    setEdges((eds) => eds.map((e) => ({ ...e, data: { ...e.data, targetStatus: "pending" } })));
    try {
      const graph = nodesAndEdgesToGraphSpec(nodes, edges);
      const submitted = await submitRun(graph);
      pollUntilDone(submitted.run_id);
    } catch (e) {
      setRunError(String(e));
    } finally {
      setIsSubmitting(false);
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
  const selectedTraceRecord = run?.trace.find((t) => t.node_id === selectedNodeId) ?? null;

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
            {run && <span className={`run-bar__status status-${run.status}`}>{run.status}</span>}
            {runError && <span className="run-bar__error">{runError}</span>}
            {loadError && <span className="run-bar__error">{loadError}</span>}
          </div>
          <div className="canvas-wrapper" onDrop={onDrop} onDragOver={onDragOver}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
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

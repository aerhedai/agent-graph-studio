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
import { pollRun, submitRun } from "../api/client";
import type { NodeTypeInfo, RunStatusResponse } from "../api/types";
import { nodesAndEdgesToGraphSpec } from "../graph/serialize";
import { NodeInspectorPanel } from "../panels/NodeInspectorPanel";
import { GenericNode, type GenericFlowNode, type GenericNodeData, type NodeStatus } from "./GenericNode";
import { Palette } from "./Palette";
import { slotTypesCompatible } from "./typeCompat";

const nodeTypes = { generic: GenericNode };
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

function CanvasInner() {
  const [nodes, setNodes, onNodesChange] = useNodesState<GenericFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [run, setRun] = useState<RunStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const { screenToFlowPosition } = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const pollTimeoutRef = useRef<number | null>(null);

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
        config: {},
        configSchema: nodeTypeInfo.config_schema,
        inputs: nodeTypeInfo.dynamic_schema ? [] : nodeTypeInfo.inputs,
        outputs: nodeTypeInfo.dynamic_schema ? [] : nodeTypeInfo.outputs,
        dynamicSchema: nodeTypeInfo.dynamic_schema,
        status: "pending",
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
  const isValidConnection: IsValidConnection = useCallback(
    (connection) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);
      if (!sourceNode || !targetNode) return false;

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

  const onConnect = useCallback(
    (connection: Connection) => setEdges((eds) => addEdge(connection, eds)),
    [setEdges],
  );

  // --- run + live trace polling (spec-005 §4/§6) -----------------------
  const applyRunToNodes = useCallback(
    (nextRun: RunStatusResponse) => {
      setNodes((nds) =>
        nds.map((n) => ({ ...n, data: { ...n.data, status: statusForNode(n.id, nextRun) } })),
      );
    },
    [setNodes],
  );

  const pollUntilDone = useCallback(
    (runId: string) => {
      pollRun(runId)
        .then((status) => {
          setRun(status);
          applyRunToNodes(status);
          if (status.status === "running") {
            pollTimeoutRef.current = window.setTimeout(() => pollUntilDone(runId), POLL_INTERVAL_MS);
          }
        })
        .catch((e: unknown) => setRunError(String(e)));
    },
    [applyRunToNodes],
  );

  async function handleRun() {
    setIsSubmitting(true);
    setRunError(null);
    setRun(null);
    setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: "pending" } })));
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

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;
  const selectedTraceRecord = run?.trace.find((t) => t.node_id === selectedNodeId) ?? null;

  return (
    <div className="app-layout">
      <Palette />
      <div className="canvas-column">
        <div className="run-bar">
          <button type="button" onClick={() => void handleRun()} disabled={isSubmitting || run?.status === "running"}>
            {run?.status === "running" ? "Running..." : "Run"}
          </button>
          {run && <span className={`run-bar__status status-${run.status}`}>{run.status}</span>}
          {runError && <span className="run-bar__error">{runError}</span>}
        </div>
        <div className="canvas-wrapper" onDrop={onDrop} onDragOver={onDragOver}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
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
  );
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}

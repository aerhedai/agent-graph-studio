import { useEffect, useState } from "react";
import type { SlotInfo, TraceRecord } from "../api/types";
import type { GenericFlowNode } from "../canvas/GenericNode";
import { ConfigPanel } from "./ConfigPanel";
import { TraceInspector } from "./TraceInspector";

type Tab = "config" | "trace";

interface NodeInspectorPanelProps {
  node: GenericFlowNode | null;
  traceRecord: TraceRecord | null;
  hasRun: boolean;
  onConfigChange: (
    nodeId: string,
    config: Record<string, unknown>,
    inputs: SlotInfo[],
    outputs: SlotInfo[],
  ) => void;
  // spec-012: (slot name, connected sub-node) pairs for the selected node,
  // so ConfigPanel can render each connected sub-node's settings read-only
  // -- editing only happens by clicking the sub-node itself on canvas.
  connectedSubNodes: { slot: string; node: GenericFlowNode }[];
}

// Single side panel real estate, two purposes (spec-005 §4/§6): editing a
// node's config, and -- after a run -- inspecting its real trace record.
// Switches to the trace tab automatically the moment a trace becomes
// available for the selected node.
export function NodeInspectorPanel({
  node,
  traceRecord,
  hasRun,
  onConfigChange,
  connectedSubNodes,
}: NodeInspectorPanelProps) {
  const [tab, setTab] = useState<Tab>("config");

  useEffect(() => {
    setTab(traceRecord ? "trace" : "config");
  }, [node?.id, traceRecord]);

  if (!node) {
    return (
      <aside className="node-inspector node-inspector--empty">
        <p>Select a node to edit its configuration or inspect its trace.</p>
      </aside>
    );
  }

  return (
    <aside className="node-inspector">
      <h2>{node.data.nodeType}</h2>
      <p className="config-panel__id">{node.id}</p>

      <div className="node-inspector__tabs">
        <button
          type="button"
          className={tab === "config" ? "node-inspector__tab active" : "node-inspector__tab"}
          onClick={() => setTab("config")}
        >
          Config
        </button>
        <button
          type="button"
          className={tab === "trace" ? "node-inspector__tab active" : "node-inspector__tab"}
          onClick={() => setTab("trace")}
          disabled={!hasRun}
        >
          Trace
        </button>
      </div>

      {tab === "config" ? (
        <ConfigPanel node={node} onConfigChange={onConfigChange} connectedSubNodes={connectedSubNodes} />
      ) : (
        <TraceInspector traceRecord={traceRecord} isPending={hasRun && !traceRecord} />
      )}
    </aside>
  );
}

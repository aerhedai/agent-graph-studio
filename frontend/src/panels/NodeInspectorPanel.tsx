import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useEffect, useState } from "react";
import type { SlotInfo, TraceRecord } from "../api/types";
import { displayName } from "../canvas/GenericNode";
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
    inputValues: Record<string, unknown>,
  ) => void;
  // spec-012: (slot name, connected sub-node) pairs for the selected node,
  // so ConfigPanel can render each connected sub-node's settings read-only
  // -- editing only happens by clicking the sub-node itself on canvas.
  connectedSubNodes: { slot: string; node: GenericFlowNode }[];
  // spec-025: names of this node's own data input slots that currently
  // have an incoming edge -- ConfigPanel only offers a literal-value field
  // for the ones that don't.
  wiredInputSlotNames: Set<string>;
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
  wiredInputSlotNames,
}: NodeInspectorPanelProps) {
  const [tab, setTab] = useState<Tab>("config");

  useEffect(() => {
    setTab(traceRecord ? "trace" : "config");
  }, [node?.id, traceRecord]);

  if (!node) {
    return (
      <aside className="overflow-y-auto border-l border-border bg-card p-3 text-[13px] text-muted-foreground">
        <p>Select a node to edit its configuration or inspect its trace.</p>
      </aside>
    );
  }

  return (
    <aside className="overflow-y-auto border-l border-border bg-card p-3">
      <h2 className="text-base font-semibold text-foreground">
        {displayName(node.data.nodeType, node.data.integration)}
      </h2>
      <p className="mb-3 font-mono text-[11px] text-muted-foreground">{node.id}</p>

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="trace" disabled={!hasRun}>
            Trace
          </TabsTrigger>
        </TabsList>
        <TabsContent value="config">
          <ConfigPanel
            node={node}
            onConfigChange={onConfigChange}
            connectedSubNodes={connectedSubNodes}
            wiredInputSlotNames={wiredInputSlotNames}
          />
        </TabsContent>
        <TabsContent value="trace">
          <TraceInspector traceRecord={traceRecord} isPending={hasRun && !traceRecord} />
        </TabsContent>
      </Tabs>
    </aside>
  );
}

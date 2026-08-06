import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { TraceRecord } from "../api/types";

interface TraceInspectorProps {
  traceRecord: TraceRecord | null;
  isPending: boolean;
}

function JsonBlock({ children, error }: { children: string; error?: boolean }) {
  return (
    <pre
      className={cn(
        "overflow-x-auto rounded-[var(--radius-sm)] border border-border bg-background p-2 font-mono text-[11px] break-words whitespace-pre-wrap",
        error && "border-[var(--status-error)] text-[var(--status-error)]",
      )}
    >
      {children}
    </pre>
  );
}

function SectionHeading({ children }: { children: ReactNode }) {
  return <h3 className="mt-3 mb-1 text-xs tracking-[0.03em] text-muted-foreground uppercase">{children}</h3>;
}

// Clicking a node after (or during) a run shows its REAL trace record --
// inputs, outputs, token cost, side effect, error -- straight from
// GET /runs/{run_id}, never mocked data (spec-005 §6).
export function TraceInspector({ traceRecord, isPending }: TraceInspectorProps) {
  if (!traceRecord) {
    return (
      <p className="text-[13px] text-muted-foreground">
        {isPending
          ? "This node hasn't executed yet this run."
          : "No trace yet for this node -- run the graph first."}
      </p>
    );
  }

  return (
    <div>
      <dl className="mb-4 grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Status</dt>
        <dd
          className={cn(
            "font-semibold",
            traceRecord.error ? "text-[var(--status-error)]" : "text-[var(--status-success)]",
          )}
        >
          {traceRecord.error ? "error" : "success"}
        </dd>
        <dt className="text-muted-foreground">Started</dt>
        <dd className="font-mono">{traceRecord.started_at}</dd>
        <dt className="text-muted-foreground">Finished</dt>
        <dd className="font-mono">{traceRecord.finished_at}</dd>
        <dt className="text-muted-foreground">Token cost</dt>
        <dd className="font-mono">
          in {traceRecord.token_cost.input_tokens} / out {traceRecord.token_cost.output_tokens}
        </dd>
        <dt className="text-muted-foreground">Side effect</dt>
        <dd className="font-mono">{traceRecord.side_effect ? "yes" : "no"}</dd>
      </dl>

      <SectionHeading>Inputs</SectionHeading>
      <JsonBlock>{JSON.stringify(traceRecord.inputs, null, 2)}</JsonBlock>

      <SectionHeading>Outputs</SectionHeading>
      <JsonBlock>{JSON.stringify(traceRecord.outputs, null, 2)}</JsonBlock>

      {traceRecord.error && (
        <>
          <SectionHeading>Error</SectionHeading>
          <JsonBlock error>{traceRecord.error}</JsonBlock>
        </>
      )}

      {traceRecord.child_traces && (
        // Nested execution (a loop's iterations, a fan-out's branches) gets a
        // flattened/raw JSON view for this pass -- spec-005 §7's own stated
        // MVP recommendation, not a new simplification introduced here.
        <>
          <SectionHeading>
            Child traces ({traceRecord.child_traces.length}{" "}
            {traceRecord.child_traces.length === 1 ? "iteration" : "iterations"})
          </SectionHeading>
          <JsonBlock>{JSON.stringify(traceRecord.child_traces, null, 2)}</JsonBlock>
        </>
      )}
    </div>
  );
}

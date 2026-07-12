import type { TraceRecord } from "../api/types";

interface TraceInspectorProps {
  traceRecord: TraceRecord | null;
  isPending: boolean;
}

// Clicking a node after (or during) a run shows its REAL trace record --
// inputs, outputs, token cost, side effect, error -- straight from
// GET /runs/{run_id}, never mocked data (spec-005 §6).
export function TraceInspector({ traceRecord, isPending }: TraceInspectorProps) {
  if (!traceRecord) {
    return (
      <p className="trace-inspector__empty">
        {isPending
          ? "This node hasn't executed yet this run."
          : "No trace yet for this node -- run the graph first."}
      </p>
    );
  }

  return (
    <div className="trace-inspector">
      <dl className="trace-inspector__meta">
        <dt>Status</dt>
        <dd className={traceRecord.error ? "trace-inspector__status-error" : "trace-inspector__status-success"}>
          {traceRecord.error ? "error" : "success"}
        </dd>
        <dt>Started</dt>
        <dd>{traceRecord.started_at}</dd>
        <dt>Finished</dt>
        <dd>{traceRecord.finished_at}</dd>
        <dt>Token cost</dt>
        <dd>
          in {traceRecord.token_cost.input_tokens} / out {traceRecord.token_cost.output_tokens}
        </dd>
        <dt>Side effect</dt>
        <dd>{traceRecord.side_effect ? "yes" : "no"}</dd>
      </dl>

      <h3>Inputs</h3>
      <pre className="trace-inspector__json">{JSON.stringify(traceRecord.inputs, null, 2)}</pre>

      <h3>Outputs</h3>
      <pre className="trace-inspector__json">{JSON.stringify(traceRecord.outputs, null, 2)}</pre>

      {traceRecord.error && (
        <>
          <h3>Error</h3>
          <pre className="trace-inspector__json trace-inspector__error">{traceRecord.error}</pre>
        </>
      )}

      {traceRecord.child_traces && (
        // Nested execution (a loop's iterations, a fan-out's branches) gets a
        // flattened/raw JSON view for this pass -- spec-005 §7's own stated
        // MVP recommendation, not a new simplification introduced here.
        <>
          <h3>
            Child traces ({traceRecord.child_traces.length}{" "}
            {traceRecord.child_traces.length === 1 ? "iteration" : "iterations"})
          </h3>
          <pre className="trace-inspector__json">
            {JSON.stringify(traceRecord.child_traces, null, 2)}
          </pre>
        </>
      )}
    </div>
  );
}

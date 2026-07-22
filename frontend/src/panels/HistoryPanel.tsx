import { useEffect, useState } from "react";
import { listRuns } from "../api/client";
import type { RunSummary } from "../api/types";

interface HistoryPanelProps {
  onClose: () => void;
  onSelectRun: (runId: string) => void;
}

// spec-017: a real execution history view -- GET /runs has existed since
// SPEC-010 with no frontend consumer at all until now. Deliberately
// read-only, manual-refresh (SPEC-017 §6's resolved open question: "watch
// it happen" already belongs to the live-run view; history is for looking
// backward). Selecting a row hands the run_id back to Canvas.tsx, which
// loads it into the exact same `run` state the live-run view already
// renders from -- no second trace-rendering path.
export function HistoryPanel({ onClose, onSelectRun }: HistoryPanelProps) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [triggerFilter, setTriggerFilter] = useState<string>("");

  function refresh() {
    setLoading(true);
    setError(null);
    listRuns({
      status: statusFilter || undefined,
      trigger_source: triggerFilter || undefined,
      limit: 50,
    })
      .then((res) => setRuns(res.runs))
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, triggerFilter]);

  return (
    <div className="history-panel-overlay" onClick={onClose}>
      <aside className="history-panel" onClick={(e) => e.stopPropagation()}>
        <div className="history-panel__header">
          <h2>Execution history</h2>
          <button type="button" className="run-bar__secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="history-panel__filters">
          <span className="select-wrap">
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </span>
          <span className="select-wrap">
            <select value={triggerFilter} onChange={(e) => setTriggerFilter(e.target.value)}>
              <option value="">All sources</option>
              <option value="manual">Manual</option>
              <option value="schedule">Schedule</option>
              <option value="webhook">Webhook</option>
            </select>
          </span>
          <button type="button" className="run-bar__secondary" onClick={refresh} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>

        {error && <div className="run-bar__error">{error}</div>}

        <div className="history-panel__list">
          {runs.length === 0 && !loading && <p className="history-panel__empty">No runs yet.</p>}
          {runs.map((r) => (
            <button
              key={r.run_id}
              type="button"
              className="history-panel__row"
              onClick={() => onSelectRun(r.run_id)}
            >
              <span className={`run-bar__status status-${r.status}`}>{r.status}</span>
              <span className="history-panel__row-graph">{r.graph_id ?? "(no graph id)"}</span>
              <span className="history-panel__row-trigger">{r.trigger_source}</span>
              <span className="history-panel__row-time">{new Date(r.started_at).toLocaleString()}</span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

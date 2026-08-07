import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { listRuns } from "../api/client";
import type { RunSummary } from "../api/types";

interface HistoryPanelProps {
  onClose: () => void;
  onSelectRun: (runId: string) => void;
}

const STATUS_FILTER_LABELS: Record<string, string> = {
  all: "All statuses",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};
const TRIGGER_FILTER_LABELS: Record<string, string> = {
  all: "All sources",
  manual: "Manual",
  schedule: "Schedule",
  webhook: "Webhook",
  invoke: "Invoke",
};

const STATUS_BADGE_CLASSES: Record<string, string> = {
  running: "text-[var(--status-running)] bg-[color-mix(in_srgb,var(--status-running)_15%,transparent)]",
  completed: "text-[var(--status-success)] bg-[color-mix(in_srgb,var(--status-success)_15%,transparent)]",
  failed: "text-[var(--status-error)] bg-[color-mix(in_srgb,var(--status-error)_15%,transparent)]",
};

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
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[420px] max-w-[90vw] gap-3 overflow-y-auto p-4">
        <SheetHeader className="p-0">
          <SheetTitle>Execution history</SheetTitle>
        </SheetHeader>

        <div className="flex gap-2">
          <Select value={statusFilter || "all"} onValueChange={(v) => setStatusFilter(v === "all" ? "" : v)}>
            <SelectTrigger className="min-w-0 flex-1">
              <SelectValue>{STATUS_FILTER_LABELS[statusFilter || "all"]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="running">Running</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>
          <Select value={triggerFilter || "all"} onValueChange={(v) => setTriggerFilter(v === "all" ? "" : v)}>
            <SelectTrigger className="min-w-0 flex-1">
              <SelectValue>{TRIGGER_FILTER_LABELS[triggerFilter || "all"]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sources</SelectItem>
              <SelectItem value="manual">Manual</SelectItem>
              <SelectItem value="schedule">Schedule</SelectItem>
              <SelectItem value="webhook">Webhook</SelectItem>
              <SelectItem value="invoke">Invoke</SelectItem>
            </SelectContent>
          </Select>
          <Button type="button" variant="outline" onClick={refresh} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
        </div>

        {error && <div className="text-xs text-[var(--status-error)]">{error}</div>}

        <div className="flex flex-col gap-1.5">
          {runs.length === 0 && !loading && <p className="text-[13px] text-muted-foreground">No runs yet.</p>}
          {runs.map((r) => (
            <button
              key={r.run_id}
              type="button"
              className="grid cursor-pointer grid-cols-[auto_1fr_auto_auto] items-center gap-2 rounded-[var(--radius-sm)] border border-border bg-popover px-2.5 py-2 text-left font-[inherit] text-foreground transition-colors duration-150 hover:border-[var(--color-border-strong)]"
              onClick={() => onSelectRun(r.run_id)}
            >
              <Badge
                variant="outline"
                className={cn("border-none text-[11px] font-semibold tracking-[0.03em] uppercase", STATUS_BADGE_CLASSES[r.status])}
              >
                {r.status}
              </Badge>
              <span className="overflow-hidden font-mono text-xs text-ellipsis whitespace-nowrap">
                {r.graph_id ?? "(no graph id)"}
              </span>
              <span className="text-xs text-muted-foreground uppercase">{r.trigger_source}</span>
              <span className="text-xs whitespace-nowrap text-muted-foreground">
                {new Date(r.started_at).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}

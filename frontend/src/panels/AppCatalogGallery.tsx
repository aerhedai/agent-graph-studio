import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AppCatalogEntryInfo } from "../api/types";

interface AppCatalogGalleryProps {
  entries: AppCatalogEntryInfo[];
  onSelect: (entry: AppCatalogEntryInfo) => void;
  onCustom: () => void;
}

// spec-030: the first step of "+ New connection" -- pick a known app
// instead of starting from the full generic form. Purely presentational;
// both ConnectionPicker.tsx and SettingsPanel.tsx apply a selected entry to
// their own (separate) draft config state and then fall through to their
// own existing, unmodified config form.
export function AppCatalogGallery({ entries, onSelect, onCustom }: AppCatalogGalleryProps) {
  return (
    <div className="flex flex-col gap-1.5">
      {entries.map((entry) => (
        <div
          key={entry.key}
          className="flex flex-col gap-1.5 rounded-[var(--radius-sm)] border border-border bg-popover p-2.5"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold">{entry.display_name}</span>
            <Badge variant="outline" className="text-[10px]">
              {entry.server_url === null ? "self-hosted setup" : entry.requires_oauth ? "connect your account" : "ready to use"}
            </Badge>
          </div>
          <p className="text-[13px] text-muted-foreground">{entry.description}</p>
          <Button type="button" variant="outline" onClick={() => onSelect(entry)} className="self-start">
            Add
          </Button>
        </div>
      ))}

      <div className="flex flex-col gap-1.5 rounded-[var(--radius-sm)] border border-dashed border-border p-2.5">
        <span className="text-sm font-semibold">Custom connection</span>
        <p className="text-[13px] text-muted-foreground">
          Not one of the apps above -- configure a connection manually.
        </p>
        <Button type="button" variant="outline" onClick={onCustom} className="self-start">
          Custom connection
        </Button>
      </div>
    </div>
  );
}

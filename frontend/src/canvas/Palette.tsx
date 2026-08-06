import { Search } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { fetchNodeTypes } from "../api/client";
import type { NodeTypeInfo } from "../api/types";
import { CATEGORY_PRESENTATION, categoryPresentation, displayName, humanizeOperation } from "./GenericNode";

// The palette's ENTIRE data source is GET /node-types -- no type name, and
// no *category*, is hardcoded anywhere in this file. Which sections exist,
// and which types belong to them, comes entirely from each NodeTypeInfo's
// own `category` field (spec-013 §4's resolved decision) -- a new backend
// category appears here automatically the next time this component mounts.
// CATEGORY_PRESENTATION only supplies icon/color/display-order for
// categories it happens to recognize (the same presentation-only role it
// plays on the canvas nodes themselves); an unrecognized category still
// gets its own section, just sorted after the known ones.
const KNOWN_CATEGORY_ORDER = Object.keys(CATEGORY_PRESENTATION);

function PaletteItem({ nt }: { nt: NodeTypeInfo }) {
  const { icon: ItemIcon, colorVar: itemColorVar } = categoryPresentation(nt.category);
  return (
    <li
      className="flex cursor-grab items-center gap-2 rounded-[var(--radius-sm)] p-2 pl-[18px] text-[12.5px] [transition:background_120ms_var(--ease-standard),transform_150ms_var(--ease-spring)] hover:translate-x-0.5 hover:bg-popover active:cursor-grabbing"
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData("application/x-node-type", JSON.stringify(nt));
        event.dataTransfer.effectAllowed = "move";
      }}
    >
      <span
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[var(--radius-sm)]"
        style={
          {
            "--node-accent": `var(${itemColorVar})`,
            background: "color-mix(in srgb, var(--node-accent) 18%, var(--card))",
            color: "var(--node-accent)",
          } as CSSProperties
        }
      >
        <ItemIcon size={12} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-foreground">{displayName(nt.type, nt.integration)}</span>
        {nt.integration && (
          <span className="block truncate text-[10px] text-muted-foreground">
            {humanizeOperation(nt.type, nt.integration)}
          </span>
        )}
      </span>
      {nt.dynamic_schema && (
        <span className="shrink-0 rounded-[var(--radius-pill)] border border-primary/45 px-1.5 py-px text-[9px] font-bold tracking-[0.03em] text-primary uppercase">
          dynamic
        </span>
      )}
    </li>
  );
}

// A small chevron, shared between the top-level category accordion and
// the per-app accordion below -- same rotate-on-open treatment, so a
// nested accordion reads as "the same interaction, one level deeper"
// rather than a visually distinct control.
function AccordionChevron({ open }: { open: boolean }) {
  return (
    <svg
      className={cn(
        "shrink-0 text-muted-foreground [transition:transform_220ms_var(--ease-standard)]",
        open && "rotate-90",
      )}
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// spec-019, revised for discoverability at scale (a real connection can
// expose dozens of tools -- rendering all of them permanently in the DOM
// is exactly what makes "100s of nodes" unmanageable): the "apps" category
// renders two distinct shapes depending on where a type came from -- a
// manifest-backed app (e.g. Telegram) groups as App -> capability_group ->
// types (curated, 3 levels); a dynamically MCP-generated app has no
// curated capability_group, so it renders flatter as connection -> types
// (2 levels). Both are driven entirely by each type's own
// `integration`/`capability_group` fields -- no app name or connection
// name is ever hardcoded here. Each app is now its OWN collapsed-by-
// default accordion (Zapier's pattern: pick the app first, see its
// specific actions only once you've committed to that app) instead of
// every operation sitting permanently expanded in the tree.
function AppsCategoryBody({ types, isSearching }: { types: NodeTypeInfo[]; isSearching: boolean }) {
  const [openApps, setOpenApps] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    const byIntegration = new Map<string, NodeTypeInfo[]>();
    for (const nt of types) {
      const key = nt.integration ?? "(other)";
      const list = byIntegration.get(key) ?? [];
      list.push(nt);
      byIntegration.set(key, list);
    }
    return [...byIntegration.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([integration, items]) => {
        const byCapabilityGroup = new Map<string, NodeTypeInfo[]>();
        const ungrouped: NodeTypeInfo[] = [];
        for (const nt of items) {
          if (nt.capability_group) {
            const list = byCapabilityGroup.get(nt.capability_group) ?? [];
            list.push(nt);
            byCapabilityGroup.set(nt.capability_group, list);
          } else {
            ungrouped.push(nt);
          }
        }
        return { integration, capabilityGroups: [...byCapabilityGroup.entries()], ungrouped, count: items.length };
      });
  }, [types]);

  function toggleApp(integration: string) {
    setOpenApps((prev) => {
      const next = new Set(prev);
      if (next.has(integration)) next.delete(integration);
      else next.add(integration);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-1">
      {groups.map(({ integration, capabilityGroups, ungrouped, count }) => {
        const isOpen = isSearching || openApps.has(integration);
        return (
          <div key={integration} className="flex flex-col">
            <button
              type="button"
              className="flex w-full cursor-pointer items-center gap-1.5 rounded-[var(--radius-sm)] border-none bg-transparent py-1 pl-[14px] text-left font-[inherit] text-inherit select-none hover:bg-popover disabled:cursor-default"
              onClick={() => toggleApp(integration)}
              disabled={isSearching}
            >
              <AccordionChevron open={isOpen} />
              <span className="flex-1 truncate text-[11.5px] font-semibold text-foreground">{integration}</span>
              <span className="mr-1 shrink-0 rounded-[var(--radius-pill)] bg-popover px-[6px] py-px text-[9.5px] text-muted-foreground">
                {count}
              </span>
            </button>
            <div
              className={cn(
                "grid overflow-hidden [transition:grid-template-rows_220ms_var(--ease-standard)]",
                isOpen ? "[grid-template-rows:1fr]" : "[grid-template-rows:0fr]",
              )}
            >
              <div className="min-h-0 overflow-hidden">
                {capabilityGroups.map(([capabilityGroup, items]) => (
                  <div key={capabilityGroup} className="mt-1">
                    <div className="mb-0.5 pl-[34px] text-[11px] text-muted-foreground">{capabilityGroup}</div>
                    <ul className="mt-0.5 flex list-none flex-col gap-1 p-0">
                      {items.map((nt) => (
                        <PaletteItem key={nt.type} nt={nt} />
                      ))}
                    </ul>
                  </div>
                ))}
                {ungrouped.length > 0 && (
                  <ul className="mt-1 flex list-none flex-col gap-1 p-0">
                    {ungrouped.map((nt) => (
                      <PaletteItem key={nt.type} nt={nt} />
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Palette() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openCategories, setOpenCategories] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetchNodeTypes()
      .then((types) => {
        setNodeTypes(types);
        // Start with every category expanded -- with real categorization
        // now in place, collapsing everything by default would hide most
        // of the 19 registered types on first load.
        setOpenCategories(new Set(types.map((t) => t.category)));
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const categories = useMemo(() => {
    const byCategory = new Map<string, NodeTypeInfo[]>();
    for (const nt of nodeTypes) {
      const list = byCategory.get(nt.category) ?? [];
      list.push(nt);
      byCategory.set(nt.category, list);
    }
    return [...byCategory.entries()].sort(([a], [b]) => {
      const ai = KNOWN_CATEGORY_ORDER.indexOf(a);
      const bi = KNOWN_CATEGORY_ORDER.indexOf(b);
      if (ai !== -1 || bi !== -1) return (ai === -1 ? Infinity : ai) - (bi === -1 ? Infinity : bi);
      return a.localeCompare(b);
    });
  }, [nodeTypes]);

  const searchTerm = search.trim().toLowerCase();
  const isSearching = searchTerm.length > 0;

  function toggleCategory(category: string) {
    setOpenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  return (
    <aside className="flex flex-col gap-4 overflow-y-auto bg-card p-4">
      <div className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">Node Types</div>
      {error && <div className="text-xs text-[var(--status-error)]">{error}</div>}

      <label className="flex items-center gap-2 rounded-[var(--radius-md)] border border-border bg-background px-3 py-2 text-muted-foreground transition-colors duration-150 focus-within:border-[var(--color-border-strong)]">
        <Search size={14} />
        <input
          type="text"
          placeholder="Filter node types..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full border-none bg-transparent text-[12.5px] text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>

      {categories.map(([category, types]) => {
        const matches = isSearching ? types.filter((t) => t.type.toLowerCase().includes(searchTerm)) : types;
        if (isSearching && matches.length === 0) return null;
        const { colorVar, label } = categoryPresentation(category);
        const isOpen = isSearching || openCategories.has(category);

        return (
          <div key={category} className="flex flex-col">
            <button
              type="button"
              className="flex w-full cursor-pointer items-center gap-2 border-none bg-transparent py-2 text-left font-[inherit] text-inherit select-none disabled:cursor-default"
              onClick={() => toggleCategory(category)}
              disabled={isSearching}
            >
              <AccordionChevron open={isOpen} />
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: `var(${colorVar})` }} />
              <span className="flex-1 text-xs font-semibold tracking-[0.02em]">{label}</span>
              <span className="rounded-[var(--radius-pill)] bg-popover px-[7px] py-px text-[10px] text-muted-foreground">
                {matches.length}
              </span>
            </button>
            {/* CSS-grid accordion trick: animating a fr-unit row track gives
                a smooth auto-height transition without measuring pixel
                heights in JS. */}
            <div
              className={cn(
                "grid overflow-hidden [transition:grid-template-rows_220ms_var(--ease-standard)]",
                isOpen ? "[grid-template-rows:1fr]" : "[grid-template-rows:0fr]",
              )}
            >
              <div className="min-h-0 overflow-hidden">
                {category === "apps" ? (
                  <AppsCategoryBody types={matches} isSearching={isSearching} />
                ) : (
                  <ul className="mt-2 flex list-none flex-col gap-1 p-0">
                    {matches.map((nt) => (
                      <PaletteItem key={nt.type} nt={nt} />
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </aside>
  );
}

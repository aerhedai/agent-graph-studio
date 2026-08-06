import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { NodeTypeInfo } from "../api/types";
import { categoryPresentation, displayName, humanizeOperation } from "./GenericNode";

interface QuickAddSearchProps {
  // Screen-space anchor (where the double-click happened) -- null means
  // closed. Kept separate from the flow-space position the new node
  // actually gets created at (Canvas.tsx already converts one to the
  // other via screenToFlowPosition, same as the existing drag-and-drop
  // path); this component only ever deals in screen space for its own
  // popover placement.
  anchor: { x: number; y: number } | null;
  nodeTypes: NodeTypeInfo[];
  onSelect: (nodeType: NodeTypeInfo) => void;
  onClose: () => void;
}

// Ranks a node type against a query -- prefix match on its real display
// name wins, then substring match on display name, then a match anywhere
// else searchable (operation, raw type, category). Returns null (excluded)
// on no match at all. Deliberately simple substring/prefix scoring rather
// than a fuzzy-match library: at the scale this searches (currently
// dozens, the same "100s" this feature exists for), a predictable "does my
// literal text appear" model is easier to reason about while typing than
// a fuzzy scorer's less predictable ranking.
function score(nt: NodeTypeInfo, query: string): number | null {
  const name = displayName(nt.type, nt.integration).toLowerCase();
  const operation = nt.integration ? humanizeOperation(nt.type, nt.integration).toLowerCase() : "";
  const haystack = `${name} ${operation} ${nt.type} ${nt.category}`.toLowerCase();
  if (name.startsWith(query)) return 0;
  if (name.includes(query)) return 1;
  if (operation.includes(query)) return 2;
  if (haystack.includes(query)) return 3;
  return null;
}

export function QuickAddSearch({ anchor, nodeTypes, onSelect, onClose }: QuickAddSearchProps) {
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (anchor) {
      setQuery("");
      setHighlighted(0);
      // Autofocus the moment it opens -- this is a keyboard-driven
      // interaction end to end (type to filter, arrows to move, Enter to
      // place), the same shape as every command-palette precedent it's
      // modeled on.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [anchor]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      // Empty query: show everything, alphabetically -- still useful as a
      // browse-by-scrolling fallback, just not the primary path at scale.
      return [...nodeTypes].sort((a, b) => displayName(a.type, a.integration).localeCompare(displayName(b.type, b.integration)));
    }
    return nodeTypes
      .map((nt) => ({ nt, s: score(nt, q) }))
      .filter((r): r is { nt: NodeTypeInfo; s: number } => r.s !== null)
      .sort((a, b) => a.s - b.s)
      .map((r) => r.nt);
  }, [nodeTypes, query]);

  useEffect(() => {
    setHighlighted(0);
  }, [query]);

  useEffect(() => {
    if (!anchor) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [anchor, onClose]);

  if (!anchor) return null;

  // Keeps the popover on-screen even when the double-click lands near the
  // canvas's right/bottom edge, rather than letting it overflow off the
  // viewport.
  const POPOVER_WIDTH = 280;
  const POPOVER_MAX_HEIGHT = 360;
  const left = Math.min(anchor.x, window.innerWidth - POPOVER_WIDTH - 16);
  const top = Math.min(anchor.y, window.innerHeight - POPOVER_MAX_HEIGHT - 16);

  return (
    <div
      ref={containerRef}
      className="fixed z-50 flex flex-col overflow-hidden rounded-[var(--radius-md)] border border-border bg-popover shadow-[var(--shadow-xl)]"
      style={{ left, top, width: POPOVER_WIDTH, maxHeight: POPOVER_MAX_HEIGHT }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          onClose();
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          setHighlighted((h) => Math.min(h + 1, results.length - 1));
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          setHighlighted((h) => Math.max(h - 1, 0));
        } else if (e.key === "Enter") {
          e.preventDefault();
          const chosen = results[highlighted];
          if (chosen) onSelect(chosen);
        }
      }}
    >
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Add a node..."
        className="border-b border-border bg-transparent px-3 py-2 text-[13px] text-foreground outline-none placeholder:text-muted-foreground"
      />
      <div className="flex-1 overflow-y-auto p-1">
        {results.length === 0 && (
          <p className="px-2 py-3 text-[12px] text-muted-foreground">
            No node types match "{query}" -- try a different name or category.
          </p>
        )}
        {results.map((nt, i) => {
          const { icon: Icon, colorVar } = categoryPresentation(nt.category);
          const operation = nt.integration ? humanizeOperation(nt.type, nt.integration) : null;
          return (
            <button
              key={nt.type}
              type="button"
              className={cn(
                "flex w-full cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-left",
                i === highlighted ? "bg-accent" : "hover:bg-accent/60",
              )}
              onMouseEnter={() => setHighlighted(i)}
              onClick={() => onSelect(nt)}
            >
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[var(--radius-sm)]"
                style={
                  {
                    "--node-accent": `var(${colorVar})`,
                    background: "color-mix(in srgb, var(--node-accent) 18%, var(--card))",
                    color: "var(--node-accent)",
                  } as React.CSSProperties
                }
              >
                <Icon size={12} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] text-foreground">
                  {displayName(nt.type, nt.integration)}
                </span>
                {operation && <span className="block truncate text-[10px] text-muted-foreground">{operation}</span>}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

import { Search } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { fetchNodeTypes } from "../api/client";
import type { NodeTypeInfo } from "../api/types";
import { CATEGORY_PRESENTATION, categoryPresentation } from "./GenericNode";

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
    <aside className="palette">
      <div className="palette__label">Node Types</div>
      {error && <div className="palette__error">{error}</div>}

      <label className="palette__search">
        <Search size={14} />
        <input
          type="text"
          placeholder="Filter node types..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>

      {categories.map(([category, types]) => {
        const matches = isSearching ? types.filter((t) => t.type.toLowerCase().includes(searchTerm)) : types;
        if (isSearching && matches.length === 0) return null;
        const { colorVar, label } = categoryPresentation(category);
        const isOpen = isSearching || openCategories.has(category);

        return (
          <div key={category} className={`palette-category${isOpen ? " palette-category--open" : ""}`}>
            <button
              type="button"
              className="palette-category__header"
              onClick={() => toggleCategory(category)}
              disabled={isSearching}
            >
              <svg
                className="palette-category__chevron"
                width="10"
                height="10"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
              >
                <polyline points="9 18 15 12 9 6" />
              </svg>
              <span
                className="palette-category__dot"
                style={{ background: `var(${colorVar})` }}
              />
              <span className="palette-category__name">{label}</span>
              <span className="palette-category__count">{matches.length}</span>
            </button>
            <div className="palette-category__body">
              <div className="palette-category__body-inner">
                <ul className="palette__list">
                  {matches.map((nt) => {
                    const { icon: ItemIcon, colorVar: itemColorVar } = categoryPresentation(nt.category);
                    return (
                      <li
                        key={nt.type}
                        className="palette__item"
                        draggable
                        onDragStart={(event) => {
                          event.dataTransfer.setData("application/x-node-type", JSON.stringify(nt));
                          event.dataTransfer.effectAllowed = "move";
                        }}
                      >
                        <span
                          className="palette__item-icon-chip"
                          style={{ "--node-accent": `var(${itemColorVar})` } as CSSProperties}
                        >
                          <ItemIcon size={12} />
                        </span>
                        <span className="palette__item-name">{nt.type}</span>
                        {nt.dynamic_schema && <span className="palette__badge">dynamic</span>}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          </div>
        );
      })}
    </aside>
  );
}

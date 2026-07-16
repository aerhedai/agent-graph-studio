import { useEffect, useState } from "react";
import { fetchNodeTypes } from "../api/client";
import type { NodeTypeInfo } from "../api/types";

// The palette's ENTIRE data source is GET /node-types -- no type name is
// hardcoded anywhere in this file or any other frontend file. A new backend
// node type appears here automatically the next time this component mounts;
// nothing in the frontend needs to change (spec-005 §6's zero-hardcoded-list
// acceptance criterion, frontend half).
export function Palette() {
  const [nodeTypes, setNodeTypes] = useState<NodeTypeInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchNodeTypes()
      .then(setNodeTypes)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  return (
    <aside className="palette">
      <h2>Node types</h2>
      {error && <div className="palette__error">{error}</div>}
      <ul className="palette__list">
        {nodeTypes.map((nt) => (
          <li
            key={nt.type}
            className="palette__item"
            draggable
            onDragStart={(event) => {
              event.dataTransfer.setData("application/x-node-type", JSON.stringify(nt));
              event.dataTransfer.effectAllowed = "move";
            }}
          >
            <span className="palette__item-name">{nt.type}</span>
            {nt.dynamic_schema && <span className="palette__badge">dynamic</span>}
          </li>
        ))}
      </ul>
    </aside>
  );
}

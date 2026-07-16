import type { GraphSpec } from "../api/types";

// Graph JSON (backend/schema/models.py's GraphSpec) has no position data --
// it's the portable, CLI-and-canvas-shared file format, not a canvas-native
// one (spec-005 §4). Loading a graph needs *some* layout; a simple
// level-by-dependency-depth layout reads left-to-right for the linear/
// branching graphs this project's node types actually produce.
export interface LayoutPosition {
  x: number;
  y: number;
}

const X_SPACING = 220;
const Y_SPACING = 110;
const X_OFFSET = 60;
const Y_OFFSET = 80;

export function computeLayout(graph: GraphSpec): Record<string, LayoutPosition> {
  const nodeIds = graph.nodes.map((n) => n.id);
  const nodeIdSet = new Set(nodeIds);
  const adjacency = new Map<string, string[]>(nodeIds.map((id) => [id, []]));
  const incoming = new Map<string, number>(nodeIds.map((id) => [id, 0]));

  for (const edge of graph.edges) {
    if (!nodeIdSet.has(edge.from.node) || !nodeIdSet.has(edge.to.node)) continue;
    adjacency.get(edge.from.node)?.push(edge.to.node);
    incoming.set(edge.to.node, (incoming.get(edge.to.node) ?? 0) + 1);
  }

  const level = new Map<string, number>();
  const remaining = new Map(incoming);
  const queue: string[] = nodeIds.filter((id) => (incoming.get(id) ?? 0) === 0);
  for (const id of queue) level.set(id, 0);

  let head = 0;
  while (head < queue.length) {
    const id = queue[head++];
    for (const next of adjacency.get(id) ?? []) {
      const nextLevel = (level.get(id) ?? 0) + 1;
      level.set(next, Math.max(level.get(next) ?? 0, nextLevel));
      remaining.set(next, (remaining.get(next) ?? 1) - 1);
      if ((remaining.get(next) ?? 0) <= 0 && !queue.includes(next)) {
        queue.push(next);
      }
    }
  }
  // Anything never reached (e.g. part of a cycle in a malformed/hand-edited
  // file) falls back to level 0 rather than crashing the layout.
  for (const id of nodeIds) {
    if (!level.has(id)) level.set(id, 0);
  }

  const countPerLevel = new Map<number, number>();
  const positions: Record<string, LayoutPosition> = {};
  for (const id of nodeIds) {
    const lvl = level.get(id) ?? 0;
    const idx = countPerLevel.get(lvl) ?? 0;
    countPerLevel.set(lvl, idx + 1);
    positions[id] = { x: X_OFFSET + lvl * X_SPACING, y: Y_OFFSET + idx * Y_SPACING };
  }
  return positions;
}

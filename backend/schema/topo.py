from __future__ import annotations

from collections import deque

from backend.schema.models import EdgeSpec


def kahn_order(node_ids: list[str], edges: list[EdgeSpec]) -> tuple[list[str], list[str]]:
    """Topologically sort node_ids given edges, via Kahn's algorithm.

    Returns (order, remaining). `remaining` is non-empty iff the graph
    (restricted to node_ids) contains a cycle -- shared by validation's
    cycle check and (later) the executor's run ordering, so the two can
    never disagree.
    """
    indegree = {n: 0 for n in node_ids}
    adjacency: dict[str, list[str]] = {n: [] for n in node_ids}
    for e in edges:
        if e.from_.node not in adjacency or e.to.node not in indegree:
            continue
        adjacency[e.from_.node].append(e.to.node)
        indegree[e.to.node] += 1

    queue = deque(n for n in node_ids if indegree[n] == 0)
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for m in adjacency[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)

    ordered = set(order)
    remaining = [n for n in node_ids if n not in ordered]
    return order, remaining

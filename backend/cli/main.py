from __future__ import annotations

import json
import sys

import backend.connections  # noqa: F401 -- import side effect registers every connection type
import backend.nodes  # noqa: F401 -- import side effect registers the 4 MVP node types
from backend.connections.errors import ConnectionNotFoundError
from backend.connections.resolver import resolve_connections
from backend.execution.engine import run_graph
from backend.schema.loader import GraphParseError, load_graph_json
from backend.validation.errors import GraphValidationError
from backend.validation.validator import validate_graph


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("Usage: agent-graph-studio <graph.json>", file=sys.stderr)
        return 2

    path = argv[0]
    try:
        graph = load_graph_json(path)
    except GraphParseError as e:
        print(f"Failed to parse graph: {e}", file=sys.stderr)
        return 2

    # Validate first (same "aggregate every issue, including a missing
    # connection, before doing anything else" ordering as the API's
    # POST /runs), then resolve named connections to real clients here --
    # before run_graph is ever called (spec-006 §4).
    try:
        validate_graph(graph)
        resolved_connections = resolve_connections(graph)
    except GraphValidationError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ConnectionNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    run_result = run_graph(graph, resources={"connections": resolved_connections})

    print(json.dumps(run_result.model_dump(mode="json"), indent=2))
    return 0


def cli_entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())

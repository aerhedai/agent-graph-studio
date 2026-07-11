from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from backend.schema.models import GraphSpec


class GraphParseError(Exception):
    """Raised when graph input is not well-formed JSON or does not match the graph schema."""


def parse_graph_json(text: str) -> GraphSpec:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise GraphParseError(f"Invalid JSON: {e}") from e
    try:
        return GraphSpec.model_validate(raw)
    except PydanticValidationError as e:
        raise GraphParseError(f"Graph schema invalid: {e}") from e


def load_graph_json(path: str | Path) -> GraphSpec:
    return parse_graph_json(Path(path).read_text())

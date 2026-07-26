"""spec-025 Phase 5: dynamic option loading -- a hand-curated mapping from a
generated MCP node type's own literal-value input slot (SPEC-025 Phase 0) to
which *other* tool on the same connection produces live values for it, and
how to turn that tool's raw result into `[{label, value}]` pairs.

There is no generic "this parameter has live options" concept in MCP tool
schemas to auto-derive from, and no generic result shape either -- a real
MCP tool's result is plain text (`backend/mcp/transport.py`'s `call_tool`
returns `str`), and different servers format that text completely
differently (some return JSON, Context7's own tools return human-formatted
text with no JSON at all -- confirmed by calling the real, live
`https://mcp.context7.com/mcp` server during this phase's implementation).
So each binding supplies its own `parse`, the same "admin/implementer
curates this specific integration explicitly" spirit as SPEC-023's global
connections and SPEC-025's own catalog-bootstrap -- not a fragile universal
auto-parser that would silently misparse the next server's own format.

Bindings are registered against a connection's *actual generated type name*
at discovery time (see `generated_nodes.generate_node_types_for_connection`),
keyed there by duck-typing which tools a connection's live `tools/list`
actually exposes -- so this applies automatically to any connection a user
names, pointed at a recognized server shape, with zero new backend code
needed per *connection instance* (only per distinct *server integration*,
which is exactly the granularity the spec's own "hand-curated" language
describes)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class OptionBinding:
    source_tool: str
    """Which tool on the *same* connection to call for live values."""
    build_args: Callable[[dict[str, Any]], dict[str, Any]]
    """The bound field's current (in-progress) config/input values -> the
    arguments to call `source_tool` with -- e.g. forwarding a partially
    typed search term."""
    parse: Callable[[str], list[dict[str, str]]]
    """source_tool's raw result text -> [{"label": ..., "value": ...}]."""


_bindings: dict[tuple[str, str], OptionBinding] = {}


def register_option_binding(node_type: str, field_name: str, binding: OptionBinding) -> None:
    _bindings[(node_type, field_name)] = binding


def get_option_binding(node_type: str, field_name: str) -> OptionBinding | None:
    return _bindings.get((node_type, field_name))


def fields_with_bindings(node_type: str) -> list[str]:
    return sorted(field_name for (t, field_name) in _bindings if t == node_type)


def unregister_for_node_type(node_type: str) -> None:
    """Mirrors generated_nodes.unregister_for_connection's own "clear before
    re-registering" ordering -- called for every type name a connection is
    about to regenerate, so a stale binding from a previous discovery never
    outlives the type it was bound to."""
    for key in [k for k in _bindings if k[0] == node_type]:
        del _bindings[key]


# --- Context7 (https://mcp.context7.com/mcp) -- this phase's live-verified
# example. Its `resolve-library-id` tool returns plain, human-formatted
# text (not JSON), one library per block separated by "----------", e.g.:
#
#   - Title: React
#   - Context7-compatible library ID: /reactjs/react.dev
#   - Description: ...
#
# `query-docs`'s own `libraryId` argument expects exactly that ID -- this
# binding turns "the user must know/copy that ID first" into a live,
# type-to-search dropdown.


def _parse_context7_library_list(raw: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for block in raw.split("----------"):
        title_match = re.search(r"^- Title:\s*(.+)$", block, re.MULTILINE)
        id_match = re.search(r"^- Context7-compatible library ID:\s*(\S+)$", block, re.MULTILINE)
        if title_match and id_match:
            options.append({"label": f"{title_match.group(1).strip()} ({id_match.group(1)})", "value": id_match.group(1)})
    return options


def context7_query_docs_binding() -> OptionBinding:
    return OptionBinding(
        source_tool="resolve-library-id",
        build_args=lambda current_config: {
            "query": current_config.get("query", ""),
            "libraryName": current_config.get("query", ""),
        },
        parse=_parse_context7_library_list,
    )

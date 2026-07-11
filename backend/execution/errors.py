from __future__ import annotations


class NodeExecutionError(Exception):
    """Raised by a node's execute() body to signal a recoverable, trace-captured failure."""

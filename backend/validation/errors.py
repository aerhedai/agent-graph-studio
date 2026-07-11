from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    rule: str
    node_id: str | None
    message: str


class GraphValidationError(Exception):
    """Raised when a graph fails one or more §5 validation rules.

    Carries every issue found (validation aggregates, it does not fail
    fast) so the caller sees everything wrong with the graph at once.
    """

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        lines = "\n".join(
            f"  - [{issue.rule}] {issue.node_id or '<graph>'}: {issue.message}"
            for issue in issues
        )
        super().__init__(f"Graph validation failed with {len(issues)} error(s):\n{lines}")

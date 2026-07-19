from __future__ import annotations

import re
from typing import Callable

from pydantic import BaseModel

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class ConditionalBranchConfig(BaseModel):
    condition: str


_CONDITION_RE = re.compile(r"^(\w+)\('(.*)'\)$")
_CONDITION_FUNCS: dict[str, Callable[[str, str], bool]] = {
    "contains": lambda value, arg: arg in value,
    "equals": lambda value, arg: value == arg,
}


def _evaluate_condition(condition: str, value: str) -> bool:
    match = _CONDITION_RE.match(condition.strip())
    if not match:
        raise NodeExecutionError(f"Unparseable condition expression: {condition!r}")
    func_name, arg = match.groups()
    func = _CONDITION_FUNCS.get(func_name)
    if func is None:
        raise NodeExecutionError(f"Unknown condition function: {func_name!r}")
    return func(value, arg)


@register_node(
    "conditional_branch",
    inputs=[InputSlotSpec("value", TEXT)],
    outputs=[
        OutputSlotSpec("true_branch", TEXT),
        OutputSlotSpec("false_branch", TEXT),
    ],
    config_model=ConditionalBranchConfig,
    category="core",
)
def execute_conditional_branch(ctx: ExecutionContext) -> NodeResult:
    config = ConditionalBranchConfig.model_validate(ctx.node.config)
    value = ctx.inputs["value"]
    fired = "true_branch" if _evaluate_condition(config.condition, value) else "false_branch"
    return NodeResult(outputs={fired: value})

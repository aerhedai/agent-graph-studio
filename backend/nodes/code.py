"""`code` node: runs a single, graph-author-provided Python function against
its inputs (ARCHITECTURE.md §2.1's "Code" escape hatch).

SECURITY: function_source is executed via plain exec() with no sandboxing,
per spec-002 §7's own MVP recommendation. Do not run graphs from untrusted
sources. Full sandboxing is out of scope for this spec.
"""

from __future__ import annotations

import ast
from typing import Any

from pydantic import BaseModel, field_validator

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.models import NodeSpec
from backend.schema.types import TEXT


class CodeSourceError(ValueError):
    """Raised when function_source doesn't define exactly one simple function."""


def _parse_function(source: str) -> tuple[str, list[str]]:
    """Parse function_source via ast (no execution) and return (name, param_names).

    Requires exactly one top-level `def` with no decorators, no *args/**kwargs,
    and no default argument values -- keeps every parameter a simple, always-
    required text input slot. Raises CodeSourceError on any violation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise CodeSourceError(f"function_source is not valid Python: {e}") from e

    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise CodeSourceError(
            "function_source must contain exactly one top-level 'def' function "
            "(async functions, classes, and extra top-level statements are not supported)"
        )

    fn = tree.body[0]
    if fn.decorator_list:
        raise CodeSourceError("function_source function must not have decorators")

    args = fn.args
    if args.vararg is not None or args.kwarg is not None:
        raise CodeSourceError("function_source function must not use *args/**kwargs")
    if args.defaults or any(d is not None for d in args.kw_defaults):
        raise CodeSourceError("function_source function must not use default argument values")

    param_names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if len(param_names) != len(set(param_names)):
        raise CodeSourceError("function_source function has duplicate parameter names")

    return fn.name, param_names


class CodeConfig(BaseModel):
    function_source: str

    @field_validator("function_source")
    @classmethod
    def _validate_function_source(cls, v: str) -> str:
        _parse_function(v)
        return v


def _resolve_code_slots(
    node: NodeSpec,
) -> tuple[list[InputSlotSpec], list[OutputSlotSpec]] | None:
    """Per-instance schema: one required text input per function parameter,
    plus one fixed "result" text output. Returns None (not []) if
    function_source can't be parsed -- [] would be indistinguishable from a
    real zero-parameter function; None tells validation to skip this node
    and let check_config_schema report the real error."""
    source = node.config.get("function_source")
    if not isinstance(source, str):
        return None
    try:
        _, param_names = _parse_function(source)
    except CodeSourceError:
        return None
    inputs = [InputSlotSpec(name, TEXT) for name in param_names]
    outputs = [OutputSlotSpec("result", TEXT)]
    return inputs, outputs


@register_node(
    "code",
    inputs=[],
    outputs=[],
    config_model=CodeConfig,
    resolve_slots=_resolve_code_slots,
)
def execute_code(ctx: ExecutionContext) -> NodeResult:
    config = CodeConfig.model_validate(ctx.node.config)
    try:
        func_name, param_names = _parse_function(config.function_source)
        namespace: dict[str, Any] = {}
        exec(compile(config.function_source, f"<code node {ctx.node.id}>", "exec"), namespace)
        fn = namespace[func_name]
        kwargs = {name: ctx.inputs[name] for name in param_names}
        result = fn(**kwargs)
    except Exception as e:
        raise NodeExecutionError(f"code node execution failed: {e}") from e

    if not isinstance(result, str):
        raise NodeExecutionError(
            "code node function must return a string (result slot is type text), "
            f"got {type(result).__name__}"
        )
    return NodeResult(outputs={"result": result})

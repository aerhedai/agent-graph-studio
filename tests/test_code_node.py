from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.code import (
    CodeConfig,
    CodeSourceError,
    _parse_function,
    _resolve_code_slots,
    execute_code,
)
from backend.schema.models import NodeSpec


def _node(function_source: str) -> NodeSpec:
    return NodeSpec(id="n1", type="code", config={"function_source": function_source})


# --- _parse_function ---------------------------------------------------


def test_parse_function_valid():
    name, params = _parse_function("def transform(text, prefix):\n    return prefix + text\n")
    assert name == "transform"
    assert params == ["text", "prefix"]


def test_parse_function_zero_params():
    name, params = _parse_function("def greet():\n    return 'hi'\n")
    assert name == "greet"
    assert params == []


def test_parse_function_syntax_error():
    with pytest.raises(CodeSourceError):
        _parse_function("def broken(:\n    pass")


def test_parse_function_rejects_multiple_defs():
    with pytest.raises(CodeSourceError):
        _parse_function("def a():\n    pass\ndef b():\n    pass\n")


def test_parse_function_rejects_non_function_toplevel():
    with pytest.raises(CodeSourceError):
        _parse_function("x = 1\n")


def test_parse_function_rejects_varargs():
    with pytest.raises(CodeSourceError):
        _parse_function("def f(*args):\n    return ''\n")


def test_parse_function_rejects_kwargs():
    with pytest.raises(CodeSourceError):
        _parse_function("def f(**kwargs):\n    return ''\n")


def test_parse_function_rejects_defaults():
    with pytest.raises(CodeSourceError):
        _parse_function("def f(text='x'):\n    return text\n")


def test_parse_function_rejects_decorators():
    with pytest.raises(CodeSourceError):
        _parse_function("@staticmethod\ndef f(text):\n    return text\n")


def test_parse_function_rejects_async():
    with pytest.raises(CodeSourceError):
        _parse_function("async def f(text):\n    return text\n")


def test_parse_function_rejects_duplicate_param_names():
    with pytest.raises(CodeSourceError):
        _parse_function("def f(text, text):\n    return text\n")


# --- CodeConfig ----------------------------------------------------------


def test_code_config_rejects_malformed_source():
    with pytest.raises(ValidationError):
        CodeConfig.model_validate({"function_source": "not python("})


def test_code_config_accepts_valid_source():
    config = CodeConfig.model_validate({"function_source": "def f(text):\n    return text\n"})
    assert config.function_source


# --- _resolve_code_slots ---------------------------------------------------


def test_resolve_code_slots_valid_source():
    node = _node("def transform(text, prefix):\n    return prefix + text\n")
    resolved = _resolve_code_slots(node)
    assert resolved is not None
    inputs, outputs = resolved
    assert [s.name for s in inputs] == ["text", "prefix"]
    assert [s.name for s in outputs] == ["result"]


def test_resolve_code_slots_returns_none_on_malformed_source():
    node = _node("not python(")
    assert _resolve_code_slots(node) is None


def test_resolve_code_slots_returns_none_when_config_missing_source():
    node = NodeSpec(id="n1", type="code", config={})
    assert _resolve_code_slots(node) is None


# --- execute_code ----------------------------------------------------------


def test_execute_code_success():
    node = _node("def transform(text, prefix):\n    return prefix + text.upper()\n")
    ctx = ExecutionContext(node=node, inputs={"text": "hello", "prefix": ">> "})

    result = execute_code(ctx)

    assert result.outputs == {"result": ">> HELLO"}


def test_execute_code_wraps_runtime_error():
    node = _node("def transform(text):\n    raise ValueError('boom')\n")
    ctx = ExecutionContext(node=node, inputs={"text": "hello"})

    with pytest.raises(NodeExecutionError):
        execute_code(ctx)


def test_execute_code_rejects_non_string_return():
    node = _node("def transform(text):\n    return len(text)\n")
    ctx = ExecutionContext(node=node, inputs={"text": "hello"})

    with pytest.raises(NodeExecutionError):
        execute_code(ctx)

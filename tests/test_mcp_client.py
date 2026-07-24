from __future__ import annotations

import pytest

from backend.mcp.client import McpConnectionError, McpToolInfo, coerce_value, content_to_text, find_tool


def test_find_tool_found():
    tools = [
        McpToolInfo(name="a", param_names=[], param_json_types={}),
        McpToolInfo(name="b", param_names=[], param_json_types={}),
    ]
    assert find_tool(tools, "b") is tools[1]


def test_find_tool_not_found():
    assert find_tool([], "missing") is None


def test_coerce_value_string_passthrough():
    assert coerce_value("hello", "string") == "hello"


def test_coerce_value_integer():
    assert coerce_value("42", "integer") == 42


def test_coerce_value_number():
    assert coerce_value("3.5", "number") == 3.5


def test_coerce_value_boolean_true():
    assert coerce_value("true", "boolean") is True


def test_coerce_value_boolean_false():
    assert coerce_value("no", "boolean") is False


def test_coerce_value_object():
    assert coerce_value('{"a": 1}', "object") == {"a": 1}


def test_coerce_value_array():
    assert coerce_value("[1, 2, 3]", "array") == [1, 2, 3]


def test_coerce_value_empty_string_array_becomes_empty_list():
    # spec-019: a real dynamically-generated MCP node call hit this live --
    # a model passed "" for an optional array param (excludePatterns),
    # which is not valid JSON (json.loads("") raises), but is a common way
    # a model expresses "nothing here" for an optional param.
    assert coerce_value("", "array") == []


def test_coerce_value_empty_string_object_becomes_empty_dict():
    assert coerce_value("", "object") == {}


def test_coerce_value_whitespace_only_string_array_becomes_empty_list():
    assert coerce_value("   ", "array") == []


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _ImageBlock:
    def __init__(self) -> None:
        self.type = "image"


def test_content_to_text_joins_text_blocks():
    content = [_TextBlock("hello"), _TextBlock("world")]
    assert content_to_text(content) == "hello\nworld"


def test_content_to_text_ignores_non_text_blocks():
    content = [_TextBlock("hello"), _ImageBlock()]
    assert content_to_text(content) == "hello"


def test_content_to_text_raises_when_no_text_content():
    with pytest.raises(McpConnectionError):
        content_to_text([_ImageBlock()])


def test_content_to_text_raises_on_empty_content():
    with pytest.raises(McpConnectionError):
        content_to_text([])

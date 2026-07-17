"""Importing this package registers all MVP node types into the default registry.

Adding a new node type means creating a new module here (declaring its
input/output/config schema via @register_node) and importing it below --
no changes needed to registry/, validation/, or execution/.
"""

from backend.nodes import (  # noqa: F401
    agent,
    code,
    conditional_branch,
    fan_out,
    llm_call,
    loop,
    mcp_call,
    merge,
    text_input,
    text_output,
    uppercase_text,
)

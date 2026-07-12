"""Importing this package registers all MVP node types into the default registry.

Adding a new node type means creating a new module here (declaring its
input/output/config schema via @register_node) and importing it below --
no changes needed to registry/, validation/, or execution/.
"""

from backend.nodes import (  # noqa: F401
    code,
    conditional_branch,
    llm_call,
    mcp_call,
    text_input,
    text_output,
    uppercase_text,
)

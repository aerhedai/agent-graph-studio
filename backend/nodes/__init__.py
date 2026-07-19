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
    generic_adapter,
    ingest_document,
    llm_call,
    loop,
    mcp_call,
    memory,
    merge,
    model,
    schedule_trigger,
    telegram_adapter,
    text_input,
    text_output,
    tool_group,
    uppercase_text,
    vector_search,
    webhook_trigger,
)

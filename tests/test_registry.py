from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.registry.base import NodeDefinition, NodeRegistry, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class _DummyConfig(BaseModel):
    pass


def _dummy_execute(ctx):
    return None


def test_register_and_get(fresh_registry: NodeRegistry):
    fresh_registry.register(
        NodeDefinition(
            type_name="dummy",
            inputs=[],
            outputs=[OutputSlotSpec("out", TEXT)],
            config_model=_DummyConfig,
            execute=_dummy_execute,
            category="core",
        )
    )
    definition = fresh_registry.get("dummy")
    assert definition is not None
    assert definition.type_name == "dummy"


def test_unknown_type_lookup_returns_none(fresh_registry: NodeRegistry):
    assert fresh_registry.get("does_not_exist") is None


def test_duplicate_registration_raises(fresh_registry: NodeRegistry):
    definition = NodeDefinition(
        type_name="dummy",
        inputs=[],
        outputs=[],
        config_model=_DummyConfig,
        execute=_dummy_execute,
        category="core",
    )
    fresh_registry.register(definition)
    with pytest.raises(ValueError):
        fresh_registry.register(definition)


def test_register_node_decorator_registers_into_given_registry(fresh_registry: NodeRegistry):
    @register_node(
        "decorated",
        inputs=[],
        outputs=[OutputSlotSpec("out", TEXT)],
        config_model=_DummyConfig,
        category="core",
        registry=fresh_registry,
    )
    def execute_decorated(ctx):
        return None

    assert fresh_registry.get("decorated") is not None


def test_all_four_mvp_types_registered():
    import backend.nodes  # noqa: F401
    from backend.registry.base import default_registry

    for type_name in ("text_input", "llm_call", "conditional_branch", "text_output"):
        assert default_registry.get(type_name) is not None, f"{type_name} not registered"


def test_tool_group_is_both_root_and_sub_node():
    """spec-014: `tool_group` is the first hybrid node type -- it declares its
    own `sub_node_slots` (a root/cluster node, like `agent`) AND its own
    `sub_node_role` (a sub-node, like `model`), simultaneously. Nothing in
    `NodeDefinition` needed to change to support this; both fields are
    already independent and optional."""
    import backend.nodes  # noqa: F401
    from backend.registry.base import default_registry

    definition = default_registry.get("tool_group")
    assert definition is not None

    assert definition.sub_node_slots is not None
    assert "tools" in definition.sub_node_slots
    assert definition.sub_node_slots["tools"].cardinality == "many"
    assert definition.sub_node_slots["tools"].accepts_role is None

    assert definition.sub_node_role == "tool_group"

from __future__ import annotations

from pathlib import Path

import pytest

from backend.registry.base import NodeRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "graphs"


def load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture
def fresh_registry() -> NodeRegistry:
    return NodeRegistry()


@pytest.fixture(autouse=True, scope="session")
def _register_mvp_nodes():
    import backend.nodes  # noqa: F401

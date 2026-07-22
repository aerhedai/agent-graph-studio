from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.registry.base import NodeRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "graphs"

# spec-017: the fixed shared secret every test file's TestClient sends via
# `Authorization: Bearer {TEST_API_KEY}` -- must match isolated_api_key
# below, which sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value.
TEST_API_KEY = "test-api-key"


def load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture
def fresh_registry() -> NodeRegistry:
    return NodeRegistry()


@pytest.fixture(autouse=True, scope="session")
def _register_mvp_nodes():
    import backend.nodes  # noqa: F401
    import backend.connections  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_connections_store(tmp_path, monkeypatch):
    """Every test gets its own empty, throwaway connection store -- no test
    may ever read or write the real ~/.agent-graph-studio/connections.json.
    """
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_CONNECTIONS_PATH", str(tmp_path / "connections.json"))


@pytest.fixture(autouse=True)
def isolated_runs_db(tmp_path, monkeypatch):
    """Every test gets its own empty, throwaway runs database -- no test may
    ever read or write the real ~/.agent-graph-studio/runs.db (spec-010).
    Mirrors isolated_connections_store above exactly."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_RUNS_DB_PATH", str(tmp_path / "runs.db"))


@pytest.fixture(autouse=True)
def isolated_graphs_db(tmp_path, monkeypatch):
    """Every test gets its own empty, throwaway graphs database -- no test
    may ever read or write the real ~/.agent-graph-studio/graphs.db
    (spec-015). Mirrors isolated_runs_db above exactly."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_GRAPHS_DB_PATH", str(tmp_path / "graphs.db"))


@pytest.fixture(autouse=True)
def isolated_encryption_key(monkeypatch):
    """Every test gets a real, freshly-generated Fernet key -- the
    connections store (spec-017) refuses to operate without one, so every
    test touching it (directly or via the API) needs this set."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def isolated_api_key(monkeypatch):
    """Every test gets the same fixed shared credential (spec-017) -- see
    TEST_API_KEY above, which every TestClient(app, headers=...) across the
    test suite sends by default."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_API_KEY", TEST_API_KEY)


@pytest.fixture
def registered_test_connection():
    """Registers a connection named "test-connection" in the isolated store
    so validate_graph()'s missing_connection check passes. The stored
    type/config are placeholders -- tests needing the actual client
    typically inject resources={"connections": {"test-connection": fake}}
    directly into run_graph, bypassing real resolution/construction."""
    from backend.connections.store import add_connection

    add_connection("test-connection", "anthropic", {"api_key": "unused-in-tests"})
    return "test-connection"

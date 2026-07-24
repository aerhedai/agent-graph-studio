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


@pytest.fixture(autouse=True)
def isolated_settings_store(tmp_path, monkeypatch):
    """Every test gets its own empty, throwaway settings file -- no test may
    ever read or write the real ~/.agent-graph-studio/settings.json
    (spec-018). Mirrors isolated_connections_store above exactly."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_SETTINGS_PATH", str(tmp_path / "settings.json"))


@pytest.fixture(autouse=True)
def isolated_users_db(tmp_path, monkeypatch):
    """Every test gets its own empty, throwaway users database -- no test
    may ever read or write the real ~/.agent-graph-studio/users.db
    (spec-020). Mirrors isolated_graphs_db above exactly."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_USERS_DB_PATH", str(tmp_path / "users.db"))


# spec-020: three new required secrets, none of which any pre-existing test
# needs to know or care about individually -- every existing
# TestClient(app, headers={"Authorization": f"Bearer {TEST_API_KEY}"}) test
# keeps working completely unmodified via the shared-key branch of
# require_auth, exactly the "no existing test should need to change"
# constraint this spec was designed around.


@pytest.fixture(autouse=True)
def isolated_jwt_secret(monkeypatch):
    """A real, sufficiently long signing secret -- every test touching
    require_auth's JWT branch, or issuing/verifying a token directly,
    needs this set (backend/auth/jwt.py refuses to operate without one)."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_JWT_SECRET", "test-jwt-signing-secret-at-least-32-bytes-long")


@pytest.fixture(autouse=True)
def isolated_google_oauth_config(monkeypatch):
    """Fake-but-present Google OAuth credentials -- enough for
    ensure_google_oauth_configured()'s startup check and for building a
    real authorization URL; no test in this suite performs a real exchange
    against Google's actual endpoints (that's this spec's own live-
    verification step, run separately, not part of the automated suite)."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID", "test-google-client-id")
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET", "test-google-client-secret")


@pytest.fixture(autouse=True)
def isolated_admin_email(monkeypatch):
    """The first-admin bootstrap email -- required at startup
    (users_store.ensure_admin_email_configured)."""
    monkeypatch.setenv("AGENT_GRAPH_STUDIO_ADMIN_EMAIL", "admin@example.com")


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

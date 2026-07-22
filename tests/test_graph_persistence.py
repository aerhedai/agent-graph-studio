from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import _reactivate_persisted_graphs, app
from backend.storage import graphs_store
from backend.triggers import registry as trigger_registry

client = TestClient(app)


def _simple_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [{"id": "in", "type": "text_input", "config": {"value": "hi"}}],
        "edges": [],
    }


def _webhook_graph() -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
        ],
    }


def _deactivate_quietly(graph_id: str) -> None:
    client.post(f"/graphs/{graph_id}/deactivate")


# --- graphs_store unit tests (direct, no HTTP layer) -----------------------


def test_create_get_list_update_delete_graph_round_trip():
    row = graphs_store.create_graph("g1", "My Graph", '{"a": 1}', "2026-01-01T00:00:00")
    assert row.graph_id == "g1"
    assert row.is_active is False

    fetched = graphs_store.get_graph("g1")
    assert fetched is not None
    assert fetched.name == "My Graph"
    assert fetched.spec_json == '{"a": 1}'

    summaries = graphs_store.list_graphs()
    assert len(summaries) == 1
    assert summaries[0].graph_id == "g1"

    updated = graphs_store.update_graph("g1", "2026-01-02T00:00:00", name="Renamed")
    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.spec_json == '{"a": 1}'  # unchanged, partial update

    assert graphs_store.delete_graph("g1") is True
    assert graphs_store.get_graph("g1") is None
    assert graphs_store.delete_graph("g1") is False  # already gone


def test_update_nonexistent_graph_returns_none():
    assert graphs_store.update_graph("does-not-exist", "2026-01-01T00:00:00", name="x") is None


def test_set_active_state_upserts_a_never_explicitly_saved_graph_id():
    """SPEC-009's existing contract (graph_id is caller-chosen, doesn't need
    to have been saved first via POST /graphs) must keep working."""
    assert graphs_store.get_graph("never-saved") is None
    graphs_store.set_active_state("never-saved", '{"v": 1}', is_active=True, updated_at="2026-01-01T00:00:00")
    row = graphs_store.get_graph("never-saved")
    assert row is not None
    assert row.name == "never-saved"  # defaulted from the id itself
    assert row.is_active is True

    # Re-activating updates spec_json/is_active but preserves the name.
    graphs_store.update_graph("never-saved", "2026-01-01T00:00:01", name="Given A Real Name")
    graphs_store.set_active_state("never-saved", '{"v": 2}', is_active=True, updated_at="2026-01-01T00:00:02")
    row = graphs_store.get_graph("never-saved")
    assert row.name == "Given A Real Name"
    assert row.spec_json == '{"v": 2}'


def test_set_is_active_only_flips_the_flag_leaves_spec_untouched():
    graphs_store.create_graph("g2", "Name", '{"x": 1}', "2026-01-01T00:00:00")
    graphs_store.set_is_active("g2", is_active=True, updated_at="2026-01-01T00:00:01")
    assert graphs_store.get_graph("g2").is_active is True
    graphs_store.set_is_active("g2", is_active=False, updated_at="2026-01-01T00:00:02")
    row = graphs_store.get_graph("g2")
    assert row.is_active is False
    assert row.spec_json == '{"x": 1}'


def test_list_active_graphs_only_returns_active_rows():
    graphs_store.create_graph("active-1", "A", '{"a": 1}', "2026-01-01T00:00:00")
    graphs_store.create_graph("inactive-1", "B", '{"b": 1}', "2026-01-01T00:00:00")
    graphs_store.set_is_active("active-1", is_active=True, updated_at="2026-01-01T00:00:01")

    active = graphs_store.list_active_graphs()
    assert [r.graph_id for r in active] == ["active-1"]


# --- API layer -------------------------------------------------------------


def test_post_graphs_creates_with_server_assigned_id():
    response = client.post("/graphs", json={"name": "Test Graph", "spec": _simple_graph()})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Graph"
    assert body["is_active"] is False
    assert isinstance(body["graph_id"], str) and len(body["graph_id"]) > 0

    fetched = client.get(f"/graphs/{body['graph_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["spec"]["nodes"][0]["id"] == "in"


def test_get_graphs_list_is_summary_only_no_spec_field():
    created = client.post("/graphs", json={"name": "List Me", "spec": _simple_graph()}).json()
    listing = client.get("/graphs").json()
    entry = next(g for g in listing if g["graph_id"] == created["graph_id"])
    assert entry["name"] == "List Me"
    assert "spec" not in entry


def test_get_nonexistent_graph_returns_404():
    assert client.get("/graphs/does-not-exist").status_code == 404


def test_put_graphs_partial_update():
    created = client.post("/graphs", json={"name": "Original", "spec": _simple_graph()}).json()
    graph_id = created["graph_id"]

    renamed = client.put(f"/graphs/{graph_id}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["spec"]["nodes"][0]["id"] == "in"  # spec unchanged

    assert client.put("/graphs/does-not-exist", json={"name": "x"}).status_code == 404


def test_delete_graph_removes_it():
    created = client.post("/graphs", json={"name": "Delete Me", "spec": _simple_graph()}).json()
    graph_id = created["graph_id"]
    assert client.delete(f"/graphs/{graph_id}").status_code == 204
    assert client.get(f"/graphs/{graph_id}").status_code == 404
    assert client.delete(f"/graphs/{graph_id}").status_code == 404


def test_delete_active_graph_deactivates_first():
    graph_id = "delete-while-active"
    try:
        client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        assert trigger_registry.get_active(graph_id) is not None

        assert client.delete(f"/graphs/{graph_id}").status_code == 204
        assert trigger_registry.get_active(graph_id) is None
        assert client.get(f"/graphs/{graph_id}").status_code == 404
    finally:
        _deactivate_quietly(graph_id)


def test_graphs_active_route_still_matches_the_literal_path_not_the_dynamic_one():
    """Regression guard for the route-ordering hazard: GET /graphs/{graph_id}
    must not swallow GET /graphs/active."""
    response = client.get("/graphs/active")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# --- durable activation: persists is_active + spec, survives re-registration ---


def test_activate_persists_is_active_and_spec_to_the_graphs_store():
    graph_id = "persist-on-activate"
    try:
        client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        row = graphs_store.get_graph(graph_id)
        assert row is not None
        assert row.is_active is True
        assert row.name == graph_id  # upserted, never explicitly named
    finally:
        _deactivate_quietly(graph_id)
        assert graphs_store.get_graph(graph_id).is_active is False


def test_reactivate_persisted_graphs_reregisters_active_ones_via_startup_hook():
    """Direct call to the startup hook (bypassing FastAPI's lifespan/
    TestClient quirks entirely, per SPEC-010's own precedent of preferring
    a real, deterministic check over relying on ASGI lifespan timing) --
    simulates exactly what happens on a real process restart."""
    graph_id = "startup-reactivate-1"
    graphs_store.create_graph(graph_id, graph_id, __import__("json").dumps(_webhook_graph()), "2026-01-01T00:00:00")
    graphs_store.set_is_active(graph_id, is_active=True, updated_at="2026-01-01T00:00:01")
    assert trigger_registry.get_active(graph_id) is None  # not in memory yet -- simulates a fresh process

    try:
        _reactivate_persisted_graphs()
        assert trigger_registry.get_active(graph_id) is not None
        fire = client.post(f"/webhooks/{graph_id}/trigger", json={"hello": "world"})
        assert fire.status_code == 200
    finally:
        _deactivate_quietly(graph_id)


def test_one_broken_persisted_graph_does_not_block_others_from_reactivating():
    good_id, bad_id = "startup-good-graph", "startup-bad-graph"
    graphs_store.create_graph(good_id, good_id, __import__("json").dumps(_webhook_graph()), "2026-01-01T00:00:00")
    graphs_store.set_is_active(good_id, is_active=True, updated_at="2026-01-01T00:00:01")
    # An intentionally invalid persisted spec -- references an unregistered node type.
    bad_spec = {"version": "0.1", "nodes": [{"id": "n1", "type": "not_a_real_node_type", "config": {}}], "edges": []}
    graphs_store.create_graph(bad_id, bad_id, __import__("json").dumps(bad_spec), "2026-01-01T00:00:00")
    graphs_store.set_is_active(bad_id, is_active=True, updated_at="2026-01-01T00:00:01")

    try:
        _reactivate_persisted_graphs()  # must not raise, must not skip the good one
        assert trigger_registry.get_active(good_id) is not None
        assert trigger_registry.get_active(bad_id) is None
    finally:
        _deactivate_quietly(good_id)
        _deactivate_quietly(bad_id)

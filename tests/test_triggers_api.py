from __future__ import annotations

import time

from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.auth import jwt as auth_jwt
from backend.storage import users_store
from backend.triggers import registry as trigger_registry
from backend.triggers import scheduler as trigger_scheduler

# spec-017: must match tests/conftest.py's TEST_API_KEY (the isolated_api_key
# fixture sets AGENT_GRAPH_STUDIO_API_KEY to this same literal value).
client = TestClient(app, headers={"Authorization": "Bearer test-api-key"})


def _schedule_graph(cron: str = "*/5 * * * *") -> dict:
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "schedule_trigger", "config": {"cron": cron}},
            {
                "id": "echo",
                "type": "code",
                "config": {"function_source": "def run(ts):\n    return f'fired at {ts}'"},
            },
        ],
        "edges": [{"from": {"node": "trigger", "slot": "fired_at"}, "to": {"node": "echo", "slot": "ts"}}],
    }


def _webhook_graph() -> dict:
    # spec-012: webhook_trigger is now a cluster root -- a generic_adapter
    # sub-node (today's exact SPEC-009 passthrough behavior) must be wired
    # in via a sub_node edge for this to behave identically to before.
    return {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
            {
                "id": "parse",
                "type": "code",
                "config": {
                    "function_source": "def run(raw):\n    return __import__('json').loads(raw)['name']"
                },
            },
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
            {"from": {"node": "trigger", "slot": "payload"}, "to": {"node": "parse", "slot": "raw"}},
        ],
    }


def _wait_for_run(run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    assert body is not None
    return body


def _deactivate_quietly(graph_id: str) -> None:
    client.post(f"/graphs/{graph_id}/deactivate")


def test_activate_schedule_trigger_registers_a_real_scheduler_job():
    graph_id = "sched-graph-1"
    try:
        response = client.post(f"/graphs/{graph_id}/activate", json=_schedule_graph())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "active"
        assert body["triggers"] == [
            {"node_id": "trigger", "type": "schedule_trigger", "endpoint_or_schedule": "*/5 * * * *"}
        ]
        assert trigger_scheduler.get_jobs_for_graph(graph_id) == [f"{graph_id}:trigger"]
    finally:
        _deactivate_quietly(graph_id)


def test_activate_webhook_trigger_registers_a_real_route_and_fires_with_real_payload():
    graph_id = "webhook-graph-1"
    try:
        activate = client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        assert activate.status_code == 200
        endpoint = activate.json()["triggers"][0]["endpoint_or_schedule"]
        # spec-017: the reported endpoint now carries the API key as a
        # query param, ready to paste directly into an external webhook
        # config -- the bare route path is still what's actually registered.
        assert endpoint == f"/webhooks/{graph_id}/trigger?key=test-api-key"

        fire = client.post(endpoint, json={"name": "spec-009"})
        assert fire.status_code == 200
        run_id = fire.json()["run_id"]

        run = _wait_for_run(run_id)
        assert run["status"] == "completed"
        assert run["result"] == {}  # no text_output/result_slot node in this graph
        parse_trace = next(t for t in run["trace"] if t["node_id"] == "parse")
        assert parse_trace["error"] is None
        assert parse_trace["outputs"] == {"result": "spec-009"}
    finally:
        _deactivate_quietly(graph_id)


def test_deactivate_removes_webhook_route_and_scheduler_job():
    graph_id = "deactivate-graph-1"
    try:
        client.post(f"/graphs/{graph_id}/activate", json=_webhook_graph())
        endpoint = f"/webhooks/{graph_id}/trigger"
        assert client.post(endpoint, json={}).status_code == 200

        deactivate = client.post(f"/graphs/{graph_id}/deactivate")
        assert deactivate.status_code == 200
        assert deactivate.json() == {"status": "inactive"}

        # Route is gone -- FastAPI 404s a path with no matching route at all.
        assert client.post(endpoint, json={}).status_code == 404
        assert trigger_registry.get_active(graph_id) is None
    finally:
        _deactivate_quietly(graph_id)


def test_deactivating_a_never_activated_graph_returns_404():
    response = client.post("/graphs/never-activated/deactivate")
    assert response.status_code == 404


def test_reactivating_an_already_active_graph_id_replaces_cleanly():
    graph_id = "reactivate-graph-1"
    try:
        client.post(f"/graphs/{graph_id}/activate", json=_schedule_graph())
        assert len(trigger_scheduler.get_jobs_for_graph(graph_id)) == 1

        # Re-activate with a different cron -- must replace, not duplicate.
        response = client.post(f"/graphs/{graph_id}/activate", json=_schedule_graph(cron="*/10 * * * *"))
        assert response.status_code == 200
        jobs = trigger_scheduler.get_jobs_for_graph(graph_id)
        assert len(jobs) == 1
        assert response.json()["triggers"][0]["endpoint_or_schedule"] == "*/10 * * * *"
    finally:
        _deactivate_quietly(graph_id)


def test_activate_rejects_invalid_cron_expression():
    graph_id = "bad-cron-graph"
    graph = _schedule_graph(cron="not a cron expression")
    response = client.post(f"/graphs/{graph_id}/activate", json=graph)
    assert response.status_code == 422
    assert trigger_registry.get_active(graph_id) is None
    assert trigger_scheduler.get_jobs_for_graph(graph_id) == []


def test_activate_invalid_graph_returns_422_and_does_not_activate():
    graph_id = "invalid-graph-1"
    graph = {
        "version": "0.1",
        "nodes": [{"id": "n1", "type": "llm_call", "config": {"model": "x", "max_tokens": 10}}],
        "edges": [],
    }
    response = client.post(f"/graphs/{graph_id}/activate", json=graph)
    assert response.status_code == 422
    assert trigger_registry.get_active(graph_id) is None


def test_graphs_active_reflects_current_state():
    assert client.get("/graphs/active").json() == []

    graph_id = "active-listing-graph"
    client.post(f"/graphs/{graph_id}/activate", json=_schedule_graph())
    try:
        listing = client.get("/graphs/active").json()
        assert len(listing) == 1
        assert listing[0]["graph_id"] == graph_id
        assert listing[0]["triggers"][0]["node_id"] == "trigger"
    finally:
        _deactivate_quietly(graph_id)

    assert client.get("/graphs/active").json() == []


def test_activated_graph_can_also_be_run_manually_via_post_runs_without_conflict():
    """spec-009 §6: trigger-based and manual invocation must coexist cleanly."""
    graph_id = "coexist-graph-1"
    graph = _webhook_graph()
    try:
        client.post(f"/graphs/{graph_id}/activate", json=graph)

        manual = client.post("/runs", json=graph)
        assert manual.status_code == 202
        manual_result = _wait_for_run(manual.json()["run_id"])
        assert manual_result["status"] == "completed"
        # Manually run with no webhook body -- payload defaults to {}, so
        # json.loads(payload)['name'] raises a KeyError inside the code node,
        # a real, expected node-level error rather than a crash.
        parse_trace = next(t for t in manual_result["trace"] if t["node_id"] == "parse")
        assert parse_trace["error"] is not None

        fired = client.post(f"/webhooks/{graph_id}/trigger", json={"name": "spec-009"})
        fired_result = _wait_for_run(fired.json()["run_id"])
        assert fired_result["status"] == "completed"
        parse_trace = next(t for t in fired_result["trace"] if t["node_id"] == "parse")
        assert parse_trace["error"] is None
        assert parse_trace["outputs"] == {"result": "spec-009"}
    finally:
        _deactivate_quietly(graph_id)


def test_schedule_job_genuinely_fires_via_a_real_apscheduler_tick():
    """A real (non-mocked) confirmation that the scheduler mechanism itself
    works: registers a job directly against the same BackgroundScheduler
    singleton `add_schedule_job` uses, with a `second="*/2"` CronTrigger --
    APScheduler's CronTrigger supports sub-minute fields directly, but
    add_schedule_job's own contract is a standard 5-field crontab string
    (spec-009 §5's schedule_trigger config shape, minute granularity),
    which can't express a fast interval. Using the raw scheduler here tests
    the identical job-execution code path in a few real seconds instead of
    waiting a full production-realistic minute; the full node-config-driven
    path is confirmed by the multi-minute live demo (spec-009 §6)."""
    graph_id = "fast-fire-graph"
    graph = {
        "version": "0.1",
        "nodes": [{"id": "trigger", "type": "schedule_trigger", "config": {"cron": "0 0 1 1 *"}}],
        "edges": [],
    }
    activate = client.post(f"/graphs/{graph_id}/activate", json=graph)
    assert activate.status_code == 200

    from backend.triggers import runner as trigger_runner

    job_id = f"{graph_id}:trigger"
    scheduler = trigger_scheduler._get_scheduler()
    scheduler.remove_job(job_id)  # replace the yearly job with a fast one for this test
    scheduler.add_job(
        lambda: trigger_runner.fire(graph_id, "trigger"), trigger=CronTrigger(second="*/2"), id=job_id
    )

    from backend.api import runs as runs_module

    before = len(runs_module._runs)
    time.sleep(3)
    after = len(runs_module._runs)
    assert after > before, "expected at least one real scheduler-fired run in ~3 seconds"

    _deactivate_quietly(graph_id)


def test_webhook_fire_resolves_a_private_connection_owned_by_the_activating_user(monkeypatch):
    """Regression test for a real bug found live: `trigger_runner.fire()`
    previously called resolve_connections/resolve_connection_profiles with
    no user_id at all, which can only ever see *global* connections. Any
    activated graph referencing a *private* connection (the default for
    anything created through the normal UI, spec-021) would raise
    ConnectionNotFoundError inside fire() -- uncaught anywhere in the
    webhook handler -- surfacing as a real, uncaught 500 to the external
    caller. Found via a real Telegram webhook reporting exactly "Wrong
    response from the webhook: 500 Internal Server Error" with pending
    updates queuing up undelivered. The fix threads the activating user's
    id through `ActiveGraph.created_by`. This test proves the webhook POST
    itself now returns 200 (with a real run_id) instead of 500 -- that
    alone is sufficient proof resolve_connections succeeded, since the
    original bug's exception was raised and left uncaught before a run_id
    could ever be created."""
    import anthropic as anthropic_module

    class _FakeBlock:
        type = "text"
        text = "hi"

    class _FakeUsage:
        input_tokens = 1
        output_tokens = 1

    class _FakeResponse:
        content = [_FakeBlock()]
        usage = _FakeUsage()

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeResponse()

    class _FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic_module, "Anthropic", _FakeAnthropicClient)

    users_store.ensure_admin_bootstrapped("admin@example.com", "2026-01-01T00:00:00+00:00")
    user = users_store.create_user(
        user_id="trigger-owner-1",
        email="trigger-owner-1@example.com",
        display_name="Trigger Owner",
        role="member",
        created_at="2026-01-01T00:00:00+00:00",
        invited_by=None,
    )
    token = auth_jwt.issue_token(user.id, user.email, user.role)
    user_client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    created = user_client.post(
        "/connections",
        json={"name": "my-private-anthropic", "type": "anthropic", "config": {"api_key": "sk-test"}},
    )
    assert created.status_code == 201

    graph_id = "webhook-private-conn-graph"
    spec = {
        "version": "0.1",
        "nodes": [
            {"id": "trigger", "type": "webhook_trigger", "config": {}},
            {"id": "adapter", "type": "generic_adapter", "config": {}},
            {
                "id": "call",
                "type": "llm_call",
                "config": {"connection": "my-private-anthropic", "model": "claude-haiku-4-5", "max_tokens": 10},
            },
        ],
        "edges": [
            {"kind": "sub_node", "slot": "trigger_adapter", "from": {"node": "adapter"}, "to": {"node": "trigger"}},
            {"from": {"node": "trigger", "slot": "payload"}, "to": {"node": "call", "slot": "prompt"}},
        ],
    }
    try:
        # Activated by the connection's own owner -- exactly the real-world
        # shape (a person activates their own graph, referencing their own
        # private connections).
        activate = user_client.post(f"/graphs/{graph_id}/activate", json=spec)
        assert activate.status_code == 200
        endpoint = activate.json()["triggers"][0]["endpoint_or_schedule"].split("?")[0]

        # Fired by an external caller using only the shared API key --
        # exactly how Telegram (or any real webhook source) actually calls
        # in, with no JWT/user identity of its own.
        fire = client.post(endpoint, json={"text": "hello"})
        assert fire.status_code == 200, f"expected 200, got {fire.status_code}: {fire.text}"

        run = _wait_for_run(fire.json()["run_id"])
        assert run["status"] == "completed"
        call_trace = next(t for t in run["trace"] if t["node_id"] == "call")
        assert call_trace["error"] is None
        assert call_trace["outputs"] == {"response": "hi"}
    finally:
        _deactivate_quietly(graph_id)

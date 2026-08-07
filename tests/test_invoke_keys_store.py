from __future__ import annotations

from backend.storage import invoke_keys_store


def test_generate_validate_revoke_round_trip():
    row, token = invoke_keys_store.generate_invoke_key("g1", "ci key", "2026-01-01T00:00:00")
    assert token.startswith("agsk_")
    assert row.key_prefix == token[:12]
    assert row.timeout_seconds == 60

    found = invoke_keys_store.validate_invoke_key("g1", token)
    assert found is not None
    assert found.key_id == row.key_id
    assert found.last_used_at is None  # no `now` passed, unchanged

    stamped = invoke_keys_store.validate_invoke_key("g1", token, now="2026-01-01T00:05:00")
    assert stamped is not None
    assert stamped.last_used_at == "2026-01-01T00:05:00"

    assert invoke_keys_store.revoke_invoke_key("g1", row.key_id) is True
    assert invoke_keys_store.validate_invoke_key("g1", token) is None
    assert invoke_keys_store.revoke_invoke_key("g1", row.key_id) is False  # already gone


def test_validate_rejects_wrong_token():
    invoke_keys_store.generate_invoke_key("g1", "ci key", "2026-01-01T00:00:00")
    assert invoke_keys_store.validate_invoke_key("g1", "agsk_not-the-real-token") is None


def test_validate_scopes_strictly_to_its_own_graph_id():
    _, token = invoke_keys_store.generate_invoke_key("g1", "ci key", "2026-01-01T00:00:00")
    assert invoke_keys_store.validate_invoke_key("g1", token) is not None
    assert invoke_keys_store.validate_invoke_key("g2", token) is None


def test_list_invoke_keys_is_scoped_and_ordered_newest_first():
    invoke_keys_store.generate_invoke_key("g1", "first", "2026-01-01T00:00:00")
    invoke_keys_store.generate_invoke_key("g1", "second", "2026-01-02T00:00:00")
    invoke_keys_store.generate_invoke_key("g2", "other graph", "2026-01-01T00:00:00")

    keys = invoke_keys_store.list_invoke_keys("g1")
    assert [k.label for k in keys] == ["second", "first"]

    keys_g2 = invoke_keys_store.list_invoke_keys("g2")
    assert [k.label for k in keys_g2] == ["other graph"]


def test_generate_invoke_key_accepts_custom_timeout_and_created_by():
    row, _ = invoke_keys_store.generate_invoke_key(
        "g1", "custom", "2026-01-01T00:00:00", timeout_seconds=300, created_by="user-1"
    )
    assert row.timeout_seconds == 300
    assert row.created_by == "user-1"

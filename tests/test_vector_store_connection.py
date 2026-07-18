from __future__ import annotations

import backend.connections.vector_store_connection as vector_store_connection_module
from backend.connections.vector_store_connection import VectorStoreClient, VectorStoreConfig


def test_add_and_query_ranks_closer_vector_first(tmp_path):
    client = VectorStoreClient(tmp_path / "store.db")

    stored = client.add(
        ["about cats", "about dogs"],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        document_name="doc1",
    )
    assert stored == 2

    results = client.query([0.9, 0.1, 0.0, 0.0], top_k=2)

    assert results[0]["text"] == "about cats"
    assert results[0]["document_name"] == "doc1"
    assert results[0]["position"] == 0
    assert results[1]["text"] == "about dogs"
    assert results[0]["distance"] < results[1]["distance"]


def test_query_against_empty_store_returns_no_results(tmp_path):
    client = VectorStoreClient(tmp_path / "empty.db")
    assert client.query([1.0, 0.0], top_k=5) == []


def test_clear_removes_all_stored_chunks(tmp_path):
    client = VectorStoreClient(tmp_path / "store.db")
    client.add(["a"], [[1.0, 0.0]], document_name=None)
    assert client.query([1.0, 0.0], top_k=5) != []

    client.clear()

    assert client.query([1.0, 0.0], top_k=5) == []


def test_add_with_no_chunks_returns_zero_and_does_not_error(tmp_path):
    client = VectorStoreClient(tmp_path / "store.db")
    assert client.add([], [], document_name=None) == 0


def test_top_k_limits_result_count(tmp_path):
    client = VectorStoreClient(tmp_path / "store.db")
    client.add(
        ["a", "b", "c"],
        [[1.0, 0.0], [0.9, 0.1], [0.1, 0.9]],
        document_name=None,
    )
    results = client.query([1.0, 0.0], top_k=1)
    assert len(results) == 1
    assert results[0]["text"] == "a"


def test_test_connection_reports_success_for_a_writable_path(tmp_path):
    config = VectorStoreConfig(path=str(tmp_path / "new-store.db"))
    result = vector_store_connection_module.test_connection(config)
    assert result.success is True


def test_test_connection_reports_failure_for_an_unwritable_path():
    config = VectorStoreConfig(path="/nonexistent-root-dir-xyz/definitely/not/writable/store.db")
    result = vector_store_connection_module.test_connection(config)
    assert result.success is False

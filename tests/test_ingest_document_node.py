from __future__ import annotations

from typing import Callable

import pytest
from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type
from backend.connections.store import ConnectionProfile
from backend.connections.vector_store_connection import VectorStoreClient
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.ingest_document import _chunk_text, execute_ingest_document
from backend.schema.models import NodeSpec

# --- a fake embedding connection type, registered once for this module ----
# Mirrors test_agent_node.py's fake-connection-type pattern exactly:
# execute_ingest_document looks up `embed` via the real
# default_connection_registry (a process-wide singleton), so a fake type is
# registered once here under a unique name, with per-test behavior swapped
# via `_current_impl`.


class _FakeEmbedConfig(BaseModel):
    pass


_current_impl: Callable[..., list[float]] | None = None


def _dispatch(config: _FakeEmbedConfig, model: str, text: str) -> list[float]:
    assert _current_impl is not None, "test forgot to set _current_impl"
    return _current_impl(config, model, text)


register_connection_type(
    "fake-embed-for-ingest",
    category="local",
    config_model=_FakeEmbedConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    embed=_dispatch,
)

register_connection_type(
    "fake-no-embed-for-ingest",
    category="local",
    config_model=_FakeEmbedConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    # embed intentionally omitted -- the capability check under test.
)


@pytest.fixture(autouse=True)
def _reset_fake_impl():
    global _current_impl
    _current_impl = None
    yield
    _current_impl = None


def _node(config: dict) -> NodeSpec:
    return NodeSpec(id="ingest1", type="ingest_document", config=config)


def _base_config(**overrides) -> dict:
    config = {
        "connection": "vs-conn",
        "embedding_model_connection": "emb-conn",
        "embedding_model": "fake-model",
        "chunk_size": 10,
        "chunk_overlap": 2,
    }
    config.update(overrides)
    return config


def _ctx(config: dict, text: str, vector_client, connection_type: str = "fake-embed-for-ingest") -> ExecutionContext:
    return ExecutionContext(
        node=_node(config),
        inputs={"text": text},
        resources={
            "connections": {"vs-conn": vector_client},
            "connection_profiles": {
                "emb-conn": ConnectionProfile(name="emb-conn", type=connection_type, config={})
            },
        },
    )


# --- _chunk_text (pure helper) ----------------------------------------------


def test_chunk_text_splits_with_overlap():
    chunks = _chunk_text("abcdefghij", chunk_size=4, chunk_overlap=1)
    assert chunks == ["abcd", "defg", "ghij", "j"]


def test_chunk_text_exact_multiple_of_chunk_size_no_overlap():
    chunks = _chunk_text("abcdefgh", chunk_size=4, chunk_overlap=0)
    assert chunks == ["abcd", "efgh"]


def test_chunk_text_shorter_than_chunk_size_returns_one_chunk():
    chunks = _chunk_text("abc", chunk_size=10, chunk_overlap=2)
    assert chunks == ["abc"]


def test_chunk_text_empty_string_returns_no_chunks():
    assert _chunk_text("", chunk_size=10, chunk_overlap=2) == []


# --- execute_ingest_document -------------------------------------------------


def test_ingest_document_embeds_each_chunk_and_stores_them(tmp_path):
    def fake_embed(config, model, text):
        return [float(len(text)), 0.0]

    global _current_impl
    _current_impl = fake_embed

    vector_client = VectorStoreClient(tmp_path / "store.db")
    # "abcdefghi" (9 chars) chunked at size=3/overlap=0 -> exactly 3 chunks:
    # "abc", "def", "ghi" -- no overlap arithmetic to reason about.
    config = _base_config(chunk_size=3, chunk_overlap=0, document_name="my-doc")
    ctx = _ctx(config, "abcdefghi", vector_client)

    result = execute_ingest_document(ctx)

    assert result.outputs == {"chunks_stored": "3"}
    stored = vector_client.query([3.0, 0.0], top_k=3)
    assert len(stored) == 3
    assert all(r["document_name"] == "my-doc" for r in stored)


def test_ingest_document_rejects_overlap_greater_or_equal_to_chunk_size():
    from backend.nodes.ingest_document import IngestDocumentConfig

    with pytest.raises(ValueError):
        IngestDocumentConfig.model_validate(_base_config(chunk_size=5, chunk_overlap=5))


def test_ingest_document_raises_node_execution_error_when_embed_unsupported(tmp_path):
    vector_client = VectorStoreClient(tmp_path / "store.db")
    ctx = _ctx(
        _base_config(),
        "abcdefghij",
        vector_client,
        connection_type="fake-no-embed-for-ingest",
    )

    with pytest.raises(NodeExecutionError):
        execute_ingest_document(ctx)


def test_ingest_document_raises_node_execution_error_when_vector_connection_not_resolved(tmp_path):
    global _current_impl
    _current_impl = lambda config, model, text: [1.0]

    ctx = ExecutionContext(
        node=_node(_base_config()),
        inputs={"text": "abcdefghij"},
        resources={
            "connections": {},
            "connection_profiles": {
                "emb-conn": ConnectionProfile(name="emb-conn", type="fake-embed-for-ingest", config={})
            },
        },
    )

    with pytest.raises(NodeExecutionError):
        execute_ingest_document(ctx)

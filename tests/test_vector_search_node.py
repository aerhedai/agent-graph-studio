from __future__ import annotations

from typing import Callable

import pytest
from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type
from backend.connections.store import ConnectionProfile
from backend.connections.vector_store_connection import VectorStoreClient
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext
from backend.nodes.vector_search import execute_vector_search
from backend.schema.models import NodeSpec

# Mirrors test_ingest_document_node.py's fake-embed-connection-type pattern
# exactly (itself mirroring test_agent_node.py's precedent).


class _FakeEmbedConfig(BaseModel):
    pass


_current_impl: Callable[..., list[float]] | None = None


def _dispatch(config: _FakeEmbedConfig, model: str, text: str) -> list[float]:
    assert _current_impl is not None, "test forgot to set _current_impl"
    return _current_impl(config, model, text)


register_connection_type(
    "fake-embed-for-search",
    category="local",
    config_model=_FakeEmbedConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
    embed=_dispatch,
)

register_connection_type(
    "fake-no-embed-for-search",
    category="local",
    config_model=_FakeEmbedConfig,
    build_client=lambda config: None,
    test_connection=lambda config: ConnectionTestResult(success=True, message="ok"),
)


@pytest.fixture(autouse=True)
def _reset_fake_impl():
    global _current_impl
    _current_impl = None
    yield
    _current_impl = None


def _node(config: dict) -> NodeSpec:
    return NodeSpec(id="search1", type="vector_search", config=config)


def _base_config(**overrides) -> dict:
    config = {
        "connection": "vs-conn",
        "embedding_model_connection": "emb-conn",
        "embedding_model": "fake-model",
        "top_k": 5,
    }
    config.update(overrides)
    return config


def _ctx(config: dict, query: str, vector_client, connection_type: str = "fake-embed-for-search") -> ExecutionContext:
    return ExecutionContext(
        node=_node(config),
        inputs={"query": query},
        resources={
            "connections": {"vs-conn": vector_client},
            "connection_profiles": {
                "emb-conn": ConnectionProfile(name="emb-conn", type=connection_type, config={})
            },
        },
    )


# Fixed, unambiguous per-text embeddings (not derived from generic text
# features) -- this test is about vector_search's retrieval/ranking wiring,
# not about embedding quality, so each text's "meaning" is hand-assigned as
# a clearly-separated vector rather than computed, avoiding any risk of a
# razor-thin/fragile similarity margin between the two topics.
_TOPIC_VECTORS = {
    "cats are wonderful pets": [1.0, 0.0],
    "quantum mechanics is hard": [0.0, 1.0],
    "dogs are lovely animals": [0.9, 0.1],  # closer to the pets topic
}


def _topic_embed(config, model, text):
    return _TOPIC_VECTORS[text]


def test_vector_search_returns_most_similar_chunk_first(tmp_path):
    global _current_impl
    _current_impl = _topic_embed

    vector_client = VectorStoreClient(tmp_path / "store.db")
    vector_client.add(
        ["cats are wonderful pets"], [_TOPIC_VECTORS["cats are wonderful pets"]], document_name="pets-doc"
    )
    vector_client.add(
        ["quantum mechanics is hard"],
        [_TOPIC_VECTORS["quantum mechanics is hard"]],
        document_name="physics-doc",
    )

    ctx = _ctx(_base_config(top_k=2), "dogs are lovely animals", vector_client)
    result = execute_vector_search(ctx)

    assert "cats are wonderful pets" in result.outputs["results"]
    # The pets-related chunk should be listed before the physics one.
    text = result.outputs["results"]
    assert text.index("cats are wonderful pets") < text.index("quantum mechanics is hard")


def test_vector_search_against_empty_store_reports_no_results(tmp_path):
    global _current_impl
    _current_impl = lambda config, model, text: [1.0, 0.0]

    vector_client = VectorStoreClient(tmp_path / "empty.db")
    ctx = _ctx(_base_config(), "anything", vector_client)

    result = execute_vector_search(ctx)

    assert result.outputs == {"results": "(no results found)"}


def test_vector_search_raises_node_execution_error_when_embed_unsupported(tmp_path):
    vector_client = VectorStoreClient(tmp_path / "store.db")
    ctx = _ctx(_base_config(), "anything", vector_client, connection_type="fake-no-embed-for-search")

    with pytest.raises(NodeExecutionError):
        execute_vector_search(ctx)


def test_vector_search_raises_node_execution_error_when_vector_connection_not_resolved():
    global _current_impl
    _current_impl = lambda config, model, text: [1.0, 0.0]

    ctx = ExecutionContext(
        node=_node(_base_config()),
        inputs={"query": "anything"},
        resources={
            "connections": {},
            "connection_profiles": {
                "emb-conn": ConnectionProfile(name="emb-conn", type="fake-embed-for-search", config={})
            },
        },
    )

    with pytest.raises(NodeExecutionError):
        execute_vector_search(ctx)

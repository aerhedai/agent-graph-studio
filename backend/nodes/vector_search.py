"""`vector_search` node: embeds an incoming query the same way
`ingest_document` embeds chunks, retrieves the top-K most similar stored
chunks from a configured `vector_store` connection, and formats them as
text ready to feed into a downstream `llm_call`/`agent` node's prompt
(spec-011).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.connections.base import default_connection_registry
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class VectorSearchConfig(BaseModel):
    connection: str
    embedding_model_connection: str
    embedding_model: str
    top_k: int = Field(gt=0, default=5)


def _resolve_embed(ctx: ExecutionContext, embedding_model_connection: str, model: str):
    """Same "raw connection profile + registry capability dispatch" pattern
    already established by agent.py's complete_with_tools lookup (spec-008),
    mirrored identically in ingest_document.py -- kept duplicated rather
    than factored into a shared module, consistent with every other node
    type in this package being a self-contained file."""
    profile = ctx.resources.get("connection_profiles", {}).get(embedding_model_connection)
    if profile is None:
        raise NodeExecutionError(
            f"No resolved connection profile for '{embedding_model_connection}' -- "
            "it should have been resolved before this run started"
        )
    definition = default_connection_registry.get(profile.type)
    if definition is None or definition.embed is None:
        raise NodeExecutionError(
            f"Connection '{embedding_model_connection}' (type '{profile.type}') "
            "does not support embeddings"
        )
    connection_config = definition.config_model.model_validate(profile.config)
    return lambda text: definition.embed(connection_config, model, text)


def _format_results(results: list[dict]) -> str:
    if not results:
        return "(no results found)"
    lines = []
    for i, r in enumerate(results, start=1):
        source = f" (source: {r['document_name']})" if r.get("document_name") else ""
        lines.append(f"[{i}]{source} {r['text']}")
    return "\n\n".join(lines)


@register_node(
    "vector_search",
    inputs=[InputSlotSpec("query", TEXT)],
    outputs=[OutputSlotSpec("results", TEXT)],
    config_model=VectorSearchConfig,
    category="ai",
)
def execute_vector_search(ctx: ExecutionContext) -> NodeResult:
    config = VectorSearchConfig.model_validate(ctx.node.config)

    vector_client = ctx.resources.get("connections", {}).get(config.connection)
    if vector_client is None:
        raise NodeExecutionError(
            f"No resolved client for connection '{config.connection}' -- "
            "it should have been resolved before this run started"
        )

    embed = _resolve_embed(ctx, config.embedding_model_connection, config.embedding_model)

    try:
        query_embedding = embed(ctx.inputs["query"])
        results = vector_client.query(query_embedding, config.top_k)
    except NodeExecutionError:
        raise
    except Exception as e:
        raise NodeExecutionError(f"vector_search failed: {e}") from e

    return NodeResult(outputs={"results": _format_results(results)})

"""`ingest_document` node: chunks incoming text, embeds each chunk via a
configured Ollama connection, and stores the chunks + embeddings in a
configured `vector_store` connection (spec-011).

Two named connections, not one: `connection` (the vector store) and
`embedding_model_connection` (the embedding model) -- resolved generically
by backend/connections/resolver.py's connection_reference_names convention
(any config key that is "connection" or ends in "_connection"), not by any
node-type-specific logic here.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from backend.connections.base import default_connection_registry
from backend.execution.errors import NodeExecutionError
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class IngestDocumentConfig(BaseModel):
    connection: str
    embedding_model_connection: str
    embedding_model: str
    chunk_size: int = 500
    chunk_overlap: int = 50
    document_name: str | None = None

    @model_validator(mode="after")
    def _check_overlap_smaller_than_size(self) -> "IngestDocumentConfig":
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap must be >= 0")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Fixed-size character chunking with overlap (spec-011 §4's simplest-
    correct-approach baseline, not semantic/sentence-aware). Each chunk
    starts `chunk_size - chunk_overlap` characters after the previous one."""
    if not text:
        return []
    stride = chunk_size - chunk_overlap
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += stride
    return chunks


def _resolve_embed(ctx: ExecutionContext, embedding_model_connection: str, model: str):
    """Same "raw connection profile + registry capability dispatch" pattern
    already established by agent.py's complete_with_tools lookup (spec-008)
    -- reused here for the embed capability instead."""
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


@register_node(
    "ingest_document",
    inputs=[InputSlotSpec("text", TEXT)],
    outputs=[OutputSlotSpec("chunks_stored", TEXT)],
    config_model=IngestDocumentConfig,
    category="ai",
)
def execute_ingest_document(ctx: ExecutionContext) -> NodeResult:
    config = IngestDocumentConfig.model_validate(ctx.node.config)

    vector_client = ctx.resources.get("connections", {}).get(config.connection)
    if vector_client is None:
        raise NodeExecutionError(
            f"No resolved client for connection '{config.connection}' -- "
            "it should have been resolved before this run started"
        )

    embed = _resolve_embed(ctx, config.embedding_model_connection, config.embedding_model)

    chunks = _chunk_text(ctx.inputs["text"], config.chunk_size, config.chunk_overlap)
    try:
        embeddings = [embed(chunk) for chunk in chunks]
        stored = vector_client.add(chunks, embeddings, document_name=config.document_name)
    except NodeExecutionError:
        raise
    except Exception as e:
        raise NodeExecutionError(f"ingest_document failed: {e}") from e

    return NodeResult(outputs={"chunks_stored": str(stored)})

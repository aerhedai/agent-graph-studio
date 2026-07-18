"""The `vector_store` connection type (spec-011 §4) -- backed by
`sqlite-vec`, a lightweight SQLite extension, not Chroma as originally
sketched in the spec's first draft. Chroma depends unconditionally on
`onnxruntime`, which has no wheel for this project's Python 3.14
environment (confirmed via a real dry-run resolution failure); sqlite-vec
has no such dependency chain and installs cleanly here, while still being
a single local file -- arguably an even better fit for this project's
local-first philosophy (same reasoning as SQLite for connection profiles
and run history, spec-006/010) than a separate embedded database engine.

Embeddings are L2-normalized before storage and before query, so that
sqlite-vec's plain L2 distance ordering becomes equivalent to cosine
similarity ordering (for unit vectors, L2^2 = 2 - 2*cos_sim -- a monotonic,
rank-preserving relationship) -- verified live. This avoids depending on a
`distance_metric=cosine` table option that may not exist in every
sqlite-vec version, and is a standard, well-known technique.
"""

from __future__ import annotations

import math
import os
import sqlite3
import struct
from pathlib import Path

import sqlite_vec
from pydantic import BaseModel

from backend.connections.base import ConnectionTestResult, register_connection_type


class VectorStoreConfig(BaseModel):
    path: str


def _resolved_path(config: VectorStoreConfig) -> Path:
    return Path(os.path.expanduser(config.path))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _serialize(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class VectorStoreClient:
    """One store = one sqlite-vec-backed file, one `chunks` virtual table.
    The table's embedding dimension is fixed at first `add()` (inferred
    from the actual embedding length) -- a store is bound to whichever
    embedding model's dimensionality ingested into it first, the same
    inherent constraint any vector store has per collection/index."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

    def _table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        return row is not None

    def _ensure_table(self, dim: int) -> None:
        if self._table_exists():
            return
        self._conn.execute(
            f"CREATE VIRTUAL TABLE chunks USING vec0("
            f"embedding float[{dim}], +text TEXT, +document_name TEXT, +position INTEGER)"
        )
        self._conn.commit()

    def add(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        document_name: str | None,
    ) -> int:
        if not chunks:
            return 0
        self._ensure_table(len(embeddings[0]))
        for position, (text, embedding) in enumerate(zip(chunks, embeddings)):
            self._conn.execute(
                "INSERT INTO chunks(embedding, text, document_name, position) VALUES (?, ?, ?, ?)",
                (_serialize(_normalize(embedding)), text, document_name, position),
            )
        self._conn.commit()
        return len(chunks)

    def query(self, embedding: list[float], top_k: int) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT text, document_name, position, distance FROM chunks "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_serialize(_normalize(embedding)), top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            # No `chunks` table yet -- nothing has ever been ingested into
            # this store. Empty results, not an error: an unpopulated store
            # is a normal state for vector_search to encounter.
            return []
        return [
            {"text": text, "document_name": document_name, "position": position, "distance": distance}
            for text, document_name, position, distance in rows
        ]

    def clear(self) -> None:
        self._conn.execute("DROP TABLE IF EXISTS chunks")
        self._conn.commit()


def build_client(config: VectorStoreConfig) -> VectorStoreClient:
    return VectorStoreClient(_resolved_path(config))


def test_connection(config: VectorStoreConfig) -> ConnectionTestResult:
    try:
        client = VectorStoreClient(_resolved_path(config))
        client._conn.execute("SELECT 1")
    except Exception as e:
        return ConnectionTestResult(
            success=False, message=f"Could not open vector store at {config.path}: {e}"
        )
    return ConnectionTestResult(
        success=True, message=f"Vector store ready at {_resolved_path(config)}"
    )


register_connection_type(
    "vector_store",
    category="local",
    config_model=VectorStoreConfig,
    build_client=build_client,
    test_connection=test_connection,
)

# SPEC-011: RAG / Vector Retrieval

**Status:** Draft — ready for implementation
**Milestone:** Knowledge Retrieval
**Author:** Rohan
**Depends on:** SPEC-002 (node registry), SPEC-006 (`ConnectionType` registry)

## 1. Goal

Let a graph answer questions using external documents/knowledge it wasn't trained on — the standard RAG (Retrieval-Augmented Generation) pattern: embed and store documents ahead of time, then at query time retrieve the most relevant chunks and feed them into an `llm_call` or `agent` node's prompt.

## 2. Why this, why now

RAG is one of the most commonly deployed real-world LLM patterns, and nothing in the project currently supports it — every `llm_call` today only knows what's in the model's own training data plus whatever's explicitly passed as its prompt. This closes that gap using infrastructure already established (the `ConnectionType` registry from SPEC-006), rather than inventing a new mechanism.

## 3. Scope

In scope:
- A new `vector_store` connection type — backed by **Chroma running locally** (matches the project's existing local-first approach for Ollama), storing embeddings on disk
- An `ingest_document` node type: takes text in, chunks it, embeds each chunk (via a configurable embedding model — start with a local Ollama-hosted embedding model, e.g. `nomic-embed-text`, consistent with keeping this fully local-capable), and stores the chunks + embeddings in the configured `vector_store` connection
- A `vector_search` node type: takes a query string in, embeds it the same way, retrieves the top-N most similar stored chunks from the connection, and outputs them as text — ready to feed into a downstream `llm_call`/`agent` node's prompt
- Both new node types follow the exact same `resolve_slots`/registry pattern established since SPEC-002 — no engine changes required for either

Out of scope (future specs):
- Exposing a vector store via MCP instead of a dedicated connection type — a real, valid alternative path (your `mcp_call` node would handle it with zero new code if a suitable MCP server exists), but not pursued here since it depends on external server availability/quality outside this project's control; revisit if a good one surfaces later
- Advanced chunking strategies (semantic chunking, overlap tuning) — start with simple fixed-size chunking with overlap; smarter chunking is real, deferrable refinement
- Re-ranking retrieved results with a second model pass — a real quality improvement, not needed for a first working version
- Automatic re-ingestion / document change detection — ingestion is a manual, explicit action for v1

## 4. Design decisions (resolved)

- **Storage: `sqlite-vec`, run locally — a deliberate deviation from this section's original "Chroma" decision, confirmed before implementation.** Chroma depends unconditionally on `onnxruntime`, which has no wheel for this project's Python 3.14 environment (verified via a real dry-run resolution failure); the only clean fix would have been downgrading the project's own dev interpreter to 3.13, a bigger and separate change than this spec should force. `sqlite-vec` is a single lightweight SQLite extension (no heavy ML dependency chain), installs cleanly under Python 3.14 (verified), and is arguably an even better fit for this project's local-first philosophy than Chroma — it's just a `.db` file plus an extension, consistent with the exact same reasoning already applied to the SQLite-backed connection/run stores (SPEC-006, SPEC-010) rather than a separate embedded database engine. The `vector_store` connection's `{"path": ...}` config shape (§5) is unchanged; `path` now names a SQLite file (via a `vec0` virtual table) rather than a Chroma directory.
- **Embeddings: local, via Ollama** — using an Ollama-hosted embedding model rather than a cloud embedding API, so ingestion has zero cost and zero cloud dependency, matching how you've prioritized Ollama throughout (SPEC-002's deferred provider, SPEC-008's tool-calling target).
- **Chunking: fixed-size with overlap**, config-specified (chunk size + overlap size), not semantic/sentence-aware for v1 — simplest correct approach; a real, common baseline.
- **Two distinct node types (`ingest_document`, `vector_search`), not one combined node** — ingestion and retrieval are genuinely different operations happening at different times (you ingest once, query many times), and keeping them separate mirrors how every real RAG pipeline is actually structured.

## 5. Data model

### `vector_store` connection config (new `ConnectionType`)
```json
{ "path": "~/.agent-graph-studio/vector-stores/my-store" }
```

### `ingest_document` node config
```json
{
  "connection": "my-store",
  "embedding_model_connection": "my-pc-ollama",
  "embedding_model": "nomic-embed-text",
  "chunk_size": 500,
  "chunk_overlap": 50
}
```
- Inputs: `text` (the document content)
- Outputs: `chunks_stored` (int, how many chunks were created — simple confirmation, not the chunks themselves)

### `vector_search` node config
```json
{
  "connection": "my-store",
  "embedding_model_connection": "my-pc-ollama",
  "embedding_model": "nomic-embed-text",
  "top_k": 5
}
```
- Inputs: `query` (text)
- Outputs: `results` (text — the top-K chunks, concatenated/formatted, ready to drop into a downstream prompt)

## 6. Acceptance criteria

- [ ] `ingest_document` correctly chunks a real piece of text, embeds each chunk via a real local Ollama embedding model, and stores it in a real local Chroma instance — live-verified, non-mocked
- [ ] `vector_search` correctly retrieves the most semantically relevant chunks for a real query against previously ingested content — verified with a real example (ingest a few distinct paragraphs about different topics, query for one topic specifically, confirm the retrieved chunk is the relevant one, not an unrelated one)
- [ ] A full pipeline works end to end: `ingest_document` → (separately) `vector_search` → `llm_call`, producing a coherent answer that's clearly informed by the ingested content, not just the model's general knowledge — live-verified
- [ ] `git diff` on `engine.py` is empty — both node types work entirely through the existing `resolve_slots`/registry mechanism with no core engine changes
- [ ] Full existing test suite (SPEC-001–010) still passes unchanged

## 7. Open questions

- Should chunk metadata (source document name, position) be stored and returned alongside retrieved text, so a downstream `llm_call` could cite where information came from? Recommend: yes, store it now even if not surfaced in v1's simple text output — cheap to add at storage time, expensive to retrofit later if chunks were stored without it.
  - Resolved: adopted as recommended. `ingest_document` gains an additive, optional `document_name: str | None` config field (not in the original §5 sketch, but backward-compatible — omitting it is fine); each stored chunk's `document_name` and `position` (chunk index within that ingest call) are stored as unindexed auxiliary columns alongside its embedding, not surfaced in `vector_search`'s v1 text output.
- Should there be a way to delete/clear a vector store's contents without deleting the connection itself? Recommend: yes, a simple `DELETE /connections/{name}/vectors` style operation — small addition, avoids needing to delete and recreate an entire connection just to start over during testing.
  - Resolved: adopted as recommended, implemented as `DELETE /connections/{name}/vectors`.

## 8. Implementation notes

Written after implementation, following the SPEC-004/005/008/009/010 precedent of justifying non-obvious calls in the spec itself rather than silently.

- **Engine diff: confirmed empty, exactly as designed.** `git diff main -- backend/execution/engine.py` for this spec is a no-op. Both `ingest_document` and `vector_search` are ordinary static-schema node types (no `resolve_slots`), going through the exact same registry/`ExecutionContext.resources` mechanism every node type has used since SPEC-001/002.
- **A second architectural fork, raised and confirmed before implementation: two named connections on one node type.** `ingest_document`/`vector_search` each need both a `connection` (the vector store) and an `embedding_model_connection` (Ollama), but the generic connection-resolution code (`backend/connections/resolver.py::_referenced_connection_names`, `backend/validation/rules.py::check_missing_connections`) only ever recognized a config key literally named `"connection"` — every prior node type (`llm_call`, `agent`) needed just the one. Resolved by generalizing both to a shared convention: any config key that is exactly `"connection"` or ends with `"_connection"` is a connection reference, implemented once as `connection_reference_names()` (`backend/connections/resolver.py`) and imported by `check_missing_connections` rather than reimplemented, so the two can't drift apart. A future node needing a third named connection (e.g. a reranker) requires zero changes here.
- **`embed`, a new optional `ConnectionType` capability**, mirroring `list_models` (SPEC-006 §9) and `complete_with_tools` (SPEC-008 §5) exactly: `Callable[[BaseModel, str, str], list[float]] | None`, checked via `is not None`, implemented only for `ollama_connection.py` (`POST /api/embeddings`). `anthropic_connection.py` leaves it `None`, same precedent as `list_models`. Node bodies resolve it via `ctx.resources["connection_profiles"]` + registry lookup — the exact same "raw profile + capability dispatch" pattern `agent.py` already established for `complete_with_tools`, not a new mechanism.
- **`chunks_stored` is TEXT, not "int" as §5 originally sketched — a forced, documented deviation, the same class SPEC-009 §8 already made for `webhook_trigger.payload`.** Every slot type registered in this codebase is TEXT-only (`SlotType.JSON`/others exist in the enum but have zero real usage); a strictly int/JSON-typed output couldn't connect to any node type that exists today. `chunks_stored` carries a stringified count instead (`str(len(chunks))`).
- **Storage deviated from Chroma to `sqlite-vec`, confirmed before implementation** (already written into §4 above): `chromadb` depends unconditionally on `onnxruntime`, which has no wheel for this project's Python 3.14 environment (a real `uv pip install --dry-run` resolution failure, not a guess). `sqlite-vec` has no such dependency chain, installs cleanly, and fits the project's existing "just a SQLite file" pattern (SPEC-006/010) at least as well as a separate embedded database would have. Embeddings are L2-normalized before storage and before query, so `sqlite-vec`'s plain L2 distance ordering becomes equivalent to cosine similarity ordering (for unit vectors, `L2² = 2 - 2·cos_sim` — a monotonic, rank-preserving relationship) — this avoids depending on a `distance_metric=cosine` table option that may not exist in every `sqlite-vec` version. A store's `vec0` table is created lazily on first `add()`, with its embedding dimension inferred from the first embedding actually stored — a store is bound to whichever embedding model's dimensionality ingested into it first, same inherent constraint any vector index has per collection.
- **Live verification performed against a real remote Ollama server (`100.112.223.103` over Tailscale — the user's PC, not the local machine this session otherwise ran on), never the local macbook's Ollama, per explicit instruction.** `nomic-embed-text` was pulled there live for this spec (confirmed via a real `/api/embeddings` call returning a 768-dim vector) rather than assumed present. Three distinct-topic documents were ingested for real via the CLI (a fictional company/founder/product, pasta-cooking instructions, and black holes — 2 chunks each, 6 total), all through real Ollama embedding calls into a real `sqlite-vec` file at `~/.agent-graph-studio/vector-stores/rag-demo-store.db`. A `vector_search` query specific to the fictional company correctly retrieved only its own chunks, never the unrelated pasta/black-hole content. The full pipeline (`vector_search` → a `code` node building a context-constrained prompt → `llm_call` against a real `qwen2.5:14b`) produced an answer correctly citing the entirely invented details ("Zanzibar Frog", "Quaffle Biscuit", "1987") — information the model could not know from training data alone, conclusively demonstrating the answer was grounded in retrieved content. `DELETE /connections/{name}/vectors` was verified live against a real running `uvicorn` process: the same store/query that returned real content moments earlier returned `(no results found)` immediately after the real `DELETE` call.
- **Full test suite**: `uv run pytest tests/ -v` — 232 passed (203 pre-existing SPEC-001–010 tests unchanged + 29 new across `tests/test_vector_store_connection.py`, `tests/test_ingest_document_node.py`, `tests/test_vector_search_node.py`, and additions to `tests/test_connections.py`/`tests/test_connections_api.py`).
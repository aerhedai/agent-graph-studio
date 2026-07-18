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

- **Storage: Chroma, run locally** — an embedded, file-backed vector database, not a hosted service. Keeps this consistent with the project's local-first philosophy (same reasoning as choosing Ollama and SQLite elsewhere) and requires no new external account/billing.
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
- Should there be a way to delete/clear a vector store's contents without deleting the connection itself? Recommend: yes, a simple `DELETE /connections/{name}/vectors` style operation — small addition, avoids needing to delete and recreate an entire connection just to start over during testing.
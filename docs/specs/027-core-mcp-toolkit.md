# SPEC-027: Core MCP Toolkit — Web Search, File System, and Reference Utilities

**Status:** Draft — ready for review
**Author:** Rohan
**Depends on:** SPEC-026 (self-hosted MCP gateway — every server below is deployed as a `mcp-gateway/` Compose service, bridged the same proven way as `mcp-server-fetch`), SPEC-025 (`api_key`/`bearer` auth path), SPEC-021/023 (per-user credentials, admin-managed global connections)

## 1. Goal

Give every user a curated set of "core" capabilities close to what Claude Code itself has built in — web search, file access, structured reasoning, persistent memory, time/date utilities — available as real node types the moment they open the canvas, with zero new backend code (SPEC-026 already made this true for any server reachable as `remote`/HTTP; this spec is about *which* servers, not new plumbing).

## 2. Scope

**Servers added, all deployed via `mcp-gateway/` (Supergateway-bridged, stateless mode, reusing the exact proven recipe from `fetch`):**

| Server | Package | Runtime | Credential | Notes |
|---|---|---|---|---|
| Web search | `duckduckgo-mcp-server` | uvx | None | Swapped in for Brave Search during implementation — see §4. |
| File system | `@modelcontextprotocol/server-filesystem` | npx | None | Scoped to a new, dedicated sandbox directory (`D:\mcp-servers\fs-sandbox`), bind-mounted read/write. Shared across the whole platform for now — see §7. |
| Time/date utilities | `mcp-server-time` | uvx | None | Timezone conversion, current time, etc. |
| Sequential thinking | `@modelcontextprotocol/server-sequential-thinking` | npx | None | Structured, stepwise reasoning scratchpad. Stateless per call. |
| Memory (knowledge graph) | `@modelcontextprotocol/server-memory` | npx | None | Persistent knowledge graph across calls — needs a durable bind-mounted file, shared across the whole platform for now — see §7. |

**Explicitly excluded (resolved this session, not deferred-and-forgotten):**

- **Shell/command execution.** The existing `code` node (arbitrary Python, already sandboxed by whatever the admin controls) already covers the "escape hatch" need this project's own CLAUDE.md describes. A generic shell-exec MCP node would mean arbitrary commands, from any platform user, executing directly on rohan-pc — real, disproportionate risk for a multi-user platform. Not built.
- **Git.** Deliberately left for its own separate spec/connection later, not bundled into this "core toolkit" pass.

## 3. Why this, why now

SPEC-026 proved the mechanism (a genuinely new-to-this-backend server type, `mcp-server-fetch`, working end-to-end via Supergateway with zero backend changes beyond the one flagged SDK-bug fallback). This spec is the first real test of that mechanism at volume — five more servers, each with its own real quirks (an env-var-only credential, a stateful filesystem/memory backing store, different runtimes) — proving the gateway pattern generalizes, not just a one-off.

## 4. Design decisions (resolved)

- **Web search uses `duckduckgo-mcp-server`, not Brave Search.** Brave Search's official server was the original plan (see git history of this file) — it reads `BRAVE_API_KEY` once from its process environment at startup with no per-request auth mechanism, meaning it could only ever be one shared, admin-provided key rather than a per-user credential. During implementation, obtaining that key turned out to require a paid plan (not the generous free tier assumed when this spec was drafted), so it was swapped for `duckduckgo-mcp-server` instead — needs zero credentials at all (no key, no account), same shared-for-everyone shape as `time`/`sequential-thinking`/`memory` already have. Exposes `search` and `fetch_content`. If a higher-quality paid search provider is wanted later, that's a real, separate future decision, not blocking this spec.
- **Filesystem and memory are shared (not per-user) for now.** The official `server-filesystem`/`server-memory` packages have no identity/auth concept at all — genuine per-user isolation would mean either N per-user containers or a custom auth-aware fork of one of these servers, both real scope beyond "deploy the existing thing." Explicitly flagged as a future direction (§7), not built now.
- **Shell/command execution is out of scope entirely**, per the explicit resolution above — not a "leaning," a decided exclusion.
- **All five servers reuse the exact deployment recipe already proven for `fetch`**: Supergateway, stateless mode (`--outputTransport streamableHttp`, no `--stateful`), relying on `backend/mcp/remote_client.py`'s existing fallback (SPEC-026) for real session handling — no new backend code needed for any of them.

## 5. Data model / infrastructure (illustrative)

All additions live in `mcp-gateway/docker-compose.yml` and `mcp-gateway/Caddyfile`, following the exact pattern the `fetch` service already established — one new service block, one new Caddy site block on the next free port (8445+), no changes to `backend/`, `frontend/`, or `backend/execution/engine.py`.

```yaml
# mcp-gateway/docker-compose.yml additions (as actually shipped)
  web-search:
    image: ghcr.io/supercorp-ai/supergateway:uvx   # python/uvx-based, no credentials
    restart: unless-stopped
    command: ["--stdio", "uvx duckduckgo-mcp-server", "--outputTransport", "streamableHttp", "--port", "8000"]

  filesystem:
    image: ghcr.io/supercorp-ai/supergateway
    restart: unless-stopped
    volumes:
      - fs-sandbox:/sandbox   # bind-mounted from D:\mcp-servers\fs-sandbox on the host
    command: ["--stdio", "npx -y @modelcontextprotocol/server-filesystem /sandbox", "--outputTransport", "streamableHttp", "--port", "8000"]

  time:
    image: ghcr.io/supercorp-ai/supergateway:uvx   # python/uvx-based
    restart: unless-stopped
    command: ["--stdio", "uvx mcp-server-time", "--outputTransport", "streamableHttp", "--port", "8000"]

  sequential-thinking:
    image: ghcr.io/supercorp-ai/supergateway
    restart: unless-stopped
    command: ["--stdio", "npx -y @modelcontextprotocol/server-sequential-thinking", "--outputTransport", "streamableHttp", "--port", "8000"]

  memory:
    image: ghcr.io/supercorp-ai/supergateway
    restart: unless-stopped
    volumes:
      - memory-data:/data   # durable knowledge-graph file -- exact env var confirmed during implementation
    command: ["--stdio", "npx -y @modelcontextprotocol/server-memory", "--outputTransport", "streamableHttp", "--port", "8000"]
```

Each becomes one real, admin-created global `mcp_server` connection in Agent Graph Studio (`web-search`, `filesystem`, `time`, `sequential-thinking`, `memory`), catalog-bootstrapped (SPEC-025) so node types appear for every user immediately, not gated behind someone connecting first — meaningful here since 4 of the 5 need no per-user action at all.

## 6. Acceptance criteria

- [x] All 5 servers running as containers in `mcp-gateway/`, `restart: unless-stopped`, reachable via Caddy on their own ports (8445-8449).
- [x] Each registered as a real global connection with real generated node types visible to every user (filesystem: 14 types, time: 2, sequential-thinking: 1, memory: 9, web-search: 2).
- [x] Real, live tool calls succeeded through Agent Graph Studio for all 5: a real web search (DuckDuckGo, real results for "Model Context Protocol Anthropic"), a real file write + read-back in the sandbox (confirmed on disk on rohan-pc), a real timezone conversion (Europe/London → America/New_York), a real sequential-thinking step, a real memory write + `search_nodes` retrieval (confirmed in the real `memory.jsonl` file).
- [x] Memory's knowledge graph and the filesystem sandbox's contents survived a real `docker compose up -d --force-recreate filesystem memory` — both the file and the memory entry were retrieved again afterward, proving the durable-mount design holds in practice.
- [x] Full existing test suite passes (481 backend, 22 frontend); `git diff main -- backend/execution/engine.py` is empty, and so is the diff for `backend/`/`frontend/` as a whole — this spec needed zero code changes anywhere in the main app.

## 7. Open questions / flagged future directions

- **Per-user filesystem/memory isolation.** Explicitly not built now (shared for the whole platform) — a real future direction if genuine per-user separation becomes a real need, likely requiring either per-user subdirectories with an auth-aware proxy in front of the shared container, or per-user container instances. Not deciding the shape now, just flagging it's real and deferred.
- **A higher-quality paid search provider (Brave or similar).** DuckDuckGo covers the "core toolkit, no friction" need for now; revisit if real usage shows its result quality/rate limits (30 req/min) are a genuine limitation.

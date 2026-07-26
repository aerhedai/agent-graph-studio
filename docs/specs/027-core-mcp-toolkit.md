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
| Web search | `@modelcontextprotocol/server-brave-search` | npx | One shared, admin-provided `BRAVE_API_KEY` | See §4 — this server has no per-request credential concept at all, unlike this project's usual per-user model. |
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

- **Brave Search ships with one shared, admin-provided key**, not a per-user credential. Confirmed via its own documented interface: `@modelcontextprotocol/server-brave-search` reads `BRAVE_API_KEY` once from its process environment at startup; it has no per-request auth mechanism to attach a different caller's key to. This is a deliberate, acknowledged exception — same shape as an admin-provided shared Ollama connection already has today, not a regression, just not "bring your own key" for this one server specifically. Building per-request key injection (threading a caller's own header into a fresh per-call child spawn) was considered and explicitly deferred, not attempted here.
- **Filesystem and memory are shared (not per-user) for now.** The official `server-filesystem`/`server-memory` packages have no identity/auth concept at all — genuine per-user isolation would mean either N per-user containers or a custom auth-aware fork of one of these servers, both real scope beyond "deploy the existing thing." Explicitly flagged as a future direction (§7), not built now.
- **Shell/command execution is out of scope entirely**, per the explicit resolution above — not a "leaning," a decided exclusion.
- **All five servers reuse the exact deployment recipe already proven for `fetch`**: Supergateway, stateless mode (`--outputTransport streamableHttp`, no `--stateful`), relying on `backend/mcp/remote_client.py`'s existing fallback (SPEC-026) for real session handling — no new backend code needed for any of them.

## 5. Data model / infrastructure (illustrative)

All additions live in `mcp-gateway/docker-compose.yml` and `mcp-gateway/Caddyfile`, following the exact pattern the `fetch` service already established — one new service block, one new Caddy site block on the next free port (8445+), no changes to `backend/`, `frontend/`, or `backend/execution/engine.py`.

```yaml
# mcp-gateway/docker-compose.yml additions (illustrative)
  brave-search:
    image: ghcr.io/supercorp-ai/supergateway   # npm-based -- no :uvx tag needed
    restart: unless-stopped
    environment:
      BRAVE_API_KEY: ${BRAVE_API_KEY:?set this in .env}
    command: ["--stdio", "npx -y @modelcontextprotocol/server-brave-search", "--outputTransport", "streamableHttp", "--port", "8000"]

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

- [ ] All 5 servers running as containers in `mcp-gateway/`, `restart: unless-stopped`, reachable via Caddy on their own ports.
- [ ] Each registered as a real global connection with real generated node types visible to every user.
- [ ] Real, live tool calls succeed through Agent Graph Studio for all 5: a real web search, a real file write + read-back in the sandbox, a real time/timezone conversion, a real sequential-thinking step, a real memory write + retrieval.
- [ ] Memory's knowledge graph and the filesystem sandbox's contents survive a container recreate (`docker compose up -d --force-recreate`) — proves the durable-mount design actually holds, not just "worked once."
- [ ] Full existing test suite passes; `git diff main -- backend/execution/engine.py` is empty (this spec should need zero engine changes, same as SPEC-026).

## 7. Open questions / flagged future directions

- **Per-user filesystem/memory isolation.** Explicitly not built now (shared for the whole platform) — a real future direction if genuine per-user separation becomes a real need, likely requiring either per-user subdirectories with an auth-aware proxy in front of the shared container, or per-user container instances. Not deciding the shape now, just flagging it's real and deferred.
- **Exact `server-memory` persistence env var/path** — confirm during implementation (small, low-stakes detail, same treatment SPEC-026 gave Supergateway's own exact flags).

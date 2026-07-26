# mcp-gateway (SPEC-026)

One Docker Compose stack for every **self-hosted** MCP server this project depends on, replacing hand-run PowerShell scripts + Windows Task Scheduler entries with a single versioned file. See `docs/specs/026-mcp-gateway.md` for the full design rationale.

**What this is not**: a shared credential/aggregation layer. This gateway has no concept of users or credentials at all — it only does container lifecycle and TLS-terminated routing. Agent Graph Studio's backend keeps resolving each user's own OAuth token or API key exactly as it already does (SPEC-021/023/025); a gateway-hosted server is just an ordinary `remote` `mcp_server` connection whose `url` happens to point here. (Docker's own MCP Gateway/Toolkit product was considered and explicitly not used — it manages one shared credential set per app per install, which breaks "each user connects their own personal account".)

## Deploy layout

This directory is meant to live at `D:\mcp-servers\mcp-gateway\` on the host, as a **sibling** of the existing `D:\mcp-servers\google_workspace_mcp` checkout — `docker-compose.yml`'s build context for that service is the relative path `../google_workspace_mcp`, so its own `.venv`/`oauth-state`/`certs` stay exactly where they already are. Nothing about that checkout needs to move.

## First deploy (migrating from the old Task Scheduler setup)

1. Copy this `mcp-gateway/` directory to `D:\mcp-servers\mcp-gateway\` on the host (scp or `git clone`/`git pull` of this repo, whichever is available).
2. Create a real `.env` next to `docker-compose.yml` from `.env.example`, filled in with the same Google OAuth client id/secret the existing deployment already uses (unchanged — this migration keeps the exact same external URL, so nothing needs re-registering in Google Cloud Console).
3. `docker compose up -d --build`.
4. Confirm both containers are up: `docker compose ps` (expect `caddy` and `google-workspace`, both healthy).
5. Confirm Gmail still works through Agent Graph Studio itself (a real tool call, e.g. `list_gmail_labels`) — **only after** this succeeds, stop and delete the old Task Scheduler entries (`GoogleWorkspaceMCP`, `CaddyMCPProxy`) and the old standalone `caddy.exe` process, so there's no gap where nothing is serving Gmail.

## Adding a new self-hosted server

Two shapes, depending on whether the server already speaks HTTP or is `stdio`-only:

**Already HTTP-native** (like `google_workspace_mcp`, and `discord-mcp-server` in this same repo — both are plain HTTP servers already, no bridging needed):
```yaml
  my-new-server:
    build:
      context: ../my-new-server   # or wherever its checkout lives
    restart: unless-stopped
    environment:
      # whatever that server's own config needs
```

**`stdio`-only** (most published open-source MCP servers — an npm/pip package meant to be run as a local subprocess, which is exactly what Agent Graph Studio's own `stdio` transport can't do for anything not installed on the backend host itself): wrap it with [Supergateway](https://github.com/supercorp-ai/supergateway) in **stateless** mode (see the real `fetch` service in `docker-compose.yml` for the proven shape — stateless mode was chosen over `--stateful` after live testing: stateful mode's own session bookkeeping is what's strictly rejecting the `mcp` SDK's post-initialize notification in the first place, see `backend/mcp/remote_client.py`'s module docstring; stateless mode sidesteps that class of bug entirely, at the cost of the stdio child process restarting per request rather than staying warm):
```yaml
  my-stdio-server:
    image: ghcr.io/supercorp-ai/supergateway:uvx   # this tag bundles uv/uvx for Python-based servers (e.g. `uvx some-package`); use the plain `:latest`/`:base` tag for npm-based ones (`npx -y ...`)
    restart: unless-stopped
    command:
      - --stdio
      - "uvx some-mcp-server-package"   # or "npx -y @some/mcp-server-package"
      - --outputTransport
      - streamableHttp
      - --port
      - "8000"
```

Either way, then:
1. Add a matching Caddy site block to `Caddyfile`, on the next free port (8445, 8446, ... -- 8444 is already `fetch`), `reverse_proxy`-ing to the new service's Compose DNS name (`my-new-server:8000` — Compose's internal network resolves service names automatically).
2. Add that port to `caddy`'s `ports:` list in `docker-compose.yml`.
3. `docker compose up -d` (pulling a brand-new image over SSH will hit the same Docker-credential-helper limitation documented in this project's own memory notes -- pull/build it once at the machine directly first).
4. In Agent Graph Studio, create a real `mcp_server` connection with `url: https://rohan-pc.taild113ad.ts.net:<new-port>/mcp` (Supergateway's default endpoint path is `/mcp`, matching this project's own convention for every other MCP server it already talks to). Everything downstream — credential type, per-user OAuth/API-key connect, node generation — works exactly as it does for any other connection, using the exact same code path already proven against `fetch`.

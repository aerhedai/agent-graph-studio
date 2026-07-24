# discord-mcp-server

A small, standalone, spec-conformant [MCP](https://modelcontextprotocol.io) server exposing one tool — `send_message` — backed by Discord's `webhook.incoming` OAuth scope. Built to prove agent-graph-studio's generic per-user MCP OAuth mechanism (SPEC-021) against a real server, not a mocked one.

This is a deliberately separate project from `agent-graph-studio` itself — it's an *external* MCP server the main app connects to over HTTP, exactly like any third-party MCP server would be. See `main.py`'s module docstring for the full architecture reasoning (why it proxies Discord's token endpoint, why it doesn't use FastMCP's built-in auth subsystem).

## What it does

A user with "Manage Webhooks" permission on a Discord server authorizes this app for one specific channel. No bot, no admin approval beyond that one consent screen, no shared app-wide secret — each connection is genuinely per-user, per-channel.

## Setup

### 1. Register a Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **OAuth2 → General**: copy the **Client ID** and **Client Secret**.
3. **OAuth2 → Redirects**: add `{your agent-graph-studio public base URL}/connections/oauth/callback` — the *same* callback URL agent-graph-studio's own `/connections/oauth/start` flow already uses for every MCP OAuth connection, not a URL on this server.

You do **not** need to set a redirect URI on this server itself, and this server does **not** need `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET` as its own env vars — the token exchange it proxies carries whatever `client_id`/`client_secret` agent-graph-studio sends it, straight through to Discord.

### 2. Configure and run

```bash
cd discord-mcp-server
uv sync

export DISCORD_MCP_PUBLIC_BASE_URL="https://your-tunnel-or-domain.example.com:PORT"  # wherever this process is actually reachable from the outside
export DISCORD_MCP_PORT=8001            # optional, defaults to 8001
export DISCORD_MCP_DB_PATH=~/.discord-mcp-server/tokens.db  # optional, this is the default

uv run python3 main.py
```

### 3. Expose it publicly

Needs to be reachable at the URL you set as `DISCORD_MCP_PUBLIC_BASE_URL` — e.g. an additional Tailscale Funnel port mapping on the same node already serving agent-graph-studio's own backend (`tailscale funnel --bg 8001` or equivalent, alongside whatever already serves port 8000), or any other tunnel/reverse-proxy of your choice.

### 4. Connect it from agent-graph-studio

In the canvas, create a new `mcp_server` connection:
- `transport`: `remote`
- `url`: `{DISCORD_MCP_PUBLIC_BASE_URL}/mcp`
- `oauth_client_id` / `oauth_client_secret`: the Discord Client ID/Secret from step 1 (this server has no dynamic client registration support — Discord doesn't offer it either — so these must be supplied up front)

Saving will detect the OAuth requirement automatically; connect it via Settings → Connections afterward to complete the real consent flow.

## Verifying it's alive without a real Discord app

```bash
curl http://127.0.0.1:8001/.well-known/oauth-protected-resource
curl http://127.0.0.1:8001/.well-known/oauth-authorization-server
curl -i -X POST http://127.0.0.1:8001/mcp   # expect a 401 + WWW-Authenticate with no token
```

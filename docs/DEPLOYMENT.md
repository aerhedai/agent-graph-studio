# Deployment (SPEC-016, SPEC-017)

Docker/Docker Compose packaging so this app runs as a persistent, always-on service — the same shape n8n itself ships in — instead of two manually-started dev processes on a laptop. This doesn't pick a host for you; it makes the app deployable to any of them (a VPS, Railway, Fly.io, a home server) via a standard container interface.

## Required for ANY run, Docker or plain `uv run` (SPEC-017, SPEC-020)

Six secrets are required at startup — the backend refuses to start without any of them, deliberately (no silent fallback):

- `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` — a real Fernet key encrypting connection secrets (bot tokens, API keys) at rest. Generate one: `uv run python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `AGENT_GRAPH_STUDIO_API_KEY` — the shared credential machine callers use (webhook URLs external services hit directly, via `?key=`). Any string you choose. No longer the human login path (see SPEC-020 below) — narrowed to machine-to-machine use.
- `AGENT_GRAPH_STUDIO_JWT_SECRET` — signs session tokens issued after Google sign-in (SPEC-020). A long random string (≥32 bytes) — distinct from the encryption key above; signing sessions and encrypting connection secrets are different security domains. Generate one: `uv run python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID` / `AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET` — from a Google Cloud OAuth 2.0 Client ID (Web application type), created in [Google Cloud Console](https://console.cloud.google.com/apis/credentials). Add `{your public base URL}/auth/google/callback` as an authorized redirect URI on that client (the same URL you set via Settings → Public base URL in the app once it's running — a JSON setting, not an env var).
- `AGENT_GRAPH_STUDIO_ADMIN_EMAIL` — the Google account email that gets bootstrapped as the first admin (invite-allowlisted with `role=admin`) on every startup. Must be a real Gmail/Google Workspace address you can sign into.

For local development (not Docker), copy `.env.example` to `.env` and fill in real values — the backend loads `.env` automatically via `python-dotenv` (both `uv run uvicorn backend.api.app:app --reload` and `uv run agent-graph-studio <graph.json>` do this), no `export` needed:
```bash
cp .env.example .env
# edit .env — VS Code opens/edits it like any other file (it's git-ignored,
# real secrets stay local); .env.example documents what each var does and
# how to generate the ones that need generating.
uv run uvicorn backend.api.app:app --reload
```
`.env` is only read for local `uv run` use — Docker Compose reads real environment variables (or its own `.env` next to `docker-compose.yml`, a separate file Compose has built-in support for), see Build and run below.

The canvas gates behind a "Sign in with Google" button on first load; the session token it receives is remembered (`localStorage`) across reloads, same storage mechanism as the old API-key prompt. An existing plaintext `connections.json` from before encryption was added is migrated to encrypted storage automatically, the first time it's read — no manual step, no data loss.

Signing in requires the Public base URL (Settings panel, or `PUT /settings`) to already be set — Google's redirect URI is built from it. Only an invited email (the bootstrapped admin, or anyone that admin invites via Settings → Invite) can complete sign-in; any other real Google account is cleanly rejected, not silently given an account.

## Build and run

```bash
export AGENT_GRAPH_STUDIO_ENCRYPTION_KEY="<generated above>"
export AGENT_GRAPH_STUDIO_API_KEY="<your choice>"
export AGENT_GRAPH_STUDIO_JWT_SECRET="<generated above>"
export AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID="<from Google Cloud Console>"
export AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET="<from Google Cloud Console>"
export AGENT_GRAPH_STUDIO_ADMIN_EMAIL="<your Google account email>"
docker compose up --build
```
(Or put all six in a `.env` file next to `docker-compose.yml` — Compose reads it automatically. Omitting any of them fails fast with a clear message before anything starts building.)

- Frontend (the app itself): `http://localhost:8081`
- Backend API directly: `http://localhost:8000`

The frontend's nginx serves the built SPA and reverse-proxies everything else to the backend, so the app is reachable from one origin (`:8081`) with no CORS configuration needed. Hitting `:8000` directly is also available (handy for `curl`/debugging), but a real deployment should only need to expose `:8081` publicly. (Port `8081`, not the more obvious `8080`, purely because `8080` was already taken by something else on the machine this was built on — pick whatever host port is free in your own `docker-compose.yml`.)

To stop: `docker compose down` (this does **not** delete the named volume — your saved graphs, run history, and connections survive). To also wipe all persisted data: `docker compose down -v`.

## Persistent data

Everything durable — the connections store, run history, and saved graphs — lives under one named Docker volume (`agent-graph-studio-data`), mounted at `/data` inside the backend container. This is the exact same `~/.agent-graph-studio/` path structure the storage layer already uses locally (`backend/connections/store.py`, `backend/storage/runs_store.py`, `backend/storage/graphs_store.py`) — the backend container's `HOME` is just set to `/data`, so every store's existing default path lands in the volume with zero code changes.

To back up: the volume is a plain directory of files (one JSON file, two SQLite `.db` files). Find its location on the host with `docker volume inspect agent-graph-studio-data` and copy it, or use `docker compose exec backend tar -czf - -C /data .` to stream a tarball out.

## Environment variables

| Variable | Set by | Purpose |
|---|---|---|
| `AGENT_GRAPH_STUDIO_CONNECTIONS_PATH` | `docker-compose.yml` | Where the connections store (bot tokens, API keys, etc.) lives. Points into the mounted volume. |
| `AGENT_GRAPH_STUDIO_RUNS_DB_PATH` | `docker-compose.yml` | Where run history (SPEC-010) lives. Points into the mounted volume. |
| `AGENT_GRAPH_STUDIO_GRAPHS_DB_PATH` | `docker-compose.yml` | Where saved graphs + trigger activation state (SPEC-015) live. Points into the mounted volume. |
| `AGENT_GRAPH_STUDIO_ENCRYPTION_KEY` | **you, required** | Fernet key encrypting connection secrets at rest (SPEC-017). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_API_KEY` | **you, required** | The machine-caller credential webhook URLs use, via `?key=` (SPEC-017, narrowed by SPEC-020). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_JWT_SECRET` | **you, required** | Signs session tokens issued after Google sign-in (SPEC-020). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_ID` | **you, required** | Google OAuth 2.0 client ID for Sign in with Google (SPEC-020). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_GOOGLE_CLIENT_SECRET` | **you, required** | Google OAuth 2.0 client secret paired with the client ID above (SPEC-020). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_ADMIN_EMAIL` | **you, required** | Google account email bootstrapped as the first admin on every startup (SPEC-020). No default — the backend refuses to start without it. |
| `AGENT_GRAPH_STUDIO_USERS_DB_PATH` | `docker-compose.yml` | Where the accounts/invite-allowlist store (SPEC-020) lives. Points into the mounted volume. |
| `VITE_API_BASE` | `frontend/Dockerfile` build ARG | Baked into the frontend at **build time** (Vite's normal env-var mechanism), not read at runtime. Left empty by default so API calls are relative to whatever origin nginx serves the app from — this is what makes the same-origin reverse-proxy above work with zero frontend code changes. |

**A real limitation, not an oversight**: because `VITE_API_BASE` is build-time, changing where the frontend expects its backend (e.g. a different public URL) requires rebuilding the frontend image, not just flipping an environment variable at runtime. If this becomes painful in practice, the natural fix is a small runtime-config shim (nginx `envsubst`-templating a tiny `config.js` at container start) — deliberately not built here, since the default (empty, same-origin) setup doesn't need it and this project's convention is not to build speculative flexibility before it's actually needed.

## Connecting to an LLM (local or cloud)

This is **your own choice as the operator**, not something this app special-cases. Wherever this backend container actually runs, a `model`/`llm_call` node's connection just needs to point at whatever's reachable *from there*:
- A cloud provider (Anthropic, etc.) — works from anywhere, no networking to think about.
- Your own local model (e.g. Ollama) — needs to be reachable from wherever the container runs: on the same host (`http://host.docker.internal:11434` from inside the container, or join the container to `network_mode: host`), or over a private network like Tailscale if the container and the model server are on different machines.

There is nothing to configure in this compose file for this — it's entirely a matter of what connection profile you create in the app once it's running.

## TLS / public reachability

This compose setup runs plain HTTP. Putting it on the public internet for real means a TLS-terminating reverse proxy in front of it (Caddy, nginx with Let's Encrypt, your hosting platform's built-in TLS, a Cloudflare Tunnel, etc.) — the same requirement this project already ran into directly when testing real webhook triggers locally (Telegram, specifically, refuses to deliver to a plain-HTTP or unreachable URL). This is deliberately out of scope for this compose file itself; pick whichever TLS approach matches wherever you actually deploy.

## Known scope boundaries (see SPEC-016 for the full reasoning)

- Single-process `uvicorn`, no multi-worker/gunicorn — the in-memory parts of trigger handling still assume one process.
- No Kubernetes/Helm — Compose is sufficient at this project's current single-user, local-first scale.
- No zero-downtime/rolling deploy support.

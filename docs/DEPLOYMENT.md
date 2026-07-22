# Deployment (SPEC-016)

Docker/Docker Compose packaging so this app runs as a persistent, always-on service — the same shape n8n itself ships in — instead of two manually-started dev processes on a laptop. This doesn't pick a host for you; it makes the app deployable to any of them (a VPS, Railway, Fly.io, a home server) via a standard container interface.

## Build and run

```bash
docker compose up --build
```

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

# SPEC-016: Deployment Packaging (Docker)

**Status:** Draft — ready for implementation
**Milestone:** Toward a real, self-hostable app (n8n-parity push)
**Author:** Rohan
**Depends on:** SPEC-015 (durable trigger activation — deploying somewhere persistent is pointless if triggers still reset on every process restart)

## 1. Goal

Package the backend and frontend so this project can run as a persistent, always-on service — Docker images plus a `docker-compose.yml` — the same shape n8n itself ships in (Docker/Docker Compose/Helm, never serverless), instead of two manually-started dev processes on a laptop.

## 2. Why this, why now

Everything built so far assumes `uv run` and `npm run dev` running in a terminal you own. That's fine for development, but "become something similar to n8n" means this needs to run unattended, survive being closed, and be reachable without a personally-run tunnel (Tailscale Funnel/ngrok) that dies the moment a laptop sleeps. This spec doesn't pick a specific cloud host — it makes the app *deployable* to any of them (a VPS, Railway, Fly.io, a home server) via a standard container interface, which is the actual prerequisite regardless of where it eventually runs.

## 3. Scope

In scope:
- `backend/Dockerfile` — a production image running the FastAPI app via `uvicorn` (or `gunicorn` + uvicorn workers if concurrency needs justify it — default to plain `uvicorn` for v1, matching this project's "simplest correct approach" convention unless load testing says otherwise), built via `uv` (matching the project's existing dependency-management convention, not switching to pip for the container).
- `frontend/Dockerfile` — builds the Vite app to static assets, served by a lightweight static server (e.g. `nginx` or `serve`) — this is a static SPA, no Node process needed at runtime.
- `docker-compose.yml` at the repo root — wires backend + frontend together, with named volumes for the SQLite files (`~/.agent-graph-studio/*.db` equivalents) and the connections store, so data survives a container recreate, not just a container restart.
- A reverse-proxy note/config (nginx or Caddy) so the frontend's static assets and the backend's API share one public origin without CORS gymnastics — matches how a real deployment would actually be reached from the internet.
- `.env`/environment-variable documentation for anything that's currently a hardcoded path or `localhost` default (e.g. `VITE_API_BASE`, the SQLite path overrides already used for test isolation) — these become the real production configuration surface, not just a testing seam.
- A `docs/DEPLOYMENT.md` (or README section) documenting: build, run, where persistent data lives, how to point at an LLM connection reachable from wherever the container actually runs (per the earlier clarification: this is the operator's choice, not something the app special-cases).

Out of scope (future work, explicitly not this spec):
- Choosing/renting an actual production host — that's an operational decision made when actually deploying, not a spec deliverable
- Kubernetes/Helm packaging — Docker Compose is sufficient for this project's stated single-user/local-first scale; revisit only if that changes
- Zero-downtime rolling deploys, blue/green, etc. — not relevant at this project's current scale
- HTTPS/TLS certificate automation — assumed handled by whatever reverse proxy or platform (Railway/Fly/etc.) the operator eventually chooses; this spec's compose setup runs plain HTTP, documented as "put a real TLS-terminating proxy in front of this for anything public"

## 4. Design decisions (resolved)

- **`uv` inside the container, not a pip freeze/export.** Consistent with CLAUDE.md's dependency-management convention — `uv sync --frozen` against the committed `uv.lock` in a multi-stage build (a builder stage with `uv`, a slim final runtime stage) keeps the image reasonably small without duplicating dependency truth in a second format.
- **SQLite files and the connections store live in a named Docker volume**, mounted at the same path structure `~/.agent-graph-studio/` already uses inside the container's home dir — no code changes to the storage layer's path logic, since it already supports env-var path overrides (SPEC-010's precedent); Compose just sets those env vars to volume-mounted paths explicitly rather than relying on the container user's implicit home directory.
- **Frontend is a static build, not a running Node dev server.** `npm run build` output served by a minimal static file server — the existing `VITE_API_BASE` env var (already used for pointing at a non-default backend origin) becomes the real mechanism for the built frontend to find its backend, set at build time or via a small runtime-config shim if build-time proves too inflexible (decide during implementation, document whichever is chosen).
- **No code changes to `backend/execution/engine.py` or any node type** — this spec is purely packaging/ops, same "engine diff stays empty" discipline as SPEC-010/015.

## 5. Acceptance criteria

- [ ] `docker compose up` from a clean checkout builds both images and results in a working app reachable on a local port, with zero manual `uv`/`npm` steps outside the containers
- [ ] Data written before a `docker compose restart` (a saved graph, a run, a connection) is still present after — proving volumes are wired correctly, not just "the container happens to still have its filesystem" (verified by an explicit `docker compose down && docker compose up`, not just a soft restart, since `down` without `-v` should still preserve named volumes while proving the container's own ephemeral layer doesn't matter)
- [ ] A trigger activated before a full `docker compose down && up` cycle is still active afterward — this is SPEC-015's guarantee, verified again here at the container-lifecycle level specifically, not just the bare-process level SPEC-015 tested
- [ ] `docs/DEPLOYMENT.md` accurately documents every environment variable a real deployment needs to set, verified by actually following the doc from a clean environment (not just written and assumed correct)
- [ ] `git diff main -- backend/execution/engine.py` stays empty

## 6. Open questions

- `gunicorn` + multiple uvicorn workers, or plain single-process `uvicorn`? Recommend: single-process for v1 — this project's execution model already handles concurrency via background threads within one process (SPEC-005's async round-based scheduler, SPEC-010's per-call SQLite connections), and multi-worker would mean the in-memory `trigger_registry` (until SPEC-015 fully replaces its role) and any remaining in-process state gets partitioned unpredictably across workers. Revisit only if real load demands it.

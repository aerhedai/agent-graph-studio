# SPEC-016: production backend image. Multi-stage, uv-based (not pip
# freeze/export -- CLAUDE.md's dependency-management convention, `uv sync
# --frozen` against the committed uv.lock is the single source of truth).
# Single-process uvicorn (spec-016 §6's resolved open question) -- no
# gunicorn/multi-worker, since the in-memory trigger_registry (SPEC-009)
# still partitions unpredictably across workers.

FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app

# Dependencies first, in their own layer, so an app-code-only change
# doesn't invalidate the (slow) dependency-install layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
RUN uv sync --frozen --no-dev

FROM python:3.14-slim
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:${PATH}"

# The storage layer (backend/connections/store.py, backend/storage/*.py)
# already defaults every path to Path.home() / ".agent-graph-studio" --
# zero code changes needed here, just pointing HOME at the volume mount
# (docker-compose.yml) so that default lands in persistent storage.
ENV HOME=/data

EXPOSE 8000
CMD ["uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

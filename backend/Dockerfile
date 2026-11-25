# Build context is the repo root (see docker-compose.yml) so vendor/phase1 and vendor/phase2 are included.
# Stages: base (runtime deps) -> migrate (Alembic only, no torch) / backend (default target, the API).
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# compose service `migrate` (T3-18): alembic upgrade head against DATABASE_URL, then exits
FROM base AS migrate
COPY alembic.ini ./
COPY migrations migrations
COPY src/kosis_store src/kosis_store
ENV PYTHONPATH=/app/src
CMD ["uv", "run", "--no-sync", "alembic", "upgrade", "head"]

FROM base AS backend
# embed: sentence-transformers for the phase 2 query embedder / reranker; torch runs on CPU in the container.
# WITH_EMBED=0 (deploy): no torch; .env EMBED_URL / RERANK_URL point at the inference servers (inference/,
# backend/app/search_open.py). Default 1 keeps the models in this process as before.
ARG WITH_EMBED=1
RUN if [ "$WITH_EMBED" = "1" ]; then uv sync --frozen --no-dev --group embed; fi
COPY vendor/phase1 vendor/phase1
COPY vendor/phase2/src vendor/phase2/src
COPY src src
COPY backend backend
# HTTP clients of the inference servers only (httpx, already a dependency of openai); the server stays out
COPY inference/__init__.py inference/client.py inference/
ENV PYTHONPATH=/app:/app/src:/app/vendor/phase2/src:/app/vendor/phase1:/app/vendor/phase1/src
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "python", "-m", "backend.app.server", "--host", "0.0.0.0", "--port", "8000"]

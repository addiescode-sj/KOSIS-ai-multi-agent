# Build context is the repo root (see docker-compose.yml) so vendor/phase1 and vendor/phase2 are included.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# embed: sentence-transformers for the phase 2 query embedder / reranker; torch runs on CPU in the container
# ponytail: CPU torch makes the image large; split inference out in M3 (image size, reranker 25~39 s on CPU)
RUN uv sync --frozen --no-dev --group embed
COPY vendor/phase1 vendor/phase1
COPY vendor/phase2/src vendor/phase2/src
COPY src src
COPY backend backend
ENV PYTHONPATH=/app:/app/src:/app/vendor/phase2/src:/app/vendor/phase1:/app/vendor/phase1/src
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

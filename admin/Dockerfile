# The operator app (T3-57). Build context is the repo root: docker build -f admin/Dockerfile -t kosis-admin .
# No agent code and no torch: kosis_store (log queries, masking) + admin/ only.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group admin && useradd --uid 10001 --no-create-home app
COPY src/kosis_store src/kosis_store
COPY admin admin
ENV PYTHONPATH=/app:/app/src PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8100
# migrations: /app/.venv/bin/alembic -c admin/alembic.ini upgrade head · daily clean-up: /app/.venv/bin/python -m admin.app.cleanup
CMD ["/app/.venv/bin/uvicorn", "admin.app.main:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]

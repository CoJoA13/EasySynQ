# EasySynQ API — Python 3.12 (D4). Build context is the REPO ROOT so that
# apps/api/* and the single migrations/ tree land at predictable paths:
#   /app/alembic.ini  + script_location %(here)s/../../migrations -> /migrations
# Build with:  docker build -f apps/api/Dockerfile -t easysynq/api .   (from repo root)
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EASYSYNQ_MIGRATIONS_DIR=/migrations
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv
WORKDIR /app

# --- dependency layer (cached) ---
COPY apps/api/pyproject.toml apps/api/uv.lock* ./
RUN uv sync --no-dev --no-install-project

# --- app + migrations ---
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./alembic.ini
COPY migrations /migrations
RUN uv sync --no-dev

EXPOSE 8000
# Uvicorn workers under Gunicorn (doc 03 §1)
CMD ["uv", "run", "gunicorn", "easysynq_api.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]

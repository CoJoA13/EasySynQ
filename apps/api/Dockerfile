# EasySynQ API — Python 3.12 (D4). Build context is the REPO ROOT so that
# apps/api/* and the single migrations/ tree land at predictable paths:
#   /app/alembic.ini  + script_location %(here)s/../../migrations -> /migrations
# Build with:  docker build -f apps/api/Dockerfile -t easysynq/api .   (from repo root)

# uv binary source as a NAMED stage so Dependabot's docker ecosystem tracks the uv image — its parser
# reads `FROM` lines, not `COPY --from=<image>` (dependabot-core#5103). Only the /uv binary is copied.
FROM ghcr.io/astral-sh/uv:0.4 AS uv

FROM python:3.14-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EASYSYNQ_MIGRATIONS_DIR=/migrations
# S8b2: postgresql-client-16 (pg_dump/pg_restore) for the backup + restore-test drill. Pinned to 16
# to match the postgres:16 server (an older client refuses a newer server). Via the PGDG apt repo
# (bookworm ships only client-15); build-time only, so air-gapped *installs* are unaffected (the
# image is pre-built). Only the worker runs the drill; the shared image carries it for both.
# S-ing-1: libmagic1 — the system lib python-magic content-sniffs imported files with (doc 09 §4.1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 libmagic1 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
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

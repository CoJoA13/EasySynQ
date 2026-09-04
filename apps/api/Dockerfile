# EasySynQ API — Python 3.12 (D4). Build context is the REPO ROOT so that
# apps/api/* and the single migrations/ tree land at predictable paths:
#   /app/alembic.ini  + script_location %(here)s/../../migrations -> /migrations
# Build with:  docker build -f apps/api/Dockerfile -t easysynq/api .   (from repo root)

# uv binary source as a NAMED stage so Dependabot's docker ecosystem tracks the uv image — its parser
# reads `FROM` lines, not `COPY --from=<image>` (dependabot-core#5103). Only the /uv binary is copied.
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EASYSYNQ_MIGRATIONS_DIR=/migrations
# S8b2: postgresql-client-18 (pg_dump/pg_restore) for the backup + restore-test drill. Pinned to 18
# to match the postgres:18 server in infra/images.lock. pg_dump REFUSES a newer server outright, so
# a stale client here breaks the worker's drill in the container while CI (which runs from source
# with the host's own client) stays green. Via the PGDG apt repo
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
    && apt-get install -y --no-install-recommends postgresql-client-18 libmagic1 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# --- dependency layer (cached) ---
# uv.lock is NOT globbed: `uv.lock*` silently tolerates a missing lockfile and resolves fresh, so a
# build that lost the lock would ship un-pinned dependencies and still succeed. --locked then makes
# uv REFUSE to resolve — it errors if the lockfile is absent or out of date with pyproject.toml
# rather than quietly updating it, which is what makes the image reproducible (audit U34).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# --- app + migrations ---
COPY apps/api/src ./src
COPY apps/api/alembic.ini ./alembic.ini
COPY migrations /migrations
RUN uv sync --locked --no-dev

# --- non-root runtime (audit U33) ---
# A fixed UID/GID, not a floating one: Docker seeds a FRESH named volume with the ownership of the
# image path behind it, so mirror/secrets/backups come up writable on a new install without any
# operator step. Pre-creating the three mount points here is what makes that work — an unseeded
# volume would be created root-owned and the worker could not write it.
# The chown of /app is NOT recursive: only the directory itself must be writable (Celery Beat's
# schedule file lands in the CWD). Recursing rewrote the 159 MB venv into a duplicated 171 MB
# layer, and UV_NO_SYNC below means nothing writes into the venv at runtime anyway.
# ⚠ Volumes that ALREADY exist keep their old root ownership; the chown is in
# docs/runbooks/install-online.md ("Container users and SELinux").
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} easysynq \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home --shell /usr/sbin/nologin easysynq \
    && install -d -o easysynq -g easysynq \
        /var/lib/easysynq/qms-mirror \
        /var/lib/easysynq/backups \
        /run/secrets \
    && chown easysynq:easysynq /app
# ⚠ UV_NO_SYNC is load-bearing, not tidiness. `uv run` SYNCS the environment on every invocation,
# and it resolves the DEV dependency group the image deliberately did not install (--no-dev) — so
# every `uv run` (the api CMD, `migrate`'s alembic, the worker's celery, every `easysynq` CLI call)
# reached PyPI at container start. Offline that is fatal: an air-gapped stack died on `migrate`
# before anything else ran. Online it silently pulled dev tooling into a production container on
# every boot. UV_NO_SYNC makes `uv run` use the environment the build produced, as-is.
# UV_FROZEN/UV_NO_CACHE additionally stop it re-locking or writing a cache as a user with no home.
ENV UV_NO_SYNC=1 \
    UV_FROZEN=1 \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
USER easysynq

EXPOSE 8000
# Uvicorn workers under Gunicorn (doc 03 §1)
# `--forwarded-allow-ips ""` disables uvicorn's own ProxyHeadersMiddleware, which would otherwise
# rewrite `request.client` from X-Forwarded-For before the application sees it. Gunicorn defaults
# this to `127.0.0.1,::1`, so on any loopback-reachable deployment a caller could choose its own
# apparent peer address — and the application's trust check would then be evaluating a value the
# caller supplied. TRUSTED_PROXY_CIDRS is the single place that decides whose forwarded chain is
# believed; two mechanisms stacked would silently re-open what one of them closes.
CMD ["uv", "run", "gunicorn", "easysynq_api.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", "--forwarded-allow-ips", "", \
     "-b", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]

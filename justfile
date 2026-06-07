# EasySynQ developer task runner (dev-only; host ops use scripts/easysynq + install.sh).
# Requires: just, uv, node/npm, docker compose v2. See docs/18-mvp-implementation-plan.md §2.

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

# --- setup ---
setup:
    cd apps/api && uv sync
    cd apps/web && npm install
    pre-commit install
    just contracts

# --- contracts (OpenAPI-first; the source of truth) ---
contracts:
    bash scripts/gen-contracts.sh

contracts-check:
    bash scripts/gen-contracts.sh --check

# --- dev servers ---
api-dev:
    cd apps/api && uv run uvicorn easysynq_api.main:app --reload --host 0.0.0.0 --port 8000

web-dev:
    cd apps/web && npm run dev

# --- quality ---
lint:
    cd apps/api && uv run ruff check . && uv run mypy src
    cd apps/web && npm run lint && npm run typecheck

fmt:
    cd apps/api && uv run ruff format . && uv run ruff check --fix .
    cd apps/web && npm run fmt

# --- tests ---
test:
    cd apps/api && uv run pytest
    cd apps/web && npm test

test-contract:
    cd apps/api && uv run pytest -m contract

# --- migrations (single tree at repo root) ---
migrate-new msg="":
    cd apps/api && uv run alembic revision --autogenerate -m "{{msg}}"

migrate-up:
    cd apps/api && uv run alembic upgrade head

migrate-down:
    cd apps/api && uv run alembic downgrade -1

migrate-roundtrip:
    cd apps/api && uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head

# --- compose stack ---
up profile="s":
    docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.{{profile}}.yml up -d

down:
    docker compose --env-file .env -f infra/compose/compose.yml down

logs:
    docker compose --env-file .env -f infra/compose/compose.yml logs -f --tail=100

# (Re)create the Keycloak `demo` dev user for local login. Keycloak has no volume, so its data
# (incl. this user) is wiped on `just down` / any keycloak recreate; the realm re-imports from
# realm-export.json. Idempotent; password is the documented dev credential.
demo-user:
    #!/usr/bin/env bash
    set -euo pipefail
    pw="$(grep -m1 '^KEYCLOAK_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
    kc() { docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null; }
    kc config credentials --server http://localhost:8080 --realm master --user admin --password "$pw" >/dev/null
    kc create users -r easysynq -s username=demo -s enabled=true 2>/dev/null || true
    kc set-password -r easysynq --username demo --new-password "Demo-Password-1"
    echo "demo / Demo-Password-1 ready - sign in at http://localhost"

# --- packaging ---
airgap:
    bash scripts/airgap-bundle.sh

# Resolve every image in infra/images.lock to an @sha256 digest (a RELEASE-CEREMONY step — needs a
# connected host + Docker; never run in CI or on the air-gapped target). Prints the pinned refs to
# append to images.lock so a release ships immutable, digest-pinned images (doc 03 §15, S11).
images-update:
    @grep -vE '^\s*#|^\s*$' infra/images.lock | awk '{print $2}' | while read -r img; do \
        digest=$(docker manifest inspect "$img" >/dev/null 2>&1 && docker buildx imagetools inspect "$img" --format '{{ "{{" }}.Manifest.Digest}}' 2>/dev/null || true); \
        if [ -n "$digest" ]; then echo "$img@$digest"; else echo "# COULD NOT RESOLVE: $img"; fi; \
    done

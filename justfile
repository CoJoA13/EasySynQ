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

# Local CI: the full api + web fast loops (uv/node toolchain; no Docker). Mirror of the green gates.
check:
    cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m unit
    cd apps/web && npm run lint && npm run typecheck && npm run build && npm test

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
    bash scripts/demo-user.sh

# Dev fixture: create the SoD persona logins (priya/ken/mara) in Keycloak + seed their
# author/approver/releaser grants, so the full review->approve->release loop (S-web-5) is demoable.
# Keycloak is ephemeral (wiped on `just down`), so re-run after a reset. Idempotent; password is the
# documented dev credential.
seed-personas:
    bash scripts/seed-personas.sh

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

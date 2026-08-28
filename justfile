# EasySynQ developer task runner (dev-only; host ops use scripts/easysynq + install.sh).
# Requires: just, uv, node/npm, docker compose v2. See docs/18-mvp-implementation-plan.md §2.

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

# --- setup ---
setup:
    cd apps/api && uv sync
    npm ci --prefix apps/web
    npm ci --prefix packages/contracts --ignore-scripts
    just contracts
    pre-commit install

# --- contracts (OpenAPI-first; the source of truth) ---
contracts:
    bash scripts/gen-contracts.sh

contracts-check:
    bash scripts/gen-contracts.sh --check

authority-check:
    ./scripts/check-repo-authority.sh

doctor profile="contributor":
    ./scripts/doctor.sh "{{ profile }}"

security-npm:
    node --test scripts/tests/test-web-security-lock.mjs scripts/tests/test-npm-audit-runner.mjs scripts/tests/test-check-npm-audit.mjs scripts/tests/test-npm-audit-policy.mjs scripts/tests/test-router-rsc-policy.mjs
    node scripts/check-npm-audit.mjs

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
    cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract

test-browser:
    cd apps/web && npm run test:browser

# Local CI: the full api + web fast loops (uv/node toolchain; no Docker). Mirror of the green gates.
check:
    cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit -m unit
    cd apps/web && npm run lint && npm run build && npm test

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
    bash scripts/ensure-keycloak-db-password.sh --env-file .env
    bash scripts/migrate-keycloak-h2.sh
    docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.{{profile}}.yml -f infra/compose/compose.dev.yml up -d

down:
    bash scripts/ensure-keycloak-db-password.sh --env-file .env
    docker compose --env-file .env -f infra/compose/compose.yml down

logs:
    bash scripts/ensure-keycloak-db-password.sh --env-file .env
    docker compose --env-file .env -f infra/compose/compose.yml logs -f --tail=100

# Create/update the Keycloak `demo` dev user for local login. Identity state persists in PostgreSQL
# across container recreation; volume deletion (compose down -v) is the destructive reset. Idempotent.
demo-user:
    bash scripts/demo-user.sh

# Dev fixture: create the SoD persona logins (priya/ken/mara) in Keycloak + seed their
# author/approver/releaser grants, so the full review->approve->release loop (S-web-5) is demoable.
# Re-run whenever you want to restore the fixture passwords/grants. Idempotent; identity state
# persists in PostgreSQL across ordinary restarts and container recreation.
seed-personas:
    bash scripts/seed-personas.sh

# --- packaging ---
# Build the air-gap bundle: builds easysynq/{api,web,keycloak} from this checkout, pulls the pinned
# third-party set, and `docker save`s all of it to dist/ (+ .sha256 + .manifest.txt). Needs Docker +
# network; several GB and a long first run. The target installs it with `install.sh … --offline`.
airgap:
    bash scripts/airgap-bundle.sh

# The release gate: assert every non-dev image in infra/images.lock is @sha256-pinned. The check
# lives in test_images_lock_pinned.py and SKIPS unless EASYSYNQ_RELEASE=1, so before this recipe
# nothing ran it — a release shipped floating tags whenever the operator forgot the manual step.
# CI also runs it on a v* tag; this is the local equivalent.
release-check:
    cd apps/api && EASYSYNQ_RELEASE=1 uv run pytest tests/unit/test_images_lock_pinned.py -q

# Resolve every image in infra/images.lock to an @sha256 digest (a RELEASE-CEREMONY step — needs a
# connected host + Docker; never run in CI or on the air-gapped target). Prints the pinned refs to
# paste into images.lock so a release ships immutable, digest-pinned images (doc 03 §15, S11).
# Retries and FAILS if any image is unresolved: the old inline version printed a
# "# COULD NOT RESOLVE" comment and exited 0, so pasting a rate-limited run silently dropped that
# image from the lock.
images-update:
    bash scripts/images-update.sh

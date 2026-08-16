#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT"

ENV_FILE="$ROOT/.env"
PROJECT="easysynq-first-admin-$(openssl rand -hex 6)"

validate_project() {
  [[ "$1" =~ ^easysynq-first-admin-[a-z0-9]+$ ]]
}

if ! validate_project "$PROJECT"; then
  echo "live acceptance generated an invalid Compose project" >&2
  exit 2
fi
if [ -e "$ENV_FILE" ] || [ -L "$ENV_FILE" ]; then
  echo "live acceptance refuses an existing .env" >&2
  exit 2
fi

env_created=0
stack_started=0
COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -p "$PROJECT"
  -f infra/compose/compose.yml
  -f infra/compose/compose.s.yml
  -f infra/compose/compose.dev.yml
)

cleanup() {
  local status=$?
  local stack_clean=1
  trap - EXIT INT TERM
  set +e

  if ! validate_project "$PROJECT"; then
    echo "live acceptance refuses cleanup for an invalid Compose project" >&2
    exit 2
  fi

  if [ "$stack_started" -eq 1 ]; then
    if [ "$status" -ne 0 ]; then
      echo "live acceptance failed; bounded non-secret service diagnostics follow" >&2
      "${COMPOSE[@]}" ps >&2
      "${COMPOSE[@]}" logs --no-color --tail 200 api keycloak proxy >&2
    fi
    if ! "${COMPOSE[@]}" down -v --remove-orphans; then
      echo "live acceptance could not clean its validated Compose project" >&2
      stack_clean=0
      status=1
    fi
  fi

  if [ "$env_created" -eq 1 ]; then
    if [ "$stack_clean" -ne 1 ]; then
      echo "live acceptance preserved its exact .env because stack cleanup failed" >&2
      status=1
    elif [ "$ENV_FILE" = "$ROOT/.env" ] && [ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ]; then
      if ! unlink -- "$ENV_FILE"; then
        echo "live acceptance could not unlink its exact .env" >&2
        status=1
      fi
    elif [ -e "$ENV_FILE" ] || [ -L "$ENV_FILE" ]; then
      echo "live acceptance refuses to unlink an unexpected .env target" >&2
      status=1
    fi
  fi

  exit "$status"
}
trap cleanup EXIT INT TERM

choose_loopback_port() {
  python3 - <<'PY'
import socket

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
}

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

APP_PORT="$(choose_loopback_port)"
S3_PORT="$(choose_loopback_port)"
while [ "$S3_PORT" = "$APP_PORT" ]; do
  S3_PORT="$(choose_loopback_port)"
done
APP_ORIGIN="http://127.0.0.1:${APP_PORT}"
S3_ORIGIN="http://127.0.0.1:${S3_PORT}"

# The worktree was checked above and this invocation exclusively owns the exact file it asks the
# repository installer to create. Mark ownership before the command so a partial installer failure
# still removes only that exact path.
env_created=1
EASYSYNQ_ENV_ONLY=1 "$ROOT/scripts/install.sh" s
[ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] || {
  echo "live acceptance did not create a regular .env" >&2
  exit 2
}

set_env_value HTTP_PORT "$APP_PORT"
set_env_value S3_PORT "$S3_PORT"
set_env_value SITE_ADDRESS ":80"
set_env_value MINIO_SITE_ADDRESS "$S3_ORIGIN"
set_env_value S3_PUBLIC_ENDPOINT "$S3_ORIGIN"
set_env_value PUBLIC_BASE_URL "$APP_ORIGIN"
set_env_value APP_BASE_URL "$APP_ORIGIN"
set_env_value KEYCLOAK_HOSTNAME "$APP_ORIGIN"
set_env_value OIDC_ISSUER "${APP_ORIGIN}/realms/easysynq"
set_env_value OIDC_JWKS_URL \
  "http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs"
set_env_value OIDC_DISCOVERY_URL \
  "http://keycloak:8080/realms/easysynq/.well-known/openid-configuration"

stack_started=1
"${COMPOSE[@]}" up -d --build

ready=0
for _ in $(seq 1 90); do
  if curl -fsS "$APP_ORIGIN/readyz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "$ready" -eq 1 ] || {
  echo "live acceptance timed out waiting for /readyz" >&2
  exit 1
}

"${COMPOSE[@]}" exec -T api uv run python -m easysynq_api.cli.keycloak_redirect \
  --redirect-uri "${APP_ORIGIN}/*" >/dev/null

BOOTSTRAP_SECRET="$(
  "${COMPOSE[@]}" exec -T api uv run python -c \
    'from easysynq_api.cli.setup import mint_bootstrap; print(mint_bootstrap())'
)"
[ -n "$BOOTSTRAP_SECRET" ] || {
  echo "live acceptance could not mint the setup proof" >&2
  exit 1
}

ADMIN_USERNAME="firstadmin$(openssl rand -hex 6)"
NEW_PASSWORD="N7!$(openssl rand -hex 16)"

EASYSYNQ_LIVE_BASE_URL="${APP_ORIGIN}" \
EASYSYNQ_LIVE_SETUP_SECRET="${BOOTSTRAP_SECRET}" \
EASYSYNQ_LIVE_USERNAME="${ADMIN_USERNAME}" \
EASYSYNQ_LIVE_NEW_PASSWORD="${NEW_PASSWORD}" \
npm --prefix apps/web run test:first-admin-live

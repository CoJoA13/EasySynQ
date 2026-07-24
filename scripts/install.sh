#!/usr/bin/env bash
# EasySynQ first-run installer (host-side, no dev tools required).
# Generates secrets, writes a 0600 .env, brings up the Compose stack for the chosen
# sizing profile, and blocks until /readyz is green. The web first-run wizard (S8)
# completes configuration (org, storage+WORM verify, backup+restore gate, finalize).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROFILE="${1:-s}"
ENV_FILE="$ROOT/.env"
HTTP_PORT="${HTTP_PORT:-80}"

case "$PROFILE" in
  s|m) ;;
  *) echo "usage: install.sh [s|m]" >&2; exit 2 ;;
esac

gen_secret() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40; }

set_kv() { # set_kv KEY VALUE  (update in place or append)
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # use a non-/ delimiter; values are alnum so this is safe
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

if [ ! -f "$ENV_FILE" ]; then
  echo "install: generating $ENV_FILE from template..."
  cp "$ROOT/.env.example" "$ENV_FILE"

  PG_PW="$(gen_secret)"
  APP_PW="$(gen_secret)"
  LINKER_PW="$(gen_secret)"
  S3_KEY="$(gen_secret)"
  S3_SECRET="$(gen_secret)"
  KEK="$(gen_secret)"
  BK="$(gen_secret)"
  KC_ADMIN_PW="$(gen_secret)"
  AUDIT_SINK_SECRET="$(gen_secret)"
  AUDIT_SINK_READ_SECRET="$(gen_secret)"

  set_kv POSTGRES_USER easysynq
  set_kv POSTGRES_PASSWORD "$PG_PW"
  set_kv POSTGRES_DB easysynq
  # Role separation (S6): the app runs as the NON-owner easysynq_app role (append-only audit is
  # structurally enforced by REVOKEs); only alembic/backup use the owner DSN (DATABASE_URL_SYNC).
  set_kv DATABASE_URL "postgresql+psycopg://easysynq_app:${APP_PW}@postgres:5432/easysynq"
  set_kv DATABASE_URL_SYNC "postgresql+psycopg://easysynq:${PG_PW}@postgres:5432/easysynq"
  set_kv AUDIT_LINKER_DATABASE_URL "postgresql+psycopg://easysynq_linker:${LINKER_PW}@postgres:5432/easysynq"
  set_kv APP_DB_PASSWORD "$APP_PW"
  set_kv LINKER_DB_PASSWORD "$LINKER_PW"
  set_kv S3_ACCESS_KEY "$S3_KEY"
  set_kv S3_SECRET_KEY "$S3_SECRET"
  set_kv APP_MASTER_KEK "$KEK"
  set_kv BACKUP_ENCRYPTION_KEY "$BK"          # S11: seals the durable backup archive (AES-256-GCM)
  set_kv KEYCLOAK_ADMIN_USER admin
  set_kv KEYCLOAK_ADMIN_PASSWORD "$KC_ADMIN_PW"  # S11: also the worker's realm-export admin creds
  # Off-host audit-checkpoint sink creds (doc 12 §4.4): GENERATE the secrets so a fresh install never
  # provisions the minio-init sink users with the repo-known .env.example placeholders (the S profile
  # publishes MinIO on :9000; the read user can list/download checkpoint objects). Usernames are
  # non-secret; an operator pointing a sink at an EXTERNAL host replaces these with that host's creds.
  set_kv AUDIT_SINK_ACCESS_KEY audit-sink
  set_kv AUDIT_SINK_SECRET_KEY "$AUDIT_SINK_SECRET"
  set_kv AUDIT_SINK_READ_ACCESS_KEY audit-sink-read
  set_kv AUDIT_SINK_READ_SECRET_KEY "$AUDIT_SINK_READ_SECRET"
  set_kv EASYSYNQ_PROFILE "$PROFILE"

  chmod 600 "$ENV_FILE"
  echo "install: secrets generated (.env is 0600 — keep it safe; it is gitignored)."
  echo "install: NOTE set OIDC_ISSUER/OIDC_JWKS_URL in .env to your Keycloak realm before"
  echo "install:      first-run setup. For a reverse-proxied/localhost issuer the API container"
  echo "install:      cannot reach, also set OIDC_DISCOVERY_URL (see .env.example)."
else
  echo "install: $ENV_FILE already exists — leaving it untouched."
fi

# Env-only mode (the appliance provisioner): generate/keep the .env, skip the stack startup —
# the caller composes its own overlay set (internal TLS, appliance MinIO site) before `up`.
if [ "${EASYSYNQ_ENV_ONLY:-0}" = "1" ]; then
  echo "install: EASYSYNQ_ENV_ONLY=1 — env ready; skipping stack startup."
  exit 0
fi

echo "install: starting the stack (profile: $PROFILE)..."
docker compose \
  --env-file "$ENV_FILE" \
  -f infra/compose/compose.yml \
  -f "infra/compose/compose.${PROFILE}.yml" \
  up -d --build

echo "install: waiting for /readyz ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${HTTP_PORT}/readyz" >/dev/null 2>&1; then
    echo "install: EasySynQ is up. Open http://localhost:${HTTP_PORT}/ and complete first-run setup."
    exit 0
  fi
  sleep 3
done
echo "install: /readyz did not become green in time — check 'docker compose logs'." >&2
exit 1

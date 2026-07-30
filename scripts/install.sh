#!/usr/bin/env bash
# EasySynQ first-run installer (host-side, no dev tools required).
# Generates secrets, writes a 0600 .env, configures the browser-facing HTTPS app + object-store
# origins, brings up the Compose stack for the chosen sizing profile, and blocks until /readyz is
# green. The web first-run wizard (S8) completes configuration.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROFILE="s"
HOST_NAME=""
TLS_MODE="acme"
ENV_FILE="$ROOT/.env"
ENV_ONLY="${EASYSYNQ_ENV_ONLY:-0}"

usage() {
  cat >&2 <<'EOF'
usage: install.sh [s|m] --host <fqdn> [--tls acme|internal]

  --host      Browser-facing DNS name (without scheme or port).
  --tls       acme (default; publicly resolvable DNS) or internal (private/LAN CA).

EASYSYNQ_ENV_ONLY=1 omits --host and only generates the .env for appliance provisioning.
EOF
}

if [ $# -gt 0 ] && { [ "$1" = "s" ] || [ "$1" = "m" ]; }; then
  PROFILE="$1"
  shift
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --host)
      [ $# -ge 2 ] || { usage; exit 2; }
      HOST_NAME="$2"
      shift 2
      ;;
    --tls)
      [ $# -ge 2 ] || { usage; exit 2; }
      TLS_MODE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

case "$PROFILE" in
  s|m) ;;
  *) usage; exit 2 ;;
esac
case "$TLS_MODE" in
  acme|internal) ;;
  *) usage; exit 2 ;;
esac

if [ "$ENV_ONLY" != "1" ]; then
  [ -n "$HOST_NAME" ] || { usage; exit 2; }
  if ! bash "$ROOT/scripts/validate-dns-name.sh" "$HOST_NAME"; then
    echo "install: --host must be a valid DNS name without a scheme, path, or port" >&2
    exit 2
  fi
fi

gen_secret() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40; }

set_kv() { # set_kv KEY VALUE  (update in place or append)
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # use a non-/ delimiter (generated values and validated hostnames contain no '&')
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
  KC_DB_PW="$(gen_secret)"
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
  set_kv KEYCLOAK_DB_PASSWORD "$KC_DB_PW"
  # Off-host audit-checkpoint sink creds (doc 12 §4.4): GENERATE the secrets so a fresh install never
  # provisions the minio-init sink users with the repo-known .env.example placeholders (the dev
  # overlay publishes MinIO on loopback :9000; the read user can list/download checkpoint objects).
  # Usernames are non-secret; an operator pointing a sink at an EXTERNAL host replaces these with
  # that host's credentials.
  set_kv AUDIT_SINK_ACCESS_KEY audit-sink
  set_kv AUDIT_SINK_SECRET_KEY "$AUDIT_SINK_SECRET"
  set_kv AUDIT_SINK_READ_ACCESS_KEY audit-sink-read
  set_kv AUDIT_SINK_READ_SECRET_KEY "$AUDIT_SINK_READ_SECRET"
  set_kv EASYSYNQ_PROFILE "$PROFILE"

  chmod 600 "$ENV_FILE"
  echo "install: secrets generated (.env is 0600 — keep it safe; it is gitignored)."
else
  echo "install: $ENV_FILE already exists — preserving its secrets and operator settings."
fi

# Env-only mode (the appliance provisioner): generate/keep the .env, skip the stack startup —
# the caller applies its own hostname and internal-TLS settings before `up`.
if [ "$ENV_ONLY" = "1" ]; then
  # Backfill the durable Keycloak database credential when an older .env is reused.
  bash "$ROOT/scripts/ensure-keycloak-db-password.sh" --env-file "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "install: EASYSYNQ_ENV_ONLY=1 — env ready; skipping stack startup."
  exit 0
fi

# Gate before the legacy migration: an older Compose cannot parse production's fail-closed !reset
# tag, and must not be allowed to leave Keycloak stopped after staging an otherwise valid export.
bash "$ROOT/scripts/require-compose-version.sh"

# Backfill the new least-privilege database credential on an older online install. The legacy H2
# exporter below uses the old store; this credential is only for the PostgreSQL-backed service.
bash "$ROOT/scripts/ensure-keycloak-db-password.sh" --env-file "$ENV_FILE"

APP_ORIGIN="https://${HOST_NAME}"
MINIO_ORIGIN="https://${HOST_NAME}:9443"
TLS_DIRECTIVE=""
[ "$TLS_MODE" = "internal" ] && TLS_DIRECTIVE="tls internal"

set_kv SITE_ADDRESS "$APP_ORIGIN"
set_kv MINIO_SITE_ADDRESS "$MINIO_ORIGIN"
set_kv S3_PUBLIC_ENDPOINT "$MINIO_ORIGIN"
set_kv PUBLIC_BASE_URL "$APP_ORIGIN"
set_kv APP_BASE_URL "$APP_ORIGIN"
set_kv KEYCLOAK_HOSTNAME "$APP_ORIGIN"
set_kv OIDC_ISSUER "${APP_ORIGIN}/realms/easysynq"
set_kv OIDC_JWKS_URL "http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs"
set_kv OIDC_DISCOVERY_URL "http://keycloak:8080/realms/easysynq/.well-known/openid-configuration"
set_kv CADDY_TLS_DIRECTIVE "$TLS_DIRECTIVE"
chmod 600 "$ENV_FILE"

# Compose interpolation proves only that these values are nonempty. Compare the two complete
# browser-origin tuples before migration/start so one stale setting cannot break login or presigns.
bash "$ROOT/scripts/validate-browser-origins.sh" --env-file "$ENV_FILE"

# If this checkout replaces the old start-dev/H2 service, export it before Compose recreates the
# container. The script is a no-op for fresh or already-PostgreSQL installs.
bash "$ROOT/scripts/migrate-keycloak-h2.sh" --env-file "$ENV_FILE"

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f infra/compose/compose.yml
  -f "infra/compose/compose.${PROFILE}.yml"
  -f infra/compose/compose.production.yml
)

echo "install: starting the stack (profile: $PROFILE)..."
"${COMPOSE[@]}" up -d --build

# KC_HOSTNAME controls the issuer URL but does not authorize SPA callbacks. Append the selected URI
# through the Admin API; updating the complete representation preserves operator-added callbacks.
echo "install: authorizing ${APP_ORIGIN}/ as the SPA login callback..."
KEYCLOAK_REDIRECT_CONFIGURED=0
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T api uv run python -m easysynq_api.cli.keycloak_redirect \
      --redirect-uri "${APP_ORIGIN}/*" </dev/null >/dev/null 2>&1; then
    KEYCLOAK_REDIRECT_CONFIGURED=1
    break
  fi
  sleep 2
done
[ "$KEYCLOAK_REDIRECT_CONFIGURED" -eq 1 ] || {
  echo "install: could not authorize ${APP_ORIGIN}/ in the easysynq-web client" >&2
  exit 1
}

echo "install: waiting for /readyz ..."
for _ in $(seq 1 60); do
  if curl -fsSk --resolve "${HOST_NAME}:443:127.0.0.1" \
      "${APP_ORIGIN}/readyz" >/dev/null 2>&1; then
    echo "install: EasySynQ is up. Open ${APP_ORIGIN}/ and complete first-run setup."
    if [ "$TLS_MODE" = "internal" ]; then
      echo "install: distribute Caddy's internal root CA before workstation use (see install-online.md)."
    fi
    exit 0
  fi
  sleep 3
done
echo "install: /readyz did not become green in time — check 'docker compose logs'." >&2
exit 1

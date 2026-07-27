#!/usr/bin/env bash
# Backfill the dedicated Keycloak PostgreSQL credential on pre-Batch-13 installs. Never let the
# web-facing identity service inherit the PostgreSQL owner password as a compatibility fallback.
set -euo pipefail

ENV_FILE=".env"
if [ "${1:-}" = "--env-file" ] && [ "$#" -eq 2 ]; then
  ENV_FILE="$2"
elif [ "$#" -ne 0 ]; then
  echo "usage: ensure-keycloak-db-password.sh [--env-file <path>]" >&2
  exit 2
fi

[ -r "$ENV_FILE" ] || {
  echo "keycloak-db-password: cannot read $ENV_FILE" >&2
  exit 1
}

env_value() {
  grep -m1 -E "^$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//' || true
}

CURRENT="$(env_value KEYCLOAK_DB_PASSWORD)"
OWNER="$(env_value POSTGRES_PASSWORD)"
OWNER="${OWNER:-easysynq}"
case "$CURRENT" in
  ""|*CHANGE_ME*) ;;
  *)
    [ "$CURRENT" != "$OWNER" ] && exit 0
    ;;
esac

[ -w "$ENV_FILE" ] || {
  echo "keycloak-db-password: $ENV_FILE needs a distinct KEYCLOAK_DB_PASSWORD; rerun this first Batch 13 command with sudo so it can be persisted" >&2
  exit 1
}
command -v openssl >/dev/null 2>&1 || {
  echo "keycloak-db-password: openssl is required to generate the credential" >&2
  exit 1
}

while :; do
  GENERATED="$(openssl rand -hex 24)"
  [ "$GENERATED" != "$OWNER" ] && break
done

if grep -qE '^KEYCLOAK_DB_PASSWORD=' "$ENV_FILE"; then
  sed -i "s|^KEYCLOAK_DB_PASSWORD=.*|KEYCLOAK_DB_PASSWORD=${GENERATED}|" "$ENV_FILE"
else
  printf 'KEYCLOAK_DB_PASSWORD=%s\n' "$GENERATED" >> "$ENV_FILE"
fi

PERSISTED="$(env_value KEYCLOAK_DB_PASSWORD)"
[ -n "$PERSISTED" ] && [ "$PERSISTED" != "$OWNER" ] && [[ "$PERSISTED" != *CHANGE_ME* ]] || {
  echo "keycloak-db-password: failed to persist a distinct credential in $ENV_FILE" >&2
  exit 1
}
echo "keycloak-db-password: persisted a dedicated Keycloak database credential"

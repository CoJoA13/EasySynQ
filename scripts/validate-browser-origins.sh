#!/usr/bin/env bash
# Fail closed unless every production browser consumer sees the same app or object-store origin.
# Values exported by an upgrade wrapper take precedence over an older read-only .env.
set -euo pipefail

ENV_FILE=".env"
if [ "${1:-}" = "--env-file" ] && [ "$#" -eq 2 ]; then
  ENV_FILE="$2"
elif [ "$#" -ne 0 ]; then
  echo "usage: validate-browser-origins.sh [--env-file <path>]" >&2
  exit 2
fi

[ -r "$ENV_FILE" ] || {
  echo "browser-origins: cannot read $ENV_FILE" >&2
  exit 1
}

env_value() {
  local value
  value="$(
    grep -m1 -E "^$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- \
      | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//' || true
  )"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}

value_for() {
  local key="$1"
  if [[ -v "$key" ]]; then
    printf '%s' "${!key}"
  else
    env_value "$key"
  fi
}

fail() {
  echo "browser-origins: $*" >&2
  exit 1
}

SITE_VALUE="$(value_for SITE_ADDRESS)"
MINIO_VALUE="$(value_for MINIO_SITE_ADDRESS)"
[ -n "$SITE_VALUE" ] || fail "SITE_ADDRESS is required"
[ -n "$MINIO_VALUE" ] || fail "MINIO_SITE_ADDRESS is required"
case "$SITE_VALUE" in
  https://*) ;;
  *) fail "SITE_ADDRESS must be an HTTPS origin" ;;
esac
case "$MINIO_VALUE" in
  https://*) ;;
  *) fail "MINIO_SITE_ADDRESS must be an HTTPS origin" ;;
esac

for key in PUBLIC_BASE_URL APP_BASE_URL KEYCLOAK_HOSTNAME; do
  value="$(value_for "$key")"
  [ "$value" = "$SITE_VALUE" ] \
    || fail "$key must exactly equal SITE_ADDRESS ($SITE_VALUE)"
done

S3_VALUE="$(value_for S3_PUBLIC_ENDPOINT)"
[ "$S3_VALUE" = "$MINIO_VALUE" ] \
  || fail "S3_PUBLIC_ENDPOINT must exactly equal MINIO_SITE_ADDRESS ($MINIO_VALUE)"
[ "$MINIO_VALUE" != "$SITE_VALUE" ] \
  || fail "MINIO_SITE_ADDRESS must be a separate origin from SITE_ADDRESS"

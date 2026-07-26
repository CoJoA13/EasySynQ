#!/bin/sh
# Idempotently prepare Keycloak's dedicated PostgreSQL role/schema and its first-boot import volume.
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD is required}"

export PGPASSWORD="$POSTGRES_PASSWORD"

psql \
  --host "$POSTGRES_HOST" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set keycloak_database="$POSTGRES_DB" \
  --set keycloak_password="$KEYCLOAK_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE easysynq_keycloak LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'easysynq_keycloak')
\gexec

ALTER ROLE easysynq_keycloak WITH LOGIN PASSWORD :'keycloak_password';

SELECT 'CREATE SCHEMA keycloak AUTHORIZATION easysynq_keycloak'
WHERE NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'keycloak')
\gexec

ALTER SCHEMA keycloak OWNER TO easysynq_keycloak;
GRANT CONNECT ON DATABASE :"keycloak_database" TO easysynq_keycloak;
GRANT USAGE, CREATE ON SCHEMA keycloak TO easysynq_keycloak;
SQL

# A full legacy-H2 export, when present, wins over the stock seed. Once PostgreSQL contains the
# realm, Keycloak's --import-realm skips every later import and preserves live edits.
if [ ! -s /import/easysynq-realm.json ]; then
  cp /seed/easysynq-realm.json /import/easysynq-realm.json
fi
chmod 0444 /import/*.json

#!/usr/bin/env bash
# One-time transition from the legacy container-local Keycloak H2 store to the PostgreSQL-backed
# service. The full offline CLI export preserves realm/client edits, user IDs and credential hashes.
#
# Safe to run before every Compose `up`: it exits without mutation when no legacy container exists,
# when Keycloak already uses PostgreSQL, or when a verified legacy export is already staged.
set -Eeuo pipefail

PROJECT="${COMPOSE_PROJECT_NAME:-easysynq}"
CONTAINER=""
IMPORT_VOLUME="easysynq-keycloak-import"
SNAPSHOT_IMAGE=""
WAS_RUNNING=0
STOPPED_LEGACY=0

usage() {
  echo "usage: migrate-keycloak-h2.sh [--project <compose-project>] [--container <name-or-id>] [--import-volume <name>]" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project)
      [ $# -ge 2 ] || { usage; exit 2; }
      PROJECT="$2"
      shift 2
      ;;
    --container)
      [ $# -ge 2 ] || { usage; exit 2; }
      CONTAINER="$2"
      shift 2
      ;;
    --import-volume)
      [ $# -ge 2 ] || { usage; exit 2; }
      IMPORT_VOLUME="$2"
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

cleanup() {
  local status=$?
  trap - EXIT
  if [ -n "$SNAPSHOT_IMAGE" ]; then
    docker image rm "$SNAPSHOT_IMAGE" >/dev/null 2>&1 || true
  fi
  if [ "$status" -ne 0 ] && [ "$STOPPED_LEGACY" -eq 1 ] && [ "$WAS_RUNNING" -eq 1 ]; then
    echo "keycloak-migrate: export failed; restarting the untouched legacy container" >&2
    docker start "$CONTAINER" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

if [ -z "$CONTAINER" ]; then
  mapfile -t candidates < <(
    docker ps -a \
      --filter "label=com.docker.compose.project=${PROJECT}" \
      --filter "label=com.docker.compose.service=keycloak" \
      --format '{{.ID}}'
  )
  case "${#candidates[@]}" in
    0)
      echo "keycloak-migrate: no legacy container found; nothing to migrate"
      exit 0
      ;;
    1)
      CONTAINER="${candidates[0]}"
      ;;
    *)
      echo "keycloak-migrate: multiple Keycloak containers found for project ${PROJECT}; use --container" >&2
      exit 1
      ;;
  esac
fi

docker inspect "$CONTAINER" >/dev/null

if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" \
    | grep -q '^KC_DB=postgres$'; then
  echo "keycloak-migrate: Keycloak already uses PostgreSQL; nothing to migrate"
  exit 0
fi

LEGACY_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
docker volume inspect "$IMPORT_VOLUME" >/dev/null 2>&1 || docker volume create \
  --label "com.docker.compose.project=${PROJECT}" \
  --label "com.docker.compose.volume=keycloakimport" \
  "$IMPORT_VOLUME" >/dev/null

# A marker is written only after the CLI export and its user-bearing realm file validate. This
# distinguishes a real legacy migration from the stock first-boot realm seed in the same volume.
if docker run --rm \
    --network none \
    --volume "${IMPORT_VOLUME}:/migration-export:ro" \
    --entrypoint /bin/sh \
    "$LEGACY_IMAGE_ID" \
    -c 'test -f /migration-export/.legacy-h2-export-complete &&
        test -s /migration-export/easysynq-realm.json &&
        grep -q "\"users\"[[:space:]]*:" /migration-export/easysynq-realm.json'; then
  echo "keycloak-migrate: verified legacy export already staged; leaving the old container untouched"
  exit 0
fi

if [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" = "true" ]; then
  WAS_RUNNING=1
  echo "keycloak-migrate: stopping legacy Keycloak for a consistent offline export"
  docker stop --time 60 "$CONTAINER" >/dev/null
  STOPPED_LEGACY=1
fi

run_source_args=()
if docker inspect --format '{{range .Mounts}}{{println .Destination}}{{end}}' "$CONTAINER" \
    | grep -qx '/opt/keycloak/data'; then
  # Some image variants declare the full data directory as a volume. Reuse that stopped mount.
  run_source_args=(--volumes-from "$CONTAINER" "$LEGACY_IMAGE_ID")
else
  # The shipped legacy service stored H2 in the container writable layer. Snapshot the stopped
  # container so `kc.sh export` can open that exact database without touching the original.
  SNAPSHOT_IMAGE="easysynq/keycloak-legacy-migration:$(date -u +%Y%m%d%H%M%S)-$$"
  docker commit "$CONTAINER" "$SNAPSHOT_IMAGE" >/dev/null
  run_source_args=("$SNAPSHOT_IMAGE")
fi

echo "keycloak-migrate: exporting the easysynq realm, users and credential hashes"
docker run --rm \
  --name "easysynq-keycloak-migrate-$$" \
  --network none \
  --user 0 \
  --env KC_DB=dev-file \
  --volume "${IMPORT_VOLUME}:/migration-export" \
  --entrypoint /opt/keycloak/bin/kc.sh \
  "${run_source_args[@]}" \
  export --dir /migration-export --realm easysynq --users realm_file

docker run --rm \
  --network none \
  --user 0 \
  --volume "${IMPORT_VOLUME}:/migration-export" \
  --entrypoint /bin/sh \
  "$LEGACY_IMAGE_ID" \
  -c 'test -s /migration-export/easysynq-realm.json
      grep -q "\"realm\"[[:space:]]*:[[:space:]]*\"easysynq\"" /migration-export/easysynq-realm.json
      grep -q "\"users\"[[:space:]]*:" /migration-export/easysynq-realm.json
      touch /migration-export/.legacy-h2-export-complete'

echo "keycloak-migrate: export staged in ${IMPORT_VOLUME}; keep legacy Keycloak stopped and run Compose up"

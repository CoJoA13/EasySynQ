#!/usr/bin/env bash
# Every deployed Compose `image:` ref plus the optimized Keycloak Dockerfile base must be pinned in
# infra/images.lock, or the air-gap bundle ships a stale ref while Compose asks for the new one
# (Codex #153). Ordinary Compose refs are digest-aware: a release-pinned lock line
# (`name:tag@sha256:…`) satisfies the tag-form ref. Keycloak is also a build input, so every FROM
# must exactly match the single digest-pinned `keycloak` lock entry.
#
# Overridable inputs (for the regression harness): COMPOSE_DIR, IMAGES_LOCK, KEYCLOAK_DOCKERFILE.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${COMPOSE_DIR:-$root/infra/compose}"
lock_file="${IMAGES_LOCK:-$root/infra/images.lock}"
keycloak_dockerfile="${KEYCLOAK_DOCKERFILE:-$compose_dir/keycloak/Dockerfile}"

strip_digest() { sed 's/@sha256:.*$//'; }

# The images this repository BUILDS are named in compose.yml so the air-gap bundle can carry them
# (C13), but they are never pulled and have no place in the pull-pinning lock. scripts/app-images.sh
# is the single source of that set; drop its repositories before comparing against images.lock.
built_repos=$(bash "$root/scripts/app-images.sh" | sed 's/:[^:/]*$//' | sort -u)
drop_built() {
  local ref repo
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    # Strip any ${...} interpolation before splitting off the tag: the locally built images take
    # their tag from a Compose variable whose default value itself contains a colon.
    repo="$(printf '%s\n' "$ref" | sed 's/\${[^}]*}/_/g')"
    repo="${repo%:*}"
    if printf '%s\n' "$built_repos" | grep -qxF "$repo"; then
      continue
    fi
    printf '%s\n' "$ref"
  done
}

compose=$(grep -hE '^[[:space:]]+image:[[:space:]]' "$compose_dir"/compose.yml \
  "$compose_dir"/compose.*.yml 2>/dev/null | awk '{print $2}' | strip_digest | drop_built | sort -u)
required=$(printf '%s\n' "$compose" | sort -u)
# Lock column 2 with any digest suffix stripped: a digest pin still pins the tag underneath.
lock=$(grep -vE '^[[:space:]]*#' "$lock_file" | awk 'NF>=2 {print $2}' | strip_digest | sort -u)

missing=$(comm -23 <(printf '%s\n' "$required") <(printf '%s\n' "$lock"))
if [ -n "$missing" ]; then
  echo "::error::Deployed/base image(s) not pinned in infra/images.lock — the air-gap bundle would drift:"
  while IFS= read -r image; do
    printf '  %s\n' "$image"
  done <<<"$missing"
  echo "Add them to infra/images.lock (then \`just images-update\` for digests)."
  exit 1
fi

keycloak_lock_count=$(awk '$1 == "keycloak" {count++} END {print count + 0}' "$lock_file")
if [ "$keycloak_lock_count" -ne 1 ]; then
  echo "::error::Expected exactly one keycloak entry in $lock_file; found $keycloak_lock_count."
  echo "Keep one digest-pinned keycloak lock entry and synchronize every FROM in $keycloak_dockerfile with it."
  exit 1
fi

keycloak_lock_ref=$(awk '$1 == "keycloak" {print $2}' "$lock_file")
if [[ ! "$keycloak_lock_ref" =~ ^quay\.io/keycloak/keycloak:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}@sha256:[0-9a-f]{64}$ ]]; then
  echo "::error::The keycloak entry in $lock_file is not a valid digest-pinned Keycloak image:"
  printf '  %s\n' "$keycloak_lock_ref"
  echo "Record quay.io/keycloak/keycloak:<tag>@sha256:<64 lowercase hex characters>, then synchronize every FROM in $keycloak_dockerfile with it."
  exit 1
fi

keycloak_bases=$(awk 'toupper($1) == "FROM" && NF >= 2 {print $2}' "$keycloak_dockerfile")
if [ -z "$keycloak_bases" ]; then
  echo "::error::No Keycloak FROM references found in $keycloak_dockerfile."
  echo "Synchronize every FROM in $keycloak_dockerfile with the exact keycloak entry in $lock_file."
  exit 1
fi

keycloak_mismatch=0
while IFS= read -r keycloak_base; do
  [ -n "$keycloak_base" ] || continue
  if [ "$keycloak_base" != "$keycloak_lock_ref" ]; then
    printf '::error::Keycloak FROM does not match the digest-pinned keycloak entry in %s:\n' "$lock_file"
    printf '  Dockerfile: %s\n' "$keycloak_base"
    printf '  images.lock: %s\n' "$keycloak_lock_ref"
    keycloak_mismatch=1
  fi
done <<<"$keycloak_bases"

if [ "$keycloak_mismatch" -ne 0 ]; then
  echo "Synchronize every FROM in $keycloak_dockerfile with the exact keycloak entry in $lock_file."
  exit 1
fi

echo "OK — every Compose image ref is pinned in images.lock (digest-aware), and every Keycloak FROM matches its digest lock."

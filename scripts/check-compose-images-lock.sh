#!/usr/bin/env bash
# Every deployed Compose `image:` ref plus the optimized Keycloak Dockerfile base must be pinned in
# infra/images.lock, or the air-gap bundle ships a stale ref while Compose asks for the new one
# (Codex #153). DIGEST-AWARE: a release-pinned lock line (`name:tag@sha256:…`) satisfies the
# tag-form ref — previously the exact-string comparison made the digest-pin release ceremony and
# this gate mutually exclusive (2026-08-27 audit C15): the ceremony rewrites the lock to digest
# form, every ref stopped string-matching, and the release commit went red.
#
# Overridable inputs (for the regression harness): COMPOSE_DIR, IMAGES_LOCK, KEYCLOAK_DOCKERFILE.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${COMPOSE_DIR:-$root/infra/compose}"
lock_file="${IMAGES_LOCK:-$root/infra/images.lock}"
keycloak_dockerfile="${KEYCLOAK_DOCKERFILE:-$compose_dir/keycloak/Dockerfile}"

strip_digest() { sed 's/@sha256:.*$//'; }

compose=$(grep -hE '^[[:space:]]+image:[[:space:]]' "$compose_dir"/compose.yml \
  "$compose_dir"/compose.*.yml 2>/dev/null | awk '{print $2}' | strip_digest | sort -u)
keycloak_base=$(grep -E '^FROM[[:space:]]' "$keycloak_dockerfile" | awk '{print $2}' \
  | strip_digest | sort -u)
required=$(printf '%s\n%s\n' "$compose" "$keycloak_base" | sort -u)
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
echo "OK — every Compose image ref is pinned in images.lock (digest-aware)."

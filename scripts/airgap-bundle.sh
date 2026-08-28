#!/usr/bin/env bash
# Build an air-gapped install bundle: a `docker save` tarball carrying BOTH halves of the stack —
# the pinned third-party images (infra/images.lock) and the images this repository BUILDS
# (scripts/app-images.sh: api, web, keycloak) — plus a .sha256 sidecar for transfer-integrity
# verification and a .manifest.txt naming exactly what is inside.
#
# C13: the bundle previously saved only the locked third-party images. The application services are
# `build:` services, so on the air-gapped target Compose had to BUILD them — which needs PyPI, npm,
# and the PGDG apt repo. `docker load` therefore did NOT yield an installable stack. Building the
# application images HERE, on the connected host, is what makes the offline install real; the
# Python wheels (`uv sync --no-dev`) and the built SPA (`npm ci && build`) are baked into those
# layers, so no separate wheel/npm store is needed.
#
# A digest-pinned lock line is pulled BY DIGEST (immutable bytes) and then re-tagged to the plain
# `name:tag` that compose.yml asks for. Without that step `docker load` lands an UNTAGGED image and
# the offline `up` falls back to a network pull — the exact failure this bundle exists to prevent.
#
# Pin images.lock to @sha256 digests before a release (`just images-update`); the airgap compose
# overlay disables ACME (internal/admin-supplied TLS) and compose.offline.yml forbids pulling.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist/easysynq-airgap.tar}"
mkdir -p "$(dirname "$OUT")"

TAG="$(bash "$ROOT/scripts/app-images.sh" --tag)"
mapfile -t APP_IMAGES < <(bash "$ROOT/scripts/app-images.sh")

# VERSION is a static release string, so the tag alone cannot tell two checkouts apart. Stamp the
# build's revision onto every image so `install.sh --offline` can refuse a bundle that does not
# match the checkout it is being installed from. "unknown" when built outside a git tree — the
# installer then reports rather than compares.
REVISION="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"

# --- build the application images (the offline-install half that used to be missing) -------------
echo "airgap: building application images at tag $TAG (revision $REVISION)"
docker build --label "org.opencontainers.image.revision=$REVISION" \
  -f "$ROOT/apps/api/Dockerfile" -t "easysynq/api:$TAG" "$ROOT"
docker build --label "org.opencontainers.image.revision=$REVISION" \
  -f "$ROOT/apps/web/Dockerfile" -t "easysynq/web:$TAG" "$ROOT/apps/web"
docker build --label "org.opencontainers.image.revision=$REVISION" \
  -f "$ROOT/infra/compose/keycloak/Dockerfile" -t "easysynq/keycloak:$TAG" \
  "$ROOT/infra/compose/keycloak"

# --- fetch the pinned third-party images, normalised to the refs Compose asks for ----------------
# Collect the image refs from images.lock (skip comments/blank lines; field 2 = image:tag[@sha256])
IMAGES_LOCK="${EASYSYNQ_IMAGES_LOCK:-$ROOT/infra/images.lock}"
mapfile -t LOCKED < <(grep -vE '^\s*#|^\s*$' "$IMAGES_LOCK" | awk '{print $2}')

SAVE=("${APP_IMAGES[@]}")
for ref in "${LOCKED[@]}"; do
  echo "airgap: pulling $ref"
  docker pull --quiet "$ref" >/dev/null
  tagged="${ref%@sha256:*}"
  if [ "$tagged" != "$ref" ]; then
    # Digest-pinned: docker leaves it untagged, so restore the tag compose.yml resolves.
    docker tag "$ref" "$tagged"
  fi
  SAVE+=("$tagged")
done

echo "airgap: saving ${#SAVE[@]} images -> $OUT"
docker save "${SAVE[@]}" -o "$OUT"

{
  printf '# EasySynQ air-gap bundle — version %s\n' "$TAG"
  printf '# built from revision %s\n' "$REVISION"
  printf '# Load with: docker load -i %s\n' "$(basename "$OUT")"
  printf '# Then verify what landed: bash scripts/verify-bundle.sh %s.manifest.txt\n' \
    "$(basename "$OUT")"
  # Each built image is recorded with its IMAGE ID — the sha256 of its config, which is stable
  # across docker save/load (verified). A never-pushed image has no registry digest
  # (`RepoDigests` is empty), so this is its only content identity, and it is what lets the target
  # prove the images it loaded are the ones this bundle carried rather than an older leftover.
  printf '# built (this repository) — "<ref> <image id>":\n'
  for image in "${APP_IMAGES[@]}"; do
    printf '%s %s\n' "$image" "$(docker image inspect --format '{{.Id}}' "$image")"
  done
  printf '# pulled (infra/images.lock, saved under the tag compose.yml resolves):\n'
  printf '%s\n' "${LOCKED[@]}"
} > "$OUT.manifest.txt"

# Write the sidecar with a RELATIVE filename. `sha256sum "$OUT"` records the build host's absolute
# path, so `sha256sum -c` on the air-gapped target opens a path that does not exist there and
# reports "FAILED open or read" — on the very first step of the offline install. It also stamps the
# builder's home directory into a shipped artifact.
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" ) > "$OUT.sha256"
echo "airgap: done. On the target: sha256sum -c $(basename "$OUT").sha256 && docker load -i $(basename "$OUT")"
echo "airgap: then install offline with: ./scripts/install.sh s --host <fqdn> --tls internal --offline"

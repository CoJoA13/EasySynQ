#!/usr/bin/env bash
# Build an air-gapped install bundle: `docker save` the pinned image set (infra/images.lock)
# into a tarball for offline transfer. Application Python wheels and the npm offline store
# are added in S11 hardening. The airgap compose overlay disables ACME.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist/easysynq-airgap.tar}"
mkdir -p "$(dirname "$OUT")"

# Collect the image refs from images.lock (skip comments/blank lines; field 2 = image:tag)
mapfile -t IMAGES < <(grep -vE '^\s*#|^\s*$' "$ROOT/infra/images.lock" | awk '{print $2}')

echo "airgap: saving ${#IMAGES[@]} images -> $OUT"
docker save "${IMAGES[@]}" -o "$OUT"
echo "airgap: done. Load on the target with: docker load -i $(basename "$OUT")"

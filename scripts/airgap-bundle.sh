#!/usr/bin/env bash
# Build an air-gapped install bundle: `docker save` the pinned image set (infra/images.lock) into a
# tarball for offline transfer, plus a .sha256 sidecar for transfer-integrity verification on the
# target. The application's Python wheels (uv sync --no-dev) and the built SPA (npm ci + build) are
# baked INTO the api/web image layers at build time, so a `docker load` of this bundle yields a
# fully-installable offline stack — no separate wheel/npm store is needed (S11; doc 18 §2/§11 D-10).
# Pin images.lock to @sha256 digests before a release (`just images-update`); the airgap compose
# overlay disables ACME (internal/admin-supplied TLS).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist/easysynq-airgap.tar}"
mkdir -p "$(dirname "$OUT")"

# Collect the image refs from images.lock (skip comments/blank lines; field 2 = image:tag[@sha256])
mapfile -t IMAGES < <(grep -vE '^\s*#|^\s*$' "$ROOT/infra/images.lock" | awk '{print $2}')

echo "airgap: saving ${#IMAGES[@]} images -> $OUT"
docker save "${IMAGES[@]}" -o "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
echo "airgap: done. On the target: sha256sum -c $(basename "$OUT").sha256 && docker load -i $(basename "$OUT")"

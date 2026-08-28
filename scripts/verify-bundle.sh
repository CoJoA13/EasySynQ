#!/usr/bin/env bash
# Verify that the images loaded on THIS host are the ones a given air-gap bundle carried.
#
# The `.sha256` sidecar proves the tarball survived transfer intact; the images'
# `org.opencontainers.image.revision` label proves they were built from the checkout being
# installed from. Neither proves the images currently loaded came from THIS bundle — an older
# tarball built from the same commit loads the same tags and satisfies both. The manifest records
# each built image's IMAGE ID (the sha256 of its config, stable across docker save/load), which
# closes that gap.
#
#   bash scripts/verify-bundle.sh dist/easysynq-airgap.tar.manifest.txt
set -euo pipefail

MANIFEST="${1:-}"
if [ -z "$MANIFEST" ] || [ ! -f "$MANIFEST" ]; then
  echo "usage: verify-bundle.sh <path to easysynq-airgap.tar.manifest.txt>" >&2
  exit 2
fi

checked=0
missing=0
# Built-image lines carry two fields ("<ref> <image id>"); pulled lines carry one and are covered
# by the tarball checksum rather than an id.
while read -r ref recorded _rest; do
  case "$ref" in ''|'#'*) continue ;; esac
  [ -n "${recorded:-}" ] || continue
  actual="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)"
  if [ -z "$actual" ]; then
    echo "verify-bundle: $ref is NOT loaded on this host" >&2
    missing=$((missing + 1))
  elif [ "$actual" != "$recorded" ]; then
    echo "verify-bundle: $ref does not match the bundle" >&2
    echo "               manifest: $recorded" >&2
    echo "               loaded:   $actual" >&2
    echo "               A different bundle is loaded — re-run 'docker load' from this tarball." >&2
    missing=$((missing + 1))
  else
    checked=$((checked + 1))
  fi
done < "$MANIFEST"

if [ "$checked" -eq 0 ] && [ "$missing" -eq 0 ]; then
  echo "verify-bundle: the manifest records no image ids — rebuild the bundle with a current" >&2
  echo "               scripts/airgap-bundle.sh before relying on this check." >&2
  exit 1
fi
[ "$missing" -eq 0 ] || exit 1
echo "verify-bundle: OK — $checked built image(s) match the bundle."

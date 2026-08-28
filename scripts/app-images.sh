#!/usr/bin/env bash
# Single source of truth for the LOCALLY BUILT application images (C13).
#
# infra/images.lock pins the third-party images the stack PULLS; these three are the ones this
# repository BUILDS (the api image backs migrate/api/worker/beat). They carry an explicit
# `image:` name in compose.yml so that `docker load` of the air-gap bundle satisfies Compose
# without a network build — the whole point of the offline install.
#
# The tag is the repo's VERSION file, so the connected build host and the air-gapped target derive
# the SAME refs from the SAME checkout; neither operator has to type a tag.
#
#   scripts/app-images.sh          # -> one image ref per line
#   scripts/app-images.sh --tag    # -> just the tag
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${EASYSYNQ_VERSION_FILE:-$ROOT/VERSION}"

[ -f "$VERSION_FILE" ] || { echo "app-images: $VERSION_FILE not found" >&2; exit 1; }
# Trim the surrounding whitespace of the first line only. Deleting ALL whitespace would quietly
# turn "1.0 beta" into the valid-looking tag "1.0beta" instead of refusing it below.
TAG="$(sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;}' "$VERSION_FILE")"

# A Docker tag is [A-Za-z0-9_][A-Za-z0-9._-]{0,127}. Refuse anything else rather than emit a ref
# that only fails later inside `docker save` on the build host.
if ! [[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "app-images: VERSION ('$TAG') is not a valid Docker image tag" >&2
  exit 1
fi

if [ "${1:-}" = "--tag" ]; then
  printf '%s\n' "$TAG"
  exit 0
fi
[ $# -eq 0 ] || { echo "usage: app-images.sh [--tag]" >&2; exit 2; }

# Keep this list in step with the `image: easysynq/...` refs in infra/compose/compose.yml
# (test_airgap_packaging.py pins the two against each other).
for name in api web keycloak; do
  printf 'easysynq/%s:%s\n' "$name" "$TAG"
done

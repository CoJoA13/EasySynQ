#!/usr/bin/env bash
# Resolve every image in infra/images.lock to an immutable @sha256 digest (a RELEASE-CEREMONY step
# — needs a connected host with Docker; never run in CI or on the air-gapped target).
#
# Prints `name:tag@sha256:…` lines to paste into infra/images.lock, and EXITS NON-ZERO if any image
# could not be resolved. The previous inline recipe printed `# COULD NOT RESOLVE: <img>` and exited
# 0, so a partial result pasted verbatim silently turned that image into a COMMENT — dropping it
# from the lock entirely. Docker Hub rate-limits anonymous manifest requests (HTTP 429), and this
# happens in practice: a second run minutes after a successful one returned 429 for six of nine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="${IMAGES_LOCK:-$ROOT/infra/images.lock}"
ATTEMPTS="${IMAGES_UPDATE_ATTEMPTS:-4}"

mapfile -t REFS < <(grep -vE '^\s*#|^\s*$' "$LOCK" | awk '{print $2}' | sed 's/@sha256:.*$//')

resolved=()
unresolved=()
rate_limited=0
for ref in "${REFS[@]}"; do
  digest=""
  for attempt in $(seq 1 "$ATTEMPTS"); do
    if err="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}' 2>&1)"; then
      case "$err" in sha256:*) digest="$err"; break ;; esac
    fi
    case "$err" in *429*|*"Too Many Requests"*) rate_limited=1 ;; esac
    [ "$attempt" -lt "$ATTEMPTS" ] && sleep $((attempt * 5))
  done
  if [ -n "$digest" ]; then
    resolved+=("$ref@$digest")
  else
    unresolved+=("$ref")
  fi
done

printf '%s\n' "${resolved[@]}"

if [ "${#unresolved[@]}" -gt 0 ]; then
  echo >&2
  echo "images-update: FAILED to resolve ${#unresolved[@]} of ${#REFS[@]} images:" >&2
  printf '  %s\n' "${unresolved[@]}" >&2
  if [ "$rate_limited" -eq 1 ]; then
    echo "images-update: the registry returned 429. Anonymous Docker Hub allows a limited number" >&2
    echo "               of manifest requests per window — run 'docker login' and retry, or wait." >&2
  fi
  echo "images-update: do NOT paste a partial list; the lock would lose the missing images." >&2
  exit 1
fi

echo >&2
echo "images-update: resolved all ${#REFS[@]} images. Paste the lines above into $LOCK," >&2
echo "               keeping the service column, then run 'just release-check'." >&2

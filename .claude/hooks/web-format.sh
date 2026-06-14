#!/usr/bin/env bash
# PostToolUse hook: auto-format edited web files under apps/web with prettier (+ a
# best-effort eslint --fix), mirroring ruff-format.sh for the Python path, so every edit
# lands CI-clean (the `web` job runs eslint/tsc/build).
# Non-fatal: any problem (incl. web deps not installed) exits 0 so editing is never blocked.
#
# NB: the file path is parsed via _lib.sh (sed), NOT jq — jq isn't installed on this box,
# which silently no-op'd the earlier jq-based version of this hook.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
WEB="$ROOT/apps/web"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

# Only format web source files under apps/web (ts/tsx/css/js/jsx).
case "$file" in
  */apps/web/*.ts|*/apps/web/*.tsx|*/apps/web/*.css|*/apps/web/*.js|*/apps/web/*.jsx) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

# Prefer the locally-installed binaries; skip silently if web deps aren't installed.
PRETTIER="$WEB/node_modules/.bin/prettier"
ESLINT="$WEB/node_modules/.bin/eslint"
cd "$WEB" || exit 0
[ -x "$PRETTIER" ] && "$PRETTIER" --write "$file" >/dev/null 2>&1 || true
[ -x "$ESLINT" ] && "$ESLINT" --fix "$file" >/dev/null 2>&1 || true
exit 0

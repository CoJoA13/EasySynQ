#!/usr/bin/env bash
# PostToolUse hook: auto-format edited web files under apps/web with prettier
# (+ a best-effort eslint --fix), mirroring ruff-format.sh for the Python path.
# Reads the Claude Code hook JSON on stdin, extracts the edited file path, and
# formats it so every edit lands CI-clean (the `web` job runs eslint/tsc/build).
# Non-fatal: any problem (incl. web deps not installed) exits 0 so editing is
# never blocked.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB="$ROOT/apps/web"

# The hook payload is JSON on stdin; the edited path is .tool_input.file_path.
input="$(cat)"
file="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$file" ] && exit 0

# Only format web source files under apps/web (ts/tsx/css/js/jsx).
case "$file" in
  "$ROOT"/apps/web/*.ts|"$ROOT"/apps/web/*.tsx|"$ROOT"/apps/web/*.css \
  |"$ROOT"/apps/web/*.js|"$ROOT"/apps/web/*.jsx \
  |apps/web/*.ts|apps/web/*.tsx|apps/web/*.css|apps/web/*.js|apps/web/*.jsx) ;;
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

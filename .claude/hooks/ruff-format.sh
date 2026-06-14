#!/usr/bin/env bash
# PostToolUse hook: auto-format edited Python files under apps/api with ruff
# (`ruff format` + `ruff check --fix`) so every edit lands CI-clean.
# Non-fatal: any problem exits 0 so editing is never blocked.
#
# NB: the file path is parsed via _lib.sh (sed), NOT jq — jq isn't installed on this box,
# which silently no-op'd the earlier jq-based version of this hook.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

UV="${UV_BIN:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV="$(command -v uv 2>/dev/null || true)"
[ -z "$UV" ] && exit 0

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

# Only format Python files inside apps/api.
case "$file" in
  */apps/api/*.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

cd "$ROOT/apps/api" || exit 0
"$UV" run ruff format "$file" >/dev/null 2>&1 || true
"$UV" run ruff check --fix "$file" >/dev/null 2>&1 || true
exit 0

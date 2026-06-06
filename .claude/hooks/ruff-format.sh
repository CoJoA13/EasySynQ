#!/usr/bin/env bash
# PostToolUse hook: auto-format edited Python files under apps/api with ruff.
# Reads the Claude Code hook JSON on stdin, extracts the edited file path, and
# runs `ruff format` (+ `ruff check --fix`) on it so every edit lands CI-clean.
# Non-fatal: any problem exits 0 so editing is never blocked.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UV="${UV_BIN:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV="$(command -v uv 2>/dev/null || true)"
[ -z "$UV" ] && exit 0

# The hook payload is JSON on stdin; the edited path is .tool_input.file_path.
input="$(cat)"
file="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$file" ] && exit 0

# Only format Python files inside apps/api.
case "$file" in
  "$ROOT"/apps/api/*.py|apps/api/*.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

cd "$ROOT/apps/api" || exit 0
"$UV" run ruff format "$file" >/dev/null 2>&1 || true
"$UV" run ruff check --fix "$file" >/dev/null 2>&1 || true
exit 0

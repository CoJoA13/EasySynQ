#!/usr/bin/env bash
# PreToolUse hook: refuse Edit/Write/MultiEdit on the real .env files.
#
# The gitignored repo-root .env holds load-bearing secrets — notably BACKUP_ENCRYPTION_KEY,
# where a regenerated key makes every OLD encrypted backup unrecoverable (install.sh warns
# about exactly this). Entering/editing secrets is an owner action, not Claude's. The
# committed template (.env.example/.sample/.template) carries no secrets, so it stays editable.
#
# Exit 2 = DENY the tool call and feed stderr back to Claude (the Claude Code hook contract).
# Any parsing problem exits 0 so normal editing is never blocked.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

base="${file##*/}"   # forward-slash normalized by _lib.sh, so basename = last segment
case "$base" in
  .env.example|.env.sample|.env.template)
    exit 0 ;;                                   # templates are safe to edit
  .env|.env.*)
    echo "Refusing to edit '$base': it holds load-bearing secrets (e.g. BACKUP_ENCRYPTION_KEY — a regenerated key makes old encrypted backups unrecoverable, and D1 keeps all data on the org's own infra). Ask the owner to edit .env by hand; copy from .env.example for new keys." >&2
    exit 2 ;;
esac
exit 0

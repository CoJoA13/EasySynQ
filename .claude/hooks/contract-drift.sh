#!/usr/bin/env bash
# PostToolUse hook: when an API route module under apps/api/.../api/ is edited but
# packages/contracts/openapi.yaml has NO pending change, remind to keep the contract in sync.
# The "document new endpoints in-PR" rule is load-bearing: the redocly `contracts` CI job is
# the gate, and Codex has caught contract/enum omissions post-PR (e.g. the DcrReasonClass
# `mgmt_review` member in S-mr-3).
#
# Non-blocking: emits PostToolUse `additionalContext` (a nudge for Claude), exit 0. Stays
# quiet once openapi.yaml already shows a working-tree change (sync in progress) and for
# non-route edits.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

case "$file" in
  */apps/api/src/easysynq_api/api/*.py|apps/api/src/easysynq_api/api/*.py) ;;
  *) exit 0 ;;
esac

cd "$ROOT" || exit 0
# Already a pending contract change → the sync is in progress, stay quiet.
if git status --porcelain packages/contracts/openapi.yaml 2>/dev/null | grep -q .; then
  exit 0
fi

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Contract-sync reminder: you edited an apps/api route module but packages/contracts/openapi.yaml has no pending change. If this added or changed an endpoint, gate, request/response body, or enum member, update openapi.yaml in this PR — the redocly `contracts` CI job and the Codex post-PR review both check it. Run /check-contracts after."}}'
exit 0

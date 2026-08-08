#!/usr/bin/env bash
# Executable compatibility contracts for the Claude hooks. Each hook runs from a temporary
# repository so its root discovery and stdin payload handling match Claude Code's real behavior.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-claude-hooks.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf '  FAIL %s\n' "$1"
  exit 1
}

make_repo() {
  local repo="$1"
  mkdir -p "$repo/.claude/hooks" "$repo/.claude/commands" "$repo/docs"
  cp "$ROOT/.claude/hooks/_lib.sh" "$repo/.claude/hooks/_lib.sh"
  cp "$ROOT/.claude/hooks/test-baseline.sh" "$repo/.claude/hooks/test-baseline.sh"
  cp "$ROOT/.claude/hooks/register-range-guard.sh" "$repo/.claude/hooks/register-range-guard.sh"
  cp "$ROOT/.claude/commands/finish-slice.md" "$repo/.claude/commands/finish-slice.md"
}

write_valid_status() {
  local repo="$1"
  {
    printf '%s\n' '---'
    printf '%s\n' 'easysynq_status_schema: 1'
    printf '%s\n' 'as_of: "2026-08-08"'
    printf '%s\n' 'baseline_commit: "c15541f"'
    printf '%s\n' 'last_shipped_slice: "S-upload-identity"'
    printf '%s\n' 'migration_head: "0085"'
    printf '%s\n' 'next_migration: "0086"'
    printf '%s\n' 'api_unit_tests: 1686'
    printf '%s\n' 'web_test_files: 249'
    printf '%s\n' 'web_tests: 1468'
    printf '%s\n' 'contract_tests: 283'
    printf '%s\n' 'integration_passed: 1051'
    printf '%s\n' 'integration_skipped: 2'
    printf '%s\n' 'ci_jobs: 10'
    printf '%s\n' 'ci_checks: 14'
    printf '%s\n' '---'
  } >"$repo/docs/current-status.md"
}

write_conflicting_claude() {
  local repo="$1"
  printf '%s\n' '- 2026-08-08 — stale baseline (api unit 9999; web 8888).' >"$repo/CLAUDE.md"
}

run_baseline() {
  local repo="$1"
  bash "$repo/.claude/hooks/test-baseline.sh"
}

write_register() {
  local repo="$1"
  local range="$2"
  {
    printf 'This register resolves R1–R%s).\n\n' "$range"
    printf '## Part 3 — Resolutions R1–R%s\n\n' "$range"
    printf '%s\n' '### R64 — Existing decision'
    printf '%s\n' '### R65 — New decision'
  } >"$repo/docs/decisions-register.md"
}

run_register_guard() {
  local repo="$1"
  printf '{"tool_input":{"file_path":"%s/docs/decisions-register.md"}}' "$repo" \
    | bash "$repo/.claude/hooks/register-range-guard.sh"
}

printf '== Claude hook compatibility contract ==\n'

repo="$TMP_ROOT/valid-status"
make_repo "$repo"
write_valid_status "$repo"
baseline="$(run_baseline "$repo")"
[[ "$baseline" == *'api=1686'* ]] || fail "valid status did not surface api=1686 (output=$baseline)"
[[ "$baseline" == *'web=1468'* ]] || fail "valid status did not surface web=1468 (output=$baseline)"
printf '  ok   valid status frontmatter supplies the session baseline\n'

repo="$TMP_ROOT/conflicting-claude"
make_repo "$repo"
write_valid_status "$repo"
write_conflicting_claude "$repo"
baseline="$(run_baseline "$repo")"
[[ "$baseline" == *'api=1686'* && "$baseline" == *'web=1468'* ]] \
  || fail "parseable CLAUDE conflict changed the baseline (output=$baseline)"
[[ "$baseline" != *'9999'* && "$baseline" != *'8888'* ]] \
  || fail "parseable CLAUDE conflict was not ignored (output=$baseline)"
printf '  ok   conflicting parseable CLAUDE baseline is ignored\n'

for invalid_case in missing malformed duplicate; do
  repo="$TMP_ROOT/status-$invalid_case"
  make_repo "$repo"
  write_valid_status "$repo"
  write_conflicting_claude "$repo"
  case "$invalid_case" in
    missing)
      sed -i '/^api_unit_tests:/d' "$repo/docs/current-status.md"
      ;;
    malformed)
      sed -i 's/^web_tests: 1468$/web_tests: 1,468/' "$repo/docs/current-status.md"
      ;;
    duplicate)
      sed -i '/^ci_jobs: 10$/a ci_jobs: 10' "$repo/docs/current-status.md"
      ;;
  esac
  baseline="$(run_baseline "$repo")"
  [ -z "$baseline" ] || fail "$invalid_case status guessed a baseline (output=$baseline)"
done
printf '  ok   missing, malformed, and duplicate status keys remain silent\n'

repo="$TMP_ROOT/stale-register"
make_repo "$repo"
write_register "$repo" 64
write_conflicting_claude "$repo"
warning="$(run_register_guard "$repo")"
[[ "$warning" == *"register's intro paragraph"* && "$warning" == *"Part 3 — Resolutions"* ]] \
  || fail "stale register self-range did not warn (output=$warning)"
[[ "$warning" != *'CLAUDE.md'* ]] \
  || fail "register warning still depends on CLAUDE.md (output=$warning)"
printf '  ok   stale register self-range warns without CLAUDE.md\n'

repo="$TMP_ROOT/current-register"
make_repo "$repo"
write_register "$repo" 65
write_conflicting_claude "$repo"
warning="$(run_register_guard "$repo")"
[ -z "$warning" ] || fail "current register self-range was changed by CLAUDE.md (output=$warning)"
printf '  ok   matching register self-range ignores a stale CLAUDE.md fixture\n'

repo="$TMP_ROOT/finish-slice"
make_repo "$repo"
finish_slice="$repo/.claude/commands/finish-slice.md"
for authority_home in docs/current-status.md docs/slice-history.md docs/open-residuals.md; do
  grep -Fq "$authority_home" "$finish_slice" \
    || fail "finish-slice does not direct updates to $authority_home"
done
if grep -Eqi 'CLAUDE\.md|recent[[:space:]]+learnings|migration[[:space:]]+head' "$finish_slice"; then
  fail "finish-slice still directs compatibility or history-head ownership"
fi
printf '  ok   finish-slice owns status, history, and residual updates only\n'

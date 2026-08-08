#!/usr/bin/env bash
# Dependency-free contract for the repository-authority guard. Fixtures are complete miniature
# repositories so each named mutation has one observable AUTHORITY_* failure reason.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/scripts/check-repo-authority.sh"
PASS=0
FAIL=0

ok() { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

fixture_root() {
  local fixture
  fixture="$(mktemp -d)"
  mkdir -p "$fixture/docs" "$fixture/scripts"
  git -C "$fixture" init -q
  cat >"$fixture/AGENTS.md" <<'EOF'
# Contributor guide

Current status: docs/current-status.md
Open residuals: docs/open-residuals.md
History: docs/slice-history.md
Binding decisions: docs/decisions-register.md
EOF
  cat >"$fixture/docs/current-status.md" <<'EOF'
---
easysynq_status_schema: 1
as_of: "2026-08-08"
baseline_commit: "c15541f"
last_shipped_slice: "S-upload-identity"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 249
web_tests: 1468
contract_tests: 283
integration_passed: 1051
integration_skipped: 2
ci_jobs: 10
ci_checks: 14
---

# Current execution snapshot
EOF
  cat >"$fixture/docs/open-residuals.md" <<'EOF'
# Open residuals

## RES-EXAMPLE

Status: OPEN
EOF
  cat >"$fixture/docs/slice-history.md" <<'EOF'
# Shipped slice history

Historical evidence only. Current residuals are in docs/open-residuals.md.
EOF
  cat >"$fixture/CLAUDE.md" <<'EOF'
# Claude compatibility

Use AGENTS.md for repository authority.
EOF
  cat >"$fixture/scripts/repo-authority-live-paths.txt" <<'EOF'
AGENTS.md
CLAUDE.md
docs/current-status.md
docs/open-residuals.md
docs/slice-history.md
EOF
  printf '%s\n' "$fixture"
}

mutate_fixture() {
  local fixture="$1" case_name="$2"
  case "$case_name" in
    duplicate_status_key)
      sed -i '/easysynq_status_schema: 1/a easysynq_status_schema: 1' "$fixture/docs/current-status.md"
      ;;
    claude_current_heading)
      printf '\n## Current status\n' >>"$fixture/CLAUDE.md"
      ;;
    slice_history_head)
      printf '\nMigration head: 0085\n' >>"$fixture/docs/slice-history.md"
      ;;
    live_claude_reference)
      printf '\nCLAUDE.md owns current status and residuals.\n' >>"$fixture/AGENTS.md"
      ;;
    duplicate_residual_id)
      printf '\n## RES-EXAMPLE\n\nStatus: OPEN\n' >>"$fixture/docs/open-residuals.md"
      ;;
    unresolved_residual_ref)
      printf '\nSee RES-NOT-REGISTERED for the remaining work.\n' >>"$fixture/AGENTS.md"
      ;;
    *)
      printf 'unknown fixture mutation: %s\n' "$case_name" >&2
      return 2
      ;;
  esac
}

run_bad() {
  local case_name="$1" expected="$2" fixture output status
  fixture="$(fixture_root)"
  mutate_fixture "$fixture" "$case_name"
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 1 ] && grep -Fqx "$expected" <<<"$output"; then
    ok "$case_name emits $expected"
  else
    bad "$case_name emits $expected (status=$status output=$output)"
  fi
}

run_good() {
  local case_name="$1" fixture output status
  fixture="$(fixture_root)"
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && grep -Fqx 'AUTHORITY_OK' <<<"$output"; then
    ok "$case_name"
  else
    bad "$case_name (status=$status output=$output)"
  fi
}

printf '== repository authority contract ==\n'
run_bad duplicate_status_key AUTHORITY_DUPLICATE_STATUS_KEY
run_bad claude_current_heading AUTHORITY_CLAUDE_CURRENT_OWNER
run_bad slice_history_head AUTHORITY_HISTORY_MUTABLE_HEAD
run_bad live_claude_reference AUTHORITY_LIVE_CLAUDE_OWNER
run_bad duplicate_residual_id AUTHORITY_DUPLICATE_RESIDUAL_ID
run_bad unresolved_residual_ref AUTHORITY_UNKNOWN_RESIDUAL_ID
run_good neutral_authority_split

if [ "${1:-}" != "--fixtures-only" ]; then
  live_output="$("$GUARD" 2>&1)"
  live_status=$?
  if [ "$live_status" -eq 1 ]; then
    ok 'live tree remains diagnostically red until the authority migration'
  else
    bad "live tree remains diagnostically red until the authority migration (status=$live_status output=$live_output)"
  fi
fi

printf '%s fixture checks passed; %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

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
Owner: Repository owner
Source: Fixture
Reason: Example deferred work.
Closure contract: Ship the example.
Last reviewed: 2026-08-08
EOF
  cat >"$fixture/docs/slice-history.md" <<'EOF'
# Shipped slice history

Historical evidence only. Current residuals are in docs/open-residuals.md.
EOF
  cat >"$fixture/CLAUDE.md" <<'EOF'
# EasySynQ Claude compatibility

Read AGENTS.md before work. Current execution state is in docs/current-status.md; current residuals are in docs/open-residuals.md.

## Claude hooks and commands

- Active Claude hooks live under `.claude/hooks/`.
- Active Claude commands live under `.claude/commands/`.
- Claude hook wiring lives in `.claude/settings.json`.
- Claude session-start behavior is wired in `.claude/settings.json` and implemented by `.claude/hooks/test-baseline.sh`.

## Claude memory behavior

- Claude session memory remains tool-specific.
- Claude `/effort` selection is per-session.
- Claude persistent memory lives outside the repository under `~/.claude/projects/<path-derived-key>/memory/`.
- `MEMORY.md` is the index for Claude persistent memory.
- Claude memory paths are machine- and OS-specific.
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
      cat >>"$fixture/docs/open-residuals.md" <<'EOF'

## RES-EXAMPLE

Status: OPEN
Owner: Repository owner
Source: Duplicate fixture
Reason: Duplicate deferred work.
Closure contract: Ship the duplicate.
Last reviewed: 2026-08-08
EOF
      ;;
    unresolved_residual_ref)
      printf '\nSee RES-NOT-REGISTERED for the remaining work.\n' >>"$fixture/AGENTS.md"
      ;;
    tracked_new_consumer)
      printf 'CLAUDE.md owns current status and residuals.\n' >"$fixture/docs/new-consumer.md"
      ;;
    claude_mutable_migration)
      printf '\nThe migration head is 0085.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_status)
      printf '\nAPI unit tests: 1686.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_residual)
      printf '\nRES-EXAMPLE remains open.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_ci)
      printf '\nCI jobs: 10.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_decision)
      printf '\nThe current decision range is R1–R64.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_permission)
      printf '\nPermission catalog: 102.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_mutable_slice)
      printf '\nLast shipped slice: S-upload-identity.\n' >>"$fixture/CLAUDE.md"
      ;;
    claude_product_rule)
      sed -i '/## Claude memory behavior/i - Every imported document requires approval before publication.\n' "$fixture/CLAUDE.md"
      ;;
    claude_product_command_tail)
      sed -i '/## Claude memory behavior/i - Claude commands require every record to be approved before release.\n' "$fixture/CLAUDE.md"
      ;;
    claude_product_hook_path_tail)
      sed -i '/## Claude memory behavior/i - Active Claude hooks live under `.claude/hooks/`; every record requires approval before release.\n' "$fixture/CLAUDE.md"
      ;;
    claude_product_memory_tail)
      printf '%s\n' '- Claude session memory requires every record to be approved before release.' >>"$fixture/CLAUDE.md"
      ;;
    residual_cross_block_fields)
      cat >"$fixture/docs/open-residuals.md" <<'EOF'
# Open residuals

## RES-EXAMPLE

Status: OPEN
Owner: Repository owner
Owner: Duplicate owner
Source: Fixture one
Reason: First deferred work.
Closure contract: Ship the first example.
Last reviewed: 2026-08-08

## RES-SECOND

Status: OPEN
Source: Fixture two
Reason: Second deferred work.
Closure contract: Ship the second example.
Last reviewed: 2026-08-08
EOF
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
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 1 ] && [ "$output" = "$expected" ]; then
    ok "$case_name emits $expected"
  else
    bad "$case_name emits $expected (status=$status output=$output)"
  fi
}

run_good() {
  local case_name="$1" fixture output status
  fixture="$(fixture_root)"
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && grep -Fqx 'AUTHORITY_OK' <<<"$output"; then
    ok "$case_name"
  else
    bad "$case_name (status=$status output=$output)"
  fi
}

run_bad_residual_status_keys() {
  local key fixture output status
  local -a status_keys=(
    easysynq_status_schema
    as_of
    baseline_commit
    last_shipped_slice
    migration_head
    next_migration
    api_unit_tests
    web_test_files
    web_tests
    contract_tests
    integration_passed
    integration_skipped
    ci_jobs
    ci_checks
  )

  for key in "${status_keys[@]}"; do
    fixture="$(fixture_root)"
    printf '\n%s: fixture\n' "$key" >>"$fixture/docs/open-residuals.md"
    git -C "$fixture" add --all
    output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
    status=$?
    rm -rf "$fixture"
    if [ "$status" -eq 1 ] && [ "$output" = 'AUTHORITY_RESIDUAL_STATUS_FACT' ]; then
      ok "residual status key $key emits AUTHORITY_RESIDUAL_STATUS_FACT"
    else
      bad "residual status key $key emits AUTHORITY_RESIDUAL_STATUS_FACT (status=$status output=$output)"
    fi
  done
}

run_good_fixture_payloads() {
  local fixture output status
  fixture="$(fixture_root)"
  mkdir -p "$fixture/scripts/tests"
  cat >"$fixture/scripts/tests/test-agent-authority.sh" <<'EOF'
# Fixture payloads must never become live authority claims.
# CLAUDE.md owns current status and residuals.
# See RES-NOT-REGISTERED for the remaining work.
EOF
  cat >"$fixture/scripts/tests/test-claude-hooks.sh" <<'EOF'
# Current decision range is R1–R64.
EOF
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && [ "$output" = 'AUTHORITY_OK' ]; then
    ok 'tracked fixture payloads are not live authority consumers'
  else
    bad "tracked fixture payloads are not live authority consumers (status=$status output=$output)"
  fi
}

run_good_untracked_payload() {
  local fixture output status
  fixture="$(fixture_root)"
  git -C "$fixture" add --all
  printf 'CLAUDE.md owns current status and residuals.\n' >"$fixture/docs/untracked-authority.md"
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && [ "$output" = 'AUTHORITY_OK' ]; then
    ok 'untracked payload does not affect the authority contract'
  else
    bad "untracked payload does not affect the authority contract (status=$status output=$output)"
  fi
}

run_good_register_range() {
  local fixture output status
  fixture="$(fixture_root)"
  cat >"$fixture/docs/decisions-register.md" <<'EOF'
# Decisions register

All registered decisions are canonical here.

Bumps the resolutions range **R1–R63 → R1–R64**.
EOF
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && [ "$output" = 'AUTHORITY_OK' ]; then
    ok 'canonical decision-register range is not a live mirror'
  else
    bad "canonical decision-register range is not a live mirror (status=$status output=$output)"
  fi
}

run_good_historical_decision_range() {
  local fixture output status
  fixture="$(fixture_root)"
  cat >"$fixture/docs/documentation-audit-2026-07-30.md" <<'EOF'
# Historical documentation audit

The audit recorded the then-current register range R1–R60.
EOF
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && [ "$output" = 'AUTHORITY_OK' ]; then
    ok 'historical decision range is not a live mirror'
  else
    bad "historical decision range is not a live mirror (status=$status output=$output)"
  fi
}

run_bad_current_decision_range() {
  local fixture output status
  fixture="$(fixture_root)"
  printf 'Current decision range is R1–R60.\n' >"$fixture/docs/current-decision-mirror.md"
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 1 ] && [ "$output" = 'AUTHORITY_DECISION_RANGE_MIRROR' ]; then
    ok 'current decision range remains a rejected mirror'
  else
    bad "current decision range remains a rejected mirror (status=$status output=$output)"
  fi
}

run_good_guard_implementation() {
  local fixture output status
  fixture="$(fixture_root)"
  cat >"$fixture/scripts/check-repo-authority.sh" <<'EOF'
# The authority guard itself contains the scanner's patterns for CLAUDE.md ownership and current status.
EOF
  git -C "$fixture" add --all
  output="$(AUTHORITY_ROOT="$fixture" "$GUARD" 2>&1)"
  status=$?
  rm -rf "$fixture"
  if [ "$status" -eq 0 ] && [ "$output" = 'AUTHORITY_OK' ]; then
    ok 'authority guard implementation is not scanned as a live consumer'
  else
    bad "authority guard implementation is not scanned as a live consumer (status=$status output=$output)"
  fi
}

require_live_text() {
  local path="$1" pattern="$2" label="$3"
  if [ -f "$ROOT/$path" ] && grep -Eq "$pattern" "$ROOT/$path"; then
    ok "$label"
  else
    bad "$label"
  fi
}

reject_live_text() {
  local path="$1" pattern="$2" label="$3"
  if [ -f "$ROOT/$path" ] && ! grep -Eq "$pattern" "$ROOT/$path"; then
    ok "$label"
  else
    bad "$label"
  fi
}

validate_residual_blocks() {
  local path="$1"
  awk '
    function finish_record() {
      if (!in_record) return
      if (status != 1 || owner != 1 || source != 1 || reason != 1 || closure != 1 || reviewed != 1) {
        invalid = 1
      }
    }
    /^## RES-[A-Z][A-Z0-9-]*$/ {
      finish_record()
      in_record = 1
      records += 1
      status = owner = source = reason = closure = reviewed = 0
      next
    }
    /^Status: / { if (!in_record) invalid = 1; status += 1; if ($0 != "Status: OPEN") invalid = 1 }
    /^Owner: / { if (!in_record) invalid = 1; owner += 1 }
    /^Source: / { if (!in_record) invalid = 1; source += 1 }
    /^Reason: / { if (!in_record) invalid = 1; reason += 1 }
    /^Closure contract: / { if (!in_record) invalid = 1; closure += 1 }
    /^Last reviewed: / { if (!in_record) invalid = 1; reviewed += 1 }
    END {
      finish_record()
      if (records == 0) invalid = 1
      exit invalid
    }
  ' "$path"
}

run_neutral_document_contract() {
  local heading residual_id

  printf '== neutral authority documents ==\n'
  for heading in \
    'Authority and precedence' \
    'Repository map' \
    'Supported contributor workflow' \
    'Tests and evidence' \
    'Security and site-data boundaries' \
    'Migrations and generated files' \
    'Documentation truth' \
    'Change handoff' \
    'Tool-specific compatibility'; do
    require_live_text AGENTS.md "^## ${heading}$" "AGENTS.md declares ${heading}"
  done
  require_live_text AGENTS.md 'just (setup|check|authority-check)' \
    'AGENTS.md names stable contributor commands'
  require_live_text AGENTS.md 'docs/(12-security-and-audit|decisions-register)\.md' \
    'AGENTS.md links security authority'
  require_live_text AGENTS.md 'alembic heads' \
    'AGENTS.md names the migration-head command'
  require_live_text AGENTS.md '(generated|generation)' \
    'AGENTS.md states generated-file rules'
  require_live_text AGENTS.md '(handoff|commit)' \
    'AGENTS.md states change-handoff rules'

  require_live_text docs/current-status.md '^easysynq_status_schema: 1$' \
    'current status has the structured schema'
  require_live_text docs/current-status.md '^# Current execution snapshot$' \
    'current status owns the execution snapshot'
  reject_live_text docs/current-status.md '^##[[:space:]]+RES-' \
    'current status contains no residual records'

  for residual_id in \
    RES-INGEST-PROGRESS \
    RES-INGEST-PARTIAL-OPTIN \
    RES-R10-RECONSTRUCTION \
    RES-CAPA-REJECT \
    RES-AUDIT-CHECKPOINT-LINEAGE \
    RES-AUDIT-VERIFY-ORCHESTRATOR \
    RES-AUDIT-LONG-SCOPE-REF \
    RES-UPGRADE-LOCK-TIMEOUT \
    RES-AUDIT-KEY-ROTATION \
    RES-RISK-CLAUSE-PICKER \
    RES-RESTORE-SCRATCH-WORM-GUARD \
    RES-AUDIT-EXPORT; do
    require_live_text docs/open-residuals.md "^## ${residual_id}$" \
      "open residuals registers ${residual_id}"
  done
  if [ -f "$ROOT/docs/open-residuals.md" ] && \
      validate_residual_blocks "$ROOT/docs/open-residuals.md"; then
    ok 'every open residual independently has the exact record schema'
  else
    bad 'every open residual independently has the exact record schema'
  fi
  reject_live_text docs/open-residuals.md \
    '^(easysynq_status_schema|as_of|baseline_commit|last_shipped_slice|migration_head|next_migration|api_unit_tests|web_test_files|web_tests|contract_tests|integration_passed|integration_skipped|ci_jobs|ci_checks):' \
    'open residuals contains none of the execution-snapshot schema'

  require_live_text docs/slice-history.md 'docs/open-residuals\.md' \
    'slice history links the current residual ledger'
  reject_live_text docs/slice-history.md '^##[[:space:]].*OPEN RESIDUALS' \
    'slice history has no current OPEN section'
  if [ -f "$ROOT/docs/slice-history.md" ] && \
      ! sed -n '1,20p' "$ROOT/docs/slice-history.md" | \
        grep -Eqi 'migration[[:space:]]+head|current[[:space:]]+head'; then
    ok 'slice history preamble is history-only'
  else
    bad 'slice history preamble is history-only'
  fi
}

run_claude_compatibility_contract() {
  printf '== Claude compatibility document ==\n'
  require_live_text CLAUDE.md 'AGENTS\.md' \
    'Claude compatibility points to the contributor guide'
  require_live_text CLAUDE.md 'docs/current-status\.md' \
    'Claude compatibility points to the execution snapshot'
  require_live_text CLAUDE.md 'docs/open-residuals\.md' \
    'Claude compatibility points to the residual ledger'
  require_live_text CLAUDE.md '(Claude-specific|\.claude/)' \
    'Claude compatibility documents only Claude integration behavior'
}

printf '== repository authority contract ==\n'
run_bad duplicate_status_key AUTHORITY_DUPLICATE_STATUS_KEY
run_bad claude_current_heading AUTHORITY_CLAUDE_CURRENT_OWNER
run_bad slice_history_head AUTHORITY_HISTORY_MUTABLE_HEAD
run_bad live_claude_reference AUTHORITY_LIVE_CLAUDE_OWNER
run_bad duplicate_residual_id AUTHORITY_DUPLICATE_RESIDUAL_ID
run_bad unresolved_residual_ref AUTHORITY_UNKNOWN_RESIDUAL_ID
run_bad tracked_new_consumer AUTHORITY_LIVE_CLAUDE_OWNER
run_bad claude_mutable_migration AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_status AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_residual AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_ci AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_decision AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_permission AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_mutable_slice AUTHORITY_CLAUDE_MUTABLE_FACTS
run_bad claude_product_rule AUTHORITY_CLAUDE_PRODUCT_RULES
run_bad claude_product_command_tail AUTHORITY_CLAUDE_PRODUCT_RULES
run_bad claude_product_hook_path_tail AUTHORITY_CLAUDE_PRODUCT_RULES
run_bad claude_product_memory_tail AUTHORITY_CLAUDE_PRODUCT_RULES
run_bad residual_cross_block_fields AUTHORITY_RESIDUAL_LEDGER_SCHEMA
run_bad_residual_status_keys
run_good_fixture_payloads
run_good_untracked_payload
run_good_register_range
run_good_historical_decision_range
run_bad_current_decision_range
run_good_guard_implementation
run_good neutral_authority_split

if [ "${1:-}" != "--fixtures-only" ]; then
  run_neutral_document_contract
fi

if [ "${1:-}" != "--fixtures-only" ] && [ "${1:-}" != "--neutral-docs-only" ]; then
  run_claude_compatibility_contract
  live_output="$("$GUARD" 2>&1)"
  live_status=$?
  if [ "$live_status" -eq 0 ] && [ "$live_output" = 'AUTHORITY_OK' ]; then
    ok 'live authority tree is fully migrated'
  else
    bad "live authority tree is fully migrated (status=$live_status output=$live_output)"
  fi
fi

printf '%s fixture checks passed; %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

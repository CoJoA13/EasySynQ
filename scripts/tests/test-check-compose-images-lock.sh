#!/usr/bin/env bash
# Behavioral regressions for scripts/check-compose-images-lock.sh. Each case runs the real gate
# against fixture compose/lock trees, proving in particular that a DIGEST-PINNED release lock
# (`name:tag@sha256:…`) still satisfies the tag-form Compose refs — the shape the release ceremony
# writes (2026-08-27 audit C15).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/check-compose-images-lock.sh"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-images-lock-test.XXXXXX")"

cleanup() {
  local expected_parent="${TMPDIR:-/tmp}"
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-images-lock-test.*)
      if [ -d "$TEST_ROOT" ] && [ ! -L "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
      fi
      ;;
  esac
}
trap cleanup EXIT

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

fixture() {
  # A minimal compose tree: two compose files + the Keycloak Dockerfile base.
  local dir="$1"
  mkdir -p "$dir/keycloak"
  cat >"$dir/compose.yml" <<'EOF'
services:
  postgres:
    image: postgres:16
  redis:
    image: redis:7
EOF
  cat >"$dir/compose.s.yml" <<'EOF'
services:
  minio:
    image: minio/minio:RELEASE.2024-09-13T20-26-02Z
EOF
  cat >"$dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7 AS builder
FROM quay.io/keycloak/keycloak:26.7
EOF
}

run_gate() {
  COMPOSE_DIR="$1" IMAGES_LOCK="$2" KEYCLOAK_DOCKERFILE="$1/keycloak/Dockerfile" \
    bash "$SCRIPT" >/dev/null 2>&1
}

compose_dir="$TEST_ROOT/compose"
fixture "$compose_dir"

# Case 1: a tag-pinned lock (the pre-release shape) passes.
cat >"$TEST_ROOT/lock-tags" <<'EOF'
# service   image:tag
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z   # inline comment survives
keycloak    quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-tags"; then
  ok "tag-pinned lock passes"
else
  bad "tag-pinned lock passes"
fi

# Case 2: a DIGEST-pinned lock (the release-ceremony shape) still satisfies tag-form refs.
cat >"$TEST_ROOT/lock-digests" <<'EOF'
postgres    postgres:16@sha256:1111111111111111111111111111111111111111111111111111111111111111
redis       redis:7@sha256:2222222222222222222222222222222222222222222222222222222222222222
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z@sha256:3333333333333333333333333333333333333333333333333333333333333333
keycloak    quay.io/keycloak/keycloak:26.7@sha256:4444444444444444444444444444444444444444444444444444444444444444
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-digests"; then
  ok "digest-pinned release lock passes (C15: ceremony and gate no longer mutually exclusive)"
else
  bad "digest-pinned release lock passes (C15: ceremony and gate no longer mutually exclusive)"
fi

# Case 3: a missing ref still fails — digest-awareness must not weaken the gate.
cat >"$TEST_ROOT/lock-missing" <<'EOF'
postgres    postgres:16
redis       redis:7
keycloak    quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-missing"; then
  bad "missing minio ref fails"
else
  ok "missing minio ref fails"
fi

# Case 4: a digest on the WRONG tag does not satisfy the ref (tag-level comparison, not name-level).
cat >"$TEST_ROOT/lock-wrong-tag" <<'EOF'
postgres    postgres:15@sha256:1111111111111111111111111111111111111111111111111111111111111111
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z
keycloak    quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-wrong-tag"; then
  bad "digest-pinned WRONG tag still fails"
else
  ok "digest-pinned WRONG tag still fails"
fi

printf 'check-compose-images-lock tests: %d ok, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

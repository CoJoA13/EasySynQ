#!/usr/bin/env bash
# Behavioral regressions for scripts/check-compose-images-lock.sh. Each case runs the real gate
# against fixture compose/lock trees. Ordinary Compose refs remain digest-aware, while every
# Keycloak Dockerfile stage must use the exact digest recorded by the unique Keycloak lock entry.
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
FROM quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54 AS builder
FROM quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
}

run_gate() {
  COMPOSE_DIR="$1" IMAGES_LOCK="$2" KEYCLOAK_DOCKERFILE="$1/keycloak/Dockerfile" \
    bash "$SCRIPT" >/dev/null 2>&1
}

compose_dir="$TEST_ROOT/compose"
fixture "$compose_dir"

# Case 1: tag-form Compose lock entries remain valid when Keycloak is exactly digest-bound.
cat >"$TEST_ROOT/lock-tags" <<'EOF'
# service   image:tag
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z   # inline comment survives
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-tags"; then
  ok "tag-form Compose refs pass with an exactly digest-bound Keycloak base"
else
  bad "tag-form Compose refs pass with an exactly digest-bound Keycloak base"
fi

# Case 2: digest-pinned ordinary Compose lock entries still satisfy tag-form Compose refs.
cat >"$TEST_ROOT/lock-digests" <<'EOF'
postgres    postgres:16@sha256:1111111111111111111111111111111111111111111111111111111111111111
redis       redis:7@sha256:2222222222222222222222222222222222222222222222222222222222222222
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z@sha256:3333333333333333333333333333333333333333333333333333333333333333
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-digests"; then
  ok "digest-pinned ordinary Compose refs pass with an exact Keycloak digest"
else
  bad "digest-pinned ordinary Compose refs pass with an exact Keycloak digest"
fi

# Case 3: a missing ref still fails — digest-awareness must not weaken the gate.
cat >"$TEST_ROOT/lock-missing" <<'EOF'
postgres    postgres:16
redis       redis:7
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
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
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-wrong-tag"; then
  bad "digest-pinned WRONG tag still fails"
else
  ok "digest-pinned WRONG tag still fails"
fi

# Case 5: the LOCALLY BUILT application images are exempt (C13). They carry an `image:` name in
# compose.yml only so the air-gap bundle can save and reload them; they are never pulled, so
# demanding a lock entry would make the naming and the gate mutually exclusive.
built_dir="$TEST_ROOT/compose-built"
fixture "$built_dir"
cat >>"$built_dir/compose.yml" <<'EOF'
  api:
    build: {context: ../..}
    image: easysynq/api:${EASYSYNQ_IMAGE_TAG:-dev}
  web:
    build: {context: ../../apps/web}
    image: easysynq/web:${EASYSYNQ_IMAGE_TAG:-dev}
EOF
if run_gate "$built_dir" "$TEST_ROOT/lock-tags"; then
  ok "locally built easysynq/* refs need no lock entry (C13)"
else
  bad "locally built easysynq/* refs need no lock entry (C13)"
fi

# Case 6: the exemption is per-REPOSITORY, not a prefix match — an unrecognised easysynq/* ref is a
# real pulled image and must still be pinned, or a typo'd built name would silently skip the gate.
foreign_dir="$TEST_ROOT/compose-foreign"
fixture "$foreign_dir"
cat >>"$foreign_dir/compose.yml" <<'EOF'
  extra:
    image: easysynq/not-a-built-image:1.0
EOF
if run_gate "$foreign_dir" "$TEST_ROOT/lock-tags"; then
  bad "an unrecognised easysynq/* ref still requires a lock entry"
else
  ok "an unrecognised easysynq/* ref still requires a lock entry"
fi

# Case 7: floating Keycloak stages must not be accepted merely because their tag matches the
# digest-pinned lock entry.
floating_stages_dir="$TEST_ROOT/compose-keycloak-floating"
fixture "$floating_stages_dir"
cat >"$floating_stages_dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7 AS builder
FROM quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$floating_stages_dir" "$TEST_ROOT/lock-tags"; then
  bad "floating Keycloak stages fail against a digest-pinned lock entry"
else
  ok "floating Keycloak stages fail against a digest-pinned lock entry"
fi

# Case 8: a matching Keycloak tag with the wrong digest must fail exact-reference comparison.
wrong_digest_dir="$TEST_ROOT/compose-keycloak-wrong-digest"
fixture "$wrong_digest_dir"
cat >"$wrong_digest_dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa AS builder
FROM quay.io/keycloak/keycloak:26.7@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
EOF
if run_gate "$wrong_digest_dir" "$TEST_ROOT/lock-tags"; then
  bad "matching Keycloak tags with the wrong digest fail"
else
  ok "matching Keycloak tags with the wrong digest fail"
fi

# Case 9: stripping digests must not hide a floating stage beside an exactly pinned stage.
mixed_floating_dir="$TEST_ROOT/compose-keycloak-mixed-floating"
fixture "$mixed_floating_dir"
cat >"$mixed_floating_dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54 AS builder
FROM quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$mixed_floating_dir" "$TEST_ROOT/lock-tags"; then
  bad "one matching and one floating Keycloak stage fails"
else
  ok "one matching and one floating Keycloak stage fails"
fi

# Case 10: a floating Keycloak lock entry cannot bind digest-pinned Dockerfile stages.
cat >"$TEST_ROOT/lock-keycloak-floating" <<'EOF'
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z
keycloak    quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-keycloak-floating"; then
  bad "a floating Keycloak lock entry fails"
else
  ok "a floating Keycloak lock entry fails"
fi

# Case 11: the named Keycloak lock authority must be unique even when duplicate rows agree.
cat >"$TEST_ROOT/lock-keycloak-duplicate" <<'EOF'
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
keycloak    quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-keycloak-duplicate"; then
  bad "duplicate Keycloak lock entries fail"
else
  ok "duplicate Keycloak lock entries fail"
fi

# Case 12: a sha256 marker without a complete lowercase digest is not an immutable lock.
cat >"$TEST_ROOT/lock-keycloak-malformed" <<'EOF'
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z
keycloak    quay.io/keycloak/keycloak:26.7@sha256:abc123
EOF
if run_gate "$compose_dir" "$TEST_ROOT/lock-keycloak-malformed"; then
  bad "a malformed Keycloak digest lock entry fails"
else
  ok "a malformed Keycloak digest lock entry fails"
fi

# Case 13: Dockerfile instructions are case-insensitive, so a lowercase floating FROM must not
# escape comparison when an uppercase builder stage already matches the lock.
mixed_case_dir="$TEST_ROOT/compose-keycloak-mixed-case"
fixture "$mixed_case_dir"
cat >"$mixed_case_dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54 AS builder
from quay.io/keycloak/keycloak:26.7
EOF
if run_gate "$mixed_case_dir" "$TEST_ROOT/lock-tags"; then
  bad "a lowercase floating Keycloak FROM beside a matching stage fails"
else
  ok "a lowercase floating Keycloak FROM beside a matching stage fails"
fi

# Case 14: matching full refs still fail when the tag violates Docker's tag grammar.
invalid_tag_dir="$TEST_ROOT/compose-keycloak-invalid-tag"
fixture "$invalid_tag_dir"
cat >"$invalid_tag_dir/keycloak/Dockerfile" <<'EOF'
FROM quay.io/keycloak/keycloak:26.7/bad@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54 AS builder
FROM quay.io/keycloak/keycloak:26.7/bad@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
cat >"$TEST_ROOT/lock-keycloak-invalid-tag" <<'EOF'
postgres    postgres:16
redis       redis:7
minio       minio/minio:RELEASE.2024-09-13T20-26-02Z
keycloak    quay.io/keycloak/keycloak:26.7/bad@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
EOF
if run_gate "$invalid_tag_dir" "$TEST_ROOT/lock-keycloak-invalid-tag"; then
  bad "matching Keycloak refs with an invalid Docker tag fail"
else
  ok "matching Keycloak refs with an invalid Docker tag fail"
fi

printf 'check-compose-images-lock tests: %d ok, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

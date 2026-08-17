#!/usr/bin/env bash
# Behavioral regression for scripts/gen-contracts.sh. The real generator and launcher are copied
# into a guarded temporary fixture; fake tool executables record their CWD and argv while producing
# deterministic fixture artifacts. This catches callers accidentally selecting their own Git root,
# floating npx execution, missing Redocly config, and timestamped Python models.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GENERATOR="$ROOT/scripts/gen-contracts.sh"
LAUNCHER="$ROOT/scripts/run-contract-tool.sh"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-gen-contracts.XXXXXX")"

cleanup() {
  local expected_parent="${TMPDIR:-/tmp}"
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-gen-contracts.*)
      if [ -d "$TEST_ROOT" ] && [ ! -L "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
      fi
      ;;
  esac
}
trap cleanup EXIT

ok() { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

assert_exit() {
  local label="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want exit $want, got $got)"; fi
}

assert_file_hashes_match() {
  local label="$1" before="$2" after="$3"
  if [ "$before" = "$after" ]; then ok "$label"; else bad "$label (hashes changed)"; fi
}

assert_count() {
  local label="$1" file="$2" needle="$3" want="$4" got
  got="$(grep -Fxc "$needle" "$file" 2>/dev/null || true)"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want $want, got $got)"; fi
}

assert_contains_count() {
  local label="$1" file="$2" needle="$3" want="$4" got
  got="$(grep -Fc -- "$needle" "$file" 2>/dev/null || true)"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want $want, got $got)"; fi
}

assert_all_cwds() {
  local label="$1" file="$2" want="$3" got
  got="$(sed -n 's/^cwd=//p' "$file" | sort -u)"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want $want, got ${got:-<none>})"; fi
}

assert_codegen_formatter_sequence() {
  local label="$1" file="$2"
  if awk '
    /^argv=run datamodel-codegen / {
      if (waiting != 0) bad = 1
      waiting = 1
      codegen += 1
      next
    }
    $0 == "argv=run ruff format src/easysynq_api/_generated/models.py" {
      if (waiting != 1) bad = 1
      waiting = 0
      formatter += 1
      next
    }
    END {
      if (bad || waiting != 0 || codegen != 3 || formatter != 3) exit 1
    }
  ' "$file"; then
    ok "$label"
  else
    bad "$label"
  fi
}

assert_exact_one_final_lf() {
  local label="$1" file="$2"
  if node -e '
    const fs = require("node:fs");
    const bytes = fs.readFileSync(process.argv[1]);
    const length = bytes.length;
    process.exit(
      length > 0 &&
      bytes[length - 1] === 0x0a &&
      (length === 1 || (bytes[length - 2] !== 0x0a && bytes[length - 2] !== 0x0d))
        ? 0
        : 1,
    );
  ' "$file"; then
    ok "$label"
  else
    bad "$label"
  fi
}

hash_artifacts() {
  sha256sum \
    "$FIXTURE/packages/contracts/dist/openapi.json" \
    "$FIXTURE/apps/api/src/easysynq_api/_generated/models.py" \
    "$FIXTURE/apps/web/src/api/_generated/schema.d.ts" | awk '{print $1}' | tr '\n' ' '
}

run_generator() {
  local cwd="$1"
  RUN_CODE=0
  RUN_OUTPUT="$(cd "$cwd" && env PATH="$PATH_BIN:$PATH" bash "$FIXTURE/scripts/gen-contracts.sh" 2>&1)" || RUN_CODE=$?
}

printf '== gen-contracts.sh ==\n'

if [ ! -f "$GENERATOR" ] || [ ! -f "$LAUNCHER" ]; then
  printf 'missing generator or launcher\n' >&2
  exit 2
fi

FIXTURE="$TEST_ROOT/fixture"
RECORDS="$TEST_ROOT/records"
PATH_BIN="$TEST_ROOT/path-bin"
mkdir -p \
  "$FIXTURE/scripts" \
  "$FIXTURE/packages/contracts/node_modules/.bin" \
  "$FIXTURE/apps/api/src/easysynq_api/_generated" \
  "$FIXTURE/apps/web/src/api/_generated" \
  "$RECORDS" \
  "$PATH_BIN"
cp "$GENERATOR" "$FIXTURE/scripts/gen-contracts.sh"
cp "$LAUNCHER" "$FIXTURE/scripts/run-contract-tool.sh"
chmod +x "$FIXTURE/scripts/gen-contracts.sh" "$FIXTURE/scripts/run-contract-tool.sh"
printf 'openapi: 3.1.0\ninfo:\n  title: fixture\n  version: 1.0.0\npaths: {}\n' >"$FIXTURE/packages/contracts/openapi.yaml"
printf 'rules: {}\n' >"$FIXTURE/packages/contracts/redocly.yaml"
mkdir -p "$FIXTURE/packages/contracts/dist"
printf '{"fixture":"contract"}\n' >"$FIXTURE/packages/contracts/dist/openapi.json"
sha256sum "$FIXTURE/packages/contracts/dist/openapi.json" | awk '{print $1}' >"$FIXTURE/packages/contracts/.contract.lock"

cat >"$FIXTURE/packages/contracts/node_modules/.bin/redocly" <<EOF
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'cwd=%s\\n' "\$(pwd -P)"
  printf 'argv=%s\\n' "\$*"
  printf '%s\\n' '--'
} >>"$RECORDS/redocly.log"
case "\${1:-}" in
  lint) exit 0 ;;
  bundle)
    output=''
    while [ "\$#" -gt 0 ]; do
      if [ "\$1" = '-o' ]; then output="\$2"; break; fi
      shift
    done
    mkdir -p "\$(dirname "\$output")"
    printf '%s' '{"fixture":"contract"}' >"\$output"
    ;;
  *) exit 64 ;;
esac
EOF

cat >"$FIXTURE/packages/contracts/node_modules/.bin/openapi-typescript" <<EOF
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'cwd=%s\\n' "\$(pwd -P)"
  printf 'argv=%s\\n' "\$*"
  printf '%s\\n' '--'
} >>"$RECORDS/openapi-typescript.log"
output=''
while [ "\$#" -gt 0 ]; do
  if [ "\$1" = '-o' ]; then output="\$2"; break; fi
  shift
done
mkdir -p "\$(dirname "\$output")"
printf 'export interface Fixture { ok: true }\\n' >"\$output"
EOF

cat >"$PATH_BIN/uv" <<EOF
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'cwd=%s\\n' "\$(pwd -P)"
  printf 'argv=%s\\n' "\$*"
  printf '%s\\n' '--'
} >>"$RECORDS/uv.log"
if [ "\${1:-}" = 'run' ] && [ "\${2:-}" = 'ruff' ] && [ "\${3:-}" = 'format' ]; then
  exit 0
fi
output=''
disable_timestamp=false
while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --output) output="\$2"; shift 2; continue ;;
    --disable-timestamp) disable_timestamp=true ;;
  esac
  shift
done
mkdir -p "\$(dirname "\$output")"
if [ "\$disable_timestamp" = true ]; then
  printf 'class FixtureModel: pass\\n' >"\$output"
else
  count_file="$RECORDS/uv-count"
  count="\$(cat "\$count_file" 2>/dev/null || printf '0')"
  count=\$((count + 1))
  printf '%s\\n' "\$count" >"\$count_file"
  printf '# generated-at=%s\\nclass FixtureModel: pass\\n' "\$count" >"\$output"
fi
EOF

cat >"$PATH_BIN/npx" <<EOF
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'cwd=%s\\n' "\$(pwd -P)"
  printf 'argv=%s\\n' "\$*"
  printf '%s\\n' '--'
} >>"$RECORDS/npx.log"
if [ "\${1:-}" = '--yes' ] && [ "\${2:-}" = '@redocly/cli' ]; then
  shift 2
  exec "$FIXTURE/packages/contracts/node_modules/.bin/redocly" "\$@"
fi
if [ "\${1:-}" = '--yes' ] && [ "\${2:-}" = 'openapi-typescript' ]; then
  shift 2
  exec "$FIXTURE/packages/contracts/node_modules/.bin/openapi-typescript" "\$@"
fi
exit 64
EOF
chmod +x "$FIXTURE/packages/contracts/node_modules/.bin/redocly" \
  "$FIXTURE/packages/contracts/node_modules/.bin/openapi-typescript" \
  "$PATH_BIN/uv" "$PATH_BIN/npx"

UNRELATED="$TEST_ROOT/unrelated-repo"
mkdir -p "$UNRELATED"
git -C "$UNRELATED" init -q

run_generator "$FIXTURE"
assert_exit "root caller completes" 0 "$RUN_CODE"
assert_exact_one_final_lf \
  "bundled JSON ends in exactly one LF after a no-newline Redocly output" \
  "$FIXTURE/packages/contracts/dist/openapi.json"
ROOT_HASHES="$(hash_artifacts)"

run_generator "$FIXTURE/packages/contracts"
assert_exit "packages/contracts caller completes" 0 "$RUN_CODE"
PACKAGES_HASHES="$(hash_artifacts)"

run_generator "$UNRELATED"
assert_exit "unrelated initialized Git repository caller completes" 0 "$RUN_CODE"
UNRELATED_HASHES="$(hash_artifacts)"

assert_file_hashes_match "two full fixture generations are byte-stable" "$ROOT_HASHES" "$PACKAGES_HASHES"
assert_file_hashes_match "unrelated Git caller still writes fixture artifacts" "$PACKAGES_HASHES" "$UNRELATED_HASHES"
assert_all_cwds "Redocly always runs at the fixture root" "$RECORDS/redocly.log" "$FIXTURE"
assert_all_cwds "OpenAPI TypeScript always runs at the fixture root" "$RECORDS/openapi-typescript.log" "$FIXTURE"
assert_all_cwds "datamodel-codegen always runs in fixture apps/api" "$RECORDS/uv.log" "$FIXTURE/apps/api"
assert_count "lint receives exact repository-relative config and spec paths" "$RECORDS/redocly.log" "argv=lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml" 3
assert_count "bundle receives exact repository-relative config and output paths" "$RECORDS/redocly.log" "argv=bundle --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml -o packages/contracts/dist/openapi.json" 3
assert_count "OpenAPI TypeScript receives repository-relative input and output paths" "$RECORDS/openapi-typescript.log" "argv=packages/contracts/dist/openapi.json -o apps/web/src/api/_generated/schema.d.ts" 3
assert_contains_count "datamodel-codegen disables timestamps" "$RECORDS/uv.log" "--disable-timestamp" 3
assert_contains_count "generated Python declares only its three required Ruff exemptions" "$RECORDS/uv.log" "--custom-file-header # ruff: noqa: E501, RUF001, S105" 3
assert_count "generated Python is normalized through the repository Ruff formatter" "$RECORDS/uv.log" "argv=run ruff format src/easysynq_api/_generated/models.py" 3
assert_codegen_formatter_sequence "each datamodel codegen is immediately followed by exactly one Ruff formatter" "$RECORDS/uv.log"
if [ ! -e "$RECORDS/npx.log" ]; then ok "Redocly and OpenAPI TypeScript never execute through npx"; else bad "Redocly and OpenAPI TypeScript never execute through npx"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

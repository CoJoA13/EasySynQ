#!/usr/bin/env bash
# Exercise the real gate with synthetic reports; only the external scanner is replaced.
# Removing either scan, accepting empty evidence, dropping tooling/no-fix records, swallowing
# scanner/parser errors, or printing private report fields must break these contracts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
RUNNER="$ROOT/scripts/check-built-image-security.sh"
REAL_JQ="$(command -v jq)"
TEST_ROOT="$(mktemp -d /tmp/easysynq-image-security-test.XXXXXX)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
PASS=0
FAIL=0
mkdir -p "$TEST_ROOT/bin" "$TEST_ROOT/caller"
printf '%s\n' 'FIXTURE_PRIVATE_FINDING' >"$TEST_ROOT/caller/.trivyignore"
printf '%s\n' 'ignore-unfixed: true' 'scanners: [secret]' >"$TEST_ROOT/caller/trivy.yaml"
printf '%s\n' 'allow-rules:' '  - id: synthetic-hide-all' '    path: .*' \
  >"$TEST_ROOT/caller/trivy-secret.yaml"

ok() { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

cat >"$TEST_ROOT/bin/trivy" <<'FAKE_TRIVY'
#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = image ] || exit 91
shift
output= image_src= scanners= severity= exit_code= format= coverage= target= config= ignorefile= offline=
secret_config=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --image-src) image_src="$2"; shift 2 ;;
    --scanners) scanners="$2"; shift 2 ;;
    --severity) severity="$2"; shift 2 ;;
    --exit-code) exit_code="$2"; shift 2 ;;
    --format) format="$2"; shift 2 ;;
    --pkg-types|--vuln-type) coverage="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    --ignorefile) ignorefile="$2"; shift 2 ;;
    --secret-config) secret_config="$2"; shift 2 ;;
    --offline-scan) offline=1; shift ;;
    --quiet) shift ;;
    --*) exit 92 ;;
    *) [ -z "$target" ] || exit 93; target="$1"; shift ;;
  esac
done
[ "$image_src" = docker ] && [ "$scanners" = vuln,secret ] || exit 94
[ "$severity" = HIGH,CRITICAL ] && [ "$exit_code" = 0 ] || exit 95
[ "$format" = json ] && [ "$coverage" = os,library ] && [ -n "$output" ] || exit 96
[ "$offline" = 1 ] || exit 96
[ -f "$config" ] && [ "$(cat "$config")" = '{}' ] || exit 98
[ -f "$ignorefile" ] && [ ! -s "$ignorefile" ] || exit 98
while IFS= read -r variable; do
  case "$variable" in
    TRIVY_DB_REPOSITORY|TRIVY_JAVA_DB_REPOSITORY|TRIVY_CHECKS_BUNDLE_REPOSITORY|TRIVY_SKIP_VERSION_CHECK) ;;
    *) exit 99 ;;
  esac
done < <(compgen -e TRIVY_)
[ "$TRIVY_DB_REPOSITORY" = docker.io/aquasec/trivy-db:2 ] || exit 99
[ "$TRIVY_JAVA_DB_REPOSITORY" = docker.io/aquasec/trivy-java-db:1 ] || exit 99
[ "$TRIVY_CHECKS_BUNDLE_REPOSITORY" = docker.io/aquasec/trivy-checks:1 ] || exit 99
[ "$TRIVY_SKIP_VERSION_CHECK" = true ] || exit 99
case "$target" in
  easysynq-api:scan) component=api ;;
  easysynq-web:scan) component=web ;;
  *) exit 97 ;;
esac
printf '%s\n' "$target" >>"$IMAGE_FIXTURE_DIR/targets"
printf '%s\n' "$output" >>"$IMAGE_FIXTURE_DIR/outputs"
stat -c '%a' "$(dirname "$output")" >>"$IMAGE_FIXTURE_DIR/directory-modes"
printf '%s\n' FIXTURE_PRIVATE_SCANNER_STDOUT
printf '%s\n' FIXTURE_PRIVATE_SCANNER_STDERR >&2
scenario="$(cat "$IMAGE_FIXTURE_DIR/$component.scenario")"
case "$scenario" in
  caller_secret_config)
    if [ -f "$secret_config" ] && [ "$(cat "$secret_config")" = '{}' ]; then
      cp "$IMAGE_FIXTURE_DIR/$component.json" "$output"
    else
      "$IMAGE_FIXTURE_REAL_JQ" 'del(.Results[].Secrets)' "$IMAGE_FIXTURE_DIR/$component.json" >"$output"
    fi ;;
  missing_report) exit 0 ;;
  empty_report) : >"$output" ;;
  malformed_json) printf '%s\n' 'FIXTURE_PRIVATE_NOT_JSON' >"$output" ;;
  extra_json_document) cat "$IMAGE_FIXTURE_DIR/$component.json" "$IMAGE_FIXTURE_DIR/$component.json" >"$output" ;;
  report_directory) mkdir "$output" ;;
  report_symlink) ln -s "$IMAGE_FIXTURE_DIR/$component.json" "$output" ;;
  write_failure) printf '{}' >"$output/missing/report.json"; exit 98 ;;
  *) cp "$IMAGE_FIXTURE_DIR/$component.json" "$output" ;;
esac
if [ -f "$output" ] && [ ! -L "$output" ]; then
  stat -c '%a' "$output" >>"$IMAGE_FIXTURE_DIR/report-modes"
fi
[ "$scenario" != scanner_failure ] || exit 37
FAKE_TRIVY

cat >"$TEST_ROOT/bin/jq" <<'FAKE_JQ'
#!/usr/bin/env bash
if [ "${IMAGE_FIXTURE_PARSER_FAILURE:-0}" = 1 ]; then
  echo FIXTURE_PRIVATE_PARSER_FAILURE >&2
  exit 19
fi
exec "$IMAGE_FIXTURE_REAL_JQ" "$@"
FAKE_JQ
chmod 755 "$TEST_ROOT/bin/trivy" "$TEST_ROOT/bin/jq"

make_report() {
  local component="$1" scenario="$2" destination="$3" mutation='.'
  # All identities, package names, versions and findings here are invented fixture data.
  case "$scenario" in
    absent_collections) mutation='del(.Results[0].Vulnerabilities)' ;;
    fixed_high) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH", FixedVersion:"2.0", PkgName:"FIXTURE_PRIVATE_OS"}]' ;;
    fixed_critical) mutation='.Results[0].Vulnerabilities = [{Severity:"CRITICAL", FixedVersion:"2.0"}]' ;;
    tooling_high) mutation='.Results += [{Target:"/usr/local/lib/node_modules/FIXTURE_PRIVATE_TOOL", Class:"lang-pkgs", Type:"node-pkg", Vulnerabilities:[{Severity:"HIGH", FixedVersion:"2.0"}]}]' ;;
    library_critical) mutation='.Results += [{Target:"/app/FIXTURE_PRIVATE_LIBRARY", Class:"lang-pkgs", Type:"python-pkg", Vulnerabilities:[{Severity:"CRITICAL", FixedVersion:"2.0"}]}]' ;;
    no_fix) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH"}, {Severity:"CRITICAL", FixedVersion:""}]' ;;
    mixed) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH", FixedVersion:"2.0"}, {Severity:"CRITICAL"}] | .Results += [{Target:"/app/FIXTURE_PRIVATE_LIBRARY", Class:"lang-pkgs", Type:"node-pkg", Vulnerabilities:[{Severity:"CRITICAL", FixedVersion:"2.0"}, {Severity:"HIGH", FixedVersion:""}]}]' ;;
    secret_only|caller_secret_config) mutation='.Results += [{Target:"/app/FIXTURE_PRIVATE_SECRET", Class:"secret", Type:"secret", Secrets:[{Severity:"HIGH", Match:"FIXTURE_PRIVATE_SECRET_CONTENT"}]}]' ;;
    secret_without_type) mutation='.Results += [{Target:"/app/FIXTURE_PRIVATE_SECRET", Class:"secret", Secrets:[{Severity:"HIGH", Match:"FIXTURE_PRIVATE_SECRET_CONTENT"}]}]' ;;
    secret_null_type) mutation='.Results += [{Target:"/app/FIXTURE_PRIVATE_SECRET", Class:"secret", Type:null, Secrets:[{Severity:"HIGH"}]}]' ;;
    secret_nonstring_type) mutation='.Results += [{Target:"/app/FIXTURE_PRIVATE_SECRET", Class:"secret", Type:7, Secrets:[{Severity:"HIGH"}]}]' ;;
    package_without_type) mutation='del(.Results[0].Type)' ;;
    empty_object) mutation='{}' ;;
    empty_results) mutation='.Results = []' ;;
    missing_results) mutation='del(.Results)' ;;
    null_results) mutation='.Results = null' ;;
    object_results) mutation='.Results = {}' ;;
    empty_result) mutation='.Results = [{}]' ;;
    no_os_result) mutation='.Results = [{Target:"/app/synthetic", Class:"lang-pkgs", Type:"node-pkg"}]' ;;
    wrong_schema) mutation='.SchemaVersion = 99' ;;
    wrong_artifact_type) mutation='.ArtifactType = "filesystem"' ;;
    wrong_identity) mutation='.ArtifactName = "synthetic-unrelated:scan"' ;;
    missing_metadata) mutation='del(.Metadata)' ;;
    missing_image_id) mutation='del(.Metadata.ImageID)' ;;
    malformed_image_id) mutation='.Metadata.ImageID = "synthetic"' ;;
    missing_os) mutation='del(.Metadata.OS)' ;;
    null_vulnerabilities) mutation='.Results[0].Vulnerabilities = null' ;;
    object_vulnerabilities) mutation='.Results[0].Vulnerabilities = {}' ;;
    null_vulnerability) mutation='.Results[0].Vulnerabilities = [null]' ;;
    missing_severity) mutation='.Results[0].Vulnerabilities = [{FixedVersion:"2.0"}]' ;;
    invalid_severity) mutation='.Results[0].Vulnerabilities = [{Severity:"high", FixedVersion:"2.0"}]' ;;
    nonstring_severity) mutation='.Results[0].Vulnerabilities = [{Severity:7, FixedVersion:"2.0"}]' ;;
    null_fixed) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH", FixedVersion:null}]' ;;
    nonstring_fixed) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH", FixedVersion:7}]' ;;
    whitespace_fixed) mutation='.Results[0].Vulnerabilities = [{Severity:"HIGH", FixedVersion:" \t"}]' ;;
    invalid_second_result) mutation='.Results += [{Target:"/app/synthetic", Class:"lang-pkgs", Type:"node-pkg", Vulnerabilities:{}}]' ;;
    null_secrets) mutation='.Results[0].Secrets = null' ;;
  esac
  "$REAL_JQ" -n --arg target "easysynq-$component:scan" '
    {SchemaVersion:2, ArtifactName:$target, ArtifactType:"container_image",
     Metadata:{ImageID:("sha256:" + ("a" * 64)), OS:{Family:"debian", Name:"synthetic"}},
     Results:[{Target:"FIXTURE_PRIVATE_OS", Class:"os-pkgs", Type:"debian", Vulnerabilities:[]}]}
  ' | "$REAL_JQ" "$mutation" >"$destination"
}

run_case() {
  local label="$1" api_scenario="$2" web_scenario="$3" want_status="$4"
  local api_counts="${5:-}" web_counts="${6:-}" parser_failure="${7:-0}"
  local fixture="$TEST_ROOT/$label" status output path mode
  mkdir -p "$fixture"
  make_report api "$api_scenario" "$fixture/api.json"
  make_report web "$web_scenario" "$fixture/web.json"
  printf '%s\n' "$api_scenario" >"$fixture/api.scenario"
  printf '%s\n' "$web_scenario" >"$fixture/web.scenario"
  status=0
  (
    cd "$TEST_ROOT/caller"
    PATH="$TEST_ROOT/bin:$PATH" IMAGE_FIXTURE_DIR="$fixture" \
      IMAGE_FIXTURE_REAL_JQ="$REAL_JQ" IMAGE_FIXTURE_PARSER_FAILURE="$parser_failure" \
      TRIVY_DB_REPOSITORY=docker.io/aquasec/trivy-db:2 \
      TRIVY_JAVA_DB_REPOSITORY=docker.io/aquasec/trivy-java-db:1 \
      TRIVY_CHECKS_BUNDLE_REPOSITORY=docker.io/aquasec/trivy-checks:1 \
      TRIVY_SKIP_VERSION_CHECK=true TRIVY_IGNORE_UNFIXED=true TRIVY_IGNORE_STATUS=fixed \
      TRIVY_SKIP_DIRS=/ TRIVY_SEVERITY=LOW TRIVY_SCANNERS=secret TRIVY_VEX=fixture \
      bash "$RUNNER"
  ) >"$fixture/stdout" 2>"$fixture/stderr" || status=$?
  if [ "$status" -eq "$want_status" ]; then ok "$label status"; else bad "$label status ($status; expected $want_status)"; fi
  output="$(cat "$fixture/stdout" "$fixture/stderr")"
  case "$output" in *FIXTURE_PRIVATE*) bad "$label private data leaked" ;; *) ok "$label output privacy" ;; esac
  if [ "$want_status" -eq 2 ]; then
    case "$output" in *'threshold PASS'*) bad "$label emitted a pass verdict" ;; *) ok "$label has no pass verdict" ;; esac
  else
    if [ "$(cat "$fixture/targets" 2>/dev/null || :)" = $'easysynq-api:scan\neasysynq-web:scan' ]; then
      ok "$label scans both built images once with full coverage"
    else
      bad "$label scans both built images once with full coverage"
    fi
    case "$output" in *"api $api_counts"*) ok "$label api counts" ;; *) bad "$label api counts" ;; esac
    case "$output" in *"web $web_counts"*) ok "$label web counts" ;; *) bad "$label web counts" ;; esac
    case "$output" in *'not image-security clearance'*) ok "$label bounded verdict" ;; *) bad "$label bounded verdict" ;; esac
    if [ "$api_scenario" = no_fix ] || [ "$web_scenario" = no_fix ]; then
      case "$output" in *OPEN*) ok "$label unresolved findings stay OPEN" ;; *) bad "$label unresolved findings stay OPEN" ;; esac
    fi
  fi
  if [ -f "$fixture/outputs" ]; then
    while IFS= read -r path; do
      case "$path" in "$ROOT"/*) bad "$label report path is in checkout" ;; esac
      if [ -e "$(dirname "$path")" ]; then bad "$label temporary report/log directory remains"; else ok "$label temporary directory removed"; fi
    done <"$fixture/outputs"
    while IFS= read -r mode; do
      if [ "$mode" = 700 ]; then ok "$label private directory permissions"; else bad "$label directory mode $mode"; fi
    done <"$fixture/directory-modes"
    if [ -f "$fixture/report-modes" ]; then
      while IFS= read -r mode; do
        if [ "$mode" = 600 ]; then ok "$label private report permissions"; else bad "$label report mode $mode"; fi
      done <"$fixture/report-modes"
    fi
  fi
}

clean='blocked_fixed=0 open_no_fix=0 advisory_other=0'
fixed='blocked_fixed=1 open_no_fix=0 advisory_other=0'
open='blocked_fixed=0 open_no_fix=2 advisory_other=0'
mixed='blocked_fixed=2 open_no_fix=2 advisory_other=0'
secret='blocked_fixed=0 open_no_fix=0 advisory_other=1'

run_case clean clean clean 0 "$clean" "$clean"
run_case absent_collections absent_collections clean 0 "$clean" "$clean"
run_case fixed_high_api fixed_high clean 1 "$fixed" "$clean"
run_case fixed_critical_api fixed_critical clean 1 "$fixed" "$clean"
run_case fixed_high_web clean fixed_high 1 "$clean" "$fixed"
run_case fixed_critical_web clean fixed_critical 1 "$clean" "$fixed"
run_case global_tooling tooling_high clean 1 "$fixed" "$clean"
run_case language_library clean library_critical 1 "$clean" "$fixed"
run_case open_findings no_fix no_fix 0 "$open" "$open"
run_case mixed_findings mixed mixed 1 "$mixed" "$mixed"
run_case secret_advisory secret_only clean 0 "$secret" "$clean"
run_case secret_without_type secret_without_type clean 0 "$secret" "$clean"
run_case caller_secret_config caller_secret_config clean 0 "$secret" "$clean"

for scenario in scanner_failure missing_report empty_report malformed_json extra_json_document report_directory \
  report_symlink write_failure empty_object empty_results missing_results null_results \
  object_results empty_result no_os_result wrong_schema wrong_artifact_type wrong_identity \
  missing_metadata missing_image_id malformed_image_id missing_os null_vulnerabilities \
  object_vulnerabilities null_vulnerability missing_severity invalid_severity nonstring_severity \
  null_fixed nonstring_fixed whitespace_fixed invalid_second_result null_secrets \
  secret_null_type secret_nonstring_type package_without_type; do
  run_case "$scenario" "$scenario" clean 2
done
run_case second_scan_failure clean scanner_failure 2
run_case invalid_web_evidence clean empty_results 2
run_case parser_failure clean clean 2 '' '' 1

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# Scan the two built CI artifacts. Only aggregate counts may leave this process.
set -euo pipefail
umask 077

fail() {
  printf 'Built-image security error: %s\n' "$1" >&2
  exit 2
}

[ "$#" -eq 0 ] || fail 'this gate takes no arguments'
scan_tmp="$(mktemp -d /tmp/easysynq-image-security.XXXXXX 2>/dev/null)" \
  || fail 'temporary directory setup failed'

cleanup() {
  local status=$?
  trap - EXIT
  if ! rm -rf -- "$scan_tmp" >/dev/null 2>&1; then
    fail 'temporary evidence cleanup failed'
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 2' HUP INT TERM

# Trivy also reads environment settings and caller config/ignore files. None may silently
# narrow this scan. Keep only the existing CI database and version-check settings.
for variable in "${!TRIVY_@}"; do
  case "$variable" in
    TRIVY_DB_REPOSITORY|TRIVY_JAVA_DB_REPOSITORY|TRIVY_CHECKS_BUNDLE_REPOSITORY|TRIVY_SKIP_VERSION_CHECK) ;;
    *) unset "$variable" || fail 'scanner environment setup failed' ;;
  esac
done
if ! {
  printf '{}\n' >"$scan_tmp/trivy.yaml" &&
  : >"$scan_tmp/empty.ignore"
} 2>/dev/null; then
  fail 'private scan configuration setup failed'
fi

blocked_counts=()
open_counts=()
other_counts=()
for component in api web; do
  target="easysynq-$component:scan"
  report="$scan_tmp/$component.json"
  if ! {
    trivy image --image-src docker --offline-scan --scanners vuln,secret --pkg-types os,library \
      --severity HIGH,CRITICAL --exit-code 0 --format json \
      --config "$scan_tmp/trivy.yaml" --ignorefile "$scan_tmp/empty.ignore" \
      --secret-config "$scan_tmp/trivy.yaml" \
      --output "$report" "$target" >"$scan_tmp/$component.log" 2>&1
  } 2>/dev/null; then
    fail "$component scanner failed"
  fi
  if [ ! -f "$report" ] || [ ! -s "$report" ] || [ ! -r "$report" ] || [ -L "$report" ]; then
    fail "$component report missing or unreadable"
  fi

  # Slurp to reject empty input and multiple JSON documents. A successful scan must contain
  # actual image and Debian OS evidence; optional clean result collections are valid only
  # inside that evidence. Never default an unsupported report shape to zero findings.
  if ! counts="$(jq -ers --arg target "$target" '
    def nonempty_string: type == "string" and length > 0;
    def severity: . == "HIGH" or . == "CRITICAL";
    def vulnerability:
      type == "object"
      and (.Severity | severity)
      and (if has("FixedVersion") then
        (.FixedVersion | type == "string" and (. == "" or test("\\S")))
        else true end);
    def result:
      type == "object"
      and (.Target | nonempty_string)
      and ((.Class == "secret" and (has("Type") | not)) or (.Type | nonempty_string))
      and (.Class == "os-pkgs" or .Class == "lang-pkgs" or .Class == "secret")
      and ((has("Vulnerabilities") | not)
           or (.Vulnerabilities | type == "array" and all(.[]; vulnerability)))
      and ((has("Secrets") | not)
           or (.Secrets | type == "array" and all(.[];
             type == "object" and (.Severity | severity))));
    if length != 1 then error("report document count") else .[0] end
    | if (
        type == "object"
        and .SchemaVersion == 2
        and .ArtifactType == "container_image"
        and .ArtifactName == $target
        and (.Metadata | type == "object")
        and (.Metadata.ImageID | type == "string" and test("^sha256:[0-9a-f]{64}$"))
        and (.Metadata.OS | type == "object")
        and .Metadata.OS.Family == "debian"
        and (.Metadata.OS.Name | nonempty_string)
        and (.Results | type == "array" and length > 0 and all(.[]; result))
        and any(.Results[]; .Class == "os-pkgs" and .Type == "debian")
      ) then . else error("unsupported image report") end
    | [.Results[] | .Vulnerabilities[]?] as $vulnerabilities
    | [
        ($vulnerabilities | map(select(.FixedVersion != null and .FixedVersion != "")) | length),
        ($vulnerabilities | map(select(.FixedVersion == null or .FixedVersion == "")) | length),
        ([.Results[] | .Secrets[]?] | length)
      ] | @tsv
  ' "$report" 2>/dev/null)"; then
    fail "$component report validation failed"
  fi
  if [[ "$counts" =~ ^([0-9]+)$'\t'([0-9]+)$'\t'([0-9]+)$ ]]; then
    blocked_counts+=("${BASH_REMATCH[1]}")
    open_counts+=("${BASH_REMATCH[2]}")
    other_counts+=("${BASH_REMATCH[3]}")
  else
    fail "$component report summary failed"
  fi
done

# Both reports are valid before any findings verdict. Remove private evidence before emitting
# a pass so a cleanup failure cannot leave a successful-looking security summary.
if ! rm -rf -- "$scan_tmp" >/dev/null 2>&1; then
  fail 'temporary evidence cleanup failed'
fi
trap - EXIT HUP INT TERM

status=0
verdict=PASS
open_note=''
if [ "${blocked_counts[0]}" -gt 0 ] || [ "${blocked_counts[1]}" -gt 0 ]; then
  status=1
  verdict=BLOCKED
fi
if [ "${open_counts[0]}" -gt 0 ] || [ "${open_counts[1]}" -gt 0 ]; then
  open_note=$'OPEN no-fix findings require further review.\n'
fi
if ! printf 'api blocked_fixed=%s open_no_fix=%s advisory_other=%s\nweb blocked_fixed=%s open_no_fix=%s advisory_other=%s\n%sBuilt-image fixed-version threshold %s; not image-security clearance.\n' \
  "${blocked_counts[0]}" "${open_counts[0]}" "${other_counts[0]}" \
  "${blocked_counts[1]}" "${open_counts[1]}" "${other_counts[1]}" "$open_note" "$verdict"; then
  fail 'summary output failed'
fi
exit "$status"

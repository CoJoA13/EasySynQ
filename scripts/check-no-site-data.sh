#!/usr/bin/env bash
# R61 backstop — refuse real site data in tracked files.
#
#   ./scripts/check-no-site-data.sh [path ...]     # default: all tracked files
#
# R61 (decisions-register) forbids committing site-specific operational records: real account
# names, hostnames, addresses, vendor products and versions, share/bucket names, per-install
# fingerprints, or a named site's weakness list. A deployment record written honestly IS a
# reconnaissance profile, and repository visibility is a setting rather than a control.
#
# This is a BACKSTOP, not the control. It catches the mechanical shapes — addresses, `.local`
# FQDNs, MACs — because those are cheap to match. It cannot see an account list, a vendor
# inventory, or a weakness summary. Passing this check does not mean a document is safe to commit;
# read R61 and sanitize at write time.
#
# Sanctioned placeholders (RFC 5737 / RFC 2606 style):
#   10.0.0.0/24 · 192.0.2.0/24 · 198.51.100.0/24 · 203.0.113.0/24
#   example.local · example.com · ORG_EXAMPLE · DC01 · 00:15:5D:00:00:01
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
report() { fail=1; printf '\n%s\n' "$1"; shift; printf '  %s\n' "$@"; }

if [ $# -gt 0 ]; then
  mapfile -t FILES < <(git ls-files -- "$@")
else
  # EVERY tracked file — binaries are skipped by `grep -I` below, not by an extension allowlist
  # (an allowlist silently exempts JSON/TOML/HTML/Dockerfiles, exactly where a stray IP hides).
  # Skip only this script, which quotes the patterns it hunts for. Textual locks can contain
  # installation endpoints too; machine-written content is not a reason to leave it uninspected.
  mapfile -t FILES < <(git ls-files \
    | grep -vE -- '^scripts/check-no-site-data\.sh$')
fi
[ ${#FILES[@]} -gt 0 ] || exit 0

# --- personal home/profile paths --------------------------------------------------------------
# Repository instructions must be portable. A literal shell user's home path is both identity
# data and a reliable workstation fingerprint. The appliance service account path is shipped
# product structure, not an operator identity, so it is the one sanctioned literal.
hits="$(grep -nHoIE '(^|[^A-Za-z0-9._-])/(var/)?home/[A-Za-z0-9._-]+' -- "${FILES[@]}" 2>/dev/null \
  | grep -vE -- '/home/easysynq($|/)' || true)"
[ -z "$hits" ] || report "Personal Linux home path (R61) — use a repo-relative path or <repo>:" $hits

hits="$(grep -nHoIE '([A-Za-z]:[/\\]|/[A-Za-z]/)Users[/\\][A-Za-z0-9._-]+' -- "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Personal Windows profile path (R61) — use <repo> or C:\\dev\\EasySynQ:" $hits

# Development-host observations age badly and can disclose an operator's platform, installed tools,
# or local constraints. Historical plans should name a generic development environment instead.
hits="$(grep -nHoIE '\b(this|current) (Linux |Windows |native-Windows )?(box|machine|workstation)\b|\bowner.?s (primary|native|development) (box|machine|workstation)\b|\bthis live (install|box)\b|ORG_EXAMPLE dogfood|populated ORG_EXAMPLE|\bactual users\b' -- "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Installation/workstation-specific prose (R61) — generalize the environment:" $hits

# --- email addresses -------------------------------------------------------------------------
# Email-shaped fixtures must use IANA/RFC-reserved example domains. The one external no-reply
# address below is commit-tool metadata, not an installation identity.
hits="$(grep -nHoIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- "${FILES[@]}" 2>/dev/null \
  | grep -viE -- '@(([^@]+\.)?example\.(com|net|org|local|test|invalid)|[a-z0-9.-]+\.(example|test|invalid)|easysynq\.local)$|:noreply@anthropic\.com$' || true)"
[ -z "$hits" ] || report "Non-example email address (R61) — use an example.com address:" $hits

# --- known site identifiers -----------------------------------------------------------------
# A deployment dossier remains a site record even after its nouns are replaced with placeholders.
# Catch the exact narrative markers from the incident so token substitution cannot make the same
# semantic failure look clean again. This stays deliberately narrow; human review remains the
# control for new prose shapes.
hits="$(grep -nHoIE '\bthis is a site-specific deployment (design|plan|record)\b|\bevery fact below was verified on the machine\b|\bthe install is live; what follows is what remains\b|\bomitting the untracked site overlay\b|\bbackup chain [—-] proven with real archives\b|\bthe outstanding go-live security gates (are|remain) site state\b' -- "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Site-deployment dossier prose (R61) — retain only generalized lessons:" $hits

# The repository owner is public GitHub metadata, but the same token used as a shell account or
# workstation identity is not. Derive it from the remote rather than committing an incident-token
# hash: low-entropy org/user identifiers are dictionary-recoverable from an unsalted digest.
repo_owner=""
if origin_url="$(git remote get-url origin 2>/dev/null)"; then
  repo_owner="$(printf '%s\n' "$origin_url" \
    | sed -nE 's#^(https://github\.com/|git@github\.com:)([A-Za-z0-9-]+)/EasySynQ(\.git)?$#\2#p' \
    | head -n 1)"
fi
if [ -n "$repo_owner" ]; then
  # Keep this scan inside the shell toolchain already required by the gate. In particular, the
  # contracts job runs before language setup and Git Bash does not guarantee a global `python3`.
  # First remove legitimate repository-metadata shapes, then match the owner as a complete token;
  # report only file + line so the diagnostic cannot republish the identity it rejected. The
  # project's own container packages are one such shape: `ghcr.io/<owner>/easysynq/<image>`, the
  # repository path in the lowercase form GHCR requires. Only that path segment is exempt.
  hits="$(grep -nHIiF -- "$repo_owner" "${FILES[@]}" 2>/dev/null \
    | sed -E 's#(https://github\.com/|git@github\.com:)[^/[:space:]]+/EasySynQ[^[:space:])>]*##g; s#`?[^/[:space:]`]+/EasySynQ`?##g; s#ghcr\.io/[^/[:space:]]+/easysynq/##g; s#(^|[^A-Za-z0-9_-])Owner:\*{0,2}[[:space:]]*[A-Za-z0-9_-]+#\1#g' \
    | grep -iE -- "(^|[^A-Za-z0-9_-])${repo_owner}([^A-Za-z0-9_-]|$)" \
    | sed -E 's#^([^:]+:[0-9]+):.*#\1:<redacted-personal-identifier>#' || true)"
  [ -z "$hits" ] || report "Repository-owner token used as a personal identifier (R61):" $hits
fi

# --- IPv4 addresses (public OR private) outside the sanctioned set ---------------------------
# ⚠ Must match all FOUR octets. A three-octet pattern makes every ISO clause number (10.2.1,
# 9.1.3) look like an address — in a QMS repository that is most of the false positives, and a
# check that cries wolf gets switched off. R61 covers ALL real addresses, not just RFC1918 — a
# WAN endpoint is site data too. Sanctioned: 10.0.0.x (the worked example) · 10.99.99.99 (the
# established `ip_allow` test address) · the RFC 5737 documentation ranges · loopback ·
# 0.0.0.0 (bind-all) · 255.x netmask/broadcast literals (structure, not an address) ·
# 8.8.8.8/8.8.4.4 (well-known anycast resolvers, placeholder-grade) · the FOUR-part ISO 9001:2015
# clause numbers (7.1.5.1/7.1.5.2/8.2.3.1/8.2.3.2 — the complete set in the standard) ·
# 3.3.1.0 (the apache/tika image pin; a version bump that reds here is extended deliberately) ·
# 172.16.0.0 (the pinned `internal` subnet from infra/compose/compose.yml, named as the shipped
# TRUSTED_PROXY_CIDRS default — repo-defined container topology, not a site address; it sits below
# the daemon's own allocation pool so it cannot collide). Only that exact base is sanctioned: any
# other 172.16.x.y still reds, so a real address cannot ride in. The public click-plugins
# four-part version is sanctioned only in a uv.lock package declaration with its exact name and
# public PyPI source, or its exact public Python distribution URL. Mask only that occurrence,
# not a line, endpoint, host, or whole lockfile.
qualified_public_version_lines() {
  local path="$1" text number=0 state=0 version_line=0 multiline=0 array_open=0 unsupported=0
  local blank='^[[:blank:]]*(#.*)?$' bracket='^[[:blank:]]*\['
  local table='^[[:blank:]]*(\[[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\]|\[\[[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\]\])[[:blank:]]*(#.*)?$'
  local header='^[[:blank:]]*\[\[package\]\][[:blank:]]*(#.*)?$'
  local name='^[[:blank:]]*name[[:blank:]]*=[[:blank:]]*"click-plugins"[[:blank:]]*(#.*)?$'
  local version='^[[:blank:]]*version[[:blank:]]*=[[:blank:]]*"1\.1\.1\.2"[[:blank:]]*(#.*)?$'
  local source='^[[:blank:]]*source[[:blank:]]*=[[:blank:]]*\{[[:blank:]]*registry[[:blank:]]*=[[:blank:]]*"https://pypi\.org/simple"[[:blank:]]*\}[[:blank:]]*(#.*)?$'
  local identity="^[[:blank:]]*['\"]?(name|version|source)['\"]?[[:blank:]]*="
  local opener='^[[:blank:]]*[A-Za-z0-9_-]+[[:blank:]]*=[[:blank:]]*\[[[:blank:]]*(#.*)?$'
  local closer='^[[:blank:]]*\][[:blank:]]*(#.*)?$'
  local plain='[A-Za-z0-9_.[:blank:],:=+*/%<>!()-]'
  local double='"[^][#"\\]*"'
  local single="'[^][#'\\\\]*'"
  local object="\{(${plain}|${double}|${single})*\}"
  local inline="^[[:blank:]]*[A-Za-z0-9_-]+[[:blank:]]*=[[:blank:]]*\[(${plain}|${double}|${single}|${object})*\][[:blank:]]*(#.*)?$"
  local -a approved=()
  [ -f "$path" ] && [ -r "$path" ] || return 0
  # Read context once. Defer qualification until the table closes so duplicate identity/source
  # keys cannot borrow an earlier public declaration. Multiline examples are conservatively denied.
  while IFS= read -r text || [ -n "$text" ]; do
    number=$((number + 1))
    if [[ "$text" == *'"""'* || "$text" == *"'''"* ]]; then multiline=1; fi
    if [[ "$text" =~ $blank ]]; then continue; fi
    # Ownership checks precede array permission, including duplicate-looking rows in an array.
    if [ "$state" -eq 4 ] && [[ "$text" =~ $identity ]]; then state=0; fi
    if [ "$array_open" -eq 1 ]; then
      if [[ "$text" =~ $closer ]]; then
        array_open=0
      elif [[ "$text" =~ $bracket ]]; then
        state=0
        unsupported=1
      fi
      # Observed dependency/wheel object rows may contain inline fields. They are never headers.
      continue
    fi
    if [[ "$text" =~ $table ]]; then
      if [ "$state" -eq 4 ]; then approved+=("$version_line"); fi
      state=0
      if [[ "$text" =~ $header ]]; then state=1; fi
      continue
    elif [[ "$text" =~ $bracket ]]; then
      # Array elements and malformed/unsupported headers cannot publish pending approval.
      state=0
      unsupported=1
      continue
    fi
    if [[ "$text" =~ $opener ]]; then
      array_open=1
      if [ "$state" -ne 4 ]; then state=0; fi
      continue
    fi
    if [[ "$text" =~ $inline ]]; then
      if [ "$state" -ne 4 ]; then state=0; fi
      continue
    fi
    if [[ "$text" == *'['* ]]; then
      # Partially filled, quoted/dotted-key or otherwise unsupported array forms fail closed.
      state=0
      unsupported=1
      continue
    fi
    case "$state" in
      1)
        if [[ "$text" =~ $name ]]; then state=2; else state=0; fi
        ;;
      2)
        if [[ "$text" =~ $version ]]; then
          version_line="$number"
          state=3
        else
          state=0
        fi
        ;;
      3)
        if [[ "$text" =~ $source ]]; then state=4; else state=0; fi
        ;;
    esac
  done < "$path"
  if [ "$state" -eq 4 ] && [ "$array_open" -eq 0 ]; then approved+=("$version_line"); fi
  if [ "$multiline" -eq 0 ] && [ "$unsupported" -eq 0 ] && [ "$array_open" -eq 0 ] \
    && [ "${#approved[@]}" -gt 0 ]; then
    printf '%s ' "${approved[@]}"
  fi
  # An empty approved set is a normal result; other IPv4 candidates still need scanning.
  return 0
}

scan_ipv4() {
  local record path line text remainder token
  local pattern='\b([0-9]{1,3}\.){3}[0-9]{1,3}\b'
  local -A public_versions=()
  for path in "${FILES[@]}"; do
    if [[ "$path" == uv.lock || "$path" == */uv.lock ]]; then
      public_versions["$path"]=" $(qualified_public_version_lines "$path") "
    fi
  done
  while IFS= read -r record; do
    path="${record%%:*}"
    remainder="${record#*:}"
    line="${remainder%%:*}"
    text="${remainder#*:}"
    if [[ "$path" == uv.lock || "$path" == */uv.lock ]]; then
      if [[ "${public_versions["$path"]:-}" == *" $line "* ]]; then
        text="$(printf '%s\n' "$text" | sed -E \
          -e 's@^([[:blank:]]*version[[:blank:]]*=[[:blank:]]*")1\.1\.1\.2("[[:blank:]]*(#|$))@\1<public-version>\2@')"
      fi
    fi
    text="$(printf '%s\n' "$text" | sed -E \
      -e 's#(https://files\.pythonhosted\.org/packages/[0-9A-Fa-f/]+/click_plugins-)1\.1\.1\.2((\.tar\.gz|-py2\.py3-none-any\.whl)([^A-Za-z0-9._-]|$))#\1<public-version>\2#g')"
    while IFS= read -r token; do
      printf '%s:%s:%s\n' "$path" "$line" "$token"
    done < <(printf '%s\n' "$text" | grep -oE -- "$pattern" || true)
  done < <(grep -nHIE -- "$pattern" "${FILES[@]}" 2>/dev/null || true)
}
hits="$(scan_ipv4 \
  | grep -vE -- ':(10\.0\.0\.[0-9]{1,3}|10\.99\.99\.99|192\.0\.2\.[0-9]{1,3}|198\.51\.100\.[0-9]{1,3}|203\.0\.113\.[0-9]{1,3}|127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|0\.0\.0\.0|255\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|8\.8\.8\.8|8\.8\.4\.4|7\.1\.5\.[12]|8\.2\.3\.[12]|3\.3\.1\.0|172\.16\.0\.0)$' || true)"
[ -z "$hits" ] || report "IPv4 address outside the sanctioned documentation/placeholder set (R61):" $hits

# --- IPv6 addresses --------------------------------------------------------------------------
# Keep the existing full/leading-group forms. Leading compression needs context: numeric and
# expression steps in indexed Python slices have the same address shape. Mask only a recognized
# bracket span, never the line, before matching additional leading-compression candidates.
# Sanctioned: RFC 3849 documentation prefix, expanded loopback, and exact bare loopback/unspecified.
mask_leading_slice_spans() {
  # Only ASCII bracket/step syntax is interpreted. Pass other bytes through in chunks; keeping
  # byte offsets local avoids repeatedly walking UTF-8 prose and leaves the caller's locale intact.
  local LC_ALL=C
  local rest="$1" prefix before inside step previous=''
  local first_chunk=1 previous_qualified=0 root_qualified
  local slice='^[[:blank:]]*:[[:blank:]]*:[[:blank:]]*(.+)$'
  local step_chars='^[A-Za-z0-9_.[:blank:]+*/%,()-]+$'
  local plain='[A-Za-z0-9_.[:blank:]+*/%,-]'
  local one_level="\(${plain}*\)"
  local two_levels="\((${plain}|${one_level})*\)"
  local balanced="^(${plain}|${two_levels})*$"
  local identifier='[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*'
  local root="(${identifier}([[:blank:]]*${two_levels})?|${two_levels})"
  local witness='[=({,;+*/%&|^~<>"`-]|\['
  witness="${witness}|'"
  local initial_receiver="^[[:blank:]]*${root}[[:blank:]]*$"
  local framed_receiver="(${witness})[[:blank:]]*${root}[[:blank:]]*$"
  while [[ "$rest" == *'['* ]]; do
    prefix="${rest%%\[*}"
    rest="${rest:$((${#prefix}+1))}"
    printf '%s' "$prefix"
    if [[ "$rest" != *']'* ]]; then
      printf '[%s\n' "$rest"
      return
    fi
    # A whitespace boundary between prose words is not a source witness. Identify the complete
    # root after indentation or explicit expression punctuation, including call/group receivers.
    # Only the first original chunk can use line start; emitted chunks cannot invent that cue.
    if [[ "$prefix" =~ ^(.*[^[:blank:]])[[:blank:]]*$ ]]; then
      before="${BASH_REMATCH[1]}"
    else
      before=''
    fi
    root_qualified=0
    if [[ -z "$before" ]]; then
      # An original closed bracket carries receiver provenance through adjacent/spaced chains.
      if [[ "$previous" == ']' && "$previous_qualified" == 1 ]]; then
        root_qualified=1
      fi
    elif [[ "$before" =~ $framed_receiver ]] \
      || [[ "$first_chunk" == 1 && "$before" =~ $initial_receiver ]] \
      || [[ "$previous" == '[' && "$previous_qualified" == 1 && "$before" =~ $initial_receiver ]]; then
      root_qualified=1
    fi
    first_chunk=0
    inside="${rest%%]*}"
    if [[ "$root_qualified" == 1 ]]; then
      if [[ "$inside" =~ $slice ]]; then
        step="${BASH_REMATCH[1]}"
        # The fixed-depth grammar admits exactly balanced groups up to depth two. Keep the
        # separate whitelist/non-whitespace predicates, including their empty-step behavior.
        if [[ "$step" =~ $step_chars && "$step" =~ [^[:blank:]] && "$step" =~ $balanced ]]; then
          printf '%s' '[<python-slice>]'
          rest="${rest:$((${#inside}+1))}"
          previous=']'
          previous_qualified=1
          continue
        fi
      fi
    fi
    # Preserve a nonnested ordinary index verbatim and carry only its original root's proof.
    # Addresses inside it remain visible to the consumer, including unqualified prose chains.
    if [[ "$inside" != *'['* ]]; then
      printf '[%s]' "$inside"
      rest="${rest:$((${#inside}+1))}"
      previous=']'
      previous_qualified="$root_qualified"
      continue
    fi
    # An invalid outer bracket may contain a valid inner slice, so consume only its opener.
    # That unresolved opener is a container cue only when its own root was source-qualified.
    printf '%s' '['
    previous='['
    previous_qualified="$root_qualified"
  done
  printf '%s\n' "$rest"
}

scan_leading_ipv6() {
  local record path line text remainder rest prefix suffix token core left right offset
  local candidate='::[0-9A-Fa-f:.]+'
  local hex='^::[0-9A-Fa-f]{1,4}(:[0-9A-Fa-f]{1,4}){0,6}$'
  local mapped='^::[Ff]{4}:([0-9]{1,3}\.){3}[0-9]{1,3}$'
  local continuation='^[A-Za-z0-9_.:]$'
  while IFS= read -r record; do
    path="${record%%:*}"
    remainder="${record#*:}"
    line="${remainder%%:*}"
    text="${remainder#*:}"
    rest="$(mask_leading_slice_spans "$text")"
    while [[ "$rest" =~ $candidate ]]; do
      token="${BASH_REMATCH[0]}"
      prefix="${rest%%"$token"*}"
      offset=$((${#prefix} + ${#token}))
      suffix="${rest:offset}"
      left="${prefix: -1}"
      right="${suffix:0:1}"
      rest="$suffix"
      [[ "$left" =~ $continuation || "$right" =~ $continuation ]] && continue
      # Normalize exactly one sentence period only when the entire remainder is a known shape.
      if [[ "$token" == *'.' && "$token" != *'..' && ( -z "$right" || "$right" == ' ' || "$right" == $'\t' ) ]]; then
        core="${token%.}"
        if [[ "$core" =~ $hex || "$core" =~ $mapped ]]; then token="$core"; fi
      fi
      # The full dotted mapped token belongs to the IPv4 policy, including documentation octets.
      # Malformed/overlong dotted candidates never fall back to a shorter pure-hex prefix.
      [[ "$token" =~ $mapped ]] && continue
      if [[ "$token" =~ $hex && "$token" != '::1' ]]; then
        printf '%s:%s:%s\n' "$path" "$line" "$token"
      fi
    done
  done < <(grep -nHIE -- '::' "${FILES[@]}" 2>/dev/null || true)
}
hits="$(
  grep -nHoIE '\b([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b|\b([0-9A-Fa-f]{1,4}:){1,7}:([0-9A-Fa-f]{1,4}(:[0-9A-Fa-f]{1,4}){0,6}\b)?' -- "${FILES[@]}" 2>/dev/null \
    | grep -viE -- ':2001:db8(:|$)|:0:0:0:0:0:0:0:1$' || true
  scan_leading_ipv6
)"
[ -z "$hits" ] || report "IPv6 address outside the RFC 3849 documentation prefix (R61):" $hits

# --- .local FQDNs other than the sanctioned examples ------------------------------------------
# Matches SINGLE-label zones too (`customer.local` — a real AD base domain is site data even
# without a host label). Allowed: example.local / *.example.local · CONTOSO.local /
# *.CONTOSO.local (the runbook's worked example) · easysynq.local (the product's own default
# from-address token) and errors.easysynq.local (an RFC-7807 problem-type URN namespace) ·
# settings.local (a gitignore FILENAME token, not a host) · test-smtp.local (the established
# synthetic SMTP host in the integration tests, the 10.99.99.99 of hostnames).
hits="$(grep -nHoIE '\b[a-zA-Z0-9][a-zA-Z0-9-]*(\.[a-zA-Z0-9][a-zA-Z0-9-]*)*\.local\b' -- "${FILES[@]}" 2>/dev/null \
  | grep -viE -- ':([a-z0-9.-]+\.)?example\.local$|:([a-z0-9.-]+\.)?contoso\.local$|:(errors\.)?easysynq\.local$|:settings\.local$|:test-smtp\.local$' || true)"
[ -z "$hits" ] || report "Non-example .local hostname (R61) — use example.local:" $hits

# --- MAC addresses other than the placeholder ------------------------------------------------
hits="$(grep -nHoiIE '\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b' -- "${FILES[@]}" 2>/dev/null \
  | grep -viE -- '00[:-]15[:-]5D[:-]00[:-]00[:-]01$' || true)"
[ -z "$hits" ] || report "MAC address (R61) — use 00:15:5D:00:00:01 as the placeholder:" $hits

# --- certificate / key fingerprints ----------------------------------------------------------
hits="$(grep -nHoIE '\b([0-9A-Fa-f]{2}:){15,}[0-9A-Fa-f]{2}\b' -- "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Certificate/key fingerprint (R61) — per-install, do not commit:" $hits

# OpenSSH-style fingerprints: `SHA256:` + 43 unpadded base64 chars. Case-sensitive on purpose —
# docker/OCI image digests are lowercase `sha256:<hex>` and must not match.
hits="$(grep -nHoE '\bSHA256:[A-Za-z0-9+/]{43}' -- "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "OpenSSH SHA256 key fingerprint (R61) — per-install, do not commit:" $hits

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'EOF'

R61 — site-specific operational records must not enter this repository, regardless of
repository visibility. Replace with placeholders and keep the concrete worksheet in the
organization's own operational documentation. See docs/decisions-register.md R61.

If a match is a legitimate example, widen the sanctioned placeholders above rather than
adding a one-off exception.
EOF
  exit 1
fi
echo "check-no-site-data: clean (mechanical shapes only — see R61 for what this cannot catch)"

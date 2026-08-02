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
#   example.local · example.com · <ORG> · DC01 · 00:15:5D:00:00:01
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
report() { fail=1; printf '\n%s\n' "$1"; shift; printf '  %s\n' "$@"; }

if [ $# -gt 0 ]; then
  mapfile -t FILES < <(git ls-files -- "$@")
else
  # Text only. Skip lockfiles and this script (it quotes the patterns it hunts for).
  mapfile -t FILES < <(git ls-files -- '*.md' '*.sh' '*.ps1' '*.py' '*.ts' '*.tsx' '*.yml' '*.yaml' \
    | grep -vE 'scripts/check-no-site-data\.sh|\.lock$|images\.lock')
fi
[ ${#FILES[@]} -gt 0 ] || exit 0

# --- private IPv4 outside the documentation ranges -------------------------------------------
# ⚠ Must match all FOUR octets. A three-octet pattern makes every ISO clause number (10.2.1,
# 9.1.3) look like an RFC1918 address — in a QMS repository that is most of the false positives,
# and a check that cries wolf gets switched off. 10.0.0.x is the sanctioned example.
# 10.99.99.99 is the established synthetic address in the `ip_allow` predicate tests.
hits="$(grep -nHoE '\b(10\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}\b' "${FILES[@]}" 2>/dev/null \
  | grep -vE ':10\.0\.0\.[0-9]{1,3}$' \
  | grep -vE ':10\.99\.99\.99$' || true)"
[ -z "$hits" ] || report "Private IPv4 outside the documentation ranges (R61):" $hits

# --- .local FQDNs other than the sanctioned examples ------------------------------------------
# Allowed: *.example.local · *.CONTOSO.local (the runbook's worked example) ·
# errors.easysynq.local (an RFC-7807 problem-type URN namespace, not a host).
hits="$(grep -nHoE '\b[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z0-9][a-zA-Z0-9.-]*\.local\b' "${FILES[@]}" 2>/dev/null \
  | grep -viE '\.example\.local$|\.contoso\.local$|:errors\.easysynq\.local$' || true)"
[ -z "$hits" ] || report "Non-example .local hostname (R61) — use example.local:" $hits

# --- MAC addresses other than the placeholder ------------------------------------------------
hits="$(grep -nHoiE '\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b' "${FILES[@]}" 2>/dev/null \
  | grep -viE '00[:-]15[:-]5D[:-]00[:-]00[:-]01$' || true)"
[ -z "$hits" ] || report "MAC address (R61) — use 00:15:5D:00:00:01 as the placeholder:" $hits

# --- certificate / key fingerprints ----------------------------------------------------------
hits="$(grep -nHoE '\b([0-9A-F]{2}:){15,}[0-9A-F]{2}\b' "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Certificate/key fingerprint (R61) — per-install, do not commit:" $hits

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

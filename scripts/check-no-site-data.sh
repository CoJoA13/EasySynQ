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
  # EVERY tracked file — binaries are skipped by `grep -I` below, not by an extension allowlist
  # (an allowlist silently exempts JSON/TOML/HTML/Dockerfiles, exactly where a stray IP hides).
  # Skip lockfiles (machine-written) and this script (it quotes the patterns it hunts for).
  mapfile -t FILES < <(git ls-files \
    | grep -vE '^scripts/check-no-site-data\.sh$|\.lock$|images\.lock')
fi
[ ${#FILES[@]} -gt 0 ] || exit 0

# --- IPv4 addresses (public OR private) outside the sanctioned set ---------------------------
# ⚠ Must match all FOUR octets. A three-octet pattern makes every ISO clause number (10.2.1,
# 9.1.3) look like an address — in a QMS repository that is most of the false positives, and a
# check that cries wolf gets switched off. R61 covers ALL real addresses, not just RFC1918 — a
# WAN endpoint is site data too. Sanctioned: 10.0.0.x (the worked example) · 10.99.99.99 (the
# established `ip_allow` test address) · the RFC 5737 documentation ranges · loopback ·
# 0.0.0.0 (bind-all) · 255.x netmask/broadcast literals (structure, not an address) ·
# 8.8.8.8/8.8.4.4 (well-known anycast resolvers, placeholder-grade) · the FOUR-part ISO 9001:2015
# clause numbers (7.1.5.1/7.1.5.2/8.2.3.1/8.2.3.2 — the complete set in the standard) ·
# 3.3.1.0 (the apache/tika image pin; a version bump that reds here is extended deliberately).
hits="$(grep -nHoIE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' "${FILES[@]}" 2>/dev/null \
  | grep -vE ':(10\.0\.0\.[0-9]{1,3}|10\.99\.99\.99|192\.0\.2\.[0-9]{1,3}|198\.51\.100\.[0-9]{1,3}|203\.0\.113\.[0-9]{1,3}|127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|0\.0\.0\.0|255\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|8\.8\.8\.8|8\.8\.4\.4|7\.1\.5\.[12]|8\.2\.3\.[12]|3\.3\.1\.0)$' || true)"
[ -z "$hits" ] || report "IPv4 address outside the sanctioned documentation/placeholder set (R61):" $hits

# --- IPv6 addresses --------------------------------------------------------------------------
# Two shapes, chosen to stay false-positive-safe: the FULL 8-group form, and a `::`-compressed
# form with at least one leading hex group (times/ratios never contain `::`; 6-group MACs have
# no `::`; a BARE leading `::1`/`::` is structural loopback/unspecified and is deliberately
# unmatched so Python slice syntax `a[::2]` cannot false-positive — every real site address
# has leading groups). Sanctioned: the RFC 3849 documentation prefix 2001:db8::/32 · the
# expanded loopback 0:0:0:0:0:0:0:1 (the established `inet`-canonicalization test literal).
hits="$(grep -nHoIE '\b([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b|\b([0-9A-Fa-f]{1,4}:){1,7}:([0-9A-Fa-f]{1,4}(:[0-9A-Fa-f]{1,4}){0,6}\b)?' "${FILES[@]}" 2>/dev/null \
  | grep -viE ':2001:db8(:|$)|:0:0:0:0:0:0:0:1$' || true)"
[ -z "$hits" ] || report "IPv6 address outside the RFC 3849 documentation prefix (R61):" $hits

# --- .local FQDNs other than the sanctioned examples ------------------------------------------
# Matches SINGLE-label zones too (`customer.local` — a real AD base domain is site data even
# without a host label). Allowed: example.local / *.example.local · CONTOSO.local /
# *.CONTOSO.local (the runbook's worked example) · easysynq.local (the product's own default
# from-address token) and errors.easysynq.local (an RFC-7807 problem-type URN namespace) ·
# settings.local (a gitignore FILENAME token, not a host) · test-smtp.local (the established
# synthetic SMTP host in the integration tests, the 10.99.99.99 of hostnames).
hits="$(grep -nHoIE '\b[a-zA-Z0-9][a-zA-Z0-9-]*(\.[a-zA-Z0-9][a-zA-Z0-9-]*)*\.local\b' "${FILES[@]}" 2>/dev/null \
  | grep -viE ':([a-z0-9.-]+\.)?example\.local$|:([a-z0-9.-]+\.)?contoso\.local$|:(errors\.)?easysynq\.local$|:settings\.local$|:test-smtp\.local$' || true)"
[ -z "$hits" ] || report "Non-example .local hostname (R61) — use example.local:" $hits

# --- MAC addresses other than the placeholder ------------------------------------------------
hits="$(grep -nHoiIE '\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b' "${FILES[@]}" 2>/dev/null \
  | grep -viE '00[:-]15[:-]5D[:-]00[:-]00[:-]01$' || true)"
[ -z "$hits" ] || report "MAC address (R61) — use 00:15:5D:00:00:01 as the placeholder:" $hits

# --- certificate / key fingerprints ----------------------------------------------------------
hits="$(grep -nHoIE '\b([0-9A-F]{2}:){15,}[0-9A-F]{2}\b' "${FILES[@]}" 2>/dev/null || true)"
[ -z "$hits" ] || report "Certificate/key fingerprint (R61) — per-install, do not commit:" $hits

# OpenSSH-style fingerprints: `SHA256:` + 43 unpadded base64 chars. Case-sensitive on purpose —
# docker/OCI image digests are lowercase `sha256:<hex>` and must not match.
hits="$(grep -nHoE '\bSHA256:[A-Za-z0-9+/]{43}' "${FILES[@]}" 2>/dev/null || true)"
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

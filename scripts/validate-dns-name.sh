#!/usr/bin/env bash
# Validate one browser-facing DNS hostname before it is interpolated into .env or a sed expression.
set -euo pipefail

[ "$#" -eq 1 ] || {
  echo "usage: validate-dns-name.sh <hostname>" >&2
  exit 2
}

NAME="$1"

valid_dns_name() {
  local name="$1" label
  local -a labels=()
  [ "${#name}" -le 253 ] || return 1
  [[ "$name" != .* && "$name" != *. ]] || return 1
  [[ "$name" != *..* ]] || return 1
  IFS='.' read -r -a labels <<< "$name"
  [ "${#labels[@]}" -gt 0 ] || return 1
  for label in "${labels[@]}"; do
    # RFC-style host label: 1–63 alnum/hyphen characters, with alnum at both edges.
    [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || return 1
  done
}

valid_dns_name "$NAME"

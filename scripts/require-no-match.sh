#!/usr/bin/env bash
# Fail unless ripgrep proves PATTERN is absent from every supplied path.
set -uo pipefail

if [ "$#" -lt 2 ]; then
  printf 'usage: %s PATTERN PATH...\n' "$0" >&2
  exit 2
fi

pattern="$1"
shift

rg -n -- "$pattern" "$@"
status=$?

case "$status" in
  0) exit 1 ;;
  1) exit 0 ;;
  *) exit "$status" ;;
esac

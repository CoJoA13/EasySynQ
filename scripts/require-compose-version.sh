#!/usr/bin/env bash
# The production overlay uses Compose's !reset tag to fail closed if a dev overlay is accidentally
# included. That tag was introduced in Docker Compose 2.24.4.
set -euo pipefail

MINIMUM_VERSION="2.24.4"
if ! VERSION_RAW="$(docker compose version --short 2>/dev/null)"; then
  echo "compose-version: Docker Compose ${MINIMUM_VERSION} or newer is required" >&2
  exit 1
fi

if [[ "$VERSION_RAW" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
  VERSION_MAJOR="${BASH_REMATCH[1]}"
  VERSION_MINOR="${BASH_REMATCH[2]}"
  VERSION_PATCH="${BASH_REMATCH[3]}"
else
  echo "compose-version: could not parse Docker Compose version '${VERSION_RAW}'" >&2
  exit 1
fi

if (( VERSION_MAJOR < 2 \
      || (VERSION_MAJOR == 2 && VERSION_MINOR < 24) \
      || (VERSION_MAJOR == 2 && VERSION_MINOR == 24 && VERSION_PATCH < 4) )); then
  echo "compose-version: Docker Compose ${MINIMUM_VERSION} or newer is required; found ${VERSION_RAW}" >&2
  exit 1
fi

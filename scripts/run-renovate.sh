#!/usr/bin/env bash
# CI entry point: npm/PyPI/Node.js/Astral distributions, with no GitHub tool downloads.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-}"
case "$mode" in validate|run) ;; *) echo 'usage: run-renovate.sh validate|run' >&2; exit 2 ;; esac
export RENOVATE_CONFIG_FILE="$PWD/renovate-self-hosted.cjs"
tools_dir="$(mktemp -d)"
trap 'rm -rf "$tools_dir"' EXIT
install_node() {
  local version="$1" destination="$2"
  local archive="node-v${version}-linux-x64.tar.xz"
  curl -fsSL "https://nodejs.org/dist/v${version}/${archive}" -o "$tools_dir/$archive"
  curl -fsSL "https://nodejs.org/dist/v${version}/SHASUMS256.txt" -o "$tools_dir/SHASUMS256.txt"
  (cd "$tools_dir" && grep "  ${archive}\$" SHASUMS256.txt | sha256sum --check --status)
  mkdir "$destination"
  tar -xJf "$tools_dir/$archive" --strip-components=1 -C "$destination"
}
# The application uses Node 26; Renovate 44's supported runtime is Node 24.
install_node 24.21.0 "$tools_dir/node24"
renovate_node="$tools_dir/node24/bin/node"
export PATH="$tools_dir/node24/bin:$PATH"
# Lifecycle scripts are disabled: dependencies come from npm, without native-addon GitHub installers.
npm install --prefix "$tools_dir/renovate" --ignore-scripts --no-audit --no-fund renovate@44.79.6
renovate_dist="$tools_dir/renovate/node_modules/renovate/dist"

if [ "$mode" = validate ]; then
  test -f renovate.json
  "$renovate_node" "$renovate_dist/config-validator.js" --strict --no-global renovate.json
  "$renovate_node" "$renovate_dist/config-validator.js" --strict renovate-self-hosted.cjs
  "$renovate_node" scripts/tests/test-renovate-images.mjs "$renovate_dist"
  exit
fi

: "${RENOVATE_TOKEN:?The protected GitLab project token must reach the scheduled job}"
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
python3 -m venv "$tools_dir/python-tools"
"$tools_dir/python-tools/bin/pip" install --only-binary=:all: --disable-pip-version-check uv==0.12.13
install_node 26.8.2 "$tools_dir/node26"
export PATH="$tools_dir/node26/bin:$tools_dir/python-tools/bin:$PATH"
export UV_PYTHON
UV_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
export UV_PYTHON_DOWNLOADS=never
node --version
npm --version
uv --version
"$UV_PYTHON" --version
export RENOVATE_CONFIG_FILE="$PWD/renovate-self-hosted.cjs"
"$renovate_node" "$renovate_dist/renovate.js"

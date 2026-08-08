#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BOOTSTRAP_SOURCE="$PROJECT_ROOT/scripts/bootstrap-fedora-dev.sh"
NODE_PIN="$PROJECT_ROOT/.node-version"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

checks=0
failures=0

ok() {
  checks=$((checks + 1))
  printf '  ok   %s\n' "$1"
}

not_ok() {
  checks=$((checks + 1))
  failures=$((failures + 1))
  printf '  FAIL %s\n' "$1" >&2
}

assert_eq() {
  local label=$1 want=$2 got=$3
  if [[ $got == "$want" ]]; then
    ok "$label"
  else
    not_ok "$label (want='$want' got='$got')"
  fi
}

assert_contains() {
  local label=$1 haystack=$2 needle=$3
  if [[ $haystack == *"$needle"* ]]; then
    ok "$label"
  else
    not_ok "$label (missing='$needle')"
  fi
}

assert_not_contains() {
  local label=$1 haystack=$2 needle=$3
  if [[ $haystack == *"$needle"* ]]; then
    not_ok "$label (unexpected='$needle')"
  else
    ok "$label"
  fi
}

assert_empty_file() {
  local label=$1 file=$2
  if [[ ! -s $file ]]; then
    ok "$label"
  else
    not_ok "$label (contents=$(<"$file"))"
  fi
}

assert_no_action_tool() {
  local label=$1 file=$2 tool=$3
  if awk -v wanted="$tool" '$1 == wanted { found=1 } END { exit found ? 0 : 1 }' "$file"; then
    not_ok "$label (unexpected tool='$tool')"
  else
    ok "$label"
  fi
}

line_number() {
  local haystack=$1 needle=$2 line number=0
  while IFS= read -r line; do
    number=$((number + 1))
    [[ $line == *"$needle"* ]] && { printf '%s' "$number"; return; }
  done <<<"$haystack"
}

if [[ ! -f $BOOTSTRAP_SOURCE ]]; then
  not_ok 'scripts/bootstrap-fedora-dev.sh exists'
  printf '\n%d checks, %d failures\n' "$checks" "$failures"
  exit 1
fi
if [[ ! -f $NODE_PIN ]] || [[ $(<"$NODE_PIN") != 22 ]]; then
  not_ok '.node-version pins Node 22'
fi

make_dispatcher() {
  local target=$1
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'state=${STUB_STATE_DIR:?}' \
    'tool=${0##*/}' \
    'log() { printf "%s\n" "$*" >>"$state/actions"; }' \
    'read_state() {' \
    '  local name=$1 default=${2-}' \
    '  if [[ -f $state/$name ]]; then IFS= read -r value <"$state/$name" || true; printf "%s" "$value";' \
    '  else printf "%s" "$default"; fi' \
    '}' \
    'package_installed() {' \
    '  local wanted=$1 package' \
    '  [[ -f $state/installed_packages ]] || return 1' \
    '  while IFS= read -r package || [[ -n $package ]]; do [[ $package == "$wanted" ]] && return 0; done <"$state/installed_packages"' \
    '  return 1' \
    '}' \
    'install_package() { package_installed "$1" || printf "%s\n" "$1" >>"$state/installed_packages"; }' \
    'case "$tool" in' \
    '  uname) log "uname $*"; read_state arch x86_64; printf "\n" ;;' \
    '  rpm)' \
    '    log "rpm $*"' \
    '    [[ ${1-} == -q && ${2-} == --quiet && $# -eq 3 ]] || exit 64' \
    '    package_installed "$3"' \
    '    ;;' \
    '  dnf)' \
    '    log "dnf $*"' \
    '    if [[ ${1-} == -q && ${2-} == repolist && ${3-} == --enabled && ${4-} == docker-ce-stable && $# -eq 4 ]]; then' \
    '      [[ $(read_state docker_repo absent) == configured ]]' \
    '    else' \
    '      printf "%s\n" "unexpected direct dnf mutation: $*" >>"$state/host_mutations"; exit 90' \
    '    fi' \
    '    ;;' \
    '  sudo)' \
    '    log "sudo $*"; printf "STUB_SUDO %s\n" "$*"' \
    '    [[ ${1-} == dnf ]] || { printf "%s\n" "sudo $*" >>"$state/host_mutations"; exit 90; }' \
    '    shift' \
    '    if [[ ${1-} == install && ${2-} == --assumeyes ]]; then' \
    '      shift' \
    '      shift' \
    '      for package in "$@"; do install_package "$package"; done' \
    '    elif [[ ${1-} == config-manager && ${2-} == addrepo && ${3-} == --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo && $# -eq 3 ]]; then' \
    '      printf "configured\n" >"$state/docker_repo"' \
    '    else' \
    '      printf "%s\n" "sudo dnf $*" >>"$state/host_mutations"; exit 90' \
    '    fi' \
    '    ;;' \
    '  node) log "node $*"; read_state node_version v22.18.0; printf "\n" ;;' \
    '  uv)' \
    '    log "uv $*"' \
    '    if [[ ${1-} == python && ${2-} == install && ${3-} == 3.12 && $# -eq 3 ]]; then printf "Python 3.12 ready\n";' \
    '    else printf "%s\n" "uv $*" >>"$state/host_mutations"; exit 90; fi' \
    '    ;;' \
    '  doctor.sh)' \
    '    log "doctor $*"' \
    '    [[ ${1-} == contributor && $# -eq 1 ]] || exit 90' \
    '    printf "doctor contributor complete\n"' \
    '    ;;' \
    '  systemctl|usermod|groupmod|firewall-cmd|setenforce|install|tee|dnf-3|npm|npx|just|pre-commit)' \
    '    log "$tool $*"; printf "%s\n" "$tool $*" >>"$state/host_mutations"; exit 90' \
    '    ;;' \
    '  *) log "$tool $*"; printf "%s\n" "unexpected $tool $*" >>"$state/host_mutations"; exit 90 ;;' \
    'esac' >"$target"
  chmod +x "$target"
}

new_fixture() {
  local name=$1
  CASE_ROOT="$TEST_ROOT/$name"
  CASE_REPO="$CASE_ROOT/repo"
  CASE_BIN="$CASE_ROOT/bin"
  CASE_STATE="$CASE_ROOT/state"
  CASE_OS_RELEASE="$CASE_ROOT/os-release"
  mkdir -p "$CASE_REPO/scripts" "$CASE_BIN" "$CASE_STATE"
  cp "$BOOTSTRAP_SOURCE" "$CASE_REPO/scripts/bootstrap-fedora-dev.sh"
  cp "$NODE_PIN" "$CASE_REPO/.node-version"
  make_dispatcher "$CASE_BIN/dispatcher"
  local tool
  for tool in dnf rpm sudo uname node uv systemctl usermod groupmod firewall-cmd setenforce install tee dnf-3 npm npx just pre-commit; do
    cp "$CASE_BIN/dispatcher" "$CASE_BIN/$tool"
  done
  cp "$CASE_BIN/dispatcher" "$CASE_REPO/scripts/doctor.sh"
  : >"$CASE_STATE/actions"
  : >"$CASE_STATE/host_mutations"
  : >"$CASE_STATE/installed_packages"
  printf 'ID=fedora\nVERSION_ID=44\n' >"$CASE_OS_RELEASE"
}

install_all_packages() {
  printf '%s\n' \
    git curl openssl dnf-plugins-core nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin \
    uv just pre-commit postgresql16 \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    >"$CASE_STATE/installed_packages"
}

run_bootstrap() {
  local input=$1
  shift
  set +e
  printf '%s' "$input" | env \
    PATH="$CASE_BIN:/usr/bin:/bin" \
    STUB_STATE_DIR="$CASE_STATE" \
    FEDORA_BOOTSTRAP_TEST_MODE=1 \
    FEDORA_BOOTSTRAP_OS_RELEASE="$CASE_OS_RELEASE" \
    USER=developer \
    bash "$CASE_REPO/scripts/bootstrap-fedora-dev.sh" "$@" \
    >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
  CASE_EXIT=$?
  set +e
  CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
}

printf '%s\n' '== Fedora developer bootstrap contract =='

new_fixture default-check
run_bootstrap ''
assert_eq 'default mode exits zero' 0 "$CASE_EXIT"
assert_empty_file 'default mode performs no host mutation' "$CASE_STATE/host_mutations"
assert_not_contains 'default mode never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '
assert_contains 'default mode prints Python action' "$CASE_OUTPUT" 'uv python install 3.12'
assert_contains 'default mode prints Docker start action' "$CASE_OUTPUT" 'sudo systemctl enable --now docker'
assert_contains 'default mode prints Docker group action' "$CASE_OUTPUT" 'sudo usermod -aG docker "$USER"'

new_fixture explicit-check
run_bootstrap '' --check
assert_eq '--check exits zero' 0 "$CASE_EXIT"
assert_empty_file '--check performs no host mutation' "$CASE_STATE/host_mutations"
assert_not_contains '--check never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '
assert_not_contains '--check does not install Python' "$(<"$CASE_STATE/actions")" 'uv python install'

new_fixture non-fedora
printf 'ID=debian\nVERSION_ID=13\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'non-Fedora exits 2' 2 "$CASE_EXIT"
assert_contains 'non-Fedora prints exact guidance' "$CASE_OUTPUT" \
  'bootstrap: Fedora Workstation 44 is required (found debian 13). Use docs/runbooks/fresh-linux-setup.md for supported developer hosts.'
assert_empty_file 'non-Fedora performs no host mutation' "$CASE_STATE/host_mutations"

new_fixture unsupported-fedora
printf 'ID=fedora\nVERSION_ID="43"\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'unsupported Fedora release exits 2' 2 "$CASE_EXIT"
assert_contains 'unsupported Fedora prints exact guidance' "$CASE_OUTPUT" \
  'bootstrap: Fedora Workstation 44 is required (found fedora 43). Use docs/runbooks/fresh-linux-setup.md for supported developer hosts.'

new_fixture unsupported-arch
printf 'aarch64\n' >"$CASE_STATE/arch"
run_bootstrap '' --check
assert_eq 'unsupported architecture exits 2' 2 "$CASE_EXIT"
assert_contains 'unsupported architecture prints exact guidance' "$CASE_OUTPUT" \
  'bootstrap: x86_64 is required (found aarch64). Use an x86_64 Fedora Workstation 44 host.'

new_fixture atomic-variant
printf 'ID=fedora\nVERSION_ID=44\nVARIANT_ID=silverblue\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'Fedora Atomic variant exits 2' 2 "$CASE_EXIT"
assert_contains 'Fedora Atomic gets distinct advanced guidance' "$CASE_OUTPUT" \
  'bootstrap: Fedora Atomic variant silverblue is not supported by this host bootstrap. See the advanced Fedora Atomic note in docs/runbooks/fresh-linux-setup.md.'
assert_empty_file 'Fedora Atomic rejection performs no host mutation' "$CASE_STATE/host_mutations"

new_fixture os-release-data-only
printf 'ID=fedora\nVERSION_ID=44\nMALICIOUS=$(systemctl enable evil)\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'os-release is parsed as data' 0 "$CASE_EXIT"
assert_empty_file 'os-release contents are never executed' "$CASE_STATE/host_mutations"

new_fixture exact-rpm-boundary
printf 'git-extra\n' >"$CASE_STATE/installed_packages"
run_bootstrap '' --check
assert_contains 'RPM package matching is exact' "$CASE_OUTPUT" 'Missing Fedora packages: git '

new_fixture node-path-shadow
install_all_packages
printf 'configured\n' >"$CASE_STATE/docker_repo"
printf 'v20.19.4\n' >"$CASE_STATE/node_version"
run_bootstrap '' --check
assert_not_contains 'installed nodejs22 binary package is not reported missing' "$CASE_OUTPUT" 'Missing Fedora packages:'
assert_contains 'earlier PATH Node is reported as shadowing' "$CASE_OUTPUT" \
  'PATH_SHADOWED: active node is v20.19.4; Fedora nodejs22-bin provides /usr/bin/node for Node 22.'

new_fixture decline-apply
run_bootstrap $'no\n' --apply
assert_eq 'declining apply exits zero' 0 "$CASE_EXIT"
assert_contains 'declining apply cancels' "$CASE_OUTPUT" 'Cancelled; no changes were made.'
assert_empty_file 'declining apply performs no host mutation' "$CASE_STATE/host_mutations"
assert_not_contains 'declining apply never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '
assert_not_contains 'declining apply never installs Python' "$(<"$CASE_STATE/actions")" 'uv python install'

new_fixture literal-confirmation
run_bootstrap $'YES\n' --apply
assert_contains 'confirmation requires literal lowercase yes' "$CASE_OUTPUT" 'Cancelled; no changes were made.'
assert_not_contains 'uppercase confirmation never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '

new_fixture root-apply
set +e
printf 'yes\n' | env \
  PATH="$CASE_BIN:/usr/bin:/bin" \
  STUB_STATE_DIR="$CASE_STATE" \
  FEDORA_BOOTSTRAP_TEST_MODE=1 \
  FEDORA_BOOTSTRAP_OS_RELEASE="$CASE_OS_RELEASE" \
  FEDORA_BOOTSTRAP_EFFECTIVE_UID=0 \
  USER=root \
  bash "$CASE_REPO/scripts/bootstrap-fedora-dev.sh" --apply \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
assert_eq 'root --apply exits 2' 2 "$CASE_EXIT"
assert_contains 'root --apply protects the unprivileged runtime step' "$CASE_OUTPUT" \
  'bootstrap: do not run --apply with sudo or as root; run it from the unprivileged developer account.'
assert_not_contains 'root --apply never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '

new_fixture complete-preview
run_bootstrap $'yes\n' --apply
BASE_COMMAND='sudo dnf install --assumeyes git curl openssl dnf-plugins-core nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin uv just pre-commit postgresql16'
REPO_COMMAND='sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo'
DOCKER_COMMAND='sudo dnf install --assumeyes docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin'
assert_contains 'preview includes exact Fedora package transaction' "$CASE_OUTPUT" "$BASE_COMMAND"
assert_contains 'preview includes official Docker repository transaction' "$CASE_OUTPUT" "$REPO_COMMAND"
assert_contains 'preview includes exact Docker package transaction' "$CASE_OUTPUT" "$DOCKER_COMMAND"
assert_eq 'preview heading is emitted once' 1 "$(awk '/^Proposed privileged transaction:$/ { count++ } END { print count + 0 }' "$CASE_ROOT/stdout")"
preview_line=$(line_number "$CASE_OUTPUT" 'Proposed privileged transaction:')
sudo_line=$(line_number "$CASE_OUTPUT" 'STUB_SUDO ')
if [[ -n $preview_line && -n $sudo_line && $preview_line -lt $sudo_line ]]; then
  ok 'the whole preview appears before the first sudo call'
else
  not_ok "the whole preview appears before the first sudo call (preview=${preview_line:-none} sudo=${sudo_line:-none})"
fi
assert_empty_file 'successful apply invokes no forbidden host action' "$CASE_STATE/host_mutations"
assert_contains 'successful apply installs Python 3.12 unprivileged' "$(<"$CASE_STATE/actions")" 'uv python install 3.12'
last_action=$(tail -n 1 "$CASE_STATE/actions")
assert_eq 'successful flow ends at contributor doctor' 'doctor contributor' "$last_action"
assert_no_action_tool 'bootstrap never installs project dependencies with npm' "$CASE_STATE/actions" npm
assert_no_action_tool 'bootstrap never runs just setup' "$CASE_STATE/actions" just
assert_no_action_tool 'bootstrap never runs pre-commit installation' "$CASE_STATE/actions" pre-commit

new_fixture second-apply
install_all_packages
printf 'configured\n' >"$CASE_STATE/docker_repo"
run_bootstrap '' --apply
assert_eq 'second --apply exits zero without confirmation' 0 "$CASE_EXIT"
assert_not_contains 'second --apply has no package transaction' "$(<"$CASE_STATE/actions")" 'sudo dnf'
assert_not_contains 'second --apply has no privileged preview' "$CASE_OUTPUT" 'Proposed privileged transaction:'
assert_empty_file 'second --apply performs no forbidden host action' "$CASE_STATE/host_mutations"
assert_eq 'second --apply still ends at contributor doctor' 'doctor contributor' "$(tail -n 1 "$CASE_STATE/actions")"

new_fixture configured-repo
printf 'configured\n' >"$CASE_STATE/docker_repo"
run_bootstrap $'yes\n' --apply
assert_not_contains 'configured Docker repository is not added again' "$(<"$CASE_STATE/actions")" 'config-manager addrepo'

new_fixture invalid-option
run_bootstrap '' --dry-run
assert_eq 'unknown option exits 2' 2 "$CASE_EXIT"
assert_contains 'unknown option prints usage' "$CASE_OUTPUT" 'usage: ./scripts/bootstrap-fedora-dev.sh [--check|--apply]'
assert_empty_file 'unknown option performs no host mutation' "$CASE_STATE/host_mutations"

printf '\n%d checks, %d failures\n' "$checks" "$failures"
(( failures == 0 ))

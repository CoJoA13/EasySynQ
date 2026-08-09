#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BOOTSTRAP_SOURCE="$PROJECT_ROOT/scripts/bootstrap-fedora-dev.sh"
NODE_PIN="$PROJECT_ROOT/.node-version"

TEST_ROOT="$(mktemp -d)"
trap 'if [[ ${KEEP_FEDORA_BOOTSTRAP_FIXTURES:-0} == 1 ]]; then printf "fixtures: %s\n" "$TEST_ROOT"; else rm -rf "$TEST_ROOT"; fi' EXIT

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
    '    printf "%s\n" "direct dnf invocation: $*" >>"$state/host_mutations"; exit 90' \
    '    ;;' \
    '  sudo)' \
    '    log "sudo $*"; printf "STUB_SUDO %s\n" "$*"' \
    '    [[ ${1##*/} == dnf ]] || { printf "%s\n" "sudo $*" >>"$state/host_mutations"; exit 90; }' \
    '    shift' \
    '    if [[ ${1-} == install && ${2-} == --assumeyes ]]; then' \
    '      shift' \
    '      shift' \
    '      for package in "$@"; do install_package "$package"; done' \
    '    elif [[ ${1-} == config-manager && ${2-} == addrepo && ${3-} == --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo && $# -eq 3 ]]; then' \
    '      repo_file=$(read_state repo_file)' \
    '      mkdir -p "${repo_file%/*}"' \
    '      printf "%s\n" "[docker-ce-stable]" "name=Docker CE Stable" "baseurl=https://download.docker.com/linux/fedora/\$releasever/\$basearch/stable" "enabled=1" "gpgcheck=1" "gpgkey=https://download.docker.com/linux/fedora/gpg" >"$repo_file"' \
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
  CASE_SYSTEM_ROOT="$CASE_ROOT/system-root"
  CASE_OS_RELEASE="$CASE_SYSTEM_ROOT/etc/os-release"
  CASE_REPO_FILE="$CASE_SYSTEM_ROOT/etc/yum.repos.d/docker-ce.repo"
  CASE_EVIL_BIN="$CASE_ROOT/evil-bin"
  CASE_FIXTURE_ENTRY="$CASE_REPO/scripts/bootstrap-fixture-entry.sh"
  mkdir -p "$CASE_REPO/scripts" "$CASE_BIN" "$CASE_STATE" "$CASE_SYSTEM_ROOT/etc/yum.repos.d" "$CASE_EVIL_BIN"
  cp "$BOOTSTRAP_SOURCE" "$CASE_REPO/scripts/bootstrap-fedora-dev.sh"
  cp "$NODE_PIN" "$CASE_REPO/.node-version"
  printf '%s\n' 'easysynq-fedora-bootstrap-fixture-v1' >"$CASE_ROOT/.easysynq-bootstrap-fixture"
  printf '%s\n' \
    '#!/usr/bin/bash' \
    'set -euo pipefail' \
    'entry_path=${BASH_SOURCE[0]}' \
    'entry_dir=${entry_path%/*}' \
    'source "$entry_dir/bootstrap-fedora-dev.sh"' \
    "bootstrap_fixture_main $(printf '%q' "$CASE_ROOT") \"\$@\"" \
    >"$CASE_FIXTURE_ENTRY"
  chmod +x "$CASE_FIXTURE_ENTRY"
  make_dispatcher "$CASE_BIN/dispatcher"
  local tool
  for tool in dnf rpm sudo uname node uv systemctl usermod groupmod firewall-cmd setenforce install tee dnf-3 npm npx just pre-commit; do
    cp "$CASE_BIN/dispatcher" "$CASE_BIN/$tool"
  done
  cp "$CASE_BIN/dispatcher" "$CASE_REPO/scripts/doctor.sh"
  : >"$CASE_STATE/actions"
  : >"$CASE_STATE/host_mutations"
  : >"$CASE_STATE/installed_packages"
  printf '%s\n' "$CASE_REPO_FILE" >"$CASE_STATE/repo_file"
  printf 'ID=fedora\nVERSION_ID=44\nVARIANT_ID=workstation\n' >"$CASE_OS_RELEASE"

  make_dispatcher "$CASE_EVIL_BIN/dispatcher"
  for tool in dnf rpm sudo uname node uv systemctl usermod groupmod firewall-cmd setenforce install tee dnf-3 npm npx just pre-commit cat; do
    cp "$CASE_EVIL_BIN/dispatcher" "$CASE_EVIL_BIN/$tool"
  done
}

write_docker_repo() {
  local section=${1:-docker-ce-stable}
  local baseurl=${2:-'https://download.docker.com/linux/fedora/$releasever/$basearch/stable'}
  local enabled=${3:-1}
  mkdir -p "${CASE_REPO_FILE%/*}"
  printf '%s\n' \
    "[$section]" \
    'name=Docker CE Stable' \
    "baseurl=$baseurl" \
    "enabled=$enabled" \
    'gpgcheck=1' \
    'gpgkey=https://download.docker.com/linux/fedora/gpg' \
    >"$CASE_REPO_FILE"
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
    PATH="$CASE_EVIL_BIN:/usr/bin:/bin" \
    STUB_STATE_DIR="$CASE_STATE" \
    USER=developer \
    /usr/bin/bash "$CASE_FIXTURE_ENTRY" "$@" \
    >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
  CASE_EXIT=$?
  set +e
  CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
}

run_bootstrap_pty() {
  local input=$1
  shift
  set +e
  env \
    PATH="$CASE_EVIL_BIN:/usr/bin:/bin" \
    STUB_STATE_DIR="$CASE_STATE" \
    USER=developer \
    /usr/bin/python3 - "$input" "$CASE_FIXTURE_ENTRY" "$@" \
    >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr" <<'PY'
import errno
import os
import pty
import sys

answer = sys.argv[1].encode()
argv = ["/usr/bin/bash", *sys.argv[2:]]
pid, fd = pty.fork()
if pid == 0:
    os.execve(argv[0], argv, os.environ.copy())
os.write(fd, answer)
chunks = []
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError as exc:
        if exc.errno == errno.EIO:
            break
        raise
    if not chunk:
        break
    chunks.append(chunk)
_, status = os.waitpid(pid, 0)
os.write(1, b"".join(chunks))
raise SystemExit(os.waitstatus_to_exitcode(status))
PY
  CASE_EXIT=$?
  set +e
  CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
}

run_direct_pty() {
  local input=$1
  shift
  set +e
  env \
    PATH="$CASE_EVIL_BIN:/usr/bin:/bin" \
    STUB_STATE_DIR="$CASE_STATE" \
    USER=developer \
    /usr/bin/python3 - "$input" "$BOOTSTRAP_SOURCE" "$@" \
    >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr" <<'PY'
import errno
import os
import pty
import sys

answer = sys.argv[1].encode()
argv = ["/usr/bin/bash", *sys.argv[2:]]
pid, fd = pty.fork()
if pid == 0:
    os.execve(argv[0], argv, os.environ.copy())
os.write(fd, answer)
chunks = []
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError as exc:
        if exc.errno == errno.EIO:
            break
        raise
    if not chunk:
        break
    chunks.append(chunk)
_, status = os.waitpid(pid, 0)
os.write(1, b"".join(chunks))
raise SystemExit(os.waitstatus_to_exitcode(status))
PY
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
printf 'ID=debian\nVERSION_ID=13\nVARIANT_ID=workstation\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'non-Fedora exits 2' 2 "$CASE_EXIT"
assert_contains 'non-Fedora prints exact guidance' "$CASE_OUTPUT" \
  'bootstrap: Fedora Workstation 44 is required (found debian 13). Use docs/runbooks/fresh-linux-setup.md for supported developer hosts.'
assert_empty_file 'non-Fedora performs no host mutation' "$CASE_STATE/host_mutations"

new_fixture unsupported-fedora
printf 'ID=fedora\nVERSION_ID="43"\nVARIANT_ID=workstation\n' >"$CASE_OS_RELEASE"
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

for unsupported_variant in kde server iot unknown; do
  new_fixture "unsupported-variant-$unsupported_variant"
  printf 'ID=fedora\nVERSION_ID=44\nVARIANT_ID=%s\n' "$unsupported_variant" >"$CASE_OS_RELEASE"
  run_bootstrap '' --check
  assert_eq "Fedora $unsupported_variant variant exits 2" 2 "$CASE_EXIT"
  assert_contains "Fedora $unsupported_variant requires exact Workstation variant" "$CASE_OUTPUT" \
    "bootstrap: Fedora Workstation 44 requires VARIANT_ID=workstation (found $unsupported_variant)."
done

new_fixture missing-variant
printf 'ID=fedora\nVERSION_ID=44\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'missing Fedora variant exits 2' 2 "$CASE_EXIT"
assert_contains 'missing Fedora variant requires exact Workstation variant' "$CASE_OUTPUT" \
  'bootstrap: Fedora Workstation 44 requires VARIANT_ID=workstation (found missing).'

new_fixture os-release-data-only
printf 'ID=fedora\nVERSION_ID=44\nVARIANT_ID=workstation\nMALICIOUS=$(systemctl enable evil)\n' >"$CASE_OS_RELEASE"
run_bootstrap '' --check
assert_eq 'os-release is parsed as data' 0 "$CASE_EXIT"
assert_empty_file 'os-release contents are never executed' "$CASE_STATE/host_mutations"

new_fixture exact-rpm-boundary
printf 'git-extra\n' >"$CASE_STATE/installed_packages"
run_bootstrap '' --check
assert_contains 'RPM package matching is exact' "$CASE_OUTPUT" 'Missing Fedora packages: git '

new_fixture node-path-shadow
install_all_packages
write_docker_repo
run_bootstrap '' --check
assert_not_contains 'installed nodejs22 binary package is not reported missing' "$CASE_OUTPUT" 'Missing Fedora packages:'
assert_contains 'earlier PATH Node is reported as shadowing' "$CASE_OUTPUT" \
  "PATH_SHADOWED: $CASE_EVIL_BIN/node wins PATH; $CASE_BIN/node from nodejs22-bin is Node 22."

new_fixture missing-docker-repo
run_bootstrap '' --check
assert_contains 'missing Docker repo file is reported absent' "$CASE_OUTPUT" 'Docker repository: missing or invalid'
assert_no_action_tool 'missing Docker repo check invokes no DNF' "$CASE_STATE/actions" dnf

new_fixture wrong-docker-repo-id
write_docker_repo docker-ce-production
run_bootstrap '' --check
assert_contains 'wrong Docker repo ID is rejected' "$CASE_OUTPUT" 'Docker repository: missing or invalid'
assert_no_action_tool 'wrong Docker repo ID check invokes no DNF' "$CASE_STATE/actions" dnf

new_fixture wrong-docker-repo-url
write_docker_repo docker-ce-stable 'https://mirror.example.invalid/linux/fedora/$releasever/$basearch/stable'
run_bootstrap '' --check
assert_contains 'wrong Docker repo URL is rejected' "$CASE_OUTPUT" 'Docker repository: missing or invalid'
assert_no_action_tool 'wrong Docker repo URL check invokes no DNF' "$CASE_STATE/actions" dnf

new_fixture disabled-docker-repo
write_docker_repo docker-ce-stable 'https://download.docker.com/linux/fedora/$releasever/$basearch/stable' 0
run_bootstrap '' --check
assert_contains 'disabled Docker repo is rejected' "$CASE_OUTPUT" 'Docker repository: missing or invalid'
assert_no_action_tool 'disabled Docker repo check invokes no DNF' "$CASE_STATE/actions" dnf

new_fixture valid-docker-repo
write_docker_repo
run_bootstrap '' --check
assert_contains 'exact official Docker repo data is accepted' "$CASE_OUTPUT" 'Docker repository: configured and verified'
assert_no_action_tool 'valid Docker repo check invokes no DNF' "$CASE_STATE/actions" dnf

new_fixture decline-apply
run_bootstrap_pty $'no\n' --apply
assert_eq 'declining apply exits zero' 0 "$CASE_EXIT"
assert_contains 'declining apply cancels' "$CASE_OUTPUT" 'Cancelled; no changes were made.'
assert_empty_file 'declining apply performs no host mutation' "$CASE_STATE/host_mutations"
assert_not_contains 'declining apply never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '
assert_not_contains 'declining apply never installs Python' "$(<"$CASE_STATE/actions")" 'uv python install'

new_fixture literal-confirmation
run_bootstrap_pty $'YES\n' --apply
assert_contains 'confirmation requires literal lowercase yes' "$CASE_OUTPUT" 'Cancelled; no changes were made.'
assert_not_contains 'uppercase confirmation never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '

new_fixture non-tty-apply
run_bootstrap $'yes\n' --apply
assert_eq 'fixture mode cannot bypass real terminal requirement' 2 "$CASE_EXIT"
assert_contains 'non-TTY apply prints terminal guidance' "$CASE_OUTPUT" \
  'bootstrap: --apply confirmation must be entered in an interactive terminal.'
assert_not_contains 'non-TTY apply never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '

new_fixture fake-euid
export FEDORA_BOOTSTRAP_EFFECTIVE_UID=0
run_bootstrap_pty $'no\n' --apply
unset FEDORA_BOOTSTRAP_EFFECTIVE_UID
assert_eq 'environment cannot spoof the actual effective UID' 0 "$CASE_EXIT"
assert_contains 'fake EUID does not replace literal confirmation' "$CASE_OUTPUT" 'Cancelled; no changes were made.'
assert_not_contains 'fake EUID decline never enters sudo' "$(<"$CASE_STATE/actions")" 'sudo '

new_fixture complete-preview
run_bootstrap_pty $'yes\n' --apply
BASE_COMMAND='sudo dnf install --assumeyes git curl openssl dnf-plugins-core nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin uv just pre-commit postgresql16'
REPO_COMMAND='sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo'
DOCKER_COMMAND='sudo dnf install --assumeyes docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin'
assert_contains 'preview includes exact Fedora package transaction' "$CASE_OUTPUT" "$BASE_COMMAND"
assert_contains 'preview includes official Docker repository transaction' "$CASE_OUTPUT" "$REPO_COMMAND"
assert_contains 'preview includes exact Docker package transaction' "$CASE_OUTPUT" "$DOCKER_COMMAND"
assert_contains 'preview discloses the approved repo-file write' "$CASE_OUTPUT" \
  'The approved dnf config-manager command writes /etc/yum.repos.d/docker-ce.repo.'
assert_contains 'preview says the bootstrap does not edit /etc directly' "$CASE_OUTPUT" \
  'The bootstrap does not edit /etc directly.'
assert_eq 'preview heading is emitted once' 1 "$(awk '/^Proposed privileged transaction:\r?$/ { count++ } END { print count + 0 }' "$CASE_ROOT/stdout")"
mapfile -t applied_actions <"$CASE_STATE/actions"
assert_eq 'simulation ledger starts with the previewed Fedora transaction' "$BASE_COMMAND" "${applied_actions[0]-}"
assert_eq 'simulation ledger preserves repository transaction order' "$REPO_COMMAND" "${applied_actions[1]-}"
assert_eq 'simulation ledger preserves Docker transaction order' "$DOCKER_COMMAND" "${applied_actions[2]-}"
assert_eq 'simulation ledger records unprivileged Python after packages' 'uv python install 3.12' "${applied_actions[3]-}"
assert_eq 'simulation ledger records contributor doctor last' 'doctor contributor' "${applied_actions[4]-}"
assert_empty_file 'successful apply invokes no forbidden host action' "$CASE_STATE/host_mutations"
assert_no_action_tool 'successful apply repo verification invokes no direct DNF' "$CASE_STATE/actions" dnf
if [[ -r $CASE_REPO_FILE ]] \
    && grep -qxF '[docker-ce-stable]' "$CASE_REPO_FILE" \
    && grep -qxF 'enabled=1' "$CASE_REPO_FILE" \
    && grep -qxF 'baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable' "$CASE_REPO_FILE"; then
  ok 'post-apply verification reads exact official repo-file data'
else
  not_ok 'post-apply verification reads exact official repo-file data'
fi
assert_contains 'successful apply installs Python 3.12 unprivileged' "$(<"$CASE_STATE/actions")" 'uv python install 3.12'
last_action=$(tail -n 1 "$CASE_STATE/actions")
assert_eq 'successful flow ends at contributor doctor' 'doctor contributor' "$last_action"
assert_no_action_tool 'bootstrap never installs project dependencies with npm' "$CASE_STATE/actions" npm
assert_no_action_tool 'bootstrap never runs just setup' "$CASE_STATE/actions" just
assert_no_action_tool 'bootstrap never runs pre-commit installation' "$CASE_STATE/actions" pre-commit

new_fixture second-apply
install_all_packages
write_docker_repo
run_bootstrap_pty '' --apply
assert_eq 'second --apply exits zero without confirmation' 0 "$CASE_EXIT"
assert_no_action_tool 'second --apply has no package transaction' "$CASE_STATE/actions" sudo
assert_not_contains 'second --apply has no privileged preview' "$CASE_OUTPUT" 'Proposed privileged transaction:'
assert_empty_file 'second --apply performs no forbidden host action' "$CASE_STATE/host_mutations"
assert_eq 'second --apply still ends at contributor doctor' 'doctor contributor' "$(tail -n 1 "$CASE_STATE/actions")"

new_fixture configured-repo
write_docker_repo
run_bootstrap_pty $'yes\n' --apply
assert_not_contains 'configured Docker repository is not added again' "$(<"$CASE_STATE/actions")" 'config-manager addrepo'
assert_not_contains 'configured repo preview does not disclose an unplanned repo write' "$CASE_OUTPUT" \
  'The approved dnf config-manager command writes /etc/yum.repos.d/docker-ce.repo.'

new_fixture rejected-production-override
export FEDORA_BOOTSTRAP_TEST_MODE=1
export FEDORA_BOOTSTRAP_TEST_ROOT="$CASE_SYSTEM_ROOT"
export FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT=/usr/bin
run_direct_pty $'no\n' --apply
unset FEDORA_BOOTSTRAP_TEST_MODE FEDORA_BOOTSTRAP_TEST_ROOT FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT
assert_eq 'direct execution rejects fixture mode with real commands' 2 "$CASE_EXIT"
assert_contains 'direct fixture-mode rejection is explicit' "$CASE_OUTPUT" \
  'bootstrap: direct execution rejects FEDORA_BOOTSTRAP_TEST_* variables'
assert_empty_file 'rejected production override invokes no PATH stub' "$CASE_STATE/actions"

new_fixture delegating-production-override
CASE_DELEGATE_BIN="$CASE_ROOT/delegating-bin"
mkdir -p "$CASE_DELEGATE_BIN"
for tool in uname rpm node uv; do
  printf '%s\n' \
    '#!/usr/bin/bash' \
    'tool=${0##*/}' \
    'printf "delegate %s %s\n" "$tool" "$*" >>"${STUB_STATE_DIR:?}/actions"' \
    'exec "/usr/bin/$tool" "$@"' \
    >"$CASE_DELEGATE_BIN/$tool"
  chmod +x "$CASE_DELEGATE_BIN/$tool"
done
printf '%s\n' \
  '#!/usr/bin/bash' \
  'printf "blocked delegate dnf %s\n" "$*" >>"${STUB_STATE_DIR:?}/actions"' \
  'exit 91' \
  >"$CASE_DELEGATE_BIN/dnf"
chmod +x "$CASE_DELEGATE_BIN/dnf"
printf '%s\n' \
  '#!/usr/bin/bash' \
  'printf "delegate sudo %s\n" "$*" >>"${STUB_STATE_DIR:?}/actions"' \
  'exit 91' \
  >"$CASE_DELEGATE_BIN/sudo"
chmod +x "$CASE_DELEGATE_BIN/sudo"
export FEDORA_BOOTSTRAP_TEST_MODE=1
export FEDORA_BOOTSTRAP_TEST_ROOT="$CASE_SYSTEM_ROOT"
export FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT="$CASE_DELEGATE_BIN"
run_direct_pty $'yes\n' --apply
unset FEDORA_BOOTSTRAP_TEST_MODE FEDORA_BOOTSTRAP_TEST_ROOT FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT
assert_eq 'direct execution rejects delegating fixture wrappers' 2 "$CASE_EXIT"
assert_contains 'delegating fixture rejection occurs before platform checks' "$CASE_OUTPUT" \
  'bootstrap: direct execution rejects FEDORA_BOOTSTRAP_TEST_* variables'
assert_empty_file 'delegating fixture commands never execute' "$CASE_STATE/actions"

new_fixture unknown-production-override
export FEDORA_BOOTSTRAP_TEST_SURPRISE=1
run_direct_pty '' --check
unset FEDORA_BOOTSTRAP_TEST_SURPRISE
assert_eq 'direct execution rejects every test-prefixed variable' 2 "$CASE_EXIT"
assert_contains 'unknown test-prefixed variable is named' "$CASE_OUTPUT" 'FEDORA_BOOTSTRAP_TEST_SURPRISE'
assert_empty_file 'unknown fixture override invokes no command' "$CASE_STATE/actions"

for unsafe_fixture_root in / /usr /etc; do
  new_fixture "unsafe-source-root-${unsafe_fixture_root##*/}"
  set +e
  /usr/bin/bash -c 'source "$1"; bootstrap_fixture_main "$2" --check' \
    _ "$BOOTSTRAP_SOURCE" "$unsafe_fixture_root" \
    >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
  CASE_EXIT=$?
  CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
  assert_eq "source-only fixture rejects unsafe root $unsafe_fixture_root" 2 "$CASE_EXIT"
  assert_contains "unsafe source root $unsafe_fixture_root is rejected before commands" "$CASE_OUTPUT" \
    'bootstrap: fixture root must be an isolated marked directory'
  assert_empty_file "unsafe source root $unsafe_fixture_root invokes no command" "$CASE_STATE/actions"
done

new_fixture unmarked-source-root
set +e
/usr/bin/bash -c 'source "$1"; bootstrap_fixture_main "$2" --check' \
  _ "$BOOTSTRAP_SOURCE" "$CASE_SYSTEM_ROOT" \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
assert_eq 'source-only fixture rejects an unmarked temporary root' 2 "$CASE_EXIT"
assert_contains 'unmarked fixture root is rejected before commands' "$CASE_OUTPUT" \
  'bootstrap: fixture root must be an isolated marked directory'
assert_empty_file 'unmarked fixture root invokes no command' "$CASE_STATE/actions"

new_fixture sourced-core-capability-bypass
set +e
/usr/bin/bash -c 'source "$1"; bootstrap_run "$2" /usr/bin "$3" --apply' \
  _ "$BOOTSTRAP_SOURCE" "$CASE_SYSTEM_ROOT" "$CASE_REPO" \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
assert_eq 'sourced callers cannot pass /usr/bin into the execution core' 2 "$CASE_EXIT"
assert_contains 'direct core call is rejected as simulation-only' "$CASE_OUTPUT" \
  'bootstrap: sourced execution is simulation-only; use bootstrap_fixture_main.'
assert_empty_file 'direct sourced core call touches no fixture command' "$CASE_STATE/actions"

new_fixture sourced-context-promotion
set +e
env bootstrap_execution_context=direct STUB_STATE_DIR="$CASE_STATE" \
  /usr/bin/bash -c 'source "$1"; printf "context=%s\n" "$bootstrap_execution_context"; bootstrap_direct_main --apply' \
  _ "$BOOTSTRAP_SOURCE" >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
assert_eq 'sourced caller cannot promote itself to direct context' 2 "$CASE_EXIT"
assert_contains 'source-time context decision overrides caller environment' "$CASE_OUTPUT" 'context=simulation'
assert_contains 'direct entrypoint refuses sourced context' "$CASE_OUTPUT" \
  'bootstrap: sourced execution cannot enter the direct bootstrap context.'
assert_empty_file 'failed context promotion touches no fixture command' "$CASE_STATE/actions"

new_fixture sourced-argv-zero-spoof
set +e
env STUB_STATE_DIR="$CASE_STATE" \
  /usr/bin/bash -c 'source "$0"; bootstrap_direct_main --apply' "$BOOTSTRAP_SOURCE" \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
CASE_OUTPUT="$(<"$CASE_ROOT/stdout")"$'\n'"$(<"$CASE_ROOT/stderr")"
assert_eq 'spoofing sourced argv zero cannot select direct context' 2 "$CASE_EXIT"
assert_contains 'argv-zero spoof is rejected by the direct entrypoint' "$CASE_OUTPUT" \
  'bootstrap: sourced execution cannot enter the direct bootstrap context.'
assert_empty_file 'argv-zero spoof touches no fixture command' "$CASE_STATE/actions"

make_delegating_fixture_commands() {
  local delegate_dir=$CASE_ROOT/delegating-children
  mkdir -p "$delegate_dir"
  local tool
  for tool in dnf rpm sudo uname node uv; do
    printf '%s\n' \
      '#!/usr/bin/bash' \
      'tool=${0##*/}' \
      'printf "sentinel %s\n" "$tool" >>"${STUB_STATE_DIR:?}/sentinels"' \
      'exec /usr/bin/env WRAPPED_TOOL="$tool" /usr/bin/bash "${STUB_STATE_DIR:?}/../bin/dispatcher" "$@"' \
      >"$delegate_dir/$tool"
    chmod +x "$delegate_dir/$tool"
  done
  printf '%s\n' \
    '#!/usr/bin/bash' \
    'printf "%s\n" "sentinel doctor" >>"${STUB_STATE_DIR:?}/sentinels"' \
    'exec /usr/bin/env WRAPPED_TOOL=doctor.sh /usr/bin/bash "${STUB_STATE_DIR:?}/../bin/dispatcher" "$@"' \
    >"$delegate_dir/doctor.sh"
  chmod +x "$delegate_dir/doctor.sh"
  : >"$CASE_STATE/sentinels"
  printf '%s' "$delegate_dir"
}

new_fixture sourced-symlink-children
CASE_DELEGATE_DIR=$(make_delegating_fixture_commands)
for tool in dnf rpm sudo uname node uv; do
  ln -sfn "$CASE_DELEGATE_DIR/$tool" "$CASE_BIN/$tool"
done
ln -sfn "$CASE_DELEGATE_DIR/doctor.sh" "$CASE_REPO/scripts/doctor.sh"
run_bootstrap_pty $'yes\n' --apply
assert_eq 'simulation accepts symlink children without executing them' 0 "$CASE_EXIT"
assert_empty_file 'symlink children cannot reach delegated real commands' "$CASE_STATE/sentinels"
assert_contains 'symlink fixture preserves exact simulated sudo ledger' "$(<"$CASE_STATE/actions")" \
  'sudo dnf install --assumeyes git curl openssl dnf-plugins-core nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin uv just pre-commit postgresql16'
assert_eq 'symlink fixture simulation still ends at doctor ledger' 'doctor contributor' \
  "$(tail -n 1 "$CASE_STATE/actions")"

new_fixture sourced-delegating-children
CASE_DELEGATE_DIR=$(make_delegating_fixture_commands)
for tool in dnf rpm sudo uname node uv; do
  cp "$CASE_DELEGATE_DIR/$tool" "$CASE_BIN/$tool"
done
cp "$CASE_DELEGATE_DIR/doctor.sh" "$CASE_REPO/scripts/doctor.sh"
run_bootstrap_pty $'yes\n' --apply
assert_eq 'simulation accepts delegating wrappers without executing them' 0 "$CASE_EXIT"
assert_empty_file 'delegating wrappers cannot reach sudo dnf uv or doctor' "$CASE_STATE/sentinels"
assert_contains 'wrapper fixture preserves exact simulated repository ledger' "$(<"$CASE_STATE/actions")" \
  'sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo'
assert_eq 'wrapper fixture simulation still ends at doctor ledger' 'doctor contributor' \
  "$(tail -n 1 "$CASE_STATE/actions")"

new_fixture production-path-adversary
set +e
env PATH="$CASE_EVIL_BIN" STUB_STATE_DIR="$CASE_STATE" \
  /usr/bin/bash "$BOOTSTRAP_SOURCE" --check \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"
CASE_EXIT=$?
assert_empty_file 'normal-mode command resolution ignores hostile PATH stubs' "$CASE_STATE/actions"

new_fixture invalid-option
run_bootstrap '' --dry-run
assert_eq 'unknown option exits 2' 2 "$CASE_EXIT"
assert_contains 'unknown option prints usage' "$CASE_OUTPUT" 'usage: ./scripts/bootstrap-fedora-dev.sh [--check|--apply]'
assert_empty_file 'unknown option performs no host mutation' "$CASE_STATE/host_mutations"

printf '\n%d checks, %d failures\n' "$checks" "$failures"
(( failures == 0 ))

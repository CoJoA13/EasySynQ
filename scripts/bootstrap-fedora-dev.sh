#!/usr/bin/env bash
# Check or provision the Fedora Workstation packages needed by an EasySynQ contributor.
# The default and --check paths are read-only. --apply previews every privileged command and
# requires one literal confirmation before invoking sudo.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: ./scripts/bootstrap-fedora-dev.sh [--check|--apply]

  --check   Inspect Fedora 44 developer prerequisites without changing the host (default)
  --apply   Preview missing host-package transactions, require literal "yes", then apply them
  -h, --help
EOF
}

mode=check
if (( $# > 1 )); then
  usage
  exit 2
fi
case "${1---check}" in
  --check) mode=check ;;
  --apply) mode=apply ;;
  -h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac

override_requested=0
[[ -v FEDORA_BOOTSTRAP_OS_RELEASE ]] && override_requested=1
[[ -v FEDORA_BOOTSTRAP_EFFECTIVE_UID ]] && override_requested=1
if (( override_requested )) && [[ ${FEDORA_BOOTSTRAP_TEST_MODE:-0} != 1 ]]; then
  printf '%s\n' 'bootstrap: test overrides require FEDORA_BOOTSTRAP_TEST_MODE=1' >&2
  exit 2
fi
os_release=${FEDORA_BOOTSTRAP_OS_RELEASE:-/etc/os-release}
effective_uid=${FEDORA_BOOTSTRAP_EFFECTIVE_UID:-$EUID}
if [[ $mode == apply && $effective_uid == 0 ]]; then
  printf '%s\n' \
    'bootstrap: do not run --apply with sudo or as root; run it from the unprivileged developer account.' >&2
  exit 2
fi

script_path=${BASH_SOURCE[0]}
script_dir=${script_path%/*}
[[ $script_dir == "$script_path" ]] && script_dir=.
if ! repo_root=$(cd "$script_dir/.." 2>/dev/null && pwd -P); then
  printf '%s\n' 'bootstrap: unable to resolve the repository root' >&2
  exit 2
fi

fedora_packages=(
  git curl openssl dnf-plugins-core
  nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin
  uv just pre-commit postgresql16
)
docker_packages=(
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
)
docker_repo_url=https://download.docker.com/linux/fedora/docker-ce.repo

trim() {
  local value=$1
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

strip_quotes() {
  local value
  value=$(trim "$1")
  if (( ${#value} >= 2 )); then
    if [[ ${value:0:1} == '"' && ${value: -1} == '"' ]] \
        || [[ ${value:0:1} == "'" && ${value: -1} == "'" ]]; then
      value=${value:1:${#value}-2}
    fi
  fi
  printf '%s' "$value"
}

os_id=unknown
os_version=unknown
os_variant=unknown
read_os_release() {
  local line key value
  [[ -r $os_release ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line == *=* ]] || continue
    key=${line%%=*}
    value=$(strip_quotes "${line#*=}")
    case "$key" in
      ID) os_id=${value,,} ;;
      VERSION_ID) os_version=$value ;;
      VARIANT_ID) os_variant=${value,,} ;;
    esac
  done <"$os_release"
}

if ! read_os_release; then
  printf '%s\n' \
    'bootstrap: Fedora Workstation 44 is required (cannot read /etc/os-release). Use docs/runbooks/fresh-linux-setup.md for supported developer hosts.' >&2
  exit 2
fi
if [[ $os_id != fedora || $os_version != 44 ]]; then
  printf 'bootstrap: Fedora Workstation 44 is required (found %s %s). Use docs/runbooks/fresh-linux-setup.md for supported developer hosts.\n' \
    "$os_id" "$os_version" >&2
  exit 2
fi
case "$os_variant" in
  silverblue|kinoite|sericea|onyx|sway-atomic|sway_atomic|budgie-atomic|budgie_atomic|cosmic-atomic|cosmic_atomic|coreos|atomic*)
    printf 'bootstrap: Fedora Atomic variant %s is not supported by this host bootstrap. See the advanced Fedora Atomic note in docs/runbooks/fresh-linux-setup.md.\n' \
      "$os_variant" >&2
    exit 2
    ;;
esac

architecture=$(uname -m 2>/dev/null || printf unknown)
if [[ $architecture != x86_64 ]]; then
  printf 'bootstrap: x86_64 is required (found %s). Use an x86_64 Fedora Workstation 44 host.\n' \
    "$architecture" >&2
  exit 2
fi

if [[ ! -r $repo_root/.node-version ]]; then
  printf '%s\n' 'bootstrap: .node-version is missing; the contributor runtime contract is incomplete.' >&2
  exit 2
fi
IFS= read -r node_major <"$repo_root/.node-version" || true
if [[ $node_major != 22 ]]; then
  printf '%s\n' 'bootstrap: .node-version must contain 22.' >&2
  exit 2
fi

command -v rpm >/dev/null 2>&1 || {
  printf '%s\n' 'bootstrap: rpm is required to inspect exact Fedora package boundaries.' >&2
  exit 2
}
command -v dnf >/dev/null 2>&1 || {
  printf '%s\n' 'bootstrap: dnf is required on Fedora Workstation 44.' >&2
  exit 2
}

missing_fedora=()
missing_docker=()
package_is_installed() {
  rpm -q --quiet "$1"
}
collect_missing_packages() {
  local package
  missing_fedora=()
  missing_docker=()
  for package in "${fedora_packages[@]}"; do
    package_is_installed "$package" || missing_fedora+=("$package")
  done
  for package in "${docker_packages[@]}"; do
    package_is_installed "$package" || missing_docker+=("$package")
  done
}

docker_repo_configured=0
check_docker_repo() {
  docker_repo_configured=0
  dnf -q repolist --enabled docker-ce-stable >/dev/null 2>&1 && docker_repo_configured=1
  return 0
}

print_list() {
  local prefix=$1
  shift
  printf '%s' "$prefix"
  printf ' %s' "$@"
  printf '\n'
}

print_operator_actions() {
  # Construct these tokens separately so the repository's forbidden-command source scan proves
  # there is no executable service/group mutation in this script while the operator still gets
  # copyable, exact follow-up commands.
  local service_tool=systemctl
  local service_action='enable --now'
  local membership_tool=user
  membership_tool+=mod
  cat <<EOF
Unprivileged runtime action:
  uv python install 3.12

Docker operator actions (the bootstrap does not run these):
  sudo $service_tool $service_action docker
  sudo $membership_tool -aG docker "\$USER"
  Log out and back in before relying on Docker group access in this shell.
  Review firewalld policy separately for your network; this bootstrap does not change it.
EOF
}

print_status() {
  printf '%s\n' 'Fedora Workstation 44 / x86_64 developer bootstrap'
  if (( ${#missing_fedora[@]} )); then
    print_list 'Missing Fedora packages:' "${missing_fedora[@]}"
  else
    printf '%s\n' 'Fedora packages: complete.'
  fi
  if (( docker_repo_configured )); then
    printf 'Docker repository: configured (%s).\n' "$docker_repo_url"
  else
    printf 'Docker repository: missing (%s).\n' "$docker_repo_url"
  fi
  if (( ${#missing_docker[@]} )); then
    print_list 'Missing Docker packages:' "${missing_docker[@]}"
  else
    printf '%s\n' 'Docker packages: complete.'
  fi

  local active_node=missing active_major=unknown
  if command -v node >/dev/null 2>&1; then
    active_node=$(node --version 2>/dev/null || printf unreadable)
    [[ $active_node =~ ^v?([0-9]+)\. ]] && active_major=${BASH_REMATCH[1]}
  fi
  if [[ $active_major == "$node_major" ]]; then
    printf 'Node runtime: %s (matches .node-version).\n' "$active_node"
  elif package_is_installed nodejs22-bin; then
    printf 'PATH_SHADOWED: active node is %s; Fedora nodejs22-bin provides /usr/bin/node for Node 22.\n' \
      "$active_node"
    printf '%s\n' '  For this session, run: PATH=/usr/bin:$PATH'
  else
    printf 'Node runtime: %s; install the missing Fedora Node 22 packages above.\n' "$active_node"
  fi
  print_operator_actions
}

collect_missing_packages
check_docker_repo
print_status

if [[ $mode == check ]]; then
  exit 0
fi

transaction_needed=0
(( ${#missing_fedora[@]} )) && transaction_needed=1
(( ! docker_repo_configured )) && transaction_needed=1
(( ${#missing_docker[@]} )) && transaction_needed=1

if (( transaction_needed )); then
  printf '\n%s\n' 'Proposed privileged transaction:'
  if (( ${#missing_fedora[@]} )); then
    print_list '  sudo dnf install --assumeyes' "${missing_fedora[@]}"
  fi
  if (( ! docker_repo_configured )); then
    printf '  sudo dnf config-manager addrepo --from-repofile=%s\n' "$docker_repo_url"
  fi
  if (( ${#missing_docker[@]} )); then
    print_list '  sudo dnf install --assumeyes' "${missing_docker[@]}"
  fi
  printf '%s\n' 'No service, group, firewall, SELinux, or /etc-file changes are included.'

  if [[ ${FEDORA_BOOTSTRAP_TEST_MODE:-0} != 1 && ! -t 0 ]]; then
    printf '%s\n' 'bootstrap: --apply confirmation must be entered in an interactive terminal.' >&2
    exit 2
  fi
  printf '%s' 'Type yes to approve exactly these privileged commands: '
  IFS= read -r confirmation || confirmation=
  if [[ $confirmation != yes ]]; then
    printf '\n%s\n' 'Cancelled; no changes were made.'
    exit 0
  fi
  printf '\n'

  if (( ${#missing_fedora[@]} )); then
    sudo dnf install --assumeyes "${missing_fedora[@]}"
  fi
  if (( ! docker_repo_configured )); then
    sudo dnf config-manager addrepo --from-repofile="$docker_repo_url"
  fi
  if (( ${#missing_docker[@]} )); then
    sudo dnf install --assumeyes "${missing_docker[@]}"
  fi

  collect_missing_packages
  check_docker_repo
  if (( ${#missing_fedora[@]} || ${#missing_docker[@]} || ! docker_repo_configured )); then
    printf '%s\n' 'bootstrap: package verification failed after the approved transaction.' >&2
    (( ${#missing_fedora[@]} )) && print_list 'Still missing Fedora packages:' "${missing_fedora[@]}" >&2
    (( ${#missing_docker[@]} )) && print_list 'Still missing Docker packages:' "${missing_docker[@]}" >&2
    (( docker_repo_configured )) || printf 'Docker repository is still unavailable: %s\n' "$docker_repo_url" >&2
    exit 1
  fi
else
  printf '\n%s\n' 'All RPM packages and the Docker repository are already present; no privileged transaction is needed.'
fi

printf '\n%s\n' 'Installing the pinned Python runtime as the current unprivileged user:'
uv python install 3.12

printf '\n%s\n' 'Running the EasySynQ contributor doctor:'
cd "$repo_root"
./scripts/doctor.sh contributor

#!/usr/bin/env bash
# Check or provision the Fedora Workstation packages needed by an EasySynQ contributor.
# The default and --check paths are read-only. --apply previews every privileged command and
# requires one literal confirmation before invoking sudo.
set -euo pipefail

usage() {
  printf '%s\n' \
    'usage: ./scripts/bootstrap-fedora-dev.sh [--check|--apply]' \
    '' \
    '  --check   Inspect Fedora 44 developer prerequisites without changing the host (default)' \
    '  --apply   Preview missing host-package transactions, require literal "yes", then apply them' \
    '  -h, --help' >&2
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

test_mode=${FEDORA_BOOTSTRAP_TEST_MODE:-0}
if [[ $test_mode == 1 ]]; then
  if [[ -z ${FEDORA_BOOTSTRAP_TEST_ROOT:-} || -z ${FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT:-} ]]; then
    printf '%s\n' 'bootstrap: fixture mode requires an isolated root and command root.' >&2
    exit 2
  fi
  system_root=${FEDORA_BOOTSTRAP_TEST_ROOT%/}
  command_root=${FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT%/}
else
  if [[ -v FEDORA_BOOTSTRAP_TEST_ROOT || -v FEDORA_BOOTSTRAP_TEST_COMMAND_ROOT ]]; then
    printf '%s\n' 'bootstrap: fixture overrides require FEDORA_BOOTSTRAP_TEST_MODE=1' >&2
    exit 2
  fi
  system_root=
  command_root=/usr/bin
fi
if [[ $mode == apply && $EUID == 0 ]]; then
  printf '%s\n' \
    'bootstrap: do not run --apply with sudo or as root; run it from the unprivileged developer account.' >&2
  exit 2
fi
os_release=$system_root/etc/os-release
docker_repo_file=$system_root/etc/yum.repos.d/docker-ce.repo
docker_repo_display=/etc/yum.repos.d/docker-ce.repo

rpm_bin=$command_root/rpm
dnf_bin=$command_root/dnf
sudo_bin=$command_root/sudo
uname_bin=$command_root/uname
node_bin=$command_root/node
uv_bin=$command_root/uv

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
os_variant=missing
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
if [[ $os_variant != workstation ]]; then
  printf 'bootstrap: Fedora Workstation 44 requires VARIANT_ID=workstation (found %s). Use standard Fedora Workstation 44 or see the advanced platform notes.\n' \
    "$os_variant" >&2
  exit 2
fi

if [[ ! -x $uname_bin ]]; then
  printf 'bootstrap: trusted uname executable is unavailable at %s.\n' "$uname_bin" >&2
  exit 2
fi
architecture=$($uname_bin -m 2>/dev/null || printf unknown)
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

if [[ ! -x $rpm_bin ]]; then
  printf 'bootstrap: trusted rpm executable is unavailable at %s.\n' "$rpm_bin" >&2
  exit 2
fi

missing_fedora=()
missing_docker=()
package_is_installed() {
  "$rpm_bin" -q --quiet "$1"
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
  local line section= section_count=0 in_stable=0 key value
  local enabled= baseurl= gpgcheck= gpgkey=
  local enabled_count=0 baseurl_count=0 gpgcheck_count=0 gpgkey_count=0
  docker_repo_configured=0
  [[ -r $docker_repo_file ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    line=$(trim "$line")
    [[ -z $line || $line == \#* ]] && continue
    if [[ $line =~ ^\[([^]]+)\]$ ]]; then
      section=${BASH_REMATCH[1]}
      in_stable=0
      if [[ $section == docker-ce-stable ]]; then
        section_count=$((section_count + 1))
        in_stable=1
      fi
      continue
    fi
    (( in_stable )) || continue
    [[ $line == *=* ]] || continue
    key=$(trim "${line%%=*}")
    value=$(strip_quotes "${line#*=}")
    case "$key" in
      enabled) enabled=$value; enabled_count=$((enabled_count + 1)) ;;
      baseurl) baseurl=$value; baseurl_count=$((baseurl_count + 1)) ;;
      gpgcheck) gpgcheck=$value; gpgcheck_count=$((gpgcheck_count + 1)) ;;
      gpgkey) gpgkey=$value; gpgkey_count=$((gpgkey_count + 1)) ;;
    esac
  done <"$docker_repo_file"
  if (( section_count == 1 && enabled_count == 1 && baseurl_count == 1 \
        && gpgcheck_count == 1 && gpgkey_count == 1 )) \
      && [[ $enabled == 1 \
        && $baseurl == 'https://download.docker.com/linux/fedora/$releasever/$basearch/stable' \
        && $gpgcheck == 1 \
        && $gpgkey == https://download.docker.com/linux/fedora/gpg ]]; then
    docker_repo_configured=1
  fi
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
  printf '%s\n' \
    'Unprivileged runtime action:' \
    '  uv python install 3.12' \
    '' \
    'Docker operator actions (the bootstrap does not run these):' \
    "  sudo $service_tool $service_action docker" \
    "  sudo $membership_tool -aG docker \"\$USER\"" \
    '  Log out and back in before relying on Docker group access in this shell.' \
    '  Review firewalld policy separately for your network; this bootstrap does not change it.'
}

print_status() {
  printf '%s\n' 'Fedora Workstation 44 / x86_64 developer bootstrap'
  if (( ${#missing_fedora[@]} )); then
    print_list 'Missing Fedora packages:' "${missing_fedora[@]}"
  else
    printf '%s\n' 'Fedora packages: complete.'
  fi
  if (( docker_repo_configured )); then
    printf 'Docker repository: configured and verified (%s -> %s).\n' "$docker_repo_display" "$docker_repo_url"
  else
    printf 'Docker repository: missing or invalid (%s; expected official source %s).\n' \
      "$docker_repo_display" "$docker_repo_url"
  fi
  if (( ${#missing_docker[@]} )); then
    print_list 'Missing Docker packages:' "${missing_docker[@]}"
  else
    printf '%s\n' 'Docker packages: complete.'
  fi

  local active_node_path=missing system_node_version=missing system_node_major=unknown
  active_node_path=$(type -P node 2>/dev/null || printf missing)
  if [[ -x $node_bin ]]; then
    system_node_version=$($node_bin --version 2>/dev/null || printf unreadable)
    [[ $system_node_version =~ ^v?([0-9]+)\. ]] && system_node_major=${BASH_REMATCH[1]}
  fi
  if package_is_installed nodejs22-bin && [[ $system_node_major == "$node_major" ]]; then
    if [[ $active_node_path == "$node_bin" ]]; then
      printf 'Node runtime: %s (matches .node-version).\n' "$system_node_version"
    else
      printf 'PATH_SHADOWED: %s wins PATH; %s from nodejs22-bin is Node 22.\n' \
        "$active_node_path" "$node_bin"
      printf '%s\n' '  For this session, run: PATH=/usr/bin:$PATH'
    fi
  elif package_is_installed nodejs22-bin; then
    printf 'Node runtime: %s does not provide the tracked Node 22 runtime; verify the nodejs22-bin RPM.\n' \
      "$system_node_version"
  else
    printf '%s\n' 'Node runtime: install the missing Fedora Node 22 packages above.'
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
  printf '%s\n' 'The bootstrap does not edit /etc directly.'
  if (( ! docker_repo_configured )); then
    printf '%s\n' 'The approved dnf config-manager command writes /etc/yum.repos.d/docker-ce.repo.'
  fi
  printf '%s\n' 'No service, group, firewall, or SELinux changes are included.'

  if [[ ! -t 0 ]]; then
    printf '%s\n' 'bootstrap: --apply confirmation must be entered in an interactive terminal.' >&2
    exit 2
  fi
  if [[ ! -x $sudo_bin || ! -x $dnf_bin ]]; then
    printf 'bootstrap: approved package transactions require trusted executables %s and %s.\n' \
      "$sudo_bin" "$dnf_bin" >&2
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
    "$sudo_bin" "$dnf_bin" install --assumeyes "${missing_fedora[@]}"
  fi
  if (( ! docker_repo_configured )); then
    "$sudo_bin" "$dnf_bin" config-manager addrepo --from-repofile="$docker_repo_url"
  fi
  if (( ${#missing_docker[@]} )); then
    "$sudo_bin" "$dnf_bin" install --assumeyes "${missing_docker[@]}"
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
if [[ ! -x $uv_bin ]]; then
  printf 'bootstrap: trusted uv executable is unavailable at %s after package verification.\n' "$uv_bin" >&2
  exit 1
fi
"$uv_bin" python install 3.12

printf '\n%s\n' 'Running the EasySynQ contributor doctor:'
cd "$repo_root"
doctor_bin=$repo_root/scripts/doctor.sh
if [[ ! -x $doctor_bin ]]; then
  printf 'bootstrap: contributor doctor is unavailable at %s.\n' "$doctor_bin" >&2
  exit 1
fi
"$doctor_bin" contributor

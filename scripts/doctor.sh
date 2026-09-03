#!/usr/bin/env bash
set -u

override_requested=0
for override_name in DOCTOR_ROOT DOCTOR_PATH DOCTOR_PROC_ROOT DOCTOR_DOCKER_SOCKET; do
  if [[ -v $override_name ]]; then
    override_requested=1
  fi
done
if (( override_requested )) && [[ ${DOCTOR_TEST_MODE:-0} != 1 ]]; then
  printf '%s\n' 'FAIL DOCTOR_OVERRIDE_REJECTED test overrides require DOCTOR_TEST_MODE=1' >&2
  exit 2
fi

profile=${1:-contributor}
case "$profile" in
  contributor) profile_rank=1 ;;
  test) profile_rank=2 ;;
  stack) profile_rank=3 ;;
  *)
    printf '%s\n' 'FAIL DOCTOR_USAGE usage: ./scripts/doctor.sh [contributor|test|stack]' >&2
    exit 2
    ;;
esac
if (( $# > 1 )); then
  printf '%s\n' 'FAIL DOCTOR_USAGE usage: ./scripts/doctor.sh [contributor|test|stack]' >&2
  exit 2
fi

DOCTOR_ROOT=${DOCTOR_ROOT:-/}
DOCTOR_PATH=${DOCTOR_PATH:-$PATH}
DOCTOR_PROC_ROOT=${DOCTOR_PROC_ROOT:-/proc}
DOCTOR_DOCKER_SOCKET=${DOCTOR_DOCKER_SOCKET:-/var/run/docker.sock}
PATH=$DOCTOR_PATH
export PATH

script_path=${BASH_SOURCE[0]}
script_dir=${script_path%/*}
[[ $script_dir == "$script_path" ]] && script_dir=.
if ! repo_root=$(cd "$script_dir/.." 2>/dev/null && pwd -P); then
  printf '%s\n' 'FAIL DOCTOR_INTERNAL unable to resolve the repository root' >&2
  exit 2
fi

blocked=0
os_id=unknown
os_version=unknown
selinux_mode=unverified
docker_cli_ok=0
docker_compose_ok=0
docker_daemon_ok=0
node_major=22
python_minor=3.12
compose_minimum=2.24.4

emit() {
  local state=$1 reason=$2 minimum_profile=$3 guidance=$4
  printf '%s %s %s\n' "$state" "$reason" "$guidance"
  if [[ $state == FAIL || $state == UNVERIFIED ]]; then
    case "$minimum_profile" in
      contributor) (( profile_rank >= 1 )) && blocked=1 ;;
      test) (( profile_rank >= 2 )) && blocked=1 ;;
      stack) (( profile_rank >= 3 )) && blocked=1 ;;
      none) ;;
      *)
        printf '%s\n' "FAIL DOCTOR_INTERNAL unknown blocker profile for $reason" >&2
        exit 2
        ;;
    esac
  fi
}

root_path() {
  local path=$1
  if [[ $DOCTOR_ROOT == / ]]; then
    printf '%s' "$path"
  else
    printf '%s%s' "${DOCTOR_ROOT%/}" "$path"
  fi
}

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

read_os_release() {
  local file line key value
  file=$(root_path /etc/os-release)
  [[ -r $file ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line == *=* ]] || continue
    key=${line%%=*}
    value=${line#*=}
    value=$(strip_quotes "$value")
    case "$key" in
      ID) os_id=${value,,} ;;
      VERSION_ID) os_version=$value ;;
    esac
  done <"$file"
}

version_at_least() {
  local raw=$1 want_major=$2 want_minor=$3 want_patch=$4
  if [[ $raw =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
    local major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]} patch=${BASH_REMATCH[3]}
    (( major > want_major \
       || (major == want_major && minor > want_minor) \
       || (major == want_major && minor == want_minor && patch >= want_patch) ))
    return
  fi
  return 1
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

has_word() {
  local words=$1 wanted=$2 word
  for word in $words; do
    [[ $word == "$wanted" ]] && return 0
  done
  return 1
}

load_runtime_contracts() {
  local line found=0 requirement_re='>=([0-9]+\.[0-9]+)' node_line_count=0
  if [[ -f $repo_root/.node-version ]]; then
    node_major=
    while IFS= read -r line || [[ -n $line ]]; do
      node_line_count=$((node_line_count + 1))
      if (( node_line_count == 1 )); then
        node_major=$line
      fi
    done <"$repo_root/.node-version"
    if (( node_line_count != 1 )) || [[ ! $node_major =~ ^[0-9]+$ ]]; then
      printf '%s\n' 'FAIL DOCTOR_CONTRACT_INVALID .node-version must contain one numeric major version' >&2
      return 1
    fi
  fi

  if [[ ! -r $repo_root/apps/api/pyproject.toml ]]; then
    printf '%s\n' 'FAIL DOCTOR_CONTRACT_INVALID apps/api/pyproject.toml is missing' >&2
    return 1
  fi
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line == requires-python* && $line =~ $requirement_re ]]; then
      python_minor=${BASH_REMATCH[1]}
      found=1
      break
    fi
  done <"$repo_root/apps/api/pyproject.toml"
  if (( ! found )); then
    printf '%s\n' 'FAIL DOCTOR_CONTRACT_INVALID could not parse requires-python from apps/api/pyproject.toml' >&2
    return 1
  fi

  found=0
  local compose_re='^MINIMUM_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$'
  if [[ ! -r $repo_root/scripts/require-compose-version.sh ]]; then
    printf '%s\n' 'FAIL DOCTOR_CONTRACT_INVALID scripts/require-compose-version.sh is missing' >&2
    return 1
  fi
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line =~ $compose_re ]]; then
      compose_minimum=${BASH_REMATCH[1]}
      found=1
      break
    fi
  done <"$repo_root/scripts/require-compose-version.sh"
  if (( ! found )); then
    printf '%s\n' 'FAIL DOCTOR_CONTRACT_INVALID could not parse the Compose minimum version' >&2
    return 1
  fi
}

check_platform() {
  local selinux_blocker=none
  if ! read_os_release; then
    emit FAIL OS_UNSUPPORTED contributor 'Cannot read /etc/os-release. Use an Ubuntu 26.04 x86_64 developer host.'
  elif [[ $os_id == ubuntu ]]; then
    emit PASS OS_SUPPORTED none "Ubuntu $os_version remains supported."
  elif [[ $os_id == fedora ]]; then
    # R71 retired the Fedora developer path. Every tool check below still works there, so this warns
    # rather than blocks: a contributor mid-migration gets a usable report, not a dead end.
    emit WARN OS_UNSUPPORTED_ADVISORY none 'Fedora is no longer the supported developer host (R71); Ubuntu 26.04 is.'
  else
    emit FAIL OS_UNSUPPORTED contributor 'Use an Ubuntu 26.04 x86_64 developer host (R71).'
  fi
  [[ $os_id == fedora ]] && selinux_blocker=stack

  local architecture
  architecture=$(uname -m 2>/dev/null || printf unknown)
  if [[ $architecture == x86_64 ]]; then
    emit PASS ARCH_SUPPORTED none 'x86_64 is supported.'
  else
    emit FAIL ARCH_UNSUPPORTED contributor 'Use an x86_64 development host.'
  fi

  if ! command_exists getenforce; then
    selinux_mode=unverified
    emit UNVERIFIED SELINUX_UNVERIFIED "$selinux_blocker" 'Install policycoreutils and run: getenforce'
  else
    selinux_mode=$(getenforce 2>/dev/null || printf unverified)
    case "${selinux_mode,,}" in
      enforcing) emit PASS SELINUX_ENFORCING none 'SELinux is enforcing.' ;;
      disabled|permissive)
        emit WARN SELINUX_DISABLED none 'SELinux is not enforcing; do not disable it to work around bind-mount labels.'
        ;;
      *)
        selinux_mode=unverified
        emit UNVERIFIED SELINUX_UNVERIFIED "$selinux_blocker" 'Run: getenforce'
        ;;
    esac
  fi
}

check_simple_tool() {
  local tool=$1 reason=$2 fedora_package=$3 ubuntu_package=$4 pass_reason=$5 install_command
  if command_exists "$tool"; then
    emit PASS "$pass_reason" none "$tool is available."
  else
    if [[ $os_id == ubuntu ]]; then
      install_command="sudo apt-get install $ubuntu_package"
    else
      install_command="sudo dnf install $fedora_package"
    fi
    emit FAIL "$reason" contributor "Run: $install_command"
  fi
}

check_node() {
  local expected_major=$node_major version major system_node system_version system_major

  if ! command_exists node; then
    emit FAIL NODE_MISSING contributor 'Install Node.js 22, then run: node --version'
    return
  fi
  version=$(node --version 2>/dev/null || true)
  if [[ $version =~ ^v?([0-9]+)\. ]]; then
    major=${BASH_REMATCH[1]}
  else
    major=unknown
  fi
  if [[ $major == "$expected_major" ]]; then
    emit PASS NODE_SUPPORTED_VERSION none "Node.js major $expected_major is active."
    return
  fi

  system_node=$(root_path /usr/bin/node)
  system_major=unknown
  if [[ -x $system_node ]]; then
    system_version=$($system_node --version 2>/dev/null || true)
    [[ $system_version =~ ^v?([0-9]+)\. ]] && system_major=${BASH_REMATCH[1]}
  fi
  if [[ $system_major == "$expected_major" ]]; then
    emit FAIL NODE_PATH_SHADOWED contributor "A system Node.js major $expected_major exists but another node wins PATH. Run for this session: PATH=/usr/bin:\$PATH"
  else
    emit FAIL NODE_UNSUPPORTED_VERSION contributor "Install and select Node.js major $expected_major, then run: node --version"
  fi
}

check_python() {
  if ! command_exists uv; then
    emit FAIL UV_MISSING contributor 'Install uv, then run: uv --version'
  elif uv python find "$python_minor" >/dev/null 2>&1; then
    emit PASS PYTHON_312_AVAILABLE none "uv can resolve Python $python_minor."
  else
    emit FAIL PYTHON_312_MISSING contributor "Run: uv python install $python_minor"
  fi
}

check_pg_dump() {
  local raw major
  if ! command_exists pg_dump; then
    emit FAIL PG_DUMP_MISSING contributor 'Install the PostgreSQL 18 client, then run: pg_dump --version'
    return
  fi
  raw=$(pg_dump --version 2>/dev/null || true)
  if [[ $raw =~ PostgreSQL\)[[:space:]]+([0-9]+) ]]; then
    major=${BASH_REMATCH[1]}
  else
    major=unknown
  fi
  # Must match the postgres server major in infra/images.lock: pg_dump refuses a NEWER server
  # outright, which surfaces as 19 backup/restore failures naming a version mismatch.
  if [[ $major == 18 ]]; then
    emit PASS PG_DUMP_SUPPORTED_VERSION none 'pg_dump major 18 is available.'
  else
    emit FAIL PG_DUMP_UNSUPPORTED_VERSION contributor 'Install PostgreSQL client major 18, then run: pg_dump --version'
  fi
}

check_docker() {
  local compose_version socket_metadata socket_mode socket_owner socket_group
  local owner_digit group_digit other_digit current_user current_groups account_groups docker_error docker_error_lower
  local compose_major compose_minor compose_patch

  if ! command_exists docker; then
    emit FAIL DOCKER_CLI_MISSING test 'Install Docker Engine CLI, then run: docker --version'
    return
  fi
  docker_cli_ok=1
  emit PASS DOCKER_CLI_AVAILABLE none 'Docker CLI is available.'

  compose_version=$(docker compose version --short 2>/dev/null || true)
  IFS=. read -r compose_major compose_minor compose_patch <<<"$compose_minimum"
  if [[ -z $compose_version ]]; then
    emit FAIL DOCKER_COMPOSE_MISSING test 'Install the Docker Compose plugin, then run: docker compose version'
  elif version_at_least "$compose_version" "$compose_major" "$compose_minor" "$compose_patch"; then
    docker_compose_ok=1
    emit PASS DOCKER_COMPOSE_SUPPORTED_VERSION none "Docker Compose $compose_minimum or newer is available."
  else
    emit FAIL DOCKER_COMPOSE_UNSUPPORTED_VERSION test "Upgrade Docker Compose to $compose_minimum or newer, then run: docker compose version"
  fi

  if [[ ! -e $DOCTOR_DOCKER_SOCKET ]]; then
    emit FAIL DOCKER_SOCKET_MISSING test 'Start Docker Engine, then run: docker info'
    return
  fi

  socket_metadata=$(stat -c '%a %U %G' "$DOCTOR_DOCKER_SOCKET" 2>/dev/null || true)
  read -r socket_mode socket_owner socket_group <<<"$socket_metadata"
  if [[ ! $socket_mode =~ ^[0-7]{3,4}$ || -z ${socket_owner:-} || -z ${socket_group:-} ]]; then
    emit FAIL DOCKER_SOCKET_PERMISSION test 'Inspect the socket with: stat -c "%a %U %G" /var/run/docker.sock'
    return
  fi
  socket_mode=${socket_mode: -3}
  owner_digit=${socket_mode:0:1}
  group_digit=${socket_mode:1:1}
  other_digit=${socket_mode:2:1}
  current_user=$(id -un 2>/dev/null || printf unknown)
  current_groups=$(id -Gn 2>/dev/null || true)
  account_groups=$(id -Gn "$current_user" 2>/dev/null || true)

  local socket_access=0
  if [[ $current_user == "$socket_owner" ]] && (( (10#$owner_digit & 6) == 6 )); then
    socket_access=1
  elif has_word "$current_groups" "$socket_group" && (( (10#$group_digit & 6) == 6 )); then
    socket_access=1
  elif (( (10#$other_digit & 6) == 6 )); then
    socket_access=1
  fi
  if (( ! socket_access )); then
    if has_word "$account_groups" "$socket_group" && ! has_word "$current_groups" "$socket_group"; then
      emit FAIL DOCKER_GROUP_SESSION_INACTIVE test "The account belongs to $socket_group but this shell does not. Start a fresh login session, then run: docker info"
    else
      emit FAIL DOCKER_SOCKET_PERMISSION test 'Do not weaken socket permissions. Configure reviewed Docker group access, then run: docker info'
    fi
    return
  fi
  emit PASS DOCKER_SOCKET_ACCESS none 'The current session has read/write Docker socket access.'

  if docker_error=$(docker info 2>&1); then
    docker_daemon_ok=1
    emit PASS DOCKER_DAEMON_REACHABLE none 'Docker daemon is reachable.'
  else
    docker_error_lower=${docker_error,,}
    if [[ $docker_error_lower == *'permission denied'* \
          || $docker_error_lower == *'access denied'* \
          || $docker_error_lower == *'operation not permitted'* \
          || ( $docker_error_lower == *'cannot connect'* && $docker_error_lower == *permission* ) ]]; then
      emit FAIL DOCKER_SOCKET_PERMISSION test 'Docker socket access was denied at runtime. Inspect it with: docker info'
    elif [[ $docker_error_lower == *'cannot connect'* \
          || $docker_error_lower == *'daemon running'* \
          || $docker_error_lower == *'connection refused'* ]]; then
      emit FAIL DOCKER_DAEMON_STOPPED test 'Start Docker Engine, then run: docker info'
    else
      emit FAIL DOCKER_DAEMON_UNREACHABLE test 'Docker is installed but unreachable. Run: docker info'
    fi
  fi
}

check_dependencies() {
  if [[ -d $repo_root/apps/api/.venv ]]; then
    emit PASS API_DEPS_PRESENT none 'API dependencies are installed.'
  else
    emit FAIL API_DEPS_MISSING test 'Run: cd apps/api && uv sync --frozen'
  fi
  if [[ -d $repo_root/apps/web/node_modules ]]; then
    emit PASS WEB_DEPS_PRESENT none 'Web dependencies are installed.'
  else
    emit FAIL WEB_DEPS_MISSING test 'Run: npm ci --prefix apps/web'
  fi
  if [[ -d $repo_root/packages/contracts/node_modules ]]; then
    emit PASS CONTRACT_DEPS_PRESENT none 'Contract dependencies are installed.'
  else
    emit FAIL CONTRACT_DEPS_MISSING test 'Run: npm ci --prefix packages/contracts --ignore-scripts'
  fi
}

declare -A template_env=()
declare -A local_env=()
declare -a template_keys=()

parse_env_file() {
  local file=$1 destination=$2 line key value
  local -n parsed=$destination
  while IFS= read -r line || [[ -n $line ]]; do
    line=$(trim "$line")
    [[ -z $line || $line == \#* || $line != *=* ]] && continue
    key=$(trim "${line%%=*}")
    [[ $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value=${line#*=}
    if [[ $value =~ ^(.*[^[:space:]])[[:space:]]+\#.*$ ]]; then
      value=${BASH_REMATCH[1]}
    fi
    value=$(strip_quotes "$value")
    if [[ $destination == template_env && ! -v parsed[$key] ]]; then
      template_keys+=("$key")
    fi
    parsed["$key"]=$value
  done <"$file"
}

is_secret_template_key() {
  local key=$1 value=$2
  [[ $value == *CHANGE_ME* \
     || $key =~ (PASSWORD|SECRET|TOKEN|KEK|ENCRYPTION_KEY)$ ]]
}

check_environment() {
  local template=$repo_root/.env.example env_file=$repo_root/.env key placeholder actual found=0
  if [[ ! -f $env_file ]]; then
    emit FAIL ENV_MISSING stack 'Run: cp .env.example .env, then replace every placeholder secret.'
    return
  fi
  emit PASS ENV_PRESENT none '.env is present.'
  [[ -r $template ]] && parse_env_file "$template" template_env
  parse_env_file "$env_file" local_env
  for key in "${template_keys[@]}"; do
    placeholder=${template_env[$key]}
    [[ -n $placeholder ]] || continue
    is_secret_template_key "$key" "$placeholder" || continue
    actual=${local_env[$key]-}
    if [[ -z $actual || $actual == "$placeholder" || $actual == *CHANGE_ME* ]]; then
      emit FAIL ENV_PLACEHOLDER_SECRET stack "$key is missing or still uses the .env.example placeholder. Edit: .env"
      found=1
    fi
  done
  (( found )) || emit PASS ENV_SECRETS_CONFIGURED none 'No known .env.example secret placeholders remain.'
}

port_listening() {
  local wanted=$1 file line sl local_address remote_address state rest port_hex port
  for file in "$DOCTOR_PROC_ROOT/net/tcp" "$DOCTOR_PROC_ROOT/net/tcp6"; do
    [[ -r $file ]] || continue
    while read -r sl local_address remote_address state rest; do
      [[ $state == 0A && $local_address == *:* ]] || continue
      port_hex=${local_address##*:}
      [[ $port_hex =~ ^[0-9A-Fa-f]{4}$ ]] || continue
      port=$((16#$port_hex))
      (( port == wanted )) && return 0
    done <"$file"
  done
  return 1
}

compose_ports() {
  local compose_profile=${local_env[EASYSYNQ_PROFILE]:-s}
  [[ $compose_profile == s || $compose_profile == m ]] || compose_profile=s
  if [[ -f $repo_root/.env ]]; then
    (cd "$repo_root" && docker compose --env-file .env \
      -f infra/compose/compose.yml -f "infra/compose/compose.$compose_profile.yml" \
      -f infra/compose/compose.dev.yml ps --format '{{.Ports}}') 2>/dev/null || true
  else
    (cd "$repo_root" && docker compose \
      -f infra/compose/compose.yml -f "infra/compose/compose.$compose_profile.yml" \
      -f infra/compose/compose.dev.yml ps --format '{{.Ports}}') 2>/dev/null || true
  fi
}

check_ports() {
  local http_port=${local_env[HTTP_PORT]:-80}
  local https_port=${local_env[HTTPS_PORT]:-443}
  local s3_port=${local_env[S3_PORT]:-9000}
  local port owned compose_output=''
  declare -A seen_ports=()
  if (( docker_cli_ok && docker_compose_ok && docker_daemon_ok )); then
    compose_output=$(compose_ports)
  fi
  for port in "$http_port" "$https_port" "$s3_port"; do
    [[ $port =~ ^[0-9]+$ ]] || continue
    [[ -v seen_ports[$port] ]] && continue
    seen_ports[$port]=1
    if ! port_listening "$port"; then
      emit PASS PORT_AVAILABLE none "Project port $port is available."
      continue
    fi
    owned=0
    if [[ $compose_output == *":$port->"* || $compose_output == *":$port-"* ]]; then
      owned=1
    fi
    if (( owned )); then
      emit PASS PORT_OWNED_BY_STACK none "Project port $port belongs to the current EasySynQ Compose project."
    else
      emit FAIL PORT_OCCUPIED stack "Project port $port has a foreign listener. Inspect it with: ss -ltnp 'sport = :$port'"
    fi
  done
}

check_selinux_labels() {
  local path context import_source=${local_env[IMPORT_SOURCE_PATH]:-../../.import-source}
  local unverified=0
  if [[ $os_id != fedora ]]; then
    emit PASS SELINUX_LABEL_NOT_REQUIRED none 'SELinux bind-label verification is not required on this host state.'
    return
  fi
  if [[ ${selinux_mode,,} == unverified ]]; then
    emit UNVERIFIED SELINUX_LABEL_UNVERIFIED stack 'Verify Fedora SELinux mode with getenforce before checking bind labels.'
    return
  fi
  if [[ ${selinux_mode,,} != enforcing ]]; then
    emit PASS SELINUX_LABEL_NOT_REQUIRED none 'SELinux bind-label verification is not required on this host state.'
    return
  fi
  local -a bind_sources=(
    "$repo_root/infra/compose/minio"
    "$repo_root/infra/compose/keycloak/keycloak-init.sh"
    "$repo_root/infra/compose/keycloak/realm-export.json"
    "$repo_root/infra/compose/caddy/Caddyfile"
  )
  if [[ $import_source == /* ]]; then
    bind_sources+=("$import_source")
  else
    bind_sources+=("$repo_root/infra/compose/$import_source")
  fi
  for path in "${bind_sources[@]}"; do
    context=$(stat -c %C "$path" 2>/dev/null || true)
    [[ $context == *container_file_t* ]] || unverified=1
  done
  if (( unverified )); then
    emit UNVERIFIED SELINUX_LABEL_UNVERIFIED stack 'Start the dev Compose stack with its :z labels, then re-run: ./scripts/doctor.sh stack'
  else
    emit PASS SELINUX_LABEL_VERIFIED none 'Developer bind sources carry a container-compatible SELinux label.'
  fi
}

if ! load_runtime_contracts; then
  exit 2
fi
check_platform
check_simple_tool git TOOL_MISSING_GIT git git TOOL_GIT_AVAILABLE
check_simple_tool curl TOOL_MISSING_CURL curl curl TOOL_CURL_AVAILABLE
check_simple_tool openssl TOOL_MISSING_OPENSSL openssl openssl TOOL_OPENSSL_AVAILABLE
check_node
check_python
check_simple_tool just JUST_MISSING just just JUST_AVAILABLE
check_simple_tool pre-commit PRECOMMIT_MISSING pre-commit pre-commit PRECOMMIT_AVAILABLE
check_pg_dump
check_docker
check_dependencies
check_environment
check_ports
check_selinux_labels

if (( blocked )); then
  emit FAIL PROFILE_BLOCKED none "$profile prerequisites are not ready. Resolve the reasons above, then re-run: ./scripts/doctor.sh $profile"
  exit 1
fi
emit PASS PROFILE_READY none "$profile"

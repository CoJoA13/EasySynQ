#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
DOCTOR_SOURCE="$PROJECT_ROOT/scripts/doctor.sh"

if [[ ! -f "$DOCTOR_SOURCE" ]]; then
  echo "doctor contract: scripts/doctor.sh does not exist" >&2
  exit 1
fi

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

make_dispatcher() {
  local target=$1
  printf '%s\n' \
    '#!/bin/bash' \
    'set -u' \
    'state=${STUB_STATE_DIR:?}' \
    'tool=${0##*/}' \
    'read_state() {' \
    '  local name=$1 default=${2-}' \
    '  if [[ -f "$state/$name" ]]; then' \
    '    IFS= read -r value <"$state/$name" || true' \
    "    printf '%s' \"\$value\"" \
    '  else' \
    "    printf '%s' \"\$default\"" \
    '  fi' \
    '}' \
    'case "$tool" in' \
    "  uname) read_state arch x86_64; printf '\\n' ;;" \
    "  getenforce) read_state selinux Enforcing; printf '\\n' ;;" \
    '  stat)' \
    '    if [[ " $* " == *" %C "* ]]; then' \
    "      read_state label_context 'system_u:object_r:container_file_t:s0'" \
    '    else' \
    "      read_state socket_stat '660 root docker'" \
    '    fi' \
    "    printf '\\n'" \
    '    ;;' \
    '  id)' \
    '    if [[ ${1-} == -un ]]; then' \
    "      printf 'developer\\n'" \
    '    elif [[ ${1-} == -Gn && $# -ge 2 ]]; then' \
    "      read_state account_groups 'developer wheel docker'; printf '\\n'" \
    '    elif [[ ${1-} == -Gn ]]; then' \
    "      read_state current_groups 'developer wheel docker'; printf '\\n'" \
    '    else exit 2; fi' \
    '    ;;' \
    "  node) read_state node_version v22.17.0; printf '\\n' ;;" \
    '  uv)' \
    '    if [[ ${1-} == python && ${2-} == find && ${3-} == 3.12 ]]; then' \
    '      [[ $(read_state python_312 present) == present ]] || exit 1' \
    "      printf '/fixture/python3.12\\n'" \
    "    else printf 'uv 0.8.4\\n'; fi" \
    '    ;;' \
    "  pg_dump) printf 'pg_dump (PostgreSQL) %s\\n' \"\$(read_state pg_dump_version 18.6)\" ;;" \
    '  docker)' \
    '    if [[ ${1-} == compose && ${2-} == version ]]; then' \
    '      case "$(read_state compose_state ok)" in' \
    "        missing) printf \"docker: 'compose' is not a docker command\\n\" >&2; exit 1 ;;" \
    "        unsupported) printf '2.23.3\\n' ;;" \
    "        *) printf '2.24.4\\n' ;;" \
    '      esac' \
    '    elif [[ ${1-} == info ]]; then' \
    '      case "$(read_state docker_info ok)" in' \
    "        stopped) printf 'Cannot connect to the Docker daemon. Is the docker daemon running?\\n' >&2; exit 1 ;;" \
    "        permission) printf 'Cannot connect to the Docker daemon socket: permission denied\\n' >&2; exit 1 ;;" \
    "        unreachable) printf 'synthetic transport failure\\n' >&2; exit 1 ;;" \
    "        *) printf 'Server Version: 27.5.1\\n' ;;" \
    '      esac' \
    '    elif [[ " $* " == *" ps "* ]]; then' \
    '      [[ " $* " == *" --env-file .env "* ]] || exit 3' \
    '      [[ " $* " == *" -f infra/compose/compose.yml "* ]] || exit 3' \
    '      [[ " $* " == *" -f infra/compose/compose.s.yml "* ]] || exit 3' \
    '      [[ " $* " == *" -f infra/compose/compose.dev.yml "* ]] || exit 3' \
    '      [[ -f "$state/owned_ports" ]] && while IFS= read -r line; do printf "%s\n" "$line"; done <"$state/owned_ports"' \
    '    else exit 2; fi' \
    '    ;;' \
    "  git) printf 'git version 2.51.0\\n' ;;" \
    "  curl) printf 'curl 8.11.1\\n' ;;" \
    "  openssl) printf 'OpenSSL 3.2.4\\n' ;;" \
    "  just) printf 'just 1.40.0\\n' ;;" \
    "  pre-commit) printf 'pre-commit 4.2.0\\n' ;;" \
    '  *) exit 2 ;;' \
    'esac' >"$target"
  chmod +x "$target"
}

new_fixture() {
  local name=$1
  CASE_ROOT="$TEST_ROOT/$name/root"
  CASE_REPO="$TEST_ROOT/$name/repo"
  CASE_BIN="$TEST_ROOT/$name/bin"
  CASE_STATE="$TEST_ROOT/$name/state"
  mkdir -p "$CASE_ROOT/etc" "$CASE_ROOT/var/run" "$CASE_ROOT/usr/bin" \
    "$CASE_ROOT/proc/net" "$CASE_REPO/scripts" "$CASE_REPO/apps/api/.venv" \
    "$CASE_REPO/apps/web/node_modules" "$CASE_REPO/packages/contracts/node_modules" \
    "$CASE_REPO/infra/compose/minio" "$CASE_REPO/infra/compose/keycloak" \
    "$CASE_REPO/infra/compose/caddy" "$CASE_REPO/.import-source" "$CASE_BIN" "$CASE_STATE"
  cp "$DOCTOR_SOURCE" "$CASE_REPO/scripts/doctor.sh"
  chmod +x "$CASE_REPO/scripts/doctor.sh"

  printf 'ID=fedora\nVERSION_ID=44\n' >"$CASE_ROOT/etc/os-release"
  printf '22\n' >"$CASE_REPO/.node-version"
  printf '[project]\nrequires-python = ">=3.12,<3.13"\n' >"$CASE_REPO/apps/api/pyproject.toml"
  printf 'MINIMUM_VERSION="2.24.4"\n' >"$CASE_REPO/scripts/require-compose-version.sh"
  printf 'POSTGRES_PASSWORD=CHANGE_ME\n' >"$CASE_REPO/.env.example"
  printf 'POSTGRES_PASSWORD=configured-for-fixture\n' >"$CASE_REPO/.env"
  : >"$CASE_ROOT/var/run/docker.sock"
  printf '  sl  local_address rem_address   st\n' >"$CASE_ROOT/proc/net/tcp"
  printf '  sl  local_address rem_address   st\n' >"$CASE_ROOT/proc/net/tcp6"
  : >"$CASE_REPO/infra/compose/keycloak/keycloak-init.sh"
  : >"$CASE_REPO/infra/compose/keycloak/realm-export.json"
  : >"$CASE_REPO/infra/compose/caddy/Caddyfile"

  make_dispatcher "$CASE_BIN/stub"
  local tool
  for tool in uname getenforce stat id git curl openssl node uv just pre-commit pg_dump docker; do
    cp "$CASE_BIN/stub" "$CASE_BIN/$tool"
  done
}

set_state() {
  printf '%s\n' "$2" >"$CASE_STATE/$1"
}

listen_on_port_80() {
  printf '%s\n' \
    '  sl  local_address rem_address   st' \
    '   0: 0100007F:0050 00000000:0000 0A' >"$CASE_ROOT/proc/net/tcp"
}

listen_on_tcp6_port_80() {
  printf '%s\n' \
    '  sl  local_address rem_address   st' \
    '   0: 00000000000000000000000000000000:0050 00000000000000000000000000000000:0000 0A' \
    >"$CASE_ROOT/proc/net/tcp6"
}

configure_case() {
  local name=$1
  case "$name" in
    os_unsupported) printf 'ID=debian\nVERSION_ID=13\n' >"$CASE_ROOT/etc/os-release" ;;
    ubuntu_ready) printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$CASE_ROOT/etc/os-release" ;;
    ubuntu_missing_git)
      printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$CASE_ROOT/etc/os-release"
      rm "$CASE_BIN/git"
      ;;
    arch_unsupported) set_state arch aarch64 ;;
    selinux_disabled) set_state selinux Disabled ;;
    selinux_unverified) rm "$CASE_BIN/getenforce" ;;
    missing_git) rm "$CASE_BIN/git" ;;
    missing_curl) rm "$CASE_BIN/curl" ;;
    missing_openssl) rm "$CASE_BIN/openssl" ;;
    node_missing) rm "$CASE_BIN/node" ;;
    node_path_shadowed)
      set_state node_version v20.19.0
      printf '%s\n' '#!/bin/bash' "printf 'v22.17.0\\n'" >"$CASE_ROOT/usr/bin/node"
      chmod +x "$CASE_ROOT/usr/bin/node"
      ;;
    node_unsupported) set_state node_version v20.19.0 ;;
    node_pin_empty) : >"$CASE_REPO/.node-version" ;;
    node_pin_nonnumeric) printf 'twenty-two\n' >"$CASE_REPO/.node-version" ;;
    node_pin_multiline) printf '22\n23\n' >"$CASE_REPO/.node-version" ;;
    node_pin_extra_newline) printf '22\n\n' >"$CASE_REPO/.node-version" ;;
    uv_missing) rm "$CASE_BIN/uv" ;;
    python_missing) set_state python_312 missing ;;
    just_missing) rm "$CASE_BIN/just" ;;
    precommit_missing) rm "$CASE_BIN/pre-commit" ;;
    pg_dump_missing) rm "$CASE_BIN/pg_dump" ;;
    pg_dump_unsupported) set_state pg_dump_version 15.13 ;;
    docker_cli_missing) rm "$CASE_BIN/docker" ;;
    docker_compose_missing) set_state compose_state missing ;;
    docker_compose_unsupported) set_state compose_state unsupported ;;
    docker_compose_contract_newer) printf 'MINIMUM_VERSION="2.25.0"\n' >"$CASE_REPO/scripts/require-compose-version.sh" ;;
    docker_socket_missing) rm "$CASE_ROOT/var/run/docker.sock" ;;
    docker_daemon_stopped) set_state docker_info stopped ;;
    docker_runtime_permission) set_state docker_info permission ;;
    docker_socket_permission)
      set_state socket_stat '600 root root'
      set_state current_groups 'developer wheel'
      set_state account_groups 'developer wheel'
      ;;
    docker_group_inactive)
      set_state current_groups 'developer wheel'
      set_state account_groups 'developer wheel docker'
      ;;
    docker_daemon_unreachable) set_state docker_info unreachable ;;
    api_deps_missing) rmdir "$CASE_REPO/apps/api/.venv" ;;
    web_deps_missing) rmdir "$CASE_REPO/apps/web/node_modules" ;;
    contract_deps_missing) rmdir "$CASE_REPO/packages/contracts/node_modules" ;;
    env_missing) rm "$CASE_REPO/.env" ;;
    env_placeholder) printf 'POSTGRES_PASSWORD=CHANGE_ME\n' >"$CASE_REPO/.env" ;;
    port_occupied) listen_on_port_80 ;;
    port_occupied_tcp6) listen_on_tcp6_port_80 ;;
    port_owned)
      listen_on_port_80
      printf '0.0.0.0:80->80/tcp\n' >"$CASE_STATE/owned_ports"
      ;;
    selinux_label_unverified) set_state label_context 'unconfined_u:object_r:user_home_t:s0' ;;
    invalid_contract) printf '[project]\nrequires-python = "not-a-version"\n' >"$CASE_REPO/apps/api/pyproject.toml" ;;
    ready) ;;
    *) printf 'unknown fixture: %s\n' "$name" >&2; exit 2 ;;
  esac
}

run_doctor() {
  local profile=$1
  set +e
  STUB_STATE_DIR="$CASE_STATE" DOCTOR_TEST_MODE=1 DOCTOR_ROOT="$CASE_ROOT" \
    DOCTOR_PATH="$CASE_BIN" DOCTOR_PROC_ROOT="$CASE_ROOT/proc" \
    DOCTOR_DOCKER_SOCKET="$CASE_ROOT/var/run/docker.sock" \
    bash "$CASE_REPO/scripts/doctor.sh" "$profile" >"$CASE_REPO/stdout" 2>"$CASE_REPO/stderr"
  CASE_EXIT=$?
  set -e
  CASE_OUTPUT="$(<"$CASE_REPO/stdout")"$'\n'"$(<"$CASE_REPO/stderr")"
}

assert_case() {
  local name=$1 profile=$2 expected_exit=$3 expected_line=$4
  new_fixture "$name-$profile"
  configure_case "$name"
  run_doctor "$profile"
  if [[ $CASE_EXIT -eq $expected_exit && "$CASE_OUTPUT" == *"$expected_line"* ]]; then
    ok "$name/$profile exits $expected_exit with $expected_line"
  else
    not_ok "$name/$profile expected exit $expected_exit and '$expected_line'; exit=$CASE_EXIT output=$CASE_OUTPUT"
  fi
}

assert_docker_case() {
  local name=$1 expected_reason=$2
  local docker_fail_count=0 line
  new_fixture "$name-test"
  configure_case "$name"
  run_doctor test
  while IFS= read -r line; do
    [[ $line == FAIL\ DOCKER_* ]] && docker_fail_count=$((docker_fail_count + 1))
  done <<<"$CASE_OUTPUT"
  if [[ $CASE_EXIT -eq 1 && "$CASE_OUTPUT" == *"FAIL $expected_reason "* \
        && $docker_fail_count -eq 1 ]]; then
    ok "$name/test reports only FAIL $expected_reason"
  else
    not_ok "$name/test did not isolate $expected_reason; exit=$CASE_EXIT docker_failures=$docker_fail_count output=$CASE_OUTPUT"
  fi
}

printf '%s\n' '== doctor reason/state contract =='
assert_case os_unsupported contributor 1 'FAIL OS_UNSUPPORTED '
assert_case ubuntu_ready contributor 0 'PASS OS_SUPPORTED Ubuntu 24.04 remains supported.'
assert_case ubuntu_missing_git contributor 1 'FAIL TOOL_MISSING_GIT Run: sudo apt-get install git'
assert_case arch_unsupported contributor 1 'FAIL ARCH_UNSUPPORTED '
assert_case selinux_disabled contributor 0 'WARN SELINUX_DISABLED '
assert_case selinux_unverified contributor 0 'UNVERIFIED SELINUX_UNVERIFIED '
assert_case selinux_unverified test 0 'UNVERIFIED SELINUX_UNVERIFIED '
assert_case selinux_unverified stack 1 'UNVERIFIED SELINUX_UNVERIFIED '
assert_case missing_git contributor 1 'FAIL TOOL_MISSING_GIT '
assert_case missing_curl contributor 1 'FAIL TOOL_MISSING_CURL '
assert_case missing_openssl contributor 1 'FAIL TOOL_MISSING_OPENSSL '
assert_case node_missing contributor 1 'FAIL NODE_MISSING '
assert_case node_path_shadowed contributor 1 'FAIL NODE_PATH_SHADOWED '
if [[ "$CASE_OUTPUT" == *'PATH=/usr/bin:$PATH'* ]]; then
  ok 'NODE_PATH_SHADOWED prints the literal current-session PATH remedy'
else
  not_ok 'NODE_PATH_SHADOWED did not print PATH=/usr/bin:$PATH'
fi
assert_case node_unsupported contributor 1 'FAIL NODE_UNSUPPORTED_VERSION '
assert_case node_pin_empty contributor 2 'FAIL DOCTOR_CONTRACT_INVALID '
assert_case node_pin_nonnumeric contributor 2 'FAIL DOCTOR_CONTRACT_INVALID '
assert_case node_pin_multiline contributor 2 'FAIL DOCTOR_CONTRACT_INVALID '
assert_case node_pin_extra_newline contributor 2 'FAIL DOCTOR_CONTRACT_INVALID '
assert_case uv_missing contributor 1 'FAIL UV_MISSING '
assert_case python_missing contributor 1 'FAIL PYTHON_312_MISSING '
assert_case just_missing contributor 1 'FAIL JUST_MISSING '
assert_case precommit_missing contributor 1 'FAIL PRECOMMIT_MISSING '
assert_case pg_dump_missing contributor 1 'FAIL PG_DUMP_MISSING '
assert_case pg_dump_unsupported contributor 1 'FAIL PG_DUMP_UNSUPPORTED_VERSION '
assert_docker_case docker_cli_missing DOCKER_CLI_MISSING
assert_docker_case docker_compose_missing DOCKER_COMPOSE_MISSING
assert_docker_case docker_compose_unsupported DOCKER_COMPOSE_UNSUPPORTED_VERSION
assert_docker_case docker_compose_contract_newer DOCKER_COMPOSE_UNSUPPORTED_VERSION
assert_docker_case docker_socket_missing DOCKER_SOCKET_MISSING
assert_docker_case docker_daemon_stopped DOCKER_DAEMON_STOPPED
assert_docker_case docker_runtime_permission DOCKER_SOCKET_PERMISSION
assert_docker_case docker_socket_permission DOCKER_SOCKET_PERMISSION
assert_docker_case docker_group_inactive DOCKER_GROUP_SESSION_INACTIVE
assert_docker_case docker_daemon_unreachable DOCKER_DAEMON_UNREACHABLE
assert_case api_deps_missing test 1 'FAIL API_DEPS_MISSING '
assert_case web_deps_missing test 1 'FAIL WEB_DEPS_MISSING '
assert_case contract_deps_missing test 1 'FAIL CONTRACT_DEPS_MISSING '
assert_case env_missing stack 1 'FAIL ENV_MISSING '
assert_case env_placeholder stack 1 'FAIL ENV_PLACEHOLDER_SECRET POSTGRES_PASSWORD '
assert_case port_occupied stack 1 'FAIL PORT_OCCUPIED '
assert_case port_occupied_tcp6 stack 1 'FAIL PORT_OCCUPIED '
assert_case port_owned stack 0 'PASS PORT_OWNED_BY_STACK '
assert_case selinux_label_unverified stack 1 'UNVERIFIED SELINUX_LABEL_UNVERIFIED '

printf '%s\n' '== doctor profile exit boundaries =='
assert_case docker_cli_missing contributor 0 'FAIL DOCKER_CLI_MISSING '
assert_case env_missing test 0 'FAIL ENV_MISSING '
assert_case port_occupied test 0 'FAIL PORT_OCCUPIED '
assert_case selinux_label_unverified test 0 'UNVERIFIED SELINUX_LABEL_UNVERIFIED '
assert_case missing_git test 1 'FAIL TOOL_MISSING_GIT '
assert_case missing_git stack 1 'FAIL TOOL_MISSING_GIT '
assert_case ready contributor 0 'PASS PROFILE_READY contributor'
assert_case ready test 0 'PASS PROFILE_READY test'
assert_case ready stack 0 'PASS PROFILE_READY stack'

new_fixture deterministic-output
configure_case ready
run_doctor stack
first_exit=$CASE_EXIT
first_output=$CASE_OUTPUT
run_doctor stack
if [[ $first_exit -eq 0 && $CASE_EXIT -eq 0 && "$first_output" == "$CASE_OUTPUT" ]]; then
  ok 'identical fixtures produce byte-identical output and exits'
else
  not_ok 'identical fixtures produced nondeterministic output or exits'
fi

printf '%s\n' '== doctor secret non-disclosure =='
new_fixture secret-sentinel
printf 'POSTGRES_PASSWORD=DOCTOR_SENTINEL_9d77\n' >"$CASE_REPO/.env.example"
printf 'POSTGRES_PASSWORD=DOCTOR_SENTINEL_9d77\n' >"$CASE_REPO/.env"
run_doctor stack
if [[ $CASE_EXIT -eq 1 && "$CASE_OUTPUT" == *'FAIL ENV_PLACEHOLDER_SECRET POSTGRES_PASSWORD '* \
      && "$CASE_OUTPUT" != *'DOCTOR_SENTINEL_9d77'* ]]; then
  ok 'placeholder diagnostics name the key and never disclose the sentinel value'
else
  not_ok "secret sentinel leaked or placeholder diagnosis failed; exit=$CASE_EXIT output=$CASE_OUTPUT"
fi

new_fixture env-not-sourced
marker="$CASE_REPO/env-was-sourced"
printf 'POSTGRES_PASSWORD=$(touch %s)\n' "$marker" >"$CASE_REPO/.env"
run_doctor stack
if [[ ! -e $marker && "$CASE_OUTPUT" != *'touch '* ]]; then
  ok '.env is parsed as data and never sourced or disclosed'
else
  not_ok '.env command content executed or appeared in doctor output'
fi

printf '%s\n' '== doctor test-seam and invocation guards =='
assert_case invalid_contract contributor 2 'FAIL DOCTOR_CONTRACT_INVALID '
for override in DOCTOR_ROOT DOCTOR_PATH DOCTOR_PROC_ROOT DOCTOR_DOCKER_SOCKET; do
  set +e
  env "$override=/synthetic/override" bash "$DOCTOR_SOURCE" contributor \
    >"$TEST_ROOT/override.out" 2>"$TEST_ROOT/override.err"
  override_exit=$?
  set -e
  override_output="$(<"$TEST_ROOT/override.out")"$'\n'"$(<"$TEST_ROOT/override.err")"
  if [[ $override_exit -eq 2 && "$override_output" == *'DOCTOR_OVERRIDE_REJECTED'* ]]; then
    ok "$override is rejected outside DOCTOR_TEST_MODE=1"
  else
    not_ok "$override bypassed the test-seam guard; exit=$override_exit output=$override_output"
  fi
done

set +e
bash "$DOCTOR_SOURCE" invalid >"$TEST_ROOT/usage.out" 2>"$TEST_ROOT/usage.err"
usage_exit=$?
set -e
if [[ $usage_exit -eq 2 && "$(<"$TEST_ROOT/usage.err")" == *'DOCTOR_USAGE'* ]]; then
  ok 'invalid profile exits 2 with DOCTOR_USAGE'
else
  not_ok 'invalid profile did not honor the exit-2 usage contract'
fi

new_fixture default-profile
set +e
STUB_STATE_DIR="$CASE_STATE" DOCTOR_TEST_MODE=1 DOCTOR_ROOT="$CASE_ROOT" \
  DOCTOR_PATH="$CASE_BIN" DOCTOR_PROC_ROOT="$CASE_ROOT/proc" \
  DOCTOR_DOCKER_SOCKET="$CASE_ROOT/var/run/docker.sock" \
  bash "$CASE_REPO/scripts/doctor.sh" >"$CASE_REPO/default.stdout" 2>"$CASE_REPO/default.stderr"
default_exit=$?
set -e
if [[ $default_exit -eq 0 && "$(<"$CASE_REPO/default.stdout")" == *'PASS PROFILE_READY contributor'* ]]; then
  ok 'omitting the profile selects contributor'
else
  not_ok 'omitting the profile did not select contributor'
fi

printf '%s\n' "$checks fixture checks; $failures failed"
(( failures == 0 ))

#!/usr/bin/env bash
# Run the repository acceptance contract inside the disposable Fedora Workstation proof guest.
set -euo pipefail

SOURCE_DIR=/var/tmp/easysynq-proof/source
WORK_DIR=/var/tmp/easysynq-proof/worktree
after_docker_group=0
stack_started=0

usage() {
  printf '%s\n' \
    'usage: ./scripts/inside-fedora-proof.sh [--source-dir /var/tmp/easysynq-proof/source]' \
    '       ./scripts/inside-fedora-proof.sh --print-plan' >&2
}

print_plan() {
  cat <<'EOF'
VARIANT_ID=workstation
VERSION_ID=44
uname -m == x86_64
getenforce == Enforcing
bootstrap-fedora-dev.sh --check
bootstrap-fedora-dev.sh --apply (literal yes; first run)
bootstrap-fedora-dev.sh --apply (literal yes; idempotence run)
explicit systemctl enable --now docker + docker group fresh session
just setup
doctor.sh contributor
doctor.sh test
docker run --rm hello-world
testcontainers DockerClient ping
pytest tests/unit -m unit
npm run lint
npm run typecheck
npm test -- --run
npm ci --prefix packages/contracts --ignore-scripts
test-run-contract-tool.sh
generated disposable .env secrets only
compose.s.yml + compose.dev.yml config --quiet
compose.s.yml + compose.dev.yml up -d
doctor.sh stack
compose.s.yml + compose.dev.yml down -v
EOF
}

replace_env_value() {
  local key=$1 value=$2 file=$3 temporary
  temporary=${file}.proof-tmp
  awk -v wanted="$key" -v replacement="$value" '
    BEGIN { replaced = 0 }
    $0 ~ "^" wanted "=" {
      print wanted "=" replacement
      replaced = 1
      next
    }
    { print }
    END { if (!replaced) exit 3 }
  ' "$file" >"$temporary"
  chmod --reference="$file" "$temporary"
  mv -f -- "$temporary" "$file"
}

generate_disposable_env() {
  local env_file=$1 pg_password app_password linker_password value
  cp .env.example "$env_file"
  chmod 0600 "$env_file"

  pg_password=$(openssl rand -hex 24)
  app_password=$(openssl rand -hex 24)
  linker_password=$(openssl rand -hex 24)
  replace_env_value POSTGRES_PASSWORD "$pg_password" "$env_file"
  replace_env_value APP_DB_PASSWORD "$app_password" "$env_file"
  replace_env_value LINKER_DB_PASSWORD "$linker_password" "$env_file"
  replace_env_value DATABASE_URL \
    "postgresql+psycopg://easysynq_app:${app_password}@postgres:5432/easysynq" "$env_file"
  replace_env_value DATABASE_URL_SYNC \
    "postgresql+psycopg://easysynq:${pg_password}@postgres:5432/easysynq" "$env_file"
  replace_env_value AUDIT_LINKER_DATABASE_URL \
    "postgresql+psycopg://easysynq_linker:${linker_password}@postgres:5432/easysynq" "$env_file"

  replace_env_value S3_ACCESS_KEY proof-s3-access "$env_file"
  for key in \
    S3_SECRET_KEY \
    AUDIT_SINK_SECRET_KEY \
    AUDIT_SINK_READ_SECRET_KEY \
    KEYCLOAK_ADMIN_PASSWORD \
    KEYCLOAK_DB_PASSWORD \
    APP_MASTER_KEK \
    BACKUP_ENCRYPTION_KEY; do
    value=$(openssl rand -hex 32)
    replace_env_value "$key" "$value" "$env_file"
  done
}

assert_platform() {
  local variant= version= line
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      VARIANT_ID=*) variant=${line#*=}; variant=${variant%\"}; variant=${variant#\"} ;;
      VERSION_ID=*) version=${line#*=}; version=${version%\"}; version=${version#\"} ;;
    esac
  done </etc/os-release
  [[ $variant == workstation ]] || {
    printf 'fedora-proof: expected VARIANT_ID=workstation, found %s\n' "${variant:-missing}" >&2
    return 1
  }
  [[ $version == 44 ]] || {
    printf 'fedora-proof: expected VERSION_ID=44, found %s\n' "${version:-missing}" >&2
    return 1
  }
  [[ $(uname -m) == x86_64 ]] || {
    printf '%s\n' 'fedora-proof: expected x86_64' >&2
    return 1
  }
  [[ $(getenforce) == Enforcing ]] || {
    printf '%s\n' 'fedora-proof: SELinux must remain Enforcing' >&2
    return 1
  }
}

run_confirmed_apply() {
  # util-linux script gives the bootstrap a real terminal while forwarding only the literal approval.
  printf 'yes\n' | script --quiet --return \
    --command './scripts/bootstrap-fedora-dev.sh --apply' /dev/null
}

cleanup_stack() {
  if (( stack_started )); then
    docker compose --env-file .env \
      -f infra/compose/compose.yml \
      -f infra/compose/compose.s.yml \
      -f infra/compose/compose.dev.yml down -v
  fi
}

run_after_docker_group() {
  local source_real work_parent
  assert_platform
  id -nG | tr ' ' '\n' | grep -Fx docker >/dev/null || {
    printf '%s\n' 'fedora-proof: fresh Docker group session was not established' >&2
    return 1
  }
  docker info >/dev/null

  source_real=$(readlink -e "$SOURCE_DIR")
  [[ -n $source_real && $source_real == /var/tmp/easysynq-proof/source ]] || {
    printf '%s\n' 'fedora-proof: source directory is not the disposable guest path' >&2
    return 1
  }
  work_parent=${WORK_DIR%/*}
  [[ $work_parent == /var/tmp/easysynq-proof ]] || return 1
  [[ ! -e $WORK_DIR && ! -L $WORK_DIR ]] || {
    printf '%s\n' 'fedora-proof: disposable worktree already exists' >&2
    return 1
  }
  mkdir "$WORK_DIR"
  cp -a "$SOURCE_DIR/." "$WORK_DIR/"
  cd "$WORK_DIR"
  [[ ! -e .env && ! -L .env ]] || {
    printf '%s\n' 'fedora-proof: a host .env reached the guest source payload' >&2
    return 1
  }
  [[ ! -e .import-source && ! -L .import-source ]] || {
    printf '%s\n' 'fedora-proof: host import/site data reached the guest source payload' >&2
    return 1
  }

  git init -q
  git config user.name 'EasySynQ Fedora Proof'
  git config user.email 'fedora-proof@example.invalid'

  just setup
  ./scripts/doctor.sh contributor
  ./scripts/doctor.sh test
  docker run --rm hello-world
  (
    cd apps/api
    uv run python - <<'PY'
from testcontainers.core.docker_client import DockerClient

assert DockerClient().client.ping() is True
print("testcontainers DockerClient ping: PASS")
PY
    uv run pytest tests/unit -m unit --tb=short
  )
  (
    cd apps/web
    npm run lint
    npm run typecheck
    npm test -- --run
  )
  npm ci --prefix packages/contracts --ignore-scripts
  bash scripts/tests/test-run-contract-tool.sh

  generate_disposable_env .env
  mkdir -p .import-source
  docker compose --env-file .env.example \
    -f infra/compose/compose.yml \
    -f infra/compose/compose.s.yml \
    -f infra/compose/compose.dev.yml config --quiet
  trap cleanup_stack EXIT
  stack_started=1
  docker compose --env-file .env \
    -f infra/compose/compose.yml \
    -f infra/compose/compose.s.yml \
    -f infra/compose/compose.dev.yml up -d
  ./scripts/doctor.sh stack
  docker compose --env-file .env \
    -f infra/compose/compose.yml \
    -f infra/compose/compose.s.yml \
    -f infra/compose/compose.dev.yml down -v
  stack_started=0
  trap - EXIT
  assert_platform
  printf '%s\n' 'FEDORA_PROOF_PASS'
}

while (( $# )); do
  case "$1" in
    --source-dir)
      (( $# >= 2 )) || { usage; exit 2; }
      SOURCE_DIR=$2
      shift 2
      ;;
    --after-docker-group)
      after_docker_group=1
      shift
      ;;
    --print-plan)
      (( $# == 1 )) || { usage; exit 2; }
      print_plan
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if (( after_docker_group )); then
  run_after_docker_group
  exit
fi

assert_platform
[[ -d $SOURCE_DIR && ! -L $SOURCE_DIR ]] || {
  printf '%s\n' 'fedora-proof: disposable guest source directory is missing or unsafe' >&2
  exit 1
}
cd "$SOURCE_DIR"
./scripts/bootstrap-fedora-dev.sh --check
run_confirmed_apply
run_confirmed_apply
sudo systemctl enable --now docker
sudo usermod -aG docker "$(id -un)"
printf -v grouped_command '%q ' bash "$SOURCE_DIR/scripts/inside-fedora-proof.sh" \
  --source-dir "$SOURCE_DIR" --after-docker-group
exec sg docker -c "$grouped_command"

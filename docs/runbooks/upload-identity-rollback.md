# Exact-version upload compatibility rollback

Use this runbook only when the current API must be replaced temporarily by an older build that cannot
pin and verify staging `VersionId`. This is a fail-closed application rollback. It preserves the new
Compose, Caddy, and MinIO initialization configuration, keeps both temporary buckets versioned, and
blocks promotion until exact-version-capable code is restored.

Run every section in one Bash session with `set -euo pipefail`. Stop immediately if an assertion fails.
The rollback and recovery artifacts must each be independently approved before starting.

## 1. Establish the full Compose command

For an installed appliance, run:

```bash
set -euo pipefail
cd /opt/easysynq
ESQ_MODE=appliance
ESQ_ENV_FILE=/opt/easysynq/.env
ESQ_COMPOSE=(sudo easysynq-compose)
ESQ_DOCKER=(sudo docker)
"${ESQ_COMPOSE[@]}" config --quiet
sudo bash scripts/validate-browser-origins.sh --env-file "$ESQ_ENV_FILE"
```

For a repository/online install, change to the repository root containing `.env`, then run this strict
profile selection. The profile filename is selected by a literal `case` arm; unvalidated text is never
interpolated into a path.

```bash
set -euo pipefail
ESQ_MODE=repository
ESQ_ENV_FILE=.env
mapfile -t ESQ_PROFILE_LINES < <(grep '^EASYSYNQ_PROFILE=' "$ESQ_ENV_FILE" || true)
if [ "${#ESQ_PROFILE_LINES[@]}" -ne 1 ]; then
  echo 'rollback: .env must contain exactly one EASYSYNQ_PROFILE assignment' >&2
  exit 1
fi
ESQ_PROFILE="${ESQ_PROFILE_LINES[0]#EASYSYNQ_PROFILE=}"
case "$ESQ_PROFILE" in
  s) ESQ_PROFILE_FILE='infra/compose/compose.s.yml' ;;
  m) ESQ_PROFILE_FILE='infra/compose/compose.m.yml' ;;
  *) echo 'rollback: EASYSYNQ_PROFILE must be exactly s or m' >&2; exit 1 ;;
esac
ESQ_COMPOSE=(
  docker compose --env-file "$ESQ_ENV_FILE"
  -f infra/compose/compose.yml
  -f "$ESQ_PROFILE_FILE"
  -f infra/compose/compose.production.yml
)
ESQ_DOCKER=(docker)
"${ESQ_COMPOSE[@]}" config --quiet
bash scripts/validate-browser-origins.sh --env-file "$ESQ_ENV_FILE"
```

Do not roll back any Compose file, either Caddyfile, or `infra/compose/minio/minio-init.sh`. Never run
`mc version suspend` on `staging` or `import-staging`. Do not add expiry rules as part of rollback.

Define this helper library once. The appliance setter performs the same-directory atomic replacement
under privilege and preserves the provisioner's `root:easysynq` ownership and `0640` mode. The
repository setter stays unprivileged and preserves its safe existing ownership and mode. Both reject
symlinks, duplicates, concurrent replacement, arbitrary keys, and arbitrary values.

```bash
# rollback-helper-library
esq_atomic_set_env_file() {
  local file="$1" expected_uid="$2" expected_gid="$3" expected_mode="$4"
  local key="$5" value="$6" count tmp='' before_identity current_identity
  [ "$key" = EASYSYNQ_COMPATIBILITY_READ_ONLY ] || {
    echo 'rollback: refusing to update an unapproved environment key' >&2
    return 1
  }
  case "$value" in 0|1) ;; *) echo 'rollback: guard value must be 0 or 1' >&2; return 1 ;; esac
  test -f "$file"
  test ! -L "$file"
  [ "$(stat -c %u "$file")" = "$expected_uid" ]
  [ "$(stat -c %g "$file")" = "$expected_gid" ]
  [ "$(stat -c %a "$file")" = "$expected_mode" ]
  before_identity="$(stat -c '%d:%i' "$file")"
  count="$(grep -Fxc "${key}=${value}" "$file" || true)"
  [ "$count" -le 1 ]
  count="$(grep -c "^${key}=" "$file" || true)"
  if [ "$count" -gt 1 ]; then
    echo "rollback: duplicate ${key} assignments in the environment file" >&2
    return 1
  fi
  umask 077
  tmp="$(mktemp "${file}.upload-identity.XXXXXX")"
  trap 'if [ -n "${tmp:-}" ]; then rm -f -- "$tmp"; fi' RETURN
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$file" >"$tmp"
  chown "$expected_uid:$expected_gid" "$tmp"
  chmod "$expected_mode" "$tmp"
  test -f "$file"
  test ! -L "$file"
  current_identity="$(stat -c '%d:%i' "$file")"
  [ "$current_identity" = "$before_identity" ] || {
    echo 'rollback: environment file changed during guarded update' >&2
    return 1
  }
  mv -T -- "$tmp" "$file"
  tmp=''
  trap - RETURN
  test ! -L "$file"
  [ "$(stat -c '%u:%g:%a' "$file")" = "${expected_uid}:${expected_gid}:${expected_mode}" ]
  [ "$(grep -c "^${key}=${value}$" "$file")" = 1 ]
}

esq_set_env_appliance() {
  local key="$1" value="$2" easysynq_gid
  easysynq_gid="$(getent group easysynq | awk -F: 'NR == 1 { print $3 }')"
  [[ "$easysynq_gid" =~ ^[0-9]+$ ]]
  sudo bash -c "$(declare -f esq_atomic_set_env_file)
set -euo pipefail
test -d /opt/easysynq
test ! -L /opt/easysynq
[ \"\$(stat -c %u /opt/easysynq)\" = 0 ]
directory_mode=\"\$(stat -c %A /opt/easysynq)\"
case \"\${directory_mode:5:1}\${directory_mode:8:1}\" in *w*) exit 1 ;; esac
test \"\$(stat -c %U:%G /opt/easysynq/.env)\" = root:easysynq
esq_atomic_set_env_file /opt/easysynq/.env 0 '$easysynq_gid' 640 \"\$1\" \"\$2\"" \
    bash "$key" "$value"
}

esq_set_env_repository() {
  local key="$1" value="$2" owner group mode current_uid current_groups
  test -f "$ESQ_ENV_FILE"
  test ! -L "$ESQ_ENV_FILE"
  owner="$(stat -c %u "$ESQ_ENV_FILE")"
  group="$(stat -c %g "$ESQ_ENV_FILE")"
  mode="$(stat -c %a "$ESQ_ENV_FILE")"
  current_uid="$(id -u)"
  [ "$owner" = "$current_uid" ] || {
    echo 'rollback: repository .env must be owned by the invoking user' >&2
    return 1
  }
  case "$mode" in 600|640) ;; *) echo 'rollback: repository .env mode must be 0600 or 0640' >&2; return 1 ;; esac
  current_groups=" $(id -G) "
  case "$current_groups" in *" $group "*) ;; *) echo 'rollback: repository .env group is inaccessible' >&2; return 1 ;; esac
  esq_atomic_set_env_file "$ESQ_ENV_FILE" "$owner" "$group" "$mode" "$key" "$value"
}

esq_set_env() {
  case "$ESQ_MODE" in
    appliance) esq_set_env_appliance "$@" ;;
    repository) esq_set_env_repository "$@" ;;
    *) echo 'rollback: unknown installation mode' >&2; return 1 ;;
  esac
}

esq_load_public_base_url() {
  local env_line
  if [ "$ESQ_MODE" = appliance ]; then
    mapfile -t ESQ_BASE_LINES < <(
      sudo awk '/^PUBLIC_BASE_URL=/ { print }' /opt/easysynq/.env
    )
  else
    mapfile -t ESQ_BASE_LINES < <(grep '^PUBLIC_BASE_URL=' "$ESQ_ENV_FILE" || true)
  fi
  if [ "${#ESQ_BASE_LINES[@]}" -ne 1 ]; then
    echo 'rollback: environment must contain exactly one PUBLIC_BASE_URL assignment' >&2
    return 1
  fi
  env_line="${ESQ_BASE_LINES[0]}"
  ESQ_BASE_URL="${env_line#PUBLIC_BASE_URL=}"
  ESQ_BASE_URL="${ESQ_BASE_URL%/}"
  case "$ESQ_BASE_URL" in
    http://*|https://*) ;;
    *) echo 'rollback: PUBLIC_BASE_URL must be an HTTP(S) origin' >&2; return 1 ;;
  esac
  case "$ESQ_BASE_URL" in
    *[[:space:]]*|*,*|*\**|*'?'*|*'#'*)
      echo 'rollback: PUBLIC_BASE_URL is not one exact origin' >&2
      return 1
      ;;
  esac
}

ESQ_TEMP_FILES=()
esq_make_temp() {
  local -n destination="$1"
  destination="$(mktemp /tmp/easysynq-upload-identity.XXXXXX)"
  ESQ_TEMP_FILES+=("$destination")
}
esq_cleanup_temp_files() {
  local file
  for file in "${ESQ_TEMP_FILES[@]}"; do
    case "$file" in /tmp/easysynq-upload-identity.*) rm -f -- "$file" ;; *) return 1 ;; esac
  done
}
trap esq_cleanup_temp_files EXIT

esq_configure_curl() {
  local authority
  if [ "$ESQ_MODE" = appliance ]; then
    case "$ESQ_BASE_URL" in https://*) ;; *) echo 'rollback: appliance origin must use HTTPS' >&2; return 1 ;; esac
    authority="${ESQ_BASE_URL#https://}"
    case "$authority" in */*|*:*|*@*) echo 'rollback: appliance URL must be an HTTPS host origin' >&2; return 1 ;; esac
    [[ "$authority" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
    ESQ_HTTPS_HOST="$authority"
    esq_make_temp ESQ_CADDY_CA
    sudo easysynq-status --ca >"$ESQ_CADDY_CA"
    chmod 0600 "$ESQ_CADDY_CA"
    openssl x509 -in "$ESQ_CADDY_CA" -noout -checkend 0
    ESQ_CURL=(
      curl --silent --show-error
      --cacert "$ESQ_CADDY_CA"
      --resolve "${ESQ_HTTPS_HOST}:443:127.0.0.1"
    )
  else
    ESQ_CURL=(curl --silent --show-error)
  fi
}

esq_resolve_api_service_image() {
  local api_container configured_image
  api_container="$("${ESQ_COMPOSE[@]}" ps -q api)"
  [[ "$api_container" =~ ^[0-9a-f]{12,64}$ ]] || {
    echo 'rollback: expected exactly one running Compose API container' >&2
    return 1
  }
  configured_image="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Config.Image}}' "$api_container")"
  case "$configured_image" in
    easysynq-api|easysynq-api:latest) ESQ_API_SERVICE_IMAGE=easysynq-api:latest ;;
    *) echo 'rollback: Compose API image name is not the expected easysynq-api service image' >&2; return 1 ;;
  esac
  ESQ_ORIGINAL_API_IMAGE_ID="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Image}}' "$api_container")"
  [[ "$ESQ_ORIGINAL_API_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]
}

esq_select_api_artifact() {
  local purpose="$1" selected_ref selected_id
  SELECTED_API_IMAGE_ID=''
  if [ "$ESQ_MODE" = appliance ]; then
    local archive archive_input sidecar sidecar_input expected actual listed load_output
    local -a sidecar_lines loaded_refs
    read -rp "Absolute path to approved ${purpose} API image archive: " archive_input
    read -rp "Absolute path to its SHA-256 sidecar: " sidecar_input
    case "$archive_input:$sidecar_input" in /*:/*) ;; *) echo 'rollback: artifact paths must be absolute' >&2; return 1 ;; esac
    test ! -L "$archive_input"; test ! -L "$sidecar_input"
    archive="$(realpath -e -- "$archive_input")"
    sidecar="$(realpath -e -- "$sidecar_input")"
    [ "$archive" != "$sidecar" ]
    test -f "$archive"; test ! -L "$archive"; test -r "$archive"
    test -f "$sidecar"; test ! -L "$sidecar"; test -r "$sidecar"
    mapfile -t sidecar_lines <"$sidecar"
    [ "${#sidecar_lines[@]}" -eq 1 ]
    [[ "${sidecar_lines[0]}" =~ ^([0-9a-f]{64})([[:space:]]+\*?([^/[:space:]]+))?$ ]]
    expected="${BASH_REMATCH[1]}"
    listed="${BASH_REMATCH[3]:-}"
    [ -z "$listed" ] || [ "$listed" = "$(basename -- "$archive")" ]
    actual="$(sha256sum -- "$archive")"
    actual="${actual%% *}"
    [ "$actual" = "$expected" ] || {
      echo "rollback: ${purpose} archive digest mismatch" >&2
      return 1
    }
    # docker load: the verified archive must declare exactly one tagged image.
    load_output="$("${ESQ_DOCKER[@]}" load --input "$archive")"
    printf '%s\n' "$load_output"
    mapfile -t loaded_refs < <(printf '%s\n' "$load_output" | sed -n 's/^Loaded image: //p')
    [ "${#loaded_refs[@]}" -eq 1 ] || {
      echo 'rollback: archive must load exactly one tagged API image' >&2
      return 1
    }
    selected_ref="${loaded_refs[0]}"
    [[ "$selected_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@-]*$ ]]
    selected_id="$("${ESQ_DOCKER[@]}" image inspect --format '{{.Id}}' "$selected_ref")"
  else
    local requested_commit resolved_commit repository_root
    read -rp "Approved full local Git commit for ${purpose} API: " requested_commit
    [[ "$requested_commit" =~ ^[0-9a-f]{40}$ ]] || {
      echo 'rollback: enter the approved full lowercase Git commit' >&2
      return 1
    }
    resolved_commit="$(git rev-parse --verify "${requested_commit}^{commit}")"
    [ "$resolved_commit" = "$requested_commit" ]
    repository_root="$(git rev-parse --show-toplevel)"
    selected_id="$(
      set -euo pipefail
      build_root="$(mktemp -d /tmp/easysynq-api-build.XXXXXX)"
      build_source="$build_root/source"
      build_tag="easysynq-api-selected:${resolved_commit}"
      worktree_added=0
      cleanup_repo_build() {
        if [ "$worktree_added" = 1 ]; then
          git -C "$repository_root" worktree remove --force "$build_source" >&2
        fi
        test ! -L "$build_root"
        case "$build_root" in /tmp/easysynq-api-build.*) rmdir "$build_root" ;; *) return 1 ;; esac
      }
      trap cleanup_repo_build EXIT
      git -C "$repository_root" worktree add --detach "$build_source" "$resolved_commit" >&2
      worktree_added=1
      # docker build: build only the approved detached source tree.
      "${ESQ_DOCKER[@]}" build --file "$build_source/apps/api/Dockerfile" \
        --tag "$build_tag" "$build_source" >&2
      "${ESQ_DOCKER[@]}" image inspect --format '{{.Id}}' "$build_tag"
    )"
  fi
  [[ "$selected_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  SELECTED_API_IMAGE_ID="$selected_id"
  "${ESQ_DOCKER[@]}" image tag "$SELECTED_API_IMAGE_ID" "$ESQ_API_SERVICE_IMAGE"
  [ "$("${ESQ_DOCKER[@]}" image inspect --format '{{.Id}}' "$ESQ_API_SERVICE_IMAGE")" = "$SELECTED_API_IMAGE_ID" ]
}

esq_require_running_api_image() {
  local expected_id="$1" api_container running_id
  [[ "$expected_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  api_container="$("${ESQ_COMPOSE[@]}" ps -q api)"
  [[ "$api_container" =~ ^[0-9a-f]{12,64}$ ]]
  # docker container inspect: compare the immutable image ID, not a mutable tag.
  running_id="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Image}}' "$api_container")"
  [ "$running_id" = "$expected_id" ] || {
    echo 'rollback: running API image ID does not match the selected artifact' >&2
    return 1
  }
}
```

## 2. Enable and prove the compatibility guard

Set the deployment interlock and recreate only the proxy:

```bash
esq_set_env EASYSYNQ_COMPATIBILITY_READ_ONLY 1
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy
```

Load and validate the application origin, configure the mode-specific TLS probe, then prompt without
echo for a bearer token and for one representative, already-committed vault GET path. An appliance
probe exports and validates Caddy's private CA and pins the configured HTTPS hostname to loopback. It
never disables certificate or hostname verification.

```bash
esq_load_public_base_url
esq_configure_curl
read -rsp 'Bearer token for rollback proof: ' ESQ_ACCESS_TOKEN
printf '\n'
if [ -z "$ESQ_ACCESS_TOKEN" ]; then
  echo 'rollback: bearer token is required' >&2
  exit 1
fi
read -rp 'Representative vault GET path beginning /api/v1/documents/ or /api/v1/records/: ' ESQ_VAULT_GET_PATH
case "$ESQ_VAULT_GET_PATH" in
  /api/v1/documents/*|/api/v1/records/*) ;;
  *) echo 'rollback: representative path must be a document or Record GET path' >&2; exit 1 ;;
esac
case "$ESQ_VAULT_GET_PATH" in
  *[[:space:]]*) echo 'rollback: representative path contains whitespace' >&2; exit 1 ;;
esac
```

Before changing application images, prove the edge guard, liveness, and an authenticated committed
read. The guard body comparison is exact. Abort rollback if any command fails. Resolve the API service
image from the live Compose container now; the build-only `api` service must use Compose's generated
`easysynq-api:latest` tag, and the original immutable image ID is recorded.

```bash
esq_make_temp ESQ_PROBE_BODY
ESQ_PROBE_STATUS="$("${ESQ_CURL[@]}" --output "$ESQ_PROBE_BODY" --write-out '%{http_code}' --request POST "$ESQ_BASE_URL/api/v1/rollback-write-probe")"
test "$ESQ_PROBE_STATUS" = 503
test "$(cat "$ESQ_PROBE_BODY")" = 'Write operations are disabled during compatibility rollback.'
"${ESQ_CURL[@]}" --fail-with-body "$ESQ_BASE_URL/healthz"
"${ESQ_CURL[@]}" --fail-with-body --header "Authorization: Bearer $ESQ_ACCESS_TOKEN" "$ESQ_BASE_URL$ESQ_VAULT_GET_PATH"
esq_resolve_api_service_image
```

## 3. Stop every asynchronous promoter, then start only the older API

Stop worker and Beat before introducing the older API. Confirm neither is running:

```bash
"${ESQ_COMPOSE[@]}" stop worker beat
ESQ_RUNNING_ASYNC="$("${ESQ_COMPOSE[@]}" ps --status running --services worker beat)"
if [ -n "$ESQ_RUNNING_ASYNC" ]; then
  echo 'rollback: worker or beat is still running' >&2
  exit 1
fi
```

For an appliance, select an approved prebuilt API image archive and its separately supplied SHA-256
sidecar. The helper verifies the archive before loading it and accepts exactly one tagged image. For a
repository install, select an approved full commit that already exists locally; the helper builds it
from a separate detached temporary worktree. In both modes, the selected immutable ID is retagged to
the actual Compose API service image, only `api` is recreated, and the running container ID must match.

```bash
# rollback-artifact-selection
esq_select_api_artifact rollback
ESQ_ROLLBACK_API_IMAGE_ID="$SELECTED_API_IMAGE_ID"
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate --no-build api
esq_require_running_api_image "$ESQ_ROLLBACK_API_IMAGE_ID"
```

Never start, recreate, or run an older `worker` or `beat`. Never run a migration during this procedure.
Never run any exact-version-incompatible worker against versioned staging. Keep the compatibility
guard enabled for the older API's entire lifetime.

Direct presigned browser PUTs use the separate MinIO origin and do not pass through Caddy's application
write guard. That is safe only because upload-init/check-in API writes are blocked and all workers are
stopped: the PUT cannot promote itself. Already-issued presigned URLs may leave harmless, versioned
staging objects for a later compatible flow. CORS response visibility at the MinIO origin does not
authorize data access; S3 IAM and the presigned request remain the access boundary.

## 4. Restore exact-version-capable code

Select a separately approved, known exact-version-capable API artifact while the guard remains enabled.
Recovery must not reuse the rollback image. Recreate only API and prove its immutable running image ID:

```bash
# recovery-artifact-selection
esq_select_api_artifact recovery
ESQ_RECOVERY_API_IMAGE_ID="$SELECTED_API_IMAGE_ID"
[ "$ESQ_RECOVERY_API_IMAGE_ID" != "$ESQ_ROLLBACK_API_IMAGE_ID" ] || {
  echo 'rollback: recovery artifact must not reuse the rollback image' >&2
  exit 1
}
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate --no-build api
esq_require_running_api_image "$ESQ_RECOVERY_API_IMAGE_ID"
```

Require readiness. A 200 includes the MinIO probe's checks that both `staging` and `import-staging`
report versioning exactly `Enabled`:

```bash
esq_make_temp ESQ_READY_BODY
ESQ_READY_STATUS="$("${ESQ_CURL[@]}" --output "$ESQ_READY_BODY" --write-out '%{http_code}' "$ESQ_BASE_URL/readyz")"
test "$ESQ_READY_STATUS" = 200
grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' "$ESQ_READY_BODY"
grep -Eq '"name"[[:space:]]*:[[:space:]]*"minio"[[:space:]]*,[[:space:]]*"ready"[[:space:]]*:[[:space:]]*true' "$ESQ_READY_BODY"
```

Only after readiness succeeds, disable the edge guard and recreate only proxy:

```bash
esq_set_env EASYSYNQ_COMPATIBILITY_READ_ONLY 0
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy
```

Repeat the deliberately non-existent, non-mutating write probe. The static guard response must be gone;
the compatible API normally returns 404. This proves routing without changing business state:

```bash
esq_make_temp ESQ_RECOVERY_BODY
ESQ_RECOVERY_STATUS="$("${ESQ_CURL[@]}" --output "$ESQ_RECOVERY_BODY" --write-out '%{http_code}' --request POST "$ESQ_BASE_URL/api/v1/rollback-write-probe")"
if [ "$ESQ_RECOVERY_STATUS" = 503 ] && \
   [ "$(cat "$ESQ_RECOVERY_BODY")" = 'Write operations are disabled during compatibility rollback.' ]; then
  echo 'rollback: compatibility guard is still active' >&2
  exit 1
fi
```

Start only the compatible worker and Beat processes, then confirm their state:

```bash
"${ESQ_COMPOSE[@]}" start worker beat
"${ESQ_COMPOSE[@]}" ps worker beat
```

Keep both staging buckets versioned and retain the CORS/configuration changes permanently. This rollback
does not persist or establish ownership of target WORM VersionIds; it only prevents an incompatible
producer from selecting or promoting the wrong staged source.

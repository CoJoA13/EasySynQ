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
ESQ_DOCKER=(sudo -n docker)
ESQ_EXPECTED_API_REPLICAS=1
"${ESQ_COMPOSE[@]}" config --quiet
sudo bash scripts/validate-browser-origins.sh --env-file "$ESQ_ENV_FILE"
```

The appliance is the single-replica S layout, so its expected API replica count is one.

For a repository/online install, change to the repository root containing `.env`, then run this strict
profile selection. The profile filename is selected by a literal `case` arm; unvalidated text is never
interpolated into a path. The literal profile also fixes the expected API replica count: one for `s`
and two for `m`.

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
  s) ESQ_PROFILE_FILE='infra/compose/compose.s.yml'; ESQ_EXPECTED_API_REPLICAS=1 ;;
  m) ESQ_PROFILE_FILE='infra/compose/compose.m.yml'; ESQ_EXPECTED_API_REPLICAS=2 ;;
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
symlinks, duplicates, metadata drift, arbitrary keys, and arbitrary values. A persistent
same-directory lock serializes every writer using this helper. Linux `RENAME_EXCHANGE` then proves
that the exact inode opened under the lock was swapped; if one noncooperating replacement is present
at that boundary, the helper atomically restores it and aborts. This cannot prevent an arbitrary
noncooperating writer from racing repeatedly. Cleanup first moves a candidate with no-overwrite
semantics into a validated operator-owned `0700` quarantine. No quarantined pathname is removed
automatically: the normal parked old environment, a restored helper temporary, and every unexpected
entry are retained and their exact safe paths are reported for manual inspection and cleanup. These
files can contain prior `.env` secrets and preserve their restrictive ownership and mode; do not
print their contents or relax their metadata while inspecting them.

```bash
# rollback-helper-library
esq_atomic_set_env_file() {
  python3 -I - "$@" <<'PY'
import ctypes
import fcntl
import os
import secrets
import stat
import sys

if len(sys.argv) != 7:
    raise SystemExit("rollback: guarded setter requires exactly six arguments")
path, uid_text, gid_text, mode_text, key, value = sys.argv[1:]
if key != "EASYSYNQ_COMPATIBILITY_READ_ONLY":
    raise SystemExit("rollback: refusing to update an unapproved environment key")
if value not in {"0", "1"}:
    raise SystemExit("rollback: guard value must be 0 or 1")
expected_uid = int(uid_text, 10)
expected_gid = int(gid_text, 10)
expected_mode = int(mode_text, 8)
parent = os.path.abspath(os.path.dirname(path) or ".")
if parent != os.path.realpath(parent):
    raise SystemExit("rollback: environment parent must not traverse a symlink")
name = os.path.basename(path)
if not name or name in {".", ".."}:
    raise SystemExit("rollback: invalid environment filename")

dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int

def rename_linux(left_fd: int, left: str, right_fd: int, right: str, flags: int) -> None:
    if renameat2(left_fd, os.fsencode(left), right_fd, os.fsencode(right), flags) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))

lock_name = f"{name}.rollback.lock"
lock_fd = os.open(
    lock_name,
    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
    0o600,
    dir_fd=dir_fd,
)
lock_stat = os.fstat(lock_fd)
if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
    raise SystemExit("rollback: unsafe environment lock file")
if lock_stat.st_uid != expected_uid or stat.S_IMODE(lock_stat.st_mode) != 0o600:
    raise SystemExit("rollback: environment lock metadata mismatch")
fcntl.flock(lock_fd, fcntl.LOCK_EX)

quarantine_name = f"{name}.rollback-quarantine"
quarantine_created = False
try:
    os.mkdir(quarantine_name, 0o700, dir_fd=dir_fd)
    quarantine_created = True
except FileExistsError:
    pass
quarantine_fd = os.open(
    quarantine_name,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    dir_fd=dir_fd,
)
if quarantine_created:
    os.fchown(quarantine_fd, expected_uid, -1)
    os.fchmod(quarantine_fd, 0o700)
quarantine_stat = os.fstat(quarantine_fd)
if (
    not stat.S_ISDIR(quarantine_stat.st_mode)
    or quarantine_stat.st_uid != expected_uid
    or stat.S_IMODE(quarantine_stat.st_mode) != 0o700
):
    raise SystemExit("rollback: unsafe cleanup quarantine")

def quarantine_retain(candidate: str, expected_identity: tuple[int, int], label: str) -> None:
    retained_name = (
        f"{label}.{expected_identity[0]:x}.{expected_identity[1]:x}."
        f"{secrets.token_hex(8)}"
    )
    safe_path = os.path.join(parent, quarantine_name, retained_name)
    source_path = os.path.join(parent, candidate)
    try:
        rename_linux(dir_fd, candidate, quarantine_fd, retained_name, 1)
    except OSError as error:
        raise SystemExit(
            f"rollback: could not establish safe quarantine move; retained in place at {source_path}"
        ) from error
    os.fsync(quarantine_fd)
    os.fsync(dir_fd)
    try:
        retained_fd = os.open(
            retained_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=quarantine_fd,
        )
    except OSError as error:
        raise SystemExit(f"rollback: retained unsafe cleanup entry at {safe_path}") from error
    retained_stat = os.fstat(retained_fd)
    retained_identity = (retained_stat.st_dev, retained_stat.st_ino)
    if retained_identity != expected_identity:
        os.close(retained_fd)
        raise SystemExit(f"rollback: retained unexpected cleanup inode at {safe_path}")
    os.close(retained_fd)
    print(
        f"rollback: retained {label} at {safe_path}; "
        "contains prior .env secrets; inspect and remove manually",
        file=sys.stderr,
    )

source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
source_stat = os.fstat(source_fd)
if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
    raise SystemExit("rollback: environment source is not one regular file")
if (
    source_stat.st_uid != expected_uid
    or source_stat.st_gid != expected_gid
    or stat.S_IMODE(source_stat.st_mode) != expected_mode
):
    raise SystemExit("rollback: environment metadata mismatch")
with os.fdopen(os.dup(source_fd), "rb") as source:
    content = source.read()
prefix = key.encode("ascii") + b"="
lines = content.splitlines(keepends=True)
matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
if len(matches) > 1:
    raise SystemExit("rollback: duplicate guarded environment assignments")
replacement = prefix + value.encode("ascii") + b"\n"
if matches:
    lines[matches[0]] = replacement
    updated = b"".join(lines)
else:
    updated = content + (b"" if not content or content.endswith(b"\n") else b"\n") + replacement

temp_name = f"{name}.upload-identity.{secrets.token_hex(8)}"
temp_fd = None
temp_identity = None
try:
    temp_fd = os.open(
        temp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=dir_fd,
    )
    view = memoryview(updated)
    while view:
        view = view[os.write(temp_fd, view):]
    os.fchown(temp_fd, expected_uid, expected_gid)
    os.fchmod(temp_fd, expected_mode)
    os.fsync(temp_fd)
    temp_stat = os.fstat(temp_fd)
    temp_identity = (temp_stat.st_dev, temp_stat.st_ino)
    os.close(temp_fd)
    temp_fd = None

    def exchange(left: str, right: str) -> None:
        rename_linux(dir_fd, left, dir_fd, right, 2)

    # final-exchange-boundary
    exchange(temp_name, name)
    parked = os.stat(temp_name, dir_fd=dir_fd, follow_symlinks=False)
    installed = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    if (parked.st_dev, parked.st_ino) != source_identity:
        concurrent_identity = (parked.st_dev, parked.st_ino)
        exchange(temp_name, name)
        restored = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        returned = os.stat(temp_name, dir_fd=dir_fd, follow_symlinks=False)
        if (restored.st_dev, restored.st_ino) != concurrent_identity:
            raise SystemExit("rollback: repeated noncooperating race; no file was removed")
        if (returned.st_dev, returned.st_ino) != temp_identity:
            raise SystemExit("rollback: exchange rollback could not prove its temporary file")
        # restoration-cleanup-boundary
        retention_identity = temp_identity
        temp_identity = None
        quarantine_retain(temp_name, retention_identity, "restored-update")
        raise SystemExit("rollback: concurrent environment replacement restored; update aborted")
    if (installed.st_dev, installed.st_ino) != temp_identity:
        raise SystemExit("rollback: noncooperating replacement after exchange; no file was removed")
    # success-cleanup-boundary
    temp_identity = None
    quarantine_retain(temp_name, source_identity, "replaced-source")
finally:
    if temp_fd is not None:
        os.close(temp_fd)
    if temp_identity is not None:
        retention_identity = temp_identity
        temp_identity = None
        quarantine_retain(temp_name, retention_identity, "failed-update")
    os.close(source_fd)
    os.close(quarantine_fd)
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
    os.close(dir_fd)
PY
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
      curl --disable --silent --show-error --noproxy '*'
      --cacert "$ESQ_CADDY_CA"
      --resolve "${ESQ_HTTPS_HOST}:443:127.0.0.1"
    )
  else
    ESQ_CURL=(curl --disable --silent --show-error --noproxy '*')
  fi
}

esq_resolve_api_service_image() {
  local api_container configured_image current_id first_configured_image=''
  local -a api_containers
  [[ "$ESQ_EXPECTED_API_REPLICAS" =~ ^[12]$ ]]
  mapfile -t api_containers < <("${ESQ_COMPOSE[@]}" ps -q --status running api)
  [ "${#api_containers[@]}" -eq "$ESQ_EXPECTED_API_REPLICAS" ] || {
    echo 'rollback: running API replica count does not match the selected profile' >&2
    return 1
  }
  ESQ_ORIGINAL_API_IMAGE_ID=''
  for api_container in "${api_containers[@]}"; do
    [[ "$api_container" =~ ^[0-9a-f]{12,64}$ ]]
    configured_image="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Config.Image}}' "$api_container")"
    case "$configured_image" in
      easysynq-api|easysynq-api:latest) ESQ_API_SERVICE_IMAGE=easysynq-api:latest ;;
      *) echo 'rollback: an API replica uses an unexpected Compose image reference' >&2; return 1 ;;
    esac
    if [ -z "$first_configured_image" ]; then
      first_configured_image="$configured_image"
    else
      [ "$configured_image" = "$first_configured_image" ] || {
        echo 'rollback: API replicas do not share one configured image reference' >&2
        return 1
      }
    fi
    current_id="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Image}}' "$api_container")"
    [[ "$current_id" =~ ^sha256:[0-9a-f]{64}$ ]]
    if [ -z "$ESQ_ORIGINAL_API_IMAGE_ID" ]; then
      ESQ_ORIGINAL_API_IMAGE_ID="$current_id"
    else
      [ "$current_id" = "$ESQ_ORIGINAL_API_IMAGE_ID" ] || {
        echo 'rollback: API replicas do not share one immutable current image' >&2
        return 1
      }
    fi
  done
}

esq_select_api_artifact() {
  local purpose="$1" selected_ref selected_id
  SELECTED_API_IMAGE_ID=''
  if [ "$ESQ_MODE" = appliance ]; then
    local archive_input sidecar_input load_output
    local -a loaded_refs
    read -rp "Absolute path to approved ${purpose} API image archive: " archive_input
    read -rp "Absolute path to its SHA-256 sidecar: " sidecar_input
    load_output="$(python3 -I - "$archive_input" "$sidecar_input" "$purpose" "${ESQ_DOCKER[@]}" <<'PY'
import fcntl
import hashlib
import os
import re
import resource
import stat
import subprocess
import sys

if len(sys.argv) < 5:
    raise SystemExit("rollback: stable artifact loader requires archive, sidecar, purpose, and Docker command")
archive_path, sidecar_path, purpose, *docker_command = sys.argv[1:]
if not os.path.isabs(archive_path) or not os.path.isabs(sidecar_path):
    raise SystemExit("rollback: artifact paths must be absolute")
if archive_path == sidecar_path:
    raise SystemExit("rollback: archive and sidecar paths must differ")

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_SIDECAR_BYTES = 8192

def stable_open(path: str, maximum_size: int, label: str) -> int:
    parent = os.path.abspath(os.path.dirname(path))
    if parent != os.path.realpath(parent):
        raise SystemExit("rollback: artifact parent must not traverse a symlink")
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(
            os.path.basename(path),
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise SystemExit("rollback: artifact input must be one regular inode")
    if metadata.st_uid not in {0, os.getuid()} or stat.S_IMODE(metadata.st_mode) & 0o022:
        os.close(descriptor)
        raise SystemExit("rollback: artifact input owner or permissions are unsafe")
    if metadata.st_size > maximum_size:
        os.close(descriptor)
        raise SystemExit(f"rollback: {label} exceeds the allowed snapshot size")
    return descriptor

soft_file_size, _ = resource.getrlimit(resource.RLIMIT_FSIZE)
snapshot_limit = MAX_ARCHIVE_BYTES
if soft_file_size != resource.RLIM_INFINITY:
    snapshot_limit = min(snapshot_limit, max(0, soft_file_size))

archive_fd = stable_open(archive_path, snapshot_limit, "archive")
sidecar_fd = None
snapshot_fd = None
try:
    sidecar_fd = stable_open(sidecar_path, MAX_SIDECAR_BYTES, "digest sidecar")
    # artifact-stable-fds-opened
    sidecar_bytes = os.read(sidecar_fd, MAX_SIDECAR_BYTES + 1)
    if len(sidecar_bytes) > MAX_SIDECAR_BYTES or os.read(sidecar_fd, 1):
        raise SystemExit("rollback: digest sidecar is too large")
    try:
        sidecar_text = sidecar_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise SystemExit("rollback: digest sidecar must be ASCII") from error
    lines = sidecar_text.splitlines()
    if len(lines) != 1:
        raise SystemExit("rollback: digest sidecar must contain exactly one line")
    match = re.fullmatch(r"([0-9a-f]{64})(?:[ \t]+\*?([^/\s]+))?", lines[0])
    if match is None:
        raise SystemExit("rollback: malformed digest sidecar")
    expected, listed = match.groups()
    if listed and listed != os.path.basename(archive_path):
        raise SystemExit("rollback: digest sidecar names a different archive")

    try:
        snapshot_fd = os.memfd_create(
            "easysynq-approved-api",
            flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
    except (AttributeError, OSError) as error:
        raise SystemExit("rollback: cannot create a sealable in-memory archive snapshot") from error
    digest = hashlib.sha256()
    copied = 0
    while True:
        # artifact-snapshot-read-boundary
        try:
            chunk = os.read(archive_fd, 1024 * 1024)
        except OSError as error:
            raise SystemExit("rollback: archive snapshot read failed") from error
        if not chunk:
            break
        copied += len(chunk)
        if copied > snapshot_limit:
            raise SystemExit("rollback: archive grew beyond the allowed snapshot size")
        view = memoryview(chunk)
        while view:
            try:
                written = os.write(snapshot_fd, view)
            except OSError as error:
                raise SystemExit("rollback: archive snapshot write failed") from error
            if written <= 0:
                raise SystemExit("rollback: archive snapshot write made no progress")
            view = view[written:]
        digest.update(chunk)
    if os.fstat(snapshot_fd).st_size != copied:
        raise SystemExit("rollback: archive snapshot size verification failed")
    if digest.hexdigest() != expected:
        raise SystemExit(f"rollback: {purpose} archive digest mismatch")

    try:
        required_seals = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(snapshot_fd, fcntl.F_ADD_SEALS, required_seals)
        observed_seals = fcntl.fcntl(snapshot_fd, fcntl.F_GET_SEALS)
    except (AttributeError, OSError) as error:
        raise SystemExit("rollback: archive snapshot sealing failed") from error
    if observed_seals & required_seals != required_seals:
        raise SystemExit("rollback: archive snapshot seal verification failed")
    os.lseek(snapshot_fd, 0, os.SEEK_SET)
    # artifact-snapshot-sealed
    # artifact-digest-complete
    stable_archive = f"/proc/{os.getpid()}/fd/{snapshot_fd}"
    completed = subprocess.run(
        [*docker_command, "load", "--input", stable_archive],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    sys.stdout.write(completed.stdout)
finally:
    if snapshot_fd is not None:
        os.close(snapshot_fd)
    if sidecar_fd is not None:
        os.close(sidecar_fd)
    os.close(archive_fd)
PY
)"
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
      build_status=0
      set +e
      "${ESQ_DOCKER[@]}" build --file "$build_source/apps/api/Dockerfile" \
        --tag "$build_tag" "$build_source" >&2
      build_status=$?
      if [ "$build_status" -eq 0 ]; then
        built_id="$("${ESQ_DOCKER[@]}" image inspect --format '{{.Id}}' "$build_tag")"
        build_status=$?
      fi
      set -e
      cleanup_repo_build
      worktree_added=0
      build_root=''
      trap - EXIT
      [ "$build_status" -eq 0 ] || exit "$build_status"
      printf '%s\n' "$built_id"
    )"
  fi
  [[ "$selected_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  SELECTED_API_IMAGE_ID="$selected_id"
  "${ESQ_DOCKER[@]}" image tag "$SELECTED_API_IMAGE_ID" "$ESQ_API_SERVICE_IMAGE"
  [ "$("${ESQ_DOCKER[@]}" image inspect --format '{{.Id}}' "$ESQ_API_SERVICE_IMAGE")" = "$SELECTED_API_IMAGE_ID" ]
}

esq_require_running_api_image() {
  local expected_id="$1" api_container running_id
  local -a running_containers all_containers
  local -A running_set=()
  [[ "$expected_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  mapfile -t running_containers < <("${ESQ_COMPOSE[@]}" ps -q --status running api)
  mapfile -t all_containers < <("${ESQ_COMPOSE[@]}" ps -aq api)
  [ "${#running_containers[@]}" -eq "$ESQ_EXPECTED_API_REPLICAS" ]
  [ "${#all_containers[@]}" -eq "$ESQ_EXPECTED_API_REPLICAS" ] || {
    echo 'rollback: stopped or extra API containers remain after cutover' >&2
    return 1
  }
  for api_container in "${running_containers[@]}"; do
    [[ "$api_container" =~ ^[0-9a-f]{12,64}$ ]]
    [ -z "${running_set[$api_container]+present}" ]
    running_set["$api_container"]=1
  done
  for api_container in "${all_containers[@]}"; do
    [ -n "${running_set[$api_container]+present}" ]
    running_id="$("${ESQ_DOCKER[@]}" container inspect --format '{{.Image}}' "$api_container")"
    [ "$running_id" = "$expected_id" ] || {
      echo 'rollback: an API replica image ID does not match the selected artifact' >&2
      return 1
    }
  done
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
`easysynq-api:latest` tag. Exactly the profile's expected number of replicas must be running; every
replica must share the same allowed configured reference and original immutable image ID.

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
sidecar. The helper opens each path once without following symlinks, validates the retained regular
inodes and their ownership/link count/permissions, and reads the expected digest from the retained
sidecar. It copies and hashes the opened archive into an 8 GiB-bounded Linux memfd, also respecting the
process file-size resource limit, then applies and verifies write/grow/shrink/further-sealing seals.
Only that immutable snapshot is rewound and exposed to noninteractive Docker through the live loader's
`/proc` FD path with stdin disabled. A source mutation during copying changes the verified snapshot
digest and aborts unless the copied bytes still equal the approved digest; a mutation after copying is
irrelevant to the sealed bytes Docker reads. It accepts exactly one tagged image. For a repository
install, select an approved full commit that already exists locally; the helper builds it from a
separate detached temporary worktree. In both modes, the selected immutable ID is retagged to the
actual Compose API service image, only `api` is recreated, and the running container ID must match.
The post-cutover check also requires exactly the expected running replica set, rejects any extra
stopped/exited API container, and compares every replica's immutable `.Image` value.

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

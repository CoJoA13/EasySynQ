#!/usr/bin/env bash
# Build, exercise, and remove one disposable Fedora 44 Workstation libvirt guest.

FEDORA_PROOF_MARKER=easysynq-fedora-proof-v1
FEDORA_PROOF_CONNECT=qemu:///system
FEDORA_PROOF_ROOT=/var/tmp

usage() {
  printf '%s\n' \
    'usage: ./scripts/run-fedora-proof.sh \' \
    '  --installer-iso /absolute/path/Fedora-Everything-netinst-x86_64-44-<build>.iso \' \
    '  --installer-iso-sha256 <64-hex-sha256> \' \
    '  --workstation-iso /absolute/path/Fedora-Workstation-Live-44-<build>.x86_64.iso \' \
    '  --workstation-iso-sha256 <64-hex-sha256> [--validate-only]' >&2
}

fedora_proof_new_vm_name() {
  local token timestamp
  timestamp=$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    IFS= read -r token </proc/sys/kernel/random/uuid
    token=${token//-/}
    token=${token:0:8}
  else
    token=$(/usr/bin/openssl rand -hex 4)
  fi
  [[ $token =~ ^[0-9a-f]{8}$ ]] || return 1
  printf 'easysynq-fedora-proof-%s-%d-%s\n' "$timestamp" "$$" "$token"
}

fedora_proof_parse_qemu_passwd() {
  local record=$1 name password uid gid gecos home shell extra
  [[ -n $record && $record != *$'\n'* ]] || {
    printf '%s\n' 'fedora-proof: qemu service account is missing or ambiguous' >&2
    return 1
  }
  IFS=: read -r name password uid gid gecos home shell extra <<<"$record"
  [[ $name == qemu && -z ${extra:-} && $uid =~ ^[0-9]+$ && $gid =~ ^[0-9]+$ \
      && $uid != 0 && $uid != "$EUID" && $home == / && $shell == */nologin ]] || {
    printf '%s\n' 'fedora-proof: qemu service account identity is unsafe' >&2
    return 1
  }
  printf '%s\n' "$uid"
}

fedora_proof_resolve_qemu_uid() {
  local record uid id_uid
  record=$(/usr/bin/getent passwd qemu) || {
    printf '%s\n' 'fedora-proof: qemu service account is unavailable' >&2
    return 1
  }
  uid=$(fedora_proof_parse_qemu_passwd "$record") || return 1
  id_uid=$(/usr/bin/id -u qemu) || return 1
  [[ $id_uid == "$uid" ]] || {
    printf '%s\n' 'fedora-proof: qemu service uid resolution disagrees across host tools' >&2
    return 1
  }
  printf '%s\n' "$uid"
}

fedora_proof_require_acl_tools() {
  local setfacl_bin=$1 getfacl_bin=$2 missing=0 path
  for path in "$setfacl_bin" "$getfacl_bin"; do
    if [[ $path != /* || ! -x $path ]]; then
      printf 'fedora-proof: required ACL tool is missing or unsafe: %s\n' "$path" >&2
      missing=1
    fi
  done
  (( missing == 0 ))
}

fedora_proof_validate_root() {
  local root=$1 canonical acl
  [[ $root == /* && -d $root && ! -L $root ]] || {
    printf '%s\n' 'fedora-proof: proof root is missing, non-absolute, or a symlink' >&2
    return 1
  }
  canonical=$(/usr/bin/readlink -e "$root") || return 1
  [[ $canonical == "$root" ]] || {
    printf '%s\n' 'fedora-proof: proof root contains a symlink component' >&2
    return 1
  }
  acl=$(/usr/bin/getfacl -cpn -- "$root") || {
    printf '%s\n' 'fedora-proof: proof root ACL cannot be read' >&2
    return 1
  }
  [[ $acl != *$'\ndefault:'* && $acl != default:* ]] || {
    printf '%s\n' \
      'fedora-proof: proof root has a default ACL; refusing artifact creation' >&2
    return 1
  }
}

fedora_proof_validate_acl_text() {
  local actual=$1 qemu_uid=$2 kind=$3 expected
  [[ $qemu_uid =~ ^[0-9]+$ && $qemu_uid != 0 ]] || return 1
  case "$kind" in
    workdir)
      expected=$(printf 'user::rwx\nuser:%s:--x\ngroup::---\nmask::--x\nother::---' "$qemu_uid")
      ;;
    disk)
      expected=$(printf 'user::rw-\nuser:%s:rw-\ngroup::---\nmask::rw-\nother::---' "$qemu_uid")
      ;;
    media)
      expected=$(printf 'user::rw-\nuser:%s:r--\ngroup::---\nmask::r--\nother::---' "$qemu_uid")
      ;;
    *)
      printf '%s\n' 'fedora-proof: unknown ACL contract' >&2
      return 1
      ;;
  esac
  [[ $actual == "$expected" ]] || {
    printf '%s\n' 'fedora-proof: effective ACL mismatch' >&2
    return 1
  }
}

fedora_proof_validate_exact_acl() {
  local path=$1 qemu_uid=$2 kind=$3 actual
  actual=$(/usr/bin/getfacl -cpn -- "$path") || {
    printf 'fedora-proof: cannot read effective ACL for %s\n' "$path" >&2
    return 1
  }
  fedora_proof_validate_acl_text "$actual" "$qemu_uid" "$kind" || {
    printf 'fedora-proof: ACL boundary failed for %s\n' "$path" >&2
    return 1
  }
}

fedora_proof_validate_private_acl() {
  local path=$1 qemu_uid=$2 actual expected owner
  [[ -f $path && ! -L $path ]] || {
    printf 'fedora-proof: private artifact is missing or unsafe: %s\n' "$path" >&2
    return 1
  }
  owner=$(/usr/bin/stat -c '%u' "$path") || return 1
  [[ $owner == "$EUID" ]] || {
    printf 'fedora-proof: private artifact owner mismatch: %s\n' "$path" >&2
    return 1
  }
  actual=$(/usr/bin/getfacl -cpn -- "$path") || return 1
  expected=$'user::rw-\ngroup::---\nother::---'
  [[ $actual == "$expected" && $actual != *"user:$qemu_uid:"* ]] || {
    printf 'fedora-proof: qemu or another principal can access private artifact: %s\n' "$path" >&2
    return 1
  }
}

fedora_proof_validate_staged_media() {
  local root=$1 caller_uid=$2 path=$3 role=$4 expected_sha=${5,,} canonical owner links actual
  fedora_proof_validate_root "$root" || return 1
  [[ $caller_uid == "$EUID" && $caller_uid =~ ^[0-9]+$ \
      && $path == "$root/easysynq-fedora-proof-media-$caller_uid-$role.iso" \
      && -f $path && ! -L $path ]] || {
    printf 'fedora-proof: staged %s path is missing or unsafe\n' "$role" >&2
    return 1
  }
  canonical=$(/usr/bin/readlink -e "$path") || return 1
  owner=$(/usr/bin/stat -c '%u' "$path") || return 1
  links=$(/usr/bin/stat -c '%h' "$path") || return 1
  [[ $canonical == "$path" && $owner == "$caller_uid" && $links == 1 ]] || {
    printf 'fedora-proof: staged %s identity mismatch\n' "$role" >&2
    return 1
  }
  actual=$(/usr/bin/sha256sum "$path") || return 1
  actual=${actual%% *}
  [[ $actual == "$expected_sha" ]] || {
    printf 'fedora-proof: staged %s checksum mismatch\n' "$role" >&2
    return 1
  }
}

fedora_proof_create_private_stage_file() (
  local root=$1 base=$2 stage canonical owner links acl
  cleanup_private_stage() {
    local stage_owner
    trap - EXIT INT TERM
    if [[ -n $stage && $stage == "$root/$base.part."* && -f $stage && ! -L $stage ]]; then
      stage_owner=$(/usr/bin/stat -c '%u' "$stage" 2>/dev/null || true)
      [[ $stage_owner == "$EUID" ]] && /usr/bin/rm -- "$stage"
    fi
  }
  trap cleanup_private_stage EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  fedora_proof_validate_root "$root" || return 1
  [[ $base == "easysynq-fedora-proof-media-$EUID-installer.iso" \
      || $base == "easysynq-fedora-proof-media-$EUID-workstation.iso" ]] || return 1
  stage=$(/usr/bin/mktemp "$root/$base.part.XXXXXX") || return 1
  canonical=$(/usr/bin/readlink -e "$stage") || return 1
  owner=$(/usr/bin/stat -c '%u' "$stage") || return 1
  links=$(/usr/bin/stat -c '%h' "$stage") || return 1
  if [[ $stage != "$root/$base.part."* || $canonical != "$stage" \
      || ! -f $stage || -L $stage || $owner != "$EUID" || $links != 1 ]] \
      || ! /usr/bin/setfacl -b -- "$stage" \
      || ! /usr/bin/chmod 0600 "$stage"; then
    printf '%s\n' 'fedora-proof: private media staging inode is unsafe' >&2
    if [[ $stage == "$root/$base.part."* && -f $stage && ! -L $stage \
        && $(/usr/bin/stat -c '%u' "$stage" 2>/dev/null) == "$EUID" ]]; then
      /usr/bin/rm -- "$stage" || true
    fi
    return 1
  fi
  acl=$(/usr/bin/getfacl -cpn -- "$stage") || return 1
  if [[ $acl != $'user::rw-\ngroup::---\nother::---' ]]; then
    printf '%s\n' 'fedora-proof: private media staging ACL could not be established' >&2
    /usr/bin/rm -- "$stage" || true
    return 1
  fi
  printf '%s\n' "$stage"
  stage=
)

fedora_proof_stage_one_media() (
  local root=$1 caller_uid=$2 qemu_uid=$3 role=$4 source=$5 expected_sha=$6 target
  local stage= actual=
  cleanup_stage() {
    local stage_owner
    trap - EXIT INT TERM
    if [[ -n $stage && $stage == "$root/easysynq-fedora-proof-media-$caller_uid-$role.iso.part."* \
        && -f $stage && ! -L $stage ]]; then
      stage_owner=$(/usr/bin/stat -c '%u' "$stage" 2>/dev/null || true)
      [[ $stage_owner == "$EUID" ]] && /usr/bin/rm -- "$stage"
    fi
  }
  trap cleanup_stage EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  fedora_proof_validate_root "$root" || return 1
  target=$root/easysynq-fedora-proof-media-$caller_uid-$role.iso
  [[ $source == /* && -f $source && ! -L $source ]] || {
    printf 'fedora-proof: %s source media is missing or unsafe\n' "$role" >&2
    return 1
  }
  if [[ ! -e $target && ! -L $target ]]; then
    stage=$(fedora_proof_create_private_stage_file \
      "$root" "easysynq-fedora-proof-media-$caller_uid-$role.iso") || return 1
    /usr/bin/dd if="$source" of="$stage" bs=4M status=none conv=notrunc || {
      printf 'fedora-proof: could not copy exact staged %s media\n' "$role" >&2
      return 1
    }
    actual=$(/usr/bin/sha256sum "$stage") || return 1
    actual=${actual%% *}
    [[ $actual == "${expected_sha,,}" ]] || {
      printf 'fedora-proof: staged %s checksum mismatch\n' "$role" >&2
      return 1
    }
    /usr/bin/setfacl -m "u:$qemu_uid:r--,m::r--" -- "$stage" || return 1
    fedora_proof_validate_exact_acl "$stage" "$qemu_uid" media || return 1
    /usr/bin/mv -n -T -- "$stage" "$target" || return 1
    [[ ! -e $stage && ! -L $stage ]] || {
      printf 'fedora-proof: exact staged %s target appeared concurrently\n' "$role" >&2
      return 1
    }
    stage=
  fi
  fedora_proof_validate_staged_media "$root" "$caller_uid" "$target" "$role" "$expected_sha" \
    || return 1
  fedora_proof_validate_exact_acl "$target" "$qemu_uid" media || return 1
  printf 'fedora-proof: retained staged %s: %s\n' "$role" "$target"
)

fedora_proof_stage_media() {
  local root=$1 caller_uid=$2 qemu_uid=$3 installer=$4 installer_sha=$5
  local workstation=$6 workstation_sha=$7
  fedora_proof_stage_one_media \
    "$root" "$caller_uid" "$qemu_uid" installer "$installer" "$installer_sha" || return 1
  fedora_proof_stage_one_media \
    "$root" "$caller_uid" "$qemu_uid" workstation "$workstation" "$workstation_sha"
}

fedora_proof_read_client_identity() {
  local pid=$1 stat rest state parent starttime key values uid=
  local -a fields
  [[ $pid =~ ^[1-9][0-9]*$ && -r /proc/$pid/stat && -r /proc/$pid/status ]] || return 1
  IFS= read -r stat </proc/"$pid"/stat || return 1
  [[ $stat == "$pid ("*') '* ]] || return 1
  rest=${stat##*) }
  read -r -a fields <<<"$rest"
  (( ${#fields[@]} >= 20 )) || return 1
  state=${fields[0]}
  parent=${fields[1]}
  starttime=${fields[19]}
  while IFS=$'\t' read -r key values; do
    if [[ $key == Uid: ]]; then
      read -r uid _ <<<"$values"
      break
    fi
  done </proc/"$pid"/status
  [[ $state =~ ^[A-Z]$ && $parent =~ ^[0-9]+$ && $starttime =~ ^[0-9]+$ \
      && $uid == "$EUID" ]] || return 1
  printf '%s %s %s\n' "$state" "$parent" "$starttime"
}

fedora_proof_capture_client_identity() {
  local pid=$1 expected_parent=$2 identity state parent starttime
  [[ $expected_parent =~ ^[1-9][0-9]*$ ]] || return 1
  identity=$(fedora_proof_read_client_identity "$pid") || {
    printf '%s\n' 'fedora-proof: launched client identity could not be captured' >&2
    return 1
  }
  read -r state parent starttime <<<"$identity"
  [[ $state != Z && $parent == "$expected_parent" ]] || {
    printf '%s\n' 'fedora-proof: launched client is not the expected direct child' >&2
    return 1
  }
  printf '%s\n' "$starttime"
}

fedora_proof_stop_client_exact() {
  local pid=$1 expected_starttime=$2 expected_parent=$3 attempts=${4:-100}
  local identity state parent starttime
  [[ $expected_starttime =~ ^[0-9]+$ && $expected_parent =~ ^[1-9][0-9]*$ \
      && $attempts =~ ^[1-9][0-9]*$ && $attempts -le 600 ]] || return 1
  identity=$(fedora_proof_read_client_identity "$pid") || {
    wait "$pid" 2>/dev/null || true
    return 0
  }
  read -r state parent starttime <<<"$identity"
  [[ $parent == "$expected_parent" && $starttime == "$expected_starttime" ]] || {
    printf '%s\n' 'fedora-proof cleanup: launched client identity mismatch; refusing signal' >&2
    return 1
  }
  if [[ $state != Z ]] && ! kill -TERM "$pid" 2>/dev/null; then
    printf '%s\n' 'fedora-proof cleanup: exact launched client could not be signalled' >&2
    return 1
  fi
  for (( _ = 0; _ < attempts; _++ )); do
    identity=$(fedora_proof_read_client_identity "$pid") || {
      wait "$pid" 2>/dev/null || true
      return 0
    }
    read -r state parent starttime <<<"$identity"
    [[ $parent == "$expected_parent" && $starttime == "$expected_starttime" ]] || {
      printf '%s\n' 'fedora-proof cleanup: launched client identity changed; refusing signal' >&2
      return 1
    }
    if [[ $state == Z ]]; then
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    /usr/bin/sleep 0.1
  done
  printf '%s\n' \
    'fedora-proof cleanup: exact launched client did not stop; retaining lifecycle artifacts' >&2
  return 1
}

fedora_proof_reset_uuid_record() {
  local uuid_file=$1 qemu_uid=$2
  fedora_proof_validate_private_acl "$uuid_file" "$qemu_uid" || return 1
  : >"$uuid_file" || return 1
  fedora_proof_validate_private_acl "$uuid_file" "$qemu_uid" || return 1
  [[ ! -s $uuid_file ]] || return 1
}

fedora_proof_disk_owner_allowed() {
  local actual=$1 caller_uid=$2 qemu_uid=$3 phase=$4
  case "$phase" in
    active)
      [[ $actual == "$caller_uid" || $actual == "$qemu_uid" ]] || {
        printf '%s\n' 'fedora-proof cleanup: active disk owner is not caller or qemu' >&2
        return 1
      }
      ;;
    cleanup)
      [[ $actual == "$caller_uid" ]] || {
        printf '%s\n' \
          'fedora-proof cleanup: caller ownership was not restored; retaining exact disk' >&2
        return 1
      }
      ;;
    *) return 1 ;;
  esac
}

fedora_proof_validate_owned_workdir() {
  local workdir=$1 proof_root=$2 work_real owner marker
  [[ $workdir == /* && -d $workdir && ! -L $workdir ]] || {
    printf '%s\n' 'fedora-proof cleanup: work directory is missing, non-absolute, or a symlink' >&2
    return 1
  }
  fedora_proof_validate_root "$proof_root" || return 1
  work_real=$(/usr/bin/readlink -e "$workdir") || return 1
  [[ $work_real == "$workdir" && $work_real == "$proof_root"/easysynq-fedora-proof.* \
      && ${work_real#"$proof_root"/} != */* ]] || {
    printf '%s\n' 'fedora-proof cleanup: work directory is outside the exact mktemp namespace' >&2
    return 1
  }
  owner=$(/usr/bin/stat -c '%u' "$workdir") || return 1
  [[ $owner == "$EUID" ]] || {
    printf '%s\n' 'fedora-proof cleanup: work directory owner mismatch' >&2
    return 1
  }
  [[ -f $workdir/.easysynq-fedora-proof && ! -L $workdir/.easysynq-fedora-proof ]] || {
    printf '%s\n' 'fedora-proof cleanup: ownership marker is missing or unsafe' >&2
    return 1
  }
  IFS= read -r marker <"$workdir/.easysynq-fedora-proof" || return 1
  [[ $marker == "$FEDORA_PROOF_MARKER" ]] || {
    printf '%s\n' 'fedora-proof cleanup: ownership marker mismatch' >&2
    return 1
  }
}

fedora_proof_validate_cleanup_identity() {
  local workdir=$1 vm_name=$2 disk=$3 proof_root=$4 qemu_uid=$5 phase=$6
  local disk_real recorded_vm owner
  fedora_proof_validate_owned_workdir "$workdir" "$proof_root" || return 1
  [[ $vm_name =~ ^easysynq-fedora-proof-[0-9]{8}T[0-9]{6}Z-[0-9]+-[0-9a-f]{8}$ ]] || {
    printf '%s\n' 'fedora-proof cleanup: VM identity mismatch' >&2
    return 1
  }
  [[ -f $workdir/vm-name && ! -L $workdir/vm-name ]] || {
    printf '%s\n' 'fedora-proof cleanup: VM identity record is missing or unsafe' >&2
    return 1
  }
  IFS= read -r recorded_vm <"$workdir/vm-name" || return 1
  [[ $recorded_vm == "$vm_name" ]] || {
    printf '%s\n' 'fedora-proof cleanup: VM identity mismatch' >&2
    return 1
  }
  [[ $disk == /* && -f $disk && ! -L $disk ]] || {
    printf '%s\n' 'fedora-proof cleanup: disk target is missing, non-regular, or a symlink' >&2
    return 1
  }
  disk_real=$(/usr/bin/readlink -e "$disk") || return 1
  [[ $disk_real == "$workdir/root.qcow2" ]] || {
    printf '%s\n' 'fedora-proof cleanup: disk target is outside owned work directory' >&2
    return 1
  }
  owner=$(/usr/bin/stat -c '%u' "$disk") || return 1
  fedora_proof_disk_owner_allowed "$owner" "$EUID" "$qemu_uid" "$phase"
}

fedora_proof_remove_disk_exact() {
  local workdir=$1 vm_name=$2 disk=$3 proof_root=$4 qemu_uid=$5 disk_fd
  fedora_proof_validate_cleanup_identity \
    "$workdir" "$vm_name" "$disk" "$proof_root" "$qemu_uid" cleanup || return 1
  exec {disk_fd}<>"$disk" || return 1
  if ! /usr/bin/flock -n "$disk_fd"; then
    printf '%s\n' 'fedora-proof cleanup: disk is locked; refusing removal' >&2
    exec {disk_fd}>&-
    return 1
  fi
  if [[ -x /usr/bin/qemu-img ]] && ! /usr/bin/qemu-img check -q "$disk"; then
    printf '%s\n' 'fedora-proof cleanup: qemu-img could not validate the exact disk; refusing removal' >&2
    /usr/bin/flock -u "$disk_fd" || true
    exec {disk_fd}>&-
    return 1
  fi
  /usr/bin/rm -- "$disk"
  /usr/bin/flock -u "$disk_fd" || true
  exec {disk_fd}>&-
}

fedora_proof_remove_owned_file() {
  local workdir=$1 path=$2 allowed=$3 proof_root=$4 real
  fedora_proof_validate_owned_workdir "$workdir" "$proof_root" || return 1
  [[ ${path##*/} == "$allowed" && $path == "$workdir/$allowed" ]] || {
    printf 'fedora-proof cleanup: unexpected file target %s\n' "$path" >&2
    return 1
  }
  [[ -e $path || -L $path ]] || return 0
  [[ -f $path && ! -L $path ]] || {
    printf 'fedora-proof cleanup: refusing non-regular target %s\n' "$path" >&2
    return 1
  }
  real=$(/usr/bin/readlink -e "$path") || return 1
  [[ $real == "$path" && $(/usr/bin/stat -c '%u' "$path") == "$EUID" ]] || {
    printf 'fedora-proof cleanup: target identity mismatch %s\n' "$path" >&2
    return 1
  }
  /usr/bin/rm -- "$path"
}

fedora_proof_destroy_domain_exact() {
  local workdir=$1 vm_name=$2 expected_uuid=$3 disk=$4 proof_root=$5 qemu_uid=$6 actual_uuid
  local type device target source found_disk=0
  fedora_proof_validate_cleanup_identity \
    "$workdir" "$vm_name" "$disk" "$proof_root" "$qemu_uid" active || return 1
  if ! /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
    return 0
  fi
  actual_uuid=$(/usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" domuuid "$vm_name") || return 1
  [[ -n $expected_uuid && $actual_uuid == "$expected_uuid" ]] || {
    printf '%s\n' 'fedora-proof cleanup: active domain UUID mismatch; refusing destroy' >&2
    return 1
  }
  while read -r type device target source; do
    [[ $type == Type || $type == ---* ]] && continue
    if [[ $type == file && $device == disk && $source == "$disk" ]]; then
      found_disk=1
    fi
  done < <(/usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" domblklist "$vm_name" --details)
  (( found_disk )) || {
    printf '%s\n' 'fedora-proof cleanup: active domain disk mismatch; refusing destroy' >&2
    return 1
  }
  /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" destroy "$vm_name" >/dev/null
  for _ in {1..20}; do
    if ! /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  printf '%s\n' 'fedora-proof cleanup: exact domain did not stop; refusing disk removal' >&2
  return 1
}

fedora_proof_grant_lifecycle_acls() {
  local proof_root=$1 workdir=$2 disk=$3 qemu_uid=$4 disk_real owner
  fedora_proof_validate_owned_workdir "$workdir" "$proof_root" || return 1
  [[ -f $disk && ! -L $disk ]] || return 1
  disk_real=$(/usr/bin/readlink -e "$disk") || return 1
  owner=$(/usr/bin/stat -c '%u' "$disk") || return 1
  [[ $disk_real == "$workdir/root.qcow2" && $owner == "$EUID" ]] || return 1

  /usr/bin/setfacl -b -k -- "$workdir" || return 1
  /usr/bin/chmod 0700 "$workdir" || return 1
  /usr/bin/setfacl -m "u:$qemu_uid:--x,m::--x" -- "$workdir" || return 1
  /usr/bin/setfacl -b -- "$disk" || return 1
  /usr/bin/chmod 0600 "$disk" || return 1
  /usr/bin/setfacl -m "u:$qemu_uid:rw-,m::rw-" -- "$disk" || return 1
  fedora_proof_validate_exact_acl "$workdir" "$qemu_uid" workdir || return 1
  fedora_proof_validate_exact_acl "$disk" "$qemu_uid" disk
}

fedora_proof_revoke_lifecycle_acls() {
  local proof_root=$1 workdir=$2 disk=$3 qemu_uid=$4 disk_real owner
  fedora_proof_validate_owned_workdir "$workdir" "$proof_root" || return 1
  [[ -f $disk && ! -L $disk ]] || return 1
  disk_real=$(/usr/bin/readlink -e "$disk") || return 1
  owner=$(/usr/bin/stat -c '%u' "$disk") || return 1
  [[ $disk_real == "$workdir/root.qcow2" ]] || return 1
  fedora_proof_disk_owner_allowed "$owner" "$EUID" "$qemu_uid" cleanup || return 1

  /usr/bin/setfacl -b -- "$disk" || return 1
  /usr/bin/chmod 0600 "$disk" || return 1
  /usr/bin/setfacl -b -k -- "$workdir" || return 1
  /usr/bin/chmod 0700 "$workdir" || return 1
  fedora_proof_validate_private_acl "$disk" "$qemu_uid" || return 1
  [[ $(/usr/bin/getfacl -cpn -- "$workdir") == $'user::rwx\ngroup::---\nother::---' ]] || {
    printf '%s\n' 'fedora-proof cleanup: workdir ACL revocation failed; retaining exact target' >&2
    return 1
  }
}

validate_sha256() {
  local label=$1 value=${2,,}
  [[ $value =~ ^[0-9a-f]{64}$ ]] || {
    printf 'fedora-proof: %s SHA-256 must be exactly 64 hexadecimal characters\n' "$label" >&2
    return 2
  }
}

validate_media() {
  local label=$1 path=$2 expected=${3,,} pattern=$4 canonical actual
  [[ $path == /* ]] || {
    printf 'fedora-proof: %s ISO must use an absolute path\n' "$label" >&2
    return 2
  }
  [[ ! -L $path ]] || {
    printf 'fedora-proof: %s ISO must not be a symlink\n' "$label" >&2
    return 2
  }
  [[ -f $path ]] || {
    printf 'fedora-proof: %s ISO must be a regular file\n' "$label" >&2
    return 2
  }
  canonical=$(/usr/bin/readlink -e "$path") || return 2
  [[ $canonical == "$path" ]] || {
    printf 'fedora-proof: %s ISO path must be canonical and contain no symlink components\n' "$label" >&2
    return 2
  }
  [[ ${path##*/} == $pattern ]] || {
    printf 'fedora-proof: %s ISO filename does not identify the required Fedora 44 media\n' "$label" >&2
    return 2
  }
  actual=$(/usr/bin/sha256sum "$path") || return 1
  actual=${actual%% *}
  [[ $actual == "$expected" ]] || {
    printf 'fedora-proof: %s ISO checksum mismatch\n' "$label" >&2
    return 1
  }
  printf 'fedora-proof: %s media: verified sha256:%s\n' "$label" "$actual"
}

fedora_proof_select_osinfo_from_list() {
  local listing=$1 candidate fedora43_available=0
  while IFS= read -r candidate; do
    case "$candidate" in
      fedora44)
        printf '%s\n' fedora44
        return 0
        ;;
      fedora43)
        fedora43_available=1
        ;;
    esac
  done <<<"$listing"
  if (( fedora43_available )); then
    printf '%s\n' \
      'fedora-proof: host osinfo-db lacks fedora44; using fedora43 device metadata only.' \
      'fedora-proof: the installed guest must still pass the Fedora Workstation 44 gates.' >&2
    printf '%s\n' fedora43
    return 0
  fi
  printf '%s\n' \
    'fedora-proof: host osinfo-db must provide fedora44 or fedora43 metadata; update osinfo-db.' >&2
  return 1
}

fedora_proof_select_osinfo() {
  local listing
  listing=$(/usr/bin/virt-install --osinfo list) || {
    printf '%s\n' 'fedora-proof: could not query virt-install OS metadata' >&2
    return 1
  }
  fedora_proof_select_osinfo_from_list "$listing"
}

fedora_proof_check_libvirt_ready() {
  local virsh_bin=${1:-/usr/bin/virsh}
  [[ $virsh_bin == /* && -x $virsh_bin ]] || {
    printf '%s\n' 'fedora-proof: invalid virsh readiness probe' >&2
    return 1
  }
  "$virsh_bin" --connect "$FEDORA_PROOF_CONNECT" uri >/dev/null || {
    printf '%s\n' 'fedora-proof: qemu:///system is unavailable; see docs/runbooks/fedora-proof.md' >&2
    return 1
  }
  "$virsh_bin" --connect "$FEDORA_PROOF_CONNECT" pool-list --all >/dev/null || {
    printf '%s\n' \
      'fedora-proof: libvirt storage capability is unavailable.' \
      'fedora-proof: run: sudo systemctl enable --now virtstoraged.socket' \
      'fedora-proof: see docs/runbooks/fedora-proof.md' >&2
    return 1
  }
}

require_host_tools() {
  local path missing=0
  for path in \
    /usr/bin/virt-install \
    /usr/bin/virsh \
    /usr/bin/qemu-img \
    /usr/bin/ssh \
    /usr/bin/ssh-keygen \
    /usr/bin/ssh-keyscan \
    /usr/bin/git \
    /usr/bin/tar \
    /usr/bin/flock \
    /usr/bin/openssl \
    /usr/bin/setfacl \
    /usr/bin/getfacl \
    /usr/bin/getent \
    /usr/bin/id \
    /usr/bin/dd \
    /usr/bin/mv; do
    if [[ ! -x $path ]]; then
      printf 'fedora-proof: required proof-host tool is missing: %s\n' "$path" >&2
      missing=1
    fi
  done
  (( missing == 0 ))
}

fedora_proof_main() {
  local installer_iso= installer_sha= workstation_iso= workstation_sha= validate_only=0
  local repo_root script_path script_dir kickstart_template osinfo qemu_uid
  local vm_name= evidence_commit= staged_installer_iso staged_workstation_iso

  while (( $# )); do
    case "$1" in
      --installer-iso)
        (( $# >= 2 )) || { usage; return 2; }
        [[ -z $installer_iso ]] || { usage; return 2; }
        installer_iso=$2
        shift 2
        ;;
      --installer-iso-sha256)
        (( $# >= 2 )) || { usage; return 2; }
        [[ -z $installer_sha ]] || { usage; return 2; }
        installer_sha=$2
        shift 2
        ;;
      --workstation-iso)
        (( $# >= 2 )) || { usage; return 2; }
        [[ -z $workstation_iso ]] || { usage; return 2; }
        workstation_iso=$2
        shift 2
        ;;
      --workstation-iso-sha256)
        (( $# >= 2 )) || { usage; return 2; }
        [[ -z $workstation_sha ]] || { usage; return 2; }
        workstation_sha=$2
        shift 2
        ;;
      --validate-only)
        validate_only=1
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        usage
        return 2
        ;;
    esac
  done
  [[ -n $installer_iso && -n $installer_sha && -n $workstation_iso && -n $workstation_sha ]] || {
    usage
    return 2
  }
  validate_sha256 installer "$installer_sha" || return
  validate_sha256 Workstation "$workstation_sha" || return
  validate_media installer "$installer_iso" "$installer_sha" \
    'Fedora-Everything-netinst-x86_64-44-*.iso' || return
  validate_media Workstation "$workstation_iso" "$workstation_sha" \
    'Fedora-Workstation-Live-44-*.x86_64.iso' || return
  [[ $installer_iso != "$workstation_iso" ]] || {
    printf '%s\n' 'fedora-proof: installer and Workstation media must be distinct files' >&2
    return 2
  }
  (( validate_only )) && return 0

  if [[ ${TMPDIR+x} == x && ${TMPDIR:-} != "$FEDORA_PROOF_ROOT" ]]; then
    printf '%s\n' \
      'fedora-proof: real proof rejects TMPDIR; artifacts use the fixed /var/tmp namespace' >&2
    return 2
  fi

  require_host_tools || return 1
  fedora_proof_require_acl_tools /usr/bin/setfacl /usr/bin/getfacl || return 1
  qemu_uid=$(fedora_proof_resolve_qemu_uid) || return 1
  script_path=${BASH_SOURCE[0]}
  script_dir=${script_path%/*}
  [[ $script_dir == "$script_path" ]] && script_dir=.
  repo_root=$(cd "$script_dir/.." && pwd -P) || return 2
  kickstart_template=$repo_root/infra/dev/fedora-proof/ks.cfg
  [[ -f $kickstart_template && ! -L $kickstart_template ]] || {
    printf '%s\n' 'fedora-proof: Kickstart template is missing or unsafe' >&2
    return 2
  }
  /usr/bin/git -C "$repo_root" diff --quiet --exit-code || {
    printf '%s\n' 'fedora-proof: tracked worktree changes must be committed before a real proof run' >&2
    return 2
  }
  /usr/bin/git -C "$repo_root" diff --cached --quiet --exit-code || {
    printf '%s\n' 'fedora-proof: staged changes must be committed before a real proof run' >&2
    return 2
  }
  "$repo_root/scripts/check-no-site-data.sh" >/dev/null
  evidence_commit=$(/usr/bin/git -C "$repo_root" rev-parse --verify 'HEAD^{commit}') || return 2
  [[ $evidence_commit =~ ^[0-9a-f]{40,64}$ ]] || {
    printf '%s\n' 'fedora-proof: could not record an exact evidence commit' >&2
    return 2
  }
  fedora_proof_check_libvirt_ready /usr/bin/virsh || return 1
  osinfo=$(fedora_proof_select_osinfo) || return 1

  vm_name=$(fedora_proof_new_vm_name) || return 1
  if /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
    printf '%s\n' 'fedora-proof: generated VM name already exists; refusing reuse' >&2
    return 1
  fi
  fedora_proof_stage_media \
    "$FEDORA_PROOF_ROOT" "$EUID" "$qemu_uid" \
    "$installer_iso" "$installer_sha" "$workstation_iso" "$workstation_sha" || return 1
  staged_installer_iso=$FEDORA_PROOF_ROOT/easysynq-fedora-proof-media-$EUID-installer.iso
  staged_workstation_iso=$FEDORA_PROOF_ROOT/easysynq-fedora-proof-media-$EUID-workstation.iso
  fedora_proof_run_lifecycle \
    "$vm_name" "$staged_installer_iso" "$installer_sha" \
    "$staged_workstation_iso" "$workstation_sha" \
    "$repo_root" "$kickstart_template" "$evidence_commit" "$osinfo" \
    "$qemu_uid" "$FEDORA_PROOF_ROOT"
}

fedora_proof_run_lifecycle() (
  local vm_name=$1 staged_installer_iso=$2 installer_sha=$3
  local staged_workstation_iso=$4 workstation_sha=$5
  local repo_root=$6 kickstart_template=$7 evidence_commit=$8 osinfo=$9
  local qemu_uid=${10} proof_root=${11}
  local workdir= disk= rendered_ks= private_key= public_key= known_hosts= repo_files=
  local uuid_file= marker_file= vm_name_file= log_dir= log_file= vm_uuid= virt_pid= guest_ip=
  local virt_starttime= virt_parent_pid= cleanup_failed=0

  log_dir=$repo_root/.fedora-proof-logs
  if [[ -e $log_dir || -L $log_dir ]]; then
    [[ -d $log_dir && ! -L $log_dir && $(/usr/bin/stat -c '%u' "$log_dir") == "$EUID" ]] || {
      printf '%s\n' 'fedora-proof: log directory exists but is not an owned regular directory' >&2
      return 1
    }
  else
    /usr/bin/mkdir -- "$log_dir"
  fi
  log_file=$log_dir/$vm_name.log
  [[ ! -e $log_file && ! -L $log_file ]] || {
    printf '%s\n' 'fedora-proof: unique log target already exists' >&2
    return 1
  }
  : >"$log_file"
  /usr/bin/chmod 0600 "$log_file"

  fedora_proof_validate_root "$proof_root" || return 1
  workdir=$(/usr/bin/mktemp -d "$proof_root/easysynq-fedora-proof.XXXXXX") || return 1
  if [[ $workdir != "$proof_root"/easysynq-fedora-proof.* \
      || ${workdir#"$proof_root"/} == */* \
      || -L $workdir \
      || $(/usr/bin/readlink -e "$workdir") != "$workdir" \
      || $(/usr/bin/stat -c '%u' "$workdir") != "$EUID" ]] \
      || ! /usr/bin/setfacl -b -k -- "$workdir" \
      || ! /usr/bin/chmod 0700 "$workdir" \
      || [[ $(/usr/bin/getfacl -cpn -- "$workdir") != $'user::rwx\ngroup::---\nother::---' ]]; then
    printf '%s\n' 'fedora-proof: could not establish the private workdir ACL boundary' >&2
    if [[ $workdir == "$proof_root"/easysynq-fedora-proof.* \
        && ${workdir#"$proof_root"/} != */* \
        && -d $workdir \
        && ! -L $workdir \
        && $(/usr/bin/stat -c '%u' "$workdir" 2>/dev/null) == "$EUID" ]]; then
      /usr/bin/rmdir -- "$workdir" || {
        printf '%s\n' 'fedora-proof: empty unsafe workdir could not be removed; retaining it' >&2
      }
    fi
    return 1
  fi
  marker_file=$workdir/.easysynq-fedora-proof
  vm_name_file=$workdir/vm-name
  uuid_file=$workdir/vm-uuid
  disk=$workdir/root.qcow2
  rendered_ks=$workdir/ks.cfg
  private_key=$workdir/id_ed25519
  public_key=$workdir/id_ed25519.pub
  known_hosts=$workdir/known_hosts
  repo_files=$workdir/repo-files
  printf '%s\n' "$FEDORA_PROOF_MARKER" >"$marker_file"
  printf '%s\n' "$vm_name" >"$vm_name_file"
  /usr/bin/chmod 0600 "$marker_file" "$vm_name_file"
  fedora_proof_validate_owned_workdir "$workdir" "$proof_root" || return 1

  cleanup_all() {
    local expected_uuid= file base
    trap - EXIT INT TERM
    if [[ -n ${virt_pid:-} ]]; then
      fedora_proof_stop_client_exact \
        "$virt_pid" "$virt_starttime" "$virt_parent_pid" 100 || cleanup_failed=1
      virt_pid=
      virt_starttime=
      virt_parent_pid=
    fi
    if [[ -n $workdir && -d $workdir ]]; then
      if [[ -f $uuid_file && ! -L $uuid_file ]]; then
        IFS= read -r expected_uuid <"$uuid_file" || expected_uuid=
      else
        expected_uuid=
      fi
      if [[ -n $vm_name && -n $disk && -f $disk ]] \
          && /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
        fedora_proof_destroy_domain_exact \
          "$workdir" "$vm_name" "$expected_uuid" "$disk" "$proof_root" "$qemu_uid" \
          || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )) && [[ -f $disk ]]; then
        fedora_proof_revoke_lifecycle_acls \
          "$proof_root" "$workdir" "$disk" "$qemu_uid" || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )) && [[ -f $disk ]]; then
        fedora_proof_remove_disk_exact \
          "$workdir" "$vm_name" "$disk" "$proof_root" "$qemu_uid" || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )); then
        for file in "$rendered_ks" "$private_key" "$public_key" "$known_hosts" "$repo_files" \
          "$uuid_file"; do
          base=${file##*/}
          fedora_proof_remove_owned_file \
            "$workdir" "$file" "$base" "$proof_root" || cleanup_failed=1
          (( cleanup_failed == 0 )) || break
        done
      fi
      if (( cleanup_failed == 0 )); then
        fedora_proof_remove_owned_file \
          "$workdir" "$vm_name_file" vm-name "$proof_root" || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )); then
        fedora_proof_remove_owned_file \
          "$workdir" "$marker_file" .easysynq-fedora-proof "$proof_root" \
          || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )); then
        /usr/bin/rmdir -- "$workdir" || {
          printf '%s\n' 'fedora-proof cleanup: work directory contains an unexpected target' >&2
          cleanup_failed=1
        }
      fi
    fi
    (( cleanup_failed == 0 ))
  }
  finish() {
    local status=$?
    trap - EXIT INT TERM
    cleanup_all || status=1
    exit "$status"
  }
  interrupted() {
    local status=$1
    trap - EXIT INT TERM
    cleanup_all || status=1
    exit "$status"
  }
  trap finish EXIT
  trap 'interrupted 130' INT
  trap 'interrupted 143' TERM

  printf 'Fedora proof VM: %s\nFedora proof disk: %s\nFedora proof log: %s\n' \
    "$vm_name" "$disk" "$log_file" | /usr/bin/tee -a "$log_file"
  printf 'Retained installer: %s\nRetained Workstation media: %s\n' \
    "$staged_installer_iso" "$staged_workstation_iso" | /usr/bin/tee -a "$log_file"
  printf 'Installer ISO SHA-256: %s\nWorkstation ISO SHA-256: %s\nEvidence commit: %s\n' \
    "${installer_sha,,}" "${workstation_sha,,}" \
    "$evidence_commit" >>"$log_file"

  /usr/bin/ssh-keygen -q -t ed25519 -N '' -C easysynq-fedora-proof -f "$private_key"
  local public_key_line escaped_key guest_password guest_password_hash escaped_password_hash
  IFS= read -r public_key_line <"$public_key"
  [[ $public_key_line =~ ^ssh-ed25519[[:space:]][A-Za-z0-9+/=]+[[:space:]]easysynq-fedora-proof$ ]] || {
    printf '%s\n' 'fedora-proof: generated SSH public key is malformed' >&2
    return 1
  }
  escaped_key=${public_key_line//\\/\\\\}
  escaped_key=${escaped_key//&/\\&}
  escaped_key=${escaped_key//|/\\|}
  guest_password=$(/usr/bin/openssl rand -hex 32)
  guest_password_hash=$(printf '%s\n' "$guest_password" | /usr/bin/openssl passwd -6 -stdin)
  unset guest_password
  [[ $guest_password_hash == \$6\$* ]] || {
    printf '%s\n' 'fedora-proof: generated guest password hash is malformed' >&2
    return 1
  }
  escaped_password_hash=${guest_password_hash//\\/\\\\}
  escaped_password_hash=${escaped_password_hash//&/\\&}
  escaped_password_hash=${escaped_password_hash//|/\\|}
  unset guest_password_hash
  /usr/bin/sed \
    -e "s|@@EASYSYNQ_SSH_PUBLIC_KEY@@|$escaped_key|" \
    -e "s|@@EASYSYNQ_PASSWORD_HASH@@|$escaped_password_hash|" \
    "$kickstart_template" >"$rendered_ks"
  unset escaped_key escaped_password_hash
  [[ $(/usr/bin/grep -Ec '@@EASYSYNQ_(SSH_PUBLIC_KEY|PASSWORD_HASH)@@' "$rendered_ks") == 0 ]] \
    || return 1
  /usr/bin/chmod 0600 "$rendered_ks" "$private_key" "$public_key"
  : >"$known_hosts"
  : >"$repo_files"
  : >"$uuid_file"
  /usr/bin/chmod 0600 "$known_hosts" "$repo_files" "$uuid_file"

  /usr/bin/qemu-img create -q -f qcow2 "$disk" 80G
  /usr/bin/chmod 0600 "$disk"
  fedora_proof_validate_cleanup_identity \
    "$workdir" "$vm_name" "$disk" "$proof_root" "$qemu_uid" cleanup || return 1
  fedora_proof_grant_lifecycle_acls "$proof_root" "$workdir" "$disk" "$qemu_uid" || return 1
  fedora_proof_validate_staged_media \
    "$proof_root" "$EUID" "$staged_installer_iso" installer "$installer_sha" || return 1
  fedora_proof_validate_staged_media \
    "$proof_root" "$EUID" "$staged_workstation_iso" workstation "$workstation_sha" || return 1
  fedora_proof_validate_exact_acl "$staged_installer_iso" "$qemu_uid" media || return 1
  fedora_proof_validate_exact_acl "$staged_workstation_iso" "$qemu_uid" media || return 1
  local private_artifact
  for private_artifact in \
    "$rendered_ks" "$private_key" "$public_key" "$known_hosts" "$repo_files" \
    "$uuid_file" "$vm_name_file" "$marker_file"; do
    fedora_proof_validate_private_acl "$private_artifact" "$qemu_uid" || return 1
  done

  start_domain_and_record_uuid() {
    local phase=$1
    shift
    fedora_proof_reset_uuid_record "$uuid_file" "$qemu_uid" || return 1
    vm_uuid=
    "$@" >>"$log_file" 2>&1 &
    virt_pid=$!
    virt_parent_pid=$BASHPID
    virt_starttime=$(fedora_proof_capture_client_identity "$virt_pid" "$virt_parent_pid") || {
      if ! kill -0 "$virt_pid" 2>/dev/null; then
        wait "$virt_pid" || true
        virt_pid=
        virt_parent_pid=
      fi
      printf 'fedora-proof: %s client identity capture failed; see %s\n' \
        "$phase" "$log_file" >&2
      return 1
    }
    for _ in {1..120}; do
      vm_uuid=$(/usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" domuuid "$vm_name" 2>/dev/null || true)
      [[ -n $vm_uuid ]] && break
      if ! kill -0 "$virt_pid" 2>/dev/null; then
        wait "$virt_pid" || true
        virt_pid=
        virt_starttime=
        virt_parent_pid=
        printf 'fedora-proof: %s domain exited before identity capture; see %s\n' \
          "$phase" "$log_file" >&2
        return 1
      fi
      sleep 0.5
    done
    [[ -n $vm_uuid ]] || {
      printf 'fedora-proof: timed out capturing %s domain identity\n' "$phase" >&2
      return 1
    }
    printf '%s\n' "$vm_uuid" >"$uuid_file"
  }

  start_domain_and_record_uuid install \
    /usr/bin/virt-install \
      --connect "$FEDORA_PROOF_CONNECT" \
      --transient \
      --name "$vm_name" \
      --memory 12288 \
      --vcpus 4 \
      --cpu host-passthrough \
      --osinfo "$osinfo" \
      --network network=default,model=virtio \
      --disk "path=$disk,format=qcow2,bus=virtio" \
      --disk "path=$staged_workstation_iso,device=cdrom,bus=sata,readonly=on" \
      --location "$staged_installer_iso" \
      --initrd-inject "$rendered_ks" \
      --extra-args "inst.ks=file:/ks.cfg inst.text console=ttyS0,115200n8" \
      --graphics none \
      --noautoconsole \
      --wait=-1
  if ! wait "$virt_pid"; then
    virt_pid=
    virt_starttime=
    virt_parent_pid=
    printf 'fedora-proof: installation failed; see %s\n' "$log_file" >&2
    return 1
  fi
  virt_pid=
  virt_starttime=
  virt_parent_pid=
  if /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
    printf '%s\n' 'fedora-proof: transient installer domain remained active after Kickstart shutdown' >&2
    return 1
  fi
  fedora_proof_validate_cleanup_identity \
    "$workdir" "$vm_name" "$disk" "$proof_root" "$qemu_uid" cleanup || return 1
  fedora_proof_validate_exact_acl "$disk" "$qemu_uid" disk || return 1

  start_domain_and_record_uuid runtime \
    /usr/bin/virt-install \
      --connect "$FEDORA_PROOF_CONNECT" \
      --transient \
      --name "$vm_name" \
      --memory 12288 \
      --vcpus 4 \
      --cpu host-passthrough \
      --osinfo "$osinfo" \
      --network network=default,model=virtio \
      --disk "path=$disk,format=qcow2,bus=virtio" \
      --import \
      --graphics none \
      --noautoconsole \
      --wait=-1

  for _ in {1..600}; do
    guest_ip=$(
      /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" domifaddr "$vm_name" --source lease 2>/dev/null \
        | /usr/bin/awk '$3 == "ipv4" {sub(/\/.*/, "", $4); print $4; exit}'
    )
    [[ -n $guest_ip ]] && break
    kill -0 "$virt_pid" 2>/dev/null || break
    sleep 1
  done
  [[ -n $guest_ip ]] || {
    printf 'fedora-proof: guest did not obtain a libvirt DHCP address; see %s\n' "$log_file" >&2
    return 1
  }

  for _ in {1..300}; do
    if /usr/bin/ssh-keyscan -T 3 -H "$guest_ip" >"$known_hosts" 2>/dev/null \
        && [[ -s $known_hosts ]]; then
      break
    fi
    kill -0 "$virt_pid" 2>/dev/null || break
    sleep 1
  done
  [[ -s $known_hosts ]] || {
    printf 'fedora-proof: SSH did not become ready; see %s\n' "$log_file" >&2
    return 1
  }
  local -a ssh_args=(
    -i "$private_key"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=$known_hosts"
    -o ConnectTimeout=10
    "easysynq@$guest_ip"
  )
  /usr/bin/ssh "${ssh_args[@]}" true

  /usr/bin/git -C "$repo_root" ls-tree -r --name-only -z "$evidence_commit" >"$repo_files"
  fedora_proof_validate_private_acl "$repo_files" "$qemu_uid" || return 1
  [[ -s $repo_files ]] || {
    printf '%s\n' 'fedora-proof: tracked repository file manifest is empty' >&2
    return 1
  }
  if /usr/bin/grep -zEq '(^|/)(\.env|\.import-source|audit-results|infra/data|qms-mirror|backups)(/|$)' \
      "$repo_files"; then
    printf '%s\n' 'fedora-proof: tracked manifest crossed the env/site-data boundary' >&2
    return 1
  fi
  /usr/bin/ssh "${ssh_args[@]}" \
    'install -d -m 0700 /var/tmp/easysynq-proof/source && tar -xf - -C /var/tmp/easysynq-proof/source' \
    < <(
      /usr/bin/git -C "$repo_root" archive --format=tar "$evidence_commit"
    )
  /usr/bin/ssh "${ssh_args[@]}" \
    'bash /var/tmp/easysynq-proof/source/scripts/inside-fedora-proof.sh --source-dir /var/tmp/easysynq-proof/source' \
    | /usr/bin/tee -a "$log_file"
  /usr/bin/grep -Fx 'FEDORA_PROOF_PASS' "$log_file" >/dev/null || {
    printf 'fedora-proof: guest did not emit the terminal pass marker; see %s\n' "$log_file" >&2
    return 1
  }
  printf 'fedora-proof: PASS; retained evidence log: %s\n' "$log_file"
)

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  fedora_proof_main "$@"
fi

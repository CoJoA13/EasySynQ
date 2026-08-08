#!/usr/bin/env bash
# Build, exercise, and remove one disposable Fedora 44 Workstation libvirt guest.

FEDORA_PROOF_MARKER=easysynq-fedora-proof-v1
FEDORA_PROOF_CONNECT=qemu:///system

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

fedora_proof_validate_owned_workdir() {
  local workdir=$1 tmp_root work_real owner marker
  [[ $workdir == /* && -d $workdir && ! -L $workdir ]] || {
    printf '%s\n' 'fedora-proof cleanup: work directory is missing, non-absolute, or a symlink' >&2
    return 1
  }
  tmp_root=$(/usr/bin/readlink -e "${TMPDIR:-/tmp}") || return 1
  work_real=$(/usr/bin/readlink -e "$workdir") || return 1
  [[ $work_real == "$workdir" && $work_real == "$tmp_root"/easysynq-fedora-proof.* \
      && ${work_real#"$tmp_root"/} != */* ]] || {
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
  local workdir=$1 vm_name=$2 disk=$3 disk_real recorded_vm
  fedora_proof_validate_owned_workdir "$workdir" || return 1
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
  [[ $(/usr/bin/stat -c '%u' "$disk") == "$EUID" ]] || {
    printf '%s\n' 'fedora-proof cleanup: disk owner mismatch' >&2
    return 1
  }
}

fedora_proof_remove_disk_exact() {
  local workdir=$1 vm_name=$2 disk=$3 disk_fd
  fedora_proof_validate_cleanup_identity "$workdir" "$vm_name" "$disk" || return 1
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
  local workdir=$1 path=$2 allowed=$3 real
  fedora_proof_validate_owned_workdir "$workdir" || return 1
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
  local workdir=$1 vm_name=$2 expected_uuid=$3 disk=$4 actual_uuid
  local type device target source found_disk=0
  fedora_proof_validate_cleanup_identity "$workdir" "$vm_name" "$disk" || return 1
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
    /usr/bin/openssl; do
    if [[ ! -x $path ]]; then
      printf 'fedora-proof: required proof-host tool is missing: %s\n' "$path" >&2
      missing=1
    fi
  done
  (( missing == 0 ))
}

fedora_proof_main() {
  local installer_iso= installer_sha= workstation_iso= workstation_sha= validate_only=0
  local repo_root script_path script_dir kickstart_template
  local workdir= vm_name= disk= rendered_ks= private_key= public_key= known_hosts= repo_files=
  local uuid_file= marker_file= vm_name_file= log_dir= log_file= vm_uuid= virt_pid= guest_ip=
  local evidence_commit= cleanup_failed=0

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

  require_host_tools || return 1
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
  /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" uri >/dev/null || {
    printf '%s\n' 'fedora-proof: qemu:///system is unavailable; see docs/runbooks/fedora-proof.md' >&2
    return 1
  }

  vm_name=$(fedora_proof_new_vm_name) || return 1
  if /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
    printf '%s\n' 'fedora-proof: generated VM name already exists; refusing reuse' >&2
    return 1
  fi
  workdir=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/easysynq-fedora-proof.XXXXXX") || return 1
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
  fedora_proof_validate_owned_workdir "$workdir" || return 1

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
  chmod 0600 "$log_file"

  cleanup_all() {
    local expected_uuid= file base
    trap - EXIT INT TERM
    if [[ -n $workdir && -d $workdir ]]; then
      if [[ -f $uuid_file && ! -L $uuid_file ]]; then
        IFS= read -r expected_uuid <"$uuid_file" || expected_uuid=
      else
        expected_uuid=
      fi
      if [[ -n $vm_name && -n $disk && -f $disk ]] \
          && /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
        fedora_proof_destroy_domain_exact "$workdir" "$vm_name" "$expected_uuid" "$disk" \
          || cleanup_failed=1
      fi
      if [[ -n ${virt_pid:-} ]]; then
        wait "$virt_pid" 2>/dev/null || true
        virt_pid=
      fi
      if (( cleanup_failed == 0 )) && [[ -f $disk ]]; then
        fedora_proof_remove_disk_exact "$workdir" "$vm_name" "$disk" || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )); then
        for file in "$rendered_ks" "$private_key" "$public_key" "$known_hosts" "$repo_files" \
          "$uuid_file"; do
          base=${file##*/}
          fedora_proof_remove_owned_file "$workdir" "$file" "$base" || cleanup_failed=1
          (( cleanup_failed == 0 )) || break
        done
      fi
      if (( cleanup_failed == 0 )); then
        fedora_proof_remove_owned_file "$workdir" "$vm_name_file" vm-name || cleanup_failed=1
      fi
      if (( cleanup_failed == 0 )); then
        fedora_proof_remove_owned_file "$workdir" "$marker_file" .easysynq-fedora-proof \
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
  chmod 0600 "$rendered_ks" "$private_key" "$public_key"

  /usr/bin/qemu-img create -q -f qcow2 "$disk" 80G
  fedora_proof_validate_cleanup_identity "$workdir" "$vm_name" "$disk" || return 1

  start_domain_and_record_uuid() {
    local phase=$1
    shift
    "$@" >>"$log_file" 2>&1 &
    virt_pid=$!
    vm_uuid=
    for _ in {1..120}; do
      vm_uuid=$(/usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" domuuid "$vm_name" 2>/dev/null || true)
      [[ -n $vm_uuid ]] && break
      if ! kill -0 "$virt_pid" 2>/dev/null; then
        wait "$virt_pid" || true
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
      --osinfo fedora44 \
      --network network=default,model=virtio \
      --disk "path=$disk,format=qcow2,bus=virtio" \
      --disk "path=$workstation_iso,device=cdrom,bus=sata,readonly=on" \
      --location "$installer_iso" \
      --initrd-inject "$rendered_ks" \
      --extra-args "inst.ks=file:/ks.cfg inst.text console=ttyS0,115200n8" \
      --graphics none \
      --noautoconsole \
      --wait=-1
  if ! wait "$virt_pid"; then
    virt_pid=
    printf 'fedora-proof: installation failed; see %s\n' "$log_file" >&2
    return 1
  fi
  virt_pid=
  if /usr/bin/virsh --connect "$FEDORA_PROOF_CONNECT" dominfo "$vm_name" >/dev/null 2>&1; then
    printf '%s\n' 'fedora-proof: transient installer domain remained active after Kickstart shutdown' >&2
    return 1
  fi

  start_domain_and_record_uuid runtime \
    /usr/bin/virt-install \
      --connect "$FEDORA_PROOF_CONNECT" \
      --transient \
      --name "$vm_name" \
      --memory 12288 \
      --vcpus 4 \
      --cpu host-passthrough \
      --osinfo fedora44 \
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

  : >"$known_hosts"
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
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -euo pipefail
  fedora_proof_main "$@"
fi

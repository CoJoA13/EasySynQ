#!/usr/bin/env bash
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
HOST_SCRIPT=$ROOT/scripts/run-fedora-proof.sh
GUEST_SCRIPT=$ROOT/scripts/inside-fedora-proof.sh
KICKSTART=$ROOT/infra/dev/fedora-proof/ks.cfg
RUNBOOK=$ROOT/docs/runbooks/fedora-proof.md
CONTRACT_VIRSH_BIN=${FEDORA_PROOF_CONTRACT_VIRSH_BIN:-/usr/bin/virsh}

if [[ ${FEDORA_PROOF_CONTRACT_VIRSH_BIN+x} == x \
    && ( $CONTRACT_VIRSH_BIN != /* || ! -x $CONTRACT_VIRSH_BIN ) ]]; then
  printf 'FAIL invalid contract virsh probe: %s\n' "$CONTRACT_VIRSH_BIN" >&2
  exit 2
fi

passed=0
skipped=0
failed=0

pass() {
  passed=$((passed + 1))
}

skip() {
  printf 'SKIP %s\n' "$1"
  skipped=$((skipped + 1))
}

fail() {
  printf 'FAIL %s\n' "$1" >&2
  failed=$((failed + 1))
}

assert_file() {
  if [[ -f $1 ]]; then
    pass
  else
    fail "$2"
  fi
}

assert_status() {
  local wanted=$1 actual=$2 label=$3
  if [[ $actual == "$wanted" ]]; then
    pass
  else
    fail "$label (wanted=$wanted actual=$actual)"
  fi
}

assert_contains() {
  local haystack=$1 needle=$2 label=$3
  if [[ $haystack == *"$needle"* ]]; then
    pass
  else
    fail "$label (missing=$needle)"
  fi
}

assert_not_contains() {
  local haystack=$1 needle=$2 label=$3
  if [[ $haystack != *"$needle"* ]]; then
    pass
  else
    fail "$label (unexpected=$needle)"
  fi
}

assert_not_exists() {
  if [[ ! -e $1 && ! -L $1 ]]; then
    pass
  else
    fail "$2"
  fi
}

assert_file "$HOST_SCRIPT" 'host Fedora proof script exists'
assert_file "$GUEST_SCRIPT" 'guest Fedora proof script exists'
assert_file "$KICKSTART" 'Fedora proof Kickstart exists'
assert_file "$RUNBOOK" 'Fedora proof runbook exists'

fixture_root=$(mktemp -d "${TMPDIR:-/tmp}/easysynq-fedora-contract.XXXXXX") || exit 2
owned_cleanup=
failure_repo=$fixture_root/failure-repo
failure_tmp=$fixture_root/failure-tmp
failure_vm=easysynq-fedora-proof-20000101T000000Z-99999-deadbeef
failure_log=$failure_repo/.fedora-proof-logs/$failure_vm.log
failure_workdir=
storage_ready_probe=$fixture_root/virsh-storage-ready-probe
storage_probe=$fixture_root/virsh-storage-probe
acl_probe=$fixture_root/acl-probe
client_probe=$fixture_root/client-probe
console_fifo=$fixture_root/console-input
console_log=$fixture_root/console.log
diagnostic_probe=$fixture_root/virsh-diagnostic-probe
diagnostic_log=$fixture_root/diagnostic.log
uuid_probe=$fixture_root/uuid-probe
uuid_virsh_probe=$fixture_root/uuid-virsh-probe
uuid_virsh_pid_file=$fixture_root/uuid-virsh-pid
acl_workdir=
stage_temp=
unsafe_stage_root=$fixture_root/unsafe-stage-root
unsafe_stage_link=$fixture_root/unsafe-stage-link
staged_installer=$fixture_root/easysynq-fedora-proof-media-$EUID-installer.iso
staged_workstation=$fixture_root/easysynq-fedora-proof-media-$EUID-workstation.iso
test_qemu_uid=65534
[[ $test_qemu_uid != "$EUID" ]] || test_qemu_uid=65533
# The desktop sandbox maps only the caller uid for POSIX ACL writes. Production resolution separately
# proves qemu is a distinct non-root uid; these filesystem cases exercise the real ACL implementation.
test_acl_uid=$EUID
cleanup_fixture() {
  local virt_log=$fixture_root/virt-cache/virt-manager/virt-install.log
  [[ $fixture_root == "${TMPDIR:-/tmp}"/easysynq-fedora-contract.* ]] || return 1
  [[ -d $fixture_root && ! -L $fixture_root ]] || return 1
  rm -f -- \
    "$fixture_root/Fedora-Everything-netinst-x86_64-44-test.iso" \
    "$fixture_root/Fedora-Workstation-Live-44-test.x86_64.iso" \
    "$fixture_root/workstation-link.iso" \
    "$fixture_root/outside.qcow2" \
    "$fixture_root/owned/root.qcow2" \
    "$fixture_root/owned/vm-name" \
    "$fixture_root/owned/.easysynq-fedora-proof" 2>/dev/null || true
  rm -f -- "$fixture_root/lock-ready" "$fixture_root/lock-release" \
    "$storage_ready_probe" "$storage_probe" \
    "$acl_probe" "$client_probe" "$console_fifo" "$console_log" "$diagnostic_probe" \
    "$diagnostic_log" "$uuid_probe" "$uuid_virsh_probe" "$uuid_virsh_pid_file" \
    "$staged_installer" "$staged_workstation" \
    2>/dev/null || true
  if [[ -n $stage_temp \
      && $stage_temp == "$fixture_root"/easysynq-fedora-proof-media-*.part.* \
      && -f $stage_temp \
      && ! -L $stage_temp ]]; then
    rm -f -- "$stage_temp" 2>/dev/null || true
  fi
  rm -f -- \
    "$unsafe_stage_root/easysynq-fedora-proof-media-$EUID-installer.iso" \
    "$unsafe_stage_root/easysynq-fedora-proof-media-$EUID-workstation.iso" 2>/dev/null || true
  [[ ! -L $unsafe_stage_link ]] || rm -f -- "$unsafe_stage_link" 2>/dev/null || true
  rmdir "$unsafe_stage_root" 2>/dev/null || true
  rm -f -- "$failure_log" "$failure_repo/ks.cfg" 2>/dev/null || true
  rmdir "$failure_repo/.fedora-proof-logs" 2>/dev/null || true
  rmdir "$failure_repo" 2>/dev/null || true
  if [[ -n $failure_workdir \
      && $failure_workdir == "$fixture_root"/easysynq-fedora-proof.* \
      && -d $failure_workdir \
      && ! -L $failure_workdir ]]; then
    rm -f -- \
      "$failure_workdir/root.qcow2" \
      "$failure_workdir/ks.cfg" \
      "$failure_workdir/id_ed25519" \
      "$failure_workdir/id_ed25519.pub" \
      "$failure_workdir/known_hosts" \
      "$failure_workdir/repo-files" \
      "$failure_workdir/vm-uuid" \
      "$failure_workdir/vm-name" \
      "$failure_workdir/.easysynq-fedora-proof" 2>/dev/null || true
    rmdir "$failure_workdir" 2>/dev/null || true
  fi
  if [[ -n $acl_workdir \
      && $acl_workdir == "$fixture_root"/easysynq-fedora-proof.* \
      && -d $acl_workdir \
      && ! -L $acl_workdir ]]; then
    rm -f -- \
      "$acl_workdir/root.qcow2" \
      "$acl_workdir/ks.cfg" \
      "$acl_workdir/id_ed25519" \
      "$acl_workdir/id_ed25519.pub" \
      "$acl_workdir/repo-files" \
      "$acl_workdir/vm-uuid" \
      "$acl_workdir/vm-name" \
      "$acl_workdir/.easysynq-fedora-proof" 2>/dev/null || true
    rmdir "$acl_workdir" 2>/dev/null || true
  fi
  rmdir "$failure_tmp" 2>/dev/null || true
  rmdir "$fixture_root/Fedora-Workstation-Live-44-directory.x86_64.iso" 2>/dev/null || true
  rmdir "$fixture_root/owned" 2>/dev/null || true
  if [[ -n $owned_cleanup \
      && $owned_cleanup == "$fixture_root"/easysynq-fedora-proof.* \
      && -d $owned_cleanup \
      && ! -L $owned_cleanup ]]; then
    rm -f -- \
      "$owned_cleanup/root.qcow2" \
      "$owned_cleanup/vm-name" \
      "$owned_cleanup/.easysynq-fedora-proof" 2>/dev/null || true
    rmdir "$owned_cleanup" 2>/dev/null || true
  fi
  if [[ -e $virt_log || -L $virt_log ]]; then
    [[ -f $virt_log && ! -L $virt_log \
        && $(/usr/bin/readlink -e "$virt_log") == "$virt_log" \
        && $(/usr/bin/stat -c '%u:%h' "$virt_log") == "$EUID:1" ]] || return 1
    /usr/bin/rm -- "$virt_log" || return 1
  fi
  rmdir "$fixture_root/virt-cache/virt-manager/boot" 2>/dev/null || true
  rmdir "$fixture_root/virt-cache/virt-manager" 2>/dev/null || true
  rmdir "$fixture_root/virt-cache" 2>/dev/null || true
  rmdir "$fixture_root" 2>/dev/null || true
}
trap cleanup_fixture EXIT

if [[ -f $HOST_SCRIPT ]]; then
  output=$("$HOST_SCRIPT" 2>&1)
  status=$?
  assert_status 2 "$status" 'missing media arguments are usage errors'
  assert_contains "$output" '--installer-iso' 'usage names the installer ISO'
  assert_contains "$output" '--workstation-iso' 'usage names the Workstation ISO'

  installer=$fixture_root/Fedora-Everything-netinst-x86_64-44-test.iso
  workstation=$fixture_root/Fedora-Workstation-Live-44-test.x86_64.iso
  printf 'installer-media\n' >"$installer"
  printf 'workstation-media\n' >"$workstation"
  installer_sha=$(sha256sum "$installer" | awk '{print $1}')
  workstation_sha=$(sha256sum "$workstation" | awk '{print $1}')
  common=(
    --installer-iso "$installer"
    --installer-iso-sha256 "$installer_sha"
    --workstation-iso "$workstation"
    --workstation-iso-sha256 "$workstation_sha"
    --validate-only
  )

  output=$("$HOST_SCRIPT" "${common[@]}" 2>&1)
  status=$?
  assert_status 0 "$status" 'validate-only accepts two exact matching media digests'
  assert_contains "$output" 'installer media: verified' 'installer digest is reported verified'
  assert_contains "$output" 'Workstation media: verified' 'Workstation digest is reported verified'

  output=$(TMPDIR="$fixture_root/home-tmp" "$HOST_SCRIPT" "${common[@]:0:8}" 2>&1)
  status=$?
  assert_status 2 "$status" 'real proof rejects a home or arbitrary TMPDIR'
  assert_contains "$output" 'fixed /var/tmp namespace' \
    'TMPDIR rejection explains the fixed proof namespace'
  if compgen -G "$fixture_root/home-tmp/easysynq-fedora-proof.*" >/dev/null; then
    fail 'TMPDIR rejection occurs before any lifecycle artifact'
  else
    pass
  fi

  host_source=$(<"$HOST_SCRIPT")
  assert_contains "$host_source" '--transient' 'both libvirt phases are transient'
  assert_contains "$host_source" '--location "$staged_installer_iso"' \
    'staged Fedora Everything media is the Anaconda location'
  assert_contains "$host_source" \
    '--disk "path=$staged_workstation_iso,device=cdrom,bus=sata,readonly=on,source.seclabel0.model=dac,source.seclabel0.relabel=no"' \
    'staged Fedora Workstation media is attached read-only'
  assert_contains "$host_source" 'archive --format=tar "$evidence_commit"' \
    'guest source is archived from the recorded evidence commit'
  osinfo_uses=$(grep -cF -- '--osinfo "$osinfo"' <<<"$host_source")
  assert_status 2 "$osinfo_uses" 'both libvirt phases use the one validated osinfo selection'
  memory_uses=$(grep -cF -- '--memory 8192' <<<"$host_source")
  assert_status 2 "$memory_uses" 'both libvirt phases assign the exact 8 GiB guest memory'
  assert_not_contains "$host_source" '--memory 12288' \
    'Fedora proof never retains the superseded 12 GiB guest allocation'
  readiness_call='fedora_proof_check_libvirt_ready /usr/bin/virsh'
  if [[ $host_source == *"$readiness_call"* \
      && $host_source == *"$readiness_call"*'fedora_proof_run_lifecycle \'* ]]; then
    pass
  else
    fail 'libvirt storage readiness runs before the artifact-owning lifecycle'
  fi
  if [[ $host_source != *'rm -rf'* && $host_source != *'find '*'-delete'* ]]; then
    pass
  else
    fail 'host lifecycle contains no recursive or enumerating deletion primitive'
  fi
  if [[ $host_source != *'setfacl -R'* && $host_source != *'setfacl -d'* \
      && $host_source != *'chcon '* && $host_source != *'semanage fcontext'* \
      && $host_source != *'setenforce 0'* ]]; then
    pass
  else
    fail 'host lifecycle contains no recursive/default ACL or SELinux bypass'
  fi
  assert_contains "$host_source" \
    'fedora_proof_stop_client_exact \' \
    'cleanup wires the exact bounded launched-client stop helper'
  assert_contains "$host_source" \
    'fedora_proof_reset_uuid_record "$uuid_file" "$qemu_uid"' \
    'each launch phase clears stale UUID state before starting a client'
  assert_not_contains "$host_source" 'wait "$virt_pid" 2>/dev/null' \
    'cleanup contains no unbounded wait for a launched client'
  assert_contains "$host_source" 'fedora_proof_create_private_stage_file() (' \
    'private stage creation is bounded by its own cleanup subshell'
  assert_contains "$host_source" 'trap cleanup_private_stage EXIT' \
    'private stage creation cleans a partial inode on every return path'
  assert_contains "$host_source" 'FEDORA_PROOF_INSTALL_DEADLINE_SECONDS=3600' \
    'installer deadline is exactly 3600 seconds'
  assert_contains "$host_source" '--autoconsole text' \
    'virt-install streams the explicit text console'
  assert_not_contains "$host_source" '--noautoconsole' \
    'Fedora proof has no blind no-autoconsole phase'
  assert_contains "$host_source" '--debug' \
    'virt-install debug output is retained with the serial console'
  assert_contains "$host_source" 'inst.text inst.notmux console=ttyS0,115200n8' \
    'Anaconda keeps ttyS0 and disables multiplexing for observable progress'
  assert_contains "$host_source" 'source.seclabel0.model=dac,source.seclabel0.relabel=no' \
    'the explicit Workstation source disables only libvirt DAC ownership relabel'
  assert_contains "$host_source" "./devices/disk[2]/source/seclabel/@model=dac" \
    'the location-generated installer source gets the exact DAC model override'
  assert_contains "$host_source" "./devices/disk[2]/source/seclabel/@relabel=no" \
    'the location-generated installer source cannot drift retained-media ownership'
  assert_not_contains "$host_source" 'model=selinux' \
    'media ownership stability never disables SELinux or sVirt relabeling'
  postlaunch_installer_owner_checks=$(grep -cF -- \
    '"$proof_root" "$EUID" "$staged_installer_iso" installer "$installer_sha"' \
    <<<"$host_source")
  postlaunch_workstation_owner_checks=$(grep -cF -- \
    '"$proof_root" "$EUID" "$staged_workstation_iso" workstation "$workstation_sha"' \
    <<<"$host_source")
  assert_status 2 "$postlaunch_installer_owner_checks" \
    'installer ownership/hash identity is revalidated after its transient domain stops'
  assert_status 2 "$postlaunch_workstation_owner_checks" \
    'Workstation-media ownership/hash identity is revalidated after its transient domain stops'
  if [[ $host_source == *'fedora_proof_capture_bounded_diagnostics '* \
      && $host_source == *'fedora_proof_capture_bounded_diagnostics '*'fedora_proof_destroy_domain_exact '* ]]; then
    pass
  else
    fail 'bounded diagnostics run before exact domain destruction on failure'
  fi
  assert_contains "$host_source" \
    'fedora_proof_query_phase_uuid "$phase_deadline" \' \
    'lifecycle UUID discovery dispatches through the hard installer deadline'
  assert_not_contains "$host_source" '--wait=-1 || return 1' \
    'installer launch preserves the exact UUID-timeout status'
  assert_contains "$host_source" 'set -o noclobber' \
    'retained proof logs use atomic no-clobber creation'
  assert_contains "$host_source" 'umask 077' \
    'retained proof logs are private from inode creation'

  output=$(
    "$HOST_SCRIPT" \
      --installer-iso "relative.iso" \
      --installer-iso-sha256 "$installer_sha" \
      --workstation-iso "$workstation" \
      --workstation-iso-sha256 "$workstation_sha" \
      --validate-only 2>&1
  )
  status=$?
  assert_status 2 "$status" 'relative installer ISO is rejected'
  assert_contains "$output" 'absolute path' 'relative installer rejection explains the boundary'

  output=$(
    "$HOST_SCRIPT" \
      --installer-iso "$installer" \
      --installer-iso-sha256 'not-a-sha256' \
      --workstation-iso "$workstation" \
      --workstation-iso-sha256 "$workstation_sha" \
      --validate-only 2>&1
  )
  status=$?
  assert_status 2 "$status" 'malformed installer SHA-256 is rejected'

  workstation_link=$fixture_root/workstation-link.iso
  ln -s "$workstation" "$workstation_link"
  output=$(
    "$HOST_SCRIPT" \
      --installer-iso "$installer" \
      --installer-iso-sha256 "$installer_sha" \
      --workstation-iso "$workstation_link" \
      --workstation-iso-sha256 "$workstation_sha" \
      --validate-only 2>&1
  )
  status=$?
  assert_status 2 "$status" 'symlink Workstation ISO is rejected'
  assert_contains "$output" 'symlink' 'symlink rejection is explicit'

  workstation_directory=$fixture_root/Fedora-Workstation-Live-44-directory.x86_64.iso
  mkdir "$workstation_directory"
  output=$(
    "$HOST_SCRIPT" \
      --installer-iso "$installer" \
      --installer-iso-sha256 "$installer_sha" \
      --workstation-iso "$workstation_directory" \
      --workstation-iso-sha256 "$workstation_sha" \
      --validate-only 2>&1
  )
  status=$?
  assert_status 2 "$status" 'non-regular Workstation ISO is rejected'
  assert_contains "$output" 'regular file' 'non-regular media rejection is explicit'
  rmdir "$workstation_directory"

  bad_sha=${workstation_sha%?}0
  [[ $bad_sha == "$workstation_sha" ]] && bad_sha=${workstation_sha%?}1
  output=$(
    "$HOST_SCRIPT" \
      --installer-iso "$installer" \
      --installer-iso-sha256 "$installer_sha" \
      --workstation-iso "$workstation" \
      --workstation-iso-sha256 "$bad_sha" \
      --validate-only 2>&1
  )
  status=$?
  assert_status 1 "$status" 'Workstation checksum mismatch fails verification'
  assert_contains "$output" 'checksum mismatch' 'checksum mismatch is explicit'

  # Sourced lifecycle seams exercise the same exact-target guards used by the EXIT trap. They do not
  # create or address a libvirt domain.
  # shellcheck source=../run-fedora-proof.sh
  source "$HOST_SCRIPT"
  if declare -F fedora_proof_check_libvirt_ready >/dev/null; then
    original_connect=$FEDORA_PROOF_CONNECT
    FEDORA_PROOF_CONNECT=test:///default

    real_libvirt_storage_ready=0
    if [[ -x $CONTRACT_VIRSH_BIN ]] \
        && fedora_proof_check_libvirt_ready "$CONTRACT_VIRSH_BIN" >/dev/null 2>&1; then
      real_libvirt_storage_ready=1
    fi

    printf '%s\n' \
      '#!/usr/bin/env bash' \
      'case "$*" in' \
      '  "--connect test:///default uri") printf "%s\\n" test:///default ;;' \
      '  "--connect test:///default pool-list --all") exit 0 ;;' \
      '  *) exit 2 ;;' \
      'esac' >"$storage_ready_probe"
    chmod 0700 "$storage_ready_probe"
    output=$(fedora_proof_check_libvirt_ready "$storage_ready_probe" 2>&1)
    status=$?
    assert_status 0 "$status" 'libvirt readiness accepts exact compute and storage capabilities'

    printf '%s\n' \
      '#!/usr/bin/env bash' \
      'case "$*" in' \
      '  "--connect test:///default uri") printf "%s\\n" test:///default ;;' \
      '  "--connect test:///default pool-list --all") exit 1 ;;' \
      '  *) exit 2 ;;' \
      'esac' >"$storage_probe"
    chmod 0700 "$storage_probe"
    output=$(fedora_proof_check_libvirt_ready "$storage_probe" 2>&1)
    status=$?
    FEDORA_PROOF_CONNECT=$original_connect
    assert_status 1 "$status" 'libvirt readiness rejects a missing storage capability'
    assert_contains "$output" 'storage capability' 'storage readiness failure names the missing boundary'
    assert_contains "$output" 'virtstoraged.socket' 'storage readiness failure gives the exact service remedy'
    rm -f -- "$storage_ready_probe" "$storage_probe"
  else
    fail 'host script exposes behavior-level libvirt storage readiness'
  fi

  if declare -F fedora_proof_parse_qemu_passwd >/dev/null; then
    output=$(fedora_proof_parse_qemu_passwd 'qemu:x:107:107:qemu user:/:/usr/sbin/nologin' 2>&1)
    status=$?
    assert_status 0 "$status" 'exact Fedora qemu service record is accepted'
    assert_status 107 "$output" 'qemu service resolution returns the numeric uid'

    output=$(fedora_proof_parse_qemu_passwd '' 2>&1)
    status=$?
    assert_status 1 "$status" 'missing qemu service record fails closed'
    assert_contains "$output" 'qemu service account' 'missing qemu account diagnostic is explicit'

    output=$(fedora_proof_parse_qemu_passwd \
      $'qemu:x:107:107:qemu user:/:/usr/sbin/nologin\nqemu:x:108:108:duplicate:/:/sbin/nologin' \
      2>&1)
    status=$?
    assert_status 1 "$status" 'ambiguous qemu service records fail closed'

    output=$(fedora_proof_parse_qemu_passwd 'qemu:x:0:0:unsafe:/:/usr/sbin/nologin' 2>&1)
    status=$?
    assert_status 1 "$status" 'root is never accepted as the qemu service uid'

    output=$(fedora_proof_parse_qemu_passwd 'qemu:x:107:107:interactive:/:/bin/bash' 2>&1)
    status=$?
    assert_status 1 "$status" 'interactive account is never accepted as the qemu service identity'
  else
    fail 'host script exposes strict qemu service uid parsing'
  fi

  if declare -F fedora_proof_require_acl_tools >/dev/null; then
    output=$(fedora_proof_require_acl_tools /usr/bin/setfacl /usr/bin/getfacl 2>&1)
    status=$?
    assert_status 0 "$status" 'exact ACL tools satisfy the host boundary'

    output=$(
      fedora_proof_require_acl_tools "$fixture_root/missing-setfacl" /usr/bin/getfacl 2>&1
    )
    status=$?
    assert_status 1 "$status" 'missing setfacl fails before staging or lifecycle artifacts'
    assert_contains "$output" 'missing-setfacl' 'missing ACL tool diagnostic names the exact tool'
  else
    fail 'host script exposes behavior-level ACL tool validation'
  fi

  if declare -F fedora_proof_validate_acl_text >/dev/null; then
    exact_media_acl=$(printf \
      'user::rw-\nuser:%s:r--\ngroup::---\nmask::r--\nother::---' "$test_qemu_uid")
    output=$(fedora_proof_validate_acl_text "$exact_media_acl" "$test_qemu_uid" media 2>&1)
    status=$?
    assert_status 0 "$status" 'distinct qemu uid has effective read-only media ACL semantics'

    masked_media_acl=$(printf \
      'user::rw-\nuser:%s:r--\t#effective:---\ngroup::---\nmask::---\nother::---' \
      "$test_qemu_uid")
    output=$(fedora_proof_validate_acl_text "$masked_media_acl" "$test_qemu_uid" media 2>&1)
    status=$?
    assert_status 1 "$status" 'distinct qemu ACL with a restrictive mask is rejected'

    output=$(fedora_proof_validate_acl_text \
      "$exact_media_acl"$'\ndefault:user:'"$test_qemu_uid"$':r--' \
      "$test_qemu_uid" media 2>&1)
    status=$?
    assert_status 1 "$status" 'default ACL entries are rejected from exact media access'
  else
    fail 'host script exposes effective ACL text validation for a distinct qemu uid'
  fi

  if declare -F fedora_proof_create_private_stage_file >/dev/null; then
    /usr/bin/setfacl -m "d:u:$test_acl_uid:rwx,d:m::rwx" -- "$fixture_root"
    output=$(fedora_proof_create_private_stage_file \
      "$fixture_root" "easysynq-fedora-proof-media-$EUID-installer.iso" 2>&1)
    status=$?
    (( status != 0 )) || stage_temp=$output
    assert_status 1 "$status" 'staging root with an inherited default ACL fails before inode creation'
    if compgen -G \
        "$fixture_root/easysynq-fedora-proof-media-$EUID-installer.iso.part.*" >/dev/null; then
      fail 'default-ACL rejection leaves no observable staging inode'
    else
      pass
    fi
    /usr/bin/setfacl -k -- "$fixture_root"
    stage_temp=$(fedora_proof_create_private_stage_file \
      "$fixture_root" "easysynq-fedora-proof-media-$EUID-installer.iso" 2>/dev/null)
    status=$?
    assert_status 0 "$status" 'private staging inode is created only after root ACL validation'
    stage_acl=$(/usr/bin/getfacl -cpn -- "$stage_temp" 2>/dev/null)
    assert_status $'user::rw-\ngroup::---\nother::---' "$stage_acl" \
      'inherited named/default ACL is removed before media bytes are copied'
    rm -f -- "$stage_temp"
    stage_temp=
  else
    fail 'host script exposes private pre-copy media staging'
  fi

  if declare -F fedora_proof_stage_media >/dev/null \
      && declare -F fedora_proof_validate_exact_acl >/dev/null; then
    output=$(
      fedora_proof_stage_media \
        "$fixture_root" "$EUID" "$test_acl_uid" \
        "$installer" "$installer_sha" "$workstation" "$workstation_sha" 2>&1
    )
    status=$?
    assert_status 0 "$status" 'verified media stages as exact retained copies'
    [[ -f $staged_installer && ! -L $staged_installer ]] \
      && pass || fail 'staged installer is one exact regular non-symlink file'
    [[ -f $staged_workstation && ! -L $staged_workstation ]] \
      && pass || fail 'staged Workstation media is one exact regular non-symlink file'
    assert_status "$EUID" "$(stat -c '%u' "$staged_installer" 2>/dev/null)" \
      'staged installer remains caller-owned'
    assert_status "$EUID" "$(stat -c '%u' "$staged_workstation" 2>/dev/null)" \
      'staged Workstation media remains caller-owned'
    assert_status "$installer_sha" "$(sha256sum "$staged_installer" | awk '{print $1}')" \
      'staged installer full hash matches after copy'
    assert_status "$workstation_sha" "$(sha256sum "$staged_workstation" | awk '{print $1}')" \
      'staged Workstation full hash matches after copy'
    assert_not_exists "$fixture_root/easysynq-fedora-proof-media-$EUID" \
      'A1 staging creates no media directory needing a traversal ACL'

    output=$(fedora_proof_validate_exact_acl \
      "$staged_installer" "$test_acl_uid" media 2>&1)
    status=$?
    assert_status 0 "$status" 'installer ACL gives only qemu effective read access'
    output=$(fedora_proof_validate_exact_acl \
      "$staged_workstation" "$test_acl_uid" media 2>&1)
    status=$?
    assert_status 0 "$status" 'Workstation ACL gives only qemu effective read access'

    source_acl=$(/usr/bin/getfacl -cpn -- "$installer")
    assert_not_contains "$source_acl" "user:$test_acl_uid:" \
      'caller source media receives no qemu ACL or home traversal dependency'

    /usr/bin/setfacl -m "u:$test_acl_uid:rw-,m::rw-" -- "$staged_installer"
    output=$(fedora_proof_stage_one_media \
      "$fixture_root" "$EUID" "$test_acl_uid" installer "$installer" "$installer_sha" 2>&1)
    status=$?
    assert_status 1 "$status" 'pre-existing retained media with a broad ACL fails closed'
    assert_contains "$output" 'ACL boundary failed' \
      'pre-existing retained ACL mismatch is explicit and is never silently repaired'
    /usr/bin/setfacl -b -- "$staged_installer"
    chmod 0600 "$staged_installer"
    /usr/bin/setfacl -m "u:$test_acl_uid:r--,m::r--" -- "$staged_installer"

    partial_stage_root=$fixture_root/partial-stage-root
    mkdir "$partial_stage_root"
    wrong_stage_sha=${installer_sha%?}0
    [[ $wrong_stage_sha == "$installer_sha" ]] && wrong_stage_sha=${installer_sha%?}1
    output=$(fedora_proof_stage_one_media \
      "$partial_stage_root" "$EUID" "$test_acl_uid" installer \
      "$installer" "$wrong_stage_sha" 2>&1)
    status=$?
    assert_status 1 "$status" 'new retained media with a wrong post-copy hash fails closed'
    assert_not_exists \
      "$partial_stage_root/easysynq-fedora-proof-media-$EUID-installer.iso" \
      'wrong post-copy hash publishes no final retained-media target'
    mapfile -t leaked_stage_parts < <(
      find "$partial_stage_root" -mindepth 1 -maxdepth 1 -type f \
        -name "easysynq-fedora-proof-media-$EUID-installer.iso.part.*" -print
    )
    if (( ${#leaked_stage_parts[@]} == 0 )); then
      pass
    else
      fail 'wrong post-copy hash leaves no exact private staging inode'
      for leaked_stage_part in "${leaked_stage_parts[@]}"; do
        if [[ $leaked_stage_part == "$partial_stage_root"/easysynq-fedora-proof-media-$EUID-installer.iso.part.* \
            && -f $leaked_stage_part && ! -L $leaked_stage_part \
            && $(stat -c '%u:%h' "$leaked_stage_part") == "$EUID:1" ]]; then
          rm -- "$leaked_stage_part"
        fi
      done
    fi
    rmdir -- "$partial_stage_root"

    mkdir "$unsafe_stage_root"
    ln -s "$unsafe_stage_root" "$unsafe_stage_link"
    output=$(
      fedora_proof_stage_media \
        "$unsafe_stage_link" "$EUID" "$test_acl_uid" \
        "$installer" "$installer_sha" "$workstation" "$workstation_sha" 2>&1
    )
    status=$?
    assert_status 1 "$status" 'symlink staging root fails closed'
    assert_not_exists \
      "$unsafe_stage_root/easysynq-fedora-proof-media-$EUID-installer.iso" \
      'staging root is validated before any copied artifact is created'
    rm -f -- "$unsafe_stage_link"

    printf 'wrong retained bytes\n' >"$acl_probe"
    bad_sha=$(sha256sum "$acl_probe" | awk '{print $1}')
    output=$(
      fedora_proof_stage_media \
        "$fixture_root" "$EUID" "$test_acl_uid" \
        "$installer" "$bad_sha" "$workstation" "$workstation_sha" 2>&1
    )
    status=$?
    assert_status 1 "$status" 'post-copy retained-media hash mismatch fails closed'
    assert_contains "$output" 'staged installer checksum mismatch' \
      'post-copy hash failure identifies the exact staged medium'

    printf 'acl-mask-probe\n' >"$acl_probe"
    chmod 0600 "$acl_probe"
    /usr/bin/setfacl -m "u:$test_acl_uid:r--,m::---" -- "$acl_probe"
    output=$(fedora_proof_validate_exact_acl "$acl_probe" "$test_acl_uid" media 2>&1)
    status=$?
    assert_status 1 "$status" 'masked qemu media ACL is rejected as ineffective'
    assert_contains "$output" 'effective ACL mismatch' 'masked ACL failure is explicit'
  else
    fail 'host script exposes exact retained-media staging and ACL validation'
  fi

  if declare -F fedora_proof_grant_lifecycle_acls >/dev/null \
      && declare -F fedora_proof_validate_private_acl >/dev/null \
      && declare -F fedora_proof_revoke_lifecycle_acls >/dev/null; then
    acl_workdir=$(mktemp -d "$fixture_root/easysynq-fedora-proof.XXXXXX")
    chmod 0700 "$acl_workdir"
    printf '%s\n' easysynq-fedora-proof-v1 >"$acl_workdir/.easysynq-fedora-proof"
    printf '%s\n' "$failure_vm" >"$acl_workdir/vm-name"
    : >"$acl_workdir/root.qcow2"
    for private in ks.cfg id_ed25519 id_ed25519.pub repo-files vm-uuid; do
      : >"$acl_workdir/$private"
      chmod 0600 "$acl_workdir/$private"
    done
    chmod 0600 "$acl_workdir/root.qcow2" "$acl_workdir/.easysynq-fedora-proof" \
      "$acl_workdir/vm-name"

    output=$(fedora_proof_grant_lifecycle_acls \
      "$fixture_root" "$acl_workdir" "$acl_workdir/root.qcow2" "$test_acl_uid" 2>&1)
    status=$?
    assert_status 0 "$status" 'lifecycle grants only exact workdir and disk ACLs'
    output=$(fedora_proof_validate_exact_acl "$acl_workdir" "$test_acl_uid" workdir 2>&1)
    status=$?
    assert_status 0 "$status" 'qemu has effective traverse-only access on the exact workdir'
    output=$(fedora_proof_validate_exact_acl \
      "$acl_workdir/root.qcow2" "$test_acl_uid" disk 2>&1)
    status=$?
    assert_status 0 "$status" 'qemu has effective read-write access on the exact qcow2'
    for private in ks.cfg id_ed25519 id_ed25519.pub repo-files vm-uuid vm-name \
      .easysynq-fedora-proof; do
      output=$(fedora_proof_validate_private_acl \
        "$acl_workdir/$private" "$test_acl_uid" 2>&1)
      status=$?
      assert_status 0 "$status" "$private remains inaccessible to qemu"
    done

    /usr/bin/setfacl -m "u:$test_acl_uid:r--" -- "$acl_workdir/id_ed25519"
    output=$(fedora_proof_validate_private_acl \
      "$acl_workdir/id_ed25519" "$test_acl_uid" 2>&1)
    status=$?
    assert_status 1 "$status" 'private-key qemu ACL fails the boundary validation'
    /usr/bin/setfacl -b -- "$acl_workdir/id_ed25519"
    chmod 0600 "$acl_workdir/id_ed25519"

    output=$(fedora_proof_revoke_lifecycle_acls \
      "$fixture_root" "$acl_workdir" "$acl_workdir/root.qcow2" "$test_acl_uid" 2>&1)
    status=$?
    assert_status 0 "$status" 'stopped lifecycle revokes exact qemu workdir and disk ACLs'
    acl_after=$(/usr/bin/getfacl -cpn -- "$acl_workdir/root.qcow2" "$acl_workdir")
    assert_not_contains "$acl_after" "user:$test_acl_uid:" \
      'revoked lifecycle leaves no qemu ACL on disk or workdir'
  else
    fail 'host script exposes exact lifecycle ACL grant, validation, and revocation'
  fi

  if declare -F fedora_proof_disk_owner_allowed >/dev/null; then
    output=$(fedora_proof_disk_owner_allowed \
      "$test_qemu_uid" "$EUID" "$test_qemu_uid" active 2>&1)
    status=$?
    assert_status 0 "$status" 'active lifecycle permits temporary exact qemu disk ownership'
    output=$(fedora_proof_disk_owner_allowed \
      "$test_qemu_uid" "$EUID" "$test_qemu_uid" cleanup 2>&1)
    status=$?
    assert_status 1 "$status" 'cleanup refuses disk still owned by qemu'
    assert_contains "$output" 'caller ownership was not restored' \
      'ownership-restoration refusal explains exact retention'
    output=$(fedora_proof_disk_owner_allowed "$EUID" "$EUID" "$test_qemu_uid" cleanup 2>&1)
    status=$?
    assert_status 0 "$status" 'cleanup accepts caller ownership after domain stop'
  else
    fail 'host script exposes active-versus-cleanup disk ownership policy'
  fi

  if declare -F fedora_proof_media_owner_allowed >/dev/null; then
    output=$(fedora_proof_media_owner_allowed "$EUID" "$EUID" installer 2>&1)
    status=$?
    assert_status 0 "$status" 'retained media accepts its exact caller owner'
    output=$(fedora_proof_media_owner_allowed "$test_qemu_uid" "$EUID" workstation 2>&1)
    status=$?
    assert_status 1 "$status" 'reproduced qemu-owned retained media drift fails closed'
    assert_contains "$output" 'retained workstation owner drifted from the caller' \
      'retained-media ownership drift has an exact actionable diagnostic'
  else
    fail 'host script exposes retained-media owner stability policy'
  fi

  client_pid=
  if declare -F fedora_proof_client_state_valid >/dev/null; then
    fedora_proof_client_state_valid t
    status=$?
    assert_status 0 "$status" 'Linux lowercase tracing-stop client state is recognized'
    fedora_proof_client_state_valid '?'
    status=$?
    assert_status 1 "$status" 'unknown client state is rejected'
  else
    fail 'host script exposes documented Linux client-state validation'
  fi

  if declare -F fedora_proof_capture_client_identity >/dev/null \
      && declare -F fedora_proof_stop_client_exact >/dev/null; then
    output=$(
      /usr/bin/timeout -s TERM -k 1 0.4 /usr/bin/bash -c '
        source "$1"
        fedora_proof_read_client_identity() { return 1; }
        /usr/bin/sleep 30 &
        child=$!
        cleanup_probe() {
          trap - EXIT INT TERM
          kill -TERM "$child" 2>/dev/null || true
          wait "$child" 2>/dev/null || true
        }
        trap cleanup_probe EXIT
        trap "exit 143" TERM
        fedora_proof_stop_client_exact "$child" 1 "$BASHPID" 1
      ' _ "$HOST_SCRIPT" 2>&1
    )
    status=$?
    assert_status 1 "$status" \
      'unreadable live client identity fails closed without an unbounded wait'
    assert_contains "$output" 'identity is unreadable; refusing wait or signal' \
      'unreadable live-client refusal explains bounded retention'

    /usr/bin/sleep 30 &
    client_pid=$!
    client_parent=$BASHPID
    client_identity=$(fedora_proof_capture_client_identity "$client_pid" "$client_parent" 2>&1)
    status=$?
    assert_status 0 "$status" 'launched client identity captures an exact direct-child start time'
    if [[ $client_identity =~ ^[0-9]+$ ]]; then
      pass
    else
      fail "captured client start time is numeric (actual=$client_identity)"
    fi

    fedora_proof_stop_client_exact \
      "$client_pid" "$((client_identity + 1))" "$client_parent" 2 \
      >"$client_probe" 2>&1
    status=$?
    output=$(<"$client_probe")
    assert_status 1 "$status" 'client cleanup refuses a mismatched process identity'
    assert_contains "$output" 'identity mismatch' 'client mismatch refusal is explicit'
    if kill -0 "$client_pid" 2>/dev/null; then
      pass
    else
      fail 'mismatched client identity is not signalled'
    fi

    fedora_proof_stop_client_exact \
      "$client_pid" "$client_identity" "$client_parent" 20 \
      >"$client_probe" 2>&1
    status=$?
    output=$(<"$client_probe")
    assert_status 0 "$status" 'exact launched client is terminated and reaped within a bound'
    if [[ -e /proc/$client_pid ]] || kill -0 "$client_pid" 2>/dev/null; then
      fail 'bounded client cleanup leaves the exact launched process alive'
    else
      pass
    fi
    client_pid=
  else
    fail 'host script exposes exact bounded launched-client cleanup seams'
  fi

  if declare -F fedora_proof_wait_client_deadline_exact >/dev/null; then
    deadline_parent=$BASHPID
    /usr/bin/sleep 30 &
    deadline_client_pid=$!
    deadline_client_start=$(fedora_proof_capture_client_identity \
      "$deadline_client_pid" "$deadline_parent")
    /usr/bin/sleep 1 &
    deadline_timer_pid=$!
    deadline_timer_start=$(fedora_proof_capture_client_identity \
      "$deadline_timer_pid" "$deadline_parent")
    deadline_started=$SECONDS
    fedora_proof_wait_client_deadline_exact \
      "$deadline_client_pid" "$deadline_client_start" "$deadline_parent" \
      "$deadline_timer_pid" "$deadline_timer_start" "$deadline_parent" \
      >"$client_probe" 2>&1
    status=$?
    output=$(<"$client_probe")
    deadline_elapsed=$((SECONDS - deadline_started))
    assert_status 124 "$status" 'installer deadline returns the standard timeout status'
    (( deadline_elapsed <= 3 )) \
      && pass || fail "installer deadline behavior is bounded (elapsed=${deadline_elapsed}s)"
    if [[ ! -e /proc/$deadline_client_pid && ! -e /proc/$deadline_timer_pid ]]; then
      pass
    else
      fail 'deadline terminates and reaps only the exact client and exact timer children'
      kill -TERM "$deadline_client_pid" "$deadline_timer_pid" 2>/dev/null || true
      wait "$deadline_client_pid" "$deadline_timer_pid" 2>/dev/null || true
    fi
    assert_contains "$output" 'exact 3600-second installation deadline expired' \
      'deadline failure names the production installation bound'
  else
    fail 'host script exposes behavior-level exact client deadline handling'
  fi

  if declare -F fedora_proof_query_phase_uuid >/dev/null; then
    original_uuid_query_timeout=$FEDORA_PROOF_UUID_QUERY_TIMEOUT_SECONDS
    : >"$uuid_probe"
    : >"$console_log"
    /usr/bin/chmod 0600 "$uuid_probe" "$console_log"
    printf '%s\n' \
      '#!/usr/bin/env bash' \
      'if [[ " $* " == *" domuuid "* ]]; then' \
      '  printf "%s\n" "$BASHPID" >"$FEDORA_UUID_HANG_PID_FILE"' \
      '  exec /usr/bin/sleep 30' \
      'fi' \
      'exit 2' >"$uuid_virsh_probe"
    /usr/bin/chmod 0700 "$uuid_virsh_probe"
    uuid_parent=$BASHPID
    /usr/bin/sleep 30 &
    uuid_client_pid=$!
    uuid_client_start=$(fedora_proof_capture_client_identity \
      "$uuid_client_pid" "$uuid_parent")
    /usr/bin/sleep 1 &
    uuid_timer_pid=$!
    uuid_timer_start=$(fedora_proof_capture_client_identity \
      "$uuid_timer_pid" "$uuid_parent")
    uuid_started=$SECONDS
    FEDORA_PROOF_UUID_QUERY_TIMEOUT_SECONDS=30
    export FEDORA_UUID_HANG_PID_FILE="$uuid_virsh_pid_file"
    fedora_proof_query_phase_uuid 1 \
      "$uuid_client_pid" "$uuid_client_start" "$uuid_parent" \
      "$uuid_timer_pid" "$uuid_timer_start" "$uuid_parent" \
      easysynq-fedora-proof-20000101T000000Z-1-deadbeef \
      "$uuid_probe" "$console_log" "$uuid_virsh_probe" \
      >"$client_probe" 2>&1
    status=$?
    uuid_elapsed=$((SECONDS - uuid_started))
    output=$(<"$client_probe")
    unset FEDORA_UUID_HANG_PID_FILE
    assert_status 124 "$status" \
      'a hanging UUID discovery returns the installer deadline status'
    (( uuid_elapsed <= 3 )) \
      && pass || fail "hanging UUID discovery is bounded by the same timer (elapsed=${uuid_elapsed}s)"
    if [[ ! -e /proc/$uuid_client_pid && ! -e /proc/$uuid_timer_pid \
        && -z ${FEDORA_PROOF_UUID_QUERY_PID:-} ]]; then
      pass
    else
      fail 'UUID deadline reaps only the exact query, client, and timer children'
      kill -TERM "$uuid_client_pid" "$uuid_timer_pid" \
        "${FEDORA_PROOF_UUID_QUERY_PID:-}" 2>/dev/null || true
      wait "$uuid_client_pid" "$uuid_timer_pid" \
        "${FEDORA_PROOF_UUID_QUERY_PID:-}" 2>/dev/null || true
    fi
    uuid_query_child_pid=$(<"$uuid_virsh_pid_file")
    if [[ $uuid_query_child_pid =~ ^[1-9][0-9]*$ \
        && ! -e /proc/$uuid_query_child_pid ]] \
        && ! kill -0 "$uuid_query_child_pid" 2>/dev/null; then
      pass
    else
      fail 'UUID deadline leaves no hanging virsh descendant'
    fi
    assert_contains "$output" 'exact 3600-second installation deadline expired' \
      'UUID discovery deadline names the production installation bound'
    FEDORA_PROOF_UUID_QUERY_TIMEOUT_SECONDS=$original_uuid_query_timeout
  else
    fail 'host script exposes behavior-level lifecycle UUID-deadline dispatch'
  fi

  if declare -F fedora_proof_launch_client_logged >/dev/null; then
    /usr/bin/mkfifo -- "$console_fifo"
    /usr/bin/chmod 0600 "$console_fifo"
    : >"$console_log"
    /usr/bin/chmod 0600 "$console_log"
    exec {console_fd}<>"$console_fifo"
    FEDORA_PROOF_LAUNCHED_PID=
    fedora_proof_launch_client_logged "$console_log" "$console_fifo" "$console_fd" \
      /usr/bin/bash -c '
        if IFS= read -r -t 0.1 _; then
          printf "%s\n" FEDORA_PROOF_TEST_UNEXPECTED_INPUT
          exit 1
        else
          status=$?
        fi
        if (( status <= 128 )); then
          printf "%s\n" FEDORA_PROOF_TEST_STDIN_EOF
          exit 1
        fi
        printf "%s\n" FEDORA_PROOF_TEST_DETACHED_STDIN_SAFE
        printf "%s\n" FEDORA_PROOF_TEST_SERIAL_MARKER
      '
    status=$?
    assert_status 0 "$status" 'detached logged client launches through the private FIFO seam'
    console_pid=$FEDORA_PROOF_LAUNCHED_PID
    wait "$console_pid"
    status=$?
    exec {console_fd}>&-
    assert_status 0 "$status" 'detached client keeps stdin open without consuming caller input'
    console_output=$(<"$console_log")
    assert_contains "$console_output" 'FEDORA_PROOF_TEST_DETACHED_STDIN_SAFE' \
      'detached no-stdin execution does not disconnect the text console'
    assert_contains "$console_output" 'FEDORA_PROOF_TEST_SERIAL_MARKER' \
      'client stdout reaches the retained proof log'
    assert_not_contains "$console_output" 'FEDORA_PROOF_TEST_STDIN_EOF' \
      'private FIFO prevents an EOF-driven virsh console disconnect'
  else
    fail 'host script exposes behavior-level detached console logging'
  fi

  if declare -F fedora_proof_reset_uuid_record >/dev/null; then
    printf '%s\n' 11111111-1111-1111-1111-111111111111 >"$acl_probe"
    /usr/bin/setfacl -b -- "$acl_probe"
    chmod 0600 "$acl_probe"
    output=$(fedora_proof_reset_uuid_record "$acl_probe" "$test_acl_uid" 2>&1)
    status=$?
    assert_status 0 "$status" 'each domain phase clears the prior UUID record safely'
    if [[ ! -s $acl_probe ]]; then
      pass
    else
      fail 'UUID reset leaves no stale installer identity for runtime cleanup'
    fi
  else
    fail 'host script exposes safe per-phase UUID reset'
  fi

  if declare -F fedora_proof_new_vm_name >/dev/null \
      && declare -F fedora_proof_validate_cleanup_identity >/dev/null \
      && declare -F fedora_proof_remove_disk_exact >/dev/null; then
    first_name=$(fedora_proof_new_vm_name)
    second_name=$(fedora_proof_new_vm_name)
    if [[ $first_name =~ ^easysynq-fedora-proof-[0-9]{8}T[0-9]{6}Z-[0-9]+-[0-9a-f]{8}$ ]]; then
      pass
    else
      fail "VM name has an exact owned prefix and unique suffix (actual=$first_name)"
    fi
    if [[ $first_name != "$second_name" ]]; then
      pass
    else
      fail 'consecutive VM names are unique'
    fi

    owned=$(mktemp -d "$fixture_root/easysynq-fedora-proof.XXXXXX")
    owned_cleanup=$owned
    printf '%s\n' 'easysynq-fedora-proof-v1' >"$owned/.easysynq-fedora-proof"
    printf '%s\n' "$first_name" >"$owned/vm-name"
    disk=$owned/root.qcow2
    outside=$fixture_root/outside.qcow2
    if [[ -x /usr/bin/qemu-img ]]; then
      /usr/bin/qemu-img create -q -f qcow2 "$disk" 1M
    else
      : >"$disk"
    fi
    : >"$outside"

    if declare -F fedora_proof_capture_bounded_diagnostics >/dev/null; then
      diagnostic_uuid=11111111-2222-3333-4444-555555555555
      : >"$diagnostic_log"
      /usr/bin/chmod 0600 "$diagnostic_log"
      printf '%s\n' \
        '#!/usr/bin/env bash' \
        'if [[ -n ${FEDORA_DIAG_HANG_COMMAND:-} && " $* " == *" $FEDORA_DIAG_HANG_COMMAND "* ]]; then' \
        '  /usr/bin/sleep 30' \
        'fi' \
        'case "$*" in' \
        '  *" dominfo $FEDORA_DIAG_VM") printf "%s\\n" "Id: 1" ;;' \
        '  *" domuuid $FEDORA_DIAG_VM") printf "%s\\n" "$FEDORA_DIAG_UUID" ;;' \
        '  *" domblklist $FEDORA_DIAG_VM --details")' \
        '    printf "%s\\n" "Type Device Target Source" "file disk vda $FEDORA_DIAG_DISK" ;;' \
        '  *" domstate $FEDORA_DIAG_VM") printf "%s\\n" running ;;' \
        '  *" domblkstat $FEDORA_DIAG_VM "*)' \
        '    for i in {1..80}; do printf "diag-line-%d\\n" "$i"; done ;;' \
        '  *" dommemstat $FEDORA_DIAG_VM") printf "%s\\n" "actual 8388608" ;;' \
        '  *" vcpuinfo $FEDORA_DIAG_VM") printf "%s\\n" "VCPU: 0" ;;' \
        '  *) exit 2 ;;' \
        'esac' >"$diagnostic_probe"
      /usr/bin/chmod 0700 "$diagnostic_probe"
      export FEDORA_DIAG_VM="$first_name" FEDORA_DIAG_UUID="$diagnostic_uuid" \
        FEDORA_DIAG_DISK="$disk"
      original_connect=$FEDORA_PROOF_CONNECT
      FEDORA_PROOF_CONNECT=test:///default
      fedora_proof_capture_bounded_diagnostics \
        "$owned" "$first_name" "$diagnostic_uuid" "$disk" "$fixture_root" \
        "$test_qemu_uid" "$diagnostic_log" "$diagnostic_probe"
      status=$?
      FEDORA_PROOF_CONNECT=$original_connect
      unset FEDORA_DIAG_VM FEDORA_DIAG_UUID FEDORA_DIAG_DISK
      assert_status 0 "$status" 'exact active domain produces a bounded diagnostic bundle'
      diagnostic_output=$(<"$diagnostic_log")
      assert_contains "$diagnostic_output" 'FEDORA_PROOF_DIAGNOSTICS_BEGIN' \
        'diagnostic bundle has a deterministic begin marker'
      assert_contains "$diagnostic_output" 'FEDORA_PROOF_DIAGNOSTICS_END' \
        'diagnostic bundle has a deterministic end marker'
      assert_contains "$diagnostic_output" 'diag-line-1' \
        'diagnostic bundle retains bounded device progress'
      assert_not_contains "$diagnostic_output" 'diag-line-41' \
        'diagnostic command output is capped before retention'
      assert_status 600 "$(/usr/bin/stat -c '%a' "$diagnostic_log")" \
        'diagnostic retention preserves the 0600 log boundary'

      for hanging_command in dominfo domuuid domblklist; do
        : >"$diagnostic_log"
        export FEDORA_DIAG_HANG_COMMAND="$hanging_command" \
          FEDORA_DIAG_VM="$first_name" FEDORA_DIAG_UUID="$diagnostic_uuid" \
          FEDORA_DIAG_DISK="$disk"
        output=$(
          /usr/bin/timeout -s TERM -k 1 4 /usr/bin/bash -c '
            source "$1"
            FEDORA_PROOF_CONNECT=test:///default
            FEDORA_PROOF_DIAGNOSTIC_TIMEOUT_SECONDS=1
            fedora_proof_capture_bounded_diagnostics \
              "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"
          ' _ "$HOST_SCRIPT" "$owned" "$first_name" "$diagnostic_uuid" "$disk" \
            "$fixture_root" "$test_qemu_uid" "$diagnostic_log" "$diagnostic_probe" 2>&1
        )
        status=$?
        unset FEDORA_DIAG_HANG_COMMAND FEDORA_DIAG_VM FEDORA_DIAG_UUID FEDORA_DIAG_DISK
        assert_status 0 "$status" \
          "hanging diagnostic $hanging_command probe is bounded before exact cleanup"
        diagnostic_output=$(<"$diagnostic_log")
        assert_contains "$diagnostic_output" 'FEDORA_PROOF_DIAGNOSTICS_BEGIN' \
          "bounded $hanging_command probe retains a diagnostic begin marker"
        assert_contains "$diagnostic_output" 'FEDORA_PROOF_DIAGNOSTICS_END' \
          "bounded $hanging_command probe retains a diagnostic end marker"
      done
    else
      fail 'host script exposes behavior-level bounded pre-delete diagnostics'
    fi

    output=$(fedora_proof_validate_cleanup_identity \
      "$owned" "$first_name" "$outside" "$fixture_root" "$test_qemu_uid" cleanup 2>&1)
    status=$?
    assert_status 1 "$status" 'cleanup rejects a disk outside its owned mktemp directory'
    assert_contains "$output" 'outside owned work directory' 'outside cleanup refusal is explicit'

    output=$(fedora_proof_validate_cleanup_identity \
      "$owned" 'unrelated-vm' "$disk" "$fixture_root" "$test_qemu_uid" cleanup 2>&1)
    status=$?
    assert_status 1 "$status" 'cleanup rejects a mismatched VM identity'
    assert_contains "$output" 'VM identity mismatch' 'VM mismatch refusal is explicit'

    lock_ready_file=$fixture_root/lock-ready
    lock_release_file=$fixture_root/lock-release
    (
      exec 9<>"$disk"
      flock -x 9
      : >"$lock_ready_file"
      while [[ ! -e $lock_release_file ]]; do sleep 0.01; done
      flock -u 9
      exec 9>&-
    ) &
    lock_pid=$!
    lock_ready=0
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if [[ -e $lock_ready_file ]] && ! flock -n "$disk" true 2>/dev/null; then
        lock_ready=1
        break
      fi
      sleep 0.01
    done
    if (( lock_ready )); then
      output=$(fedora_proof_remove_disk_exact \
        "$owned" "$first_name" "$disk" "$fixture_root" "$test_qemu_uid" 2>&1)
      status=$?
      assert_status 1 "$status" 'cleanup stops on a locked disk'
      assert_contains "$output" 'disk is locked' 'locked disk refusal is explicit'
      [[ -f $disk ]] && pass || fail 'locked disk remains present'
    else
      fail 'test acquired an exclusive disk lock'
    fi
    : >"$lock_release_file"
    wait "$lock_pid"

    output=$(fedora_proof_remove_disk_exact \
      "$owned" "$first_name" "$disk" "$fixture_root" "$test_qemu_uid" 2>&1)
    status=$?
    assert_status 0 "$status" 'cleanup removes the exact unlocked owned disk'
    assert_not_exists "$disk" 'exact unlocked owned disk was removed'
  else
    fail 'host script exposes lifecycle safety seams'
  fi

  selected_fallback=
  if declare -F fedora_proof_select_osinfo_from_list >/dev/null; then
    output=$(fedora_proof_select_osinfo_from_list $'fedora43\nfedora44\nubuntu24.04' 2>&1)
    status=$?
    assert_status 0 "$status" 'osinfo selection accepts host metadata with Fedora 44'
    assert_status fedora44 "$output" 'osinfo selection prefers exact Fedora 44 metadata'

    osinfo_warning=$fixture_root/osinfo-warning
    output=$(fedora_proof_select_osinfo_from_list $'fedora43\nfedora42\nubuntu24.04' \
      2>"$osinfo_warning")
    status=$?
    assert_status 0 "$status" 'osinfo selection accepts the documented Fedora 43 metadata fallback'
    assert_status fedora43 "$output" 'osinfo selection returns the Fedora 43 metadata fallback'
    selected_fallback=$output
    warning=$(<"$osinfo_warning")
    assert_contains "$warning" 'metadata only' 'fallback diagnostic does not weaken the Fedora 44 guest gate'
    rm -f -- "$osinfo_warning"

    output=$(fedora_proof_select_osinfo_from_list $'fedora42\nubuntu24.04' 2>&1)
    status=$?
    assert_status 1 "$status" 'osinfo selection rejects a host database without a safe Fedora fallback'
    assert_contains "$output" 'fedora44 or fedora43' 'unsupported osinfo database failure is actionable'
  else
    fail 'host script exposes behavior-level osinfo selection'
  fi

  if declare -F fedora_proof_run_lifecycle >/dev/null && [[ $selected_fallback == fedora43 ]]; then
    if declare -F fedora_proof_create_private_log >/dev/null; then
      private_log_repo=$fixture_root/private-log-repo
      mkdir "$private_log_repo"
      private_log=$(fedora_proof_create_private_log "$private_log_repo" "$failure_vm")
      status=$?
      assert_status 0 "$status" 'retained proof log is created through the private helper'
      assert_status 700 "$(/usr/bin/stat -c '%a' "$private_log_repo/.fedora-proof-logs")" \
        'retained proof log directory is private from creation'
      assert_status 600 "$(/usr/bin/stat -c '%a' "$private_log")" \
        'retained proof log inode is 0600 from creation'
      assert_status $'user::rwx\ngroup::---\nother::---' \
        "$(/usr/bin/getfacl -cpn -- "$private_log_repo/.fedora-proof-logs")" \
        'retained proof log directory has no named or default ACL'
      rm -f -- "$private_log"
      rmdir -- "$private_log_repo/.fedora-proof-logs" "$private_log_repo"

      unsafe_private_log_repo=$fixture_root/unsafe-private-log-repo
      mkdir "$unsafe_private_log_repo"
      mkdir -m 0770 "$unsafe_private_log_repo/.fedora-proof-logs"
      output=$(fedora_proof_create_private_log "$unsafe_private_log_repo" "$failure_vm" 2>&1)
      status=$?
      assert_status 1 "$status" 'group-writable retained log directory is refused'
      assert_not_exists "$unsafe_private_log_repo/.fedora-proof-logs/$failure_vm.log" \
        'unsafe retained log directory is rejected before target creation'
      chmod 0700 "$unsafe_private_log_repo/.fedora-proof-logs"
      /usr/bin/setfacl -m "d:u:$EUID:rwx,d:m::rwx" -- \
        "$unsafe_private_log_repo/.fedora-proof-logs"
      output=$(fedora_proof_create_private_log "$unsafe_private_log_repo" "$failure_vm" 2>&1)
      status=$?
      assert_status 1 "$status" 'default-ACL retained log directory is refused'
      assert_not_exists "$unsafe_private_log_repo/.fedora-proof-logs/$failure_vm.log" \
        'default ACL is rejected before retained log target creation'
      /usr/bin/setfacl -b -k -- "$unsafe_private_log_repo/.fedora-proof-logs"
      chmod 0700 "$unsafe_private_log_repo/.fedora-proof-logs"
      private_log_outside=$unsafe_private_log_repo/outside
      printf '%s\n' do-not-truncate >"$private_log_outside"
      ln -s "$private_log_outside" \
        "$unsafe_private_log_repo/.fedora-proof-logs/$failure_vm.log"
      output=$(fedora_proof_create_private_log "$unsafe_private_log_repo" "$failure_vm" 2>&1)
      status=$?
      assert_status 1 "$status" 'pre-existing retained log symlink is refused atomically'
      assert_status do-not-truncate "$(<"$private_log_outside")" \
        'retained log no-clobber creation never truncates a symlink target'
      rm -f -- "$unsafe_private_log_repo/.fedora-proof-logs/$failure_vm.log" \
        "$private_log_outside"
      rmdir -- "$unsafe_private_log_repo/.fedora-proof-logs" "$unsafe_private_log_repo"
    else
      fail 'host script exposes behavior-level private retained-log creation'
    fi

    unsafe_log_repo=$fixture_root/unsafe-log-repo
    unsafe_log_root=$fixture_root/unsafe-log-root
    mkdir "$unsafe_log_repo" "$unsafe_log_root"
    ln -s "$fixture_root" "$unsafe_log_repo/.fedora-proof-logs"
    output=$(
      fedora_proof_run_lifecycle \
        "$failure_vm" "$installer" "$installer_sha" "$workstation" "$workstation_sha" \
        "$unsafe_log_repo" "$KICKSTART" 0000000000000000000000000000000000000000 \
        "$selected_fallback" "$test_acl_uid" "$unsafe_log_root" 2>&1
    )
    status=$?
    assert_status 1 "$status" 'unsafe log directory fails before lifecycle artifact creation'
    assert_contains "$output" 'log directory exists but is not an owned regular directory' \
      'unsafe log path refusal is explicit'
    mapfile -t unsafe_log_workdirs < <(
      find "$unsafe_log_root" -mindepth 1 -maxdepth 1 -type d \
        -name 'easysynq-fedora-proof.*' -print
    )
    if (( ${#unsafe_log_workdirs[@]} == 0 )); then
      pass
    else
      fail 'unsafe log path is rejected before mktemp workdir creation'
      if (( ${#unsafe_log_workdirs[@]} == 1 )) \
          && [[ ${unsafe_log_workdirs[0]} == "$unsafe_log_root"/easysynq-fedora-proof.* ]] \
          && [[ -d ${unsafe_log_workdirs[0]} && ! -L ${unsafe_log_workdirs[0]} ]]; then
        rm -f -- \
          "${unsafe_log_workdirs[0]}/.easysynq-fedora-proof" \
          "${unsafe_log_workdirs[0]}/vm-name"
        rmdir -- "${unsafe_log_workdirs[0]}"
      fi
    fi
    rm -f -- "$unsafe_log_repo/.fedora-proof-logs"
    rmdir -- "$unsafe_log_repo" "$unsafe_log_root"

    mkdir "$failure_repo" "$failure_tmp"
    cp "$KICKSTART" "$failure_repo/ks.cfg"
    lifecycle_installer=$installer
    lifecycle_workstation=$workstation
    if [[ -f $staged_installer && -f $staged_workstation ]]; then
      lifecycle_installer=$staged_installer
      lifecycle_workstation=$staged_workstation
    fi
    original_connect=$FEDORA_PROOF_CONNECT
    original_install_deadline=$FEDORA_PROOF_INSTALL_DEADLINE_SECONDS
    FEDORA_PROOF_CONNECT=test:///default
    FEDORA_PROOF_INSTALL_DEADLINE_SECONDS=1
    output=$(
      TMPDIR="$failure_tmp" XDG_CACHE_HOME="$fixture_root/virt-cache" \
        fedora_proof_run_lifecycle \
          "$failure_vm" "$lifecycle_installer" "$installer_sha" \
          "$lifecycle_workstation" "$workstation_sha" \
          "$failure_repo" "$failure_repo/ks.cfg" 0000000000000000000000000000000000000000 \
          "$selected_fallback" "$test_acl_uid" "$fixture_root" 2>&1
    )
    status=$?
    FEDORA_PROOF_CONNECT=$original_connect
    FEDORA_PROOF_INSTALL_DEADLINE_SECONDS=$original_install_deadline
    failure_disk=${output#*Fedora proof disk: }
    failure_disk=${failure_disk%%$'\n'*}
    failure_workdir=${failure_disk%/root.qcow2}

    assert_status 1 "$status" 'failure after cleanup trap installation remains a proof failure'
    if [[ $failure_disk == "$fixture_root"/easysynq-fedora-proof.*/root.qcow2 ]]; then
      pass
    else
      fail "failure fixture reports one exact owned disk path (actual=$failure_disk)"
    fi
    if [[ $output != *'unbound variable'* ]]; then
      pass
    else
      fail 'failure cleanup never reads function locals after their scope ends'
    fi
    if (( real_libvirt_storage_ready )); then
      assert_contains "$output" 'domain exited before identity capture' \
        'fixture reaches the real post-trap virt-install failure path'
    else
      skip 'host libvirt test driver lacks storage; post-domain failure path is not available'
    fi
    assert_not_exists "$failure_workdir" 'failure cleanup removes the exact owned work directory'
    assert_not_exists "$failure_disk" 'failure cleanup removes the exact qcow2 disk'
    assert_not_exists "$failure_workdir/id_ed25519" 'failure cleanup removes the exact private key'
    assert_not_exists "$failure_workdir/.easysynq-fedora-proof" \
      'failure cleanup removes the exact ownership marker'
    assert_not_exists "$failure_workdir/console-input" \
      'failure cleanup removes the exact private console FIFO'
    [[ -f $staged_installer ]] \
      && pass || fail 'failure cleanup retains the exact staged installer'
    [[ -f $staged_workstation ]] \
      && pass || fail 'failure cleanup retains the exact staged Workstation media'
    assert_status "$EUID" "$(/usr/bin/stat -c '%u' "$staged_installer")" \
      'failed lifecycle leaves retained installer caller-owned'
    assert_status "$EUID" "$(/usr/bin/stat -c '%u' "$staged_workstation")" \
      'failed lifecycle leaves retained Workstation media caller-owned'
    failure_log_output=$(<"$failure_log")
    if (( real_libvirt_storage_ready )); then
      assert_contains "$failure_log_output" 'FEDORA_PROOF_DIAGNOSTICS_BEGIN' \
        'failed lifecycle preserves a diagnostic begin marker before disk deletion'
      assert_contains "$failure_log_output" 'FEDORA_PROOF_DIAGNOSTIC domain-identity-unavailable' \
        'failed pre-domain lifecycle records bounded diagnostic unavailability'
      assert_contains "$failure_log_output" 'FEDORA_PROOF_DIAGNOSTICS_END' \
        'failed lifecycle preserves a diagnostic end marker after exact cleanup'
    else
      skip 'host libvirt test driver lacks storage; pre-delete diagnostic begin is unavailable'
      skip 'host libvirt test driver lacks storage; domain identity diagnostic is unavailable'
      skip 'host libvirt test driver lacks storage; pre-delete diagnostic end is unavailable'
    fi
    if (( real_libvirt_storage_ready )); then
      if ! "$CONTRACT_VIRSH_BIN" --connect test:///default \
          dominfo "$failure_vm" >/dev/null 2>&1; then
        pass
      else
        fail 'failure fixture leaves no libvirt test-driver domain'
      fi
    else
      skip 'host libvirt test driver is unavailable; domain absence cannot be queried'
    fi
    rm -f -- "$failure_log" "$failure_repo/ks.cfg"
    rmdir "$failure_repo/.fedora-proof-logs" "$failure_repo" "$failure_tmp"
  else
    fail 'host script exposes the scoped real lifecycle for failure cleanup proof'
  fi
fi

if [[ -f $GUEST_SCRIPT ]]; then
  output=$("$GUEST_SCRIPT" --print-plan 2>&1)
  status=$?
  assert_status 0 "$status" 'guest proof exposes its executable acceptance plan'
  for required in \
    'VARIANT_ID=workstation' \
    'VERSION_ID=44' \
    'uname -m == x86_64' \
    'getenforce == Enforcing' \
    'bootstrap-fedora-dev.sh --check' \
    'bootstrap-fedora-dev.sh --apply (literal yes; first run)' \
    'bootstrap-fedora-dev.sh --apply (literal yes; idempotence run)' \
    'doctor.sh contributor' \
    'doctor.sh test' \
    'docker run --rm hello-world' \
    'testcontainers DockerClient ping' \
    'just setup' \
    'pytest tests/unit -m unit' \
    'npm run lint' \
    'npm run typecheck' \
    'npm test -- --run' \
    'npm ci --prefix packages/contracts --ignore-scripts' \
    'test-run-contract-tool.sh' \
    'compose.s.yml + compose.dev.yml config --quiet' \
    'compose.s.yml + compose.dev.yml up -d' \
    'doctor.sh stack' \
    'compose.s.yml + compose.dev.yml down -v'; do
    assert_contains "$output" "$required" "guest plan includes $required"
  done
fi

if [[ -f $KICKSTART ]]; then
  kickstart=$(<"$KICKSTART")
  assert_contains "$kickstart" 'liveimg --url=file:///run/install/workstation-payload/LiveOS/squashfs.img' \
    'Kickstart installs the Workstation LiveOS payload'
  assert_contains "$kickstart" '@@EASYSYNQ_SSH_PUBLIC_KEY@@' \
    'Kickstart carries only the rendered ephemeral proof key'
  assert_contains "$kickstart" '@@EASYSYNQ_PASSWORD_HASH@@' \
    'Kickstart carries only a rendered random guest password hash'
  assert_contains "$kickstart" \
    'cp -a -- /mnt/sysimage/etc/resolv.conf /mnt/sysimage/root/easysynq-proof-resolv.conf.original' \
    'Kickstart preserves the installed resolver target before networked post steps'
  assert_contains "$kickstart" 'rm -f -- /mnt/sysimage/etc/resolv.conf' \
    'Kickstart replaces a potentially dangling installed resolver symlink explicitly'
  assert_contains "$kickstart" \
    'install -m 0644 /etc/resolv.conf /mnt/sysimage/etc/resolv.conf' \
    'Kickstart hands Anaconda DHCP resolver state into the installed root'
  assert_contains "$kickstart" \
    'mv -T -- /root/easysynq-proof-resolv.conf.original /etc/resolv.conf' \
    'Kickstart restores the installed resolver target after networked post steps'
  assert_contains "$kickstart" 'selinux --enforcing' 'Kickstart requests enforcing SELinux'
  assert_contains "$kickstart" 'shutdown' 'Kickstart ends the transient install phase cleanly'
  for phase_marker in \
    'stage2-start' \
    'cd-scan-start' \
    'cd-selected device=' \
    'payload-stat path=/run/install/workstation-payload/LiveOS/squashfs.img bytes=' \
    'resolver-prep-start' \
    'resolver-prep-end' \
    'dnf-start' \
    'dnf-end' \
    'install-complete' \
    'anaconda-error'; do
    assert_contains "$kickstart" "EASYSYNQ_PROOF_PHASE $phase_marker" \
      "Kickstart serial evidence includes bounded $phase_marker progress"
  done
  assert_contains "$kickstart" '%onerror --log=/tmp/easysynq-proof-onerror.log' \
    'Kickstart emits an explicit bounded Anaconda error section'
  assert_not_contains "$kickstart" 'cat /tmp/' \
    'Kickstart diagnostics never dump an unbounded installer log'
fi

if [[ -f $RUNBOOK ]]; then
  runbook=$(<"$RUNBOOK")
  assert_contains "$runbook" \
    'sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket' \
    'clean proof-host setup enables the modular libvirt storage socket'
  assert_contains "$runbook" \
    '/var/tmp/easysynq-fedora-proof-media-$UID-installer.iso' \
    'runbook names the exact retained installer path'
  assert_contains "$runbook" \
    '/var/tmp/easysynq-fedora-proof-media-$UID-workstation.iso' \
    'runbook names the exact retained Workstation path'
  assert_contains "$runbook" 'getfacl -cpn -- "$STAGED_INSTALLER" "$STAGED_WORKSTATION"' \
    'runbook verifies exact retained-media ACLs'
  assert_contains "$runbook" 'rm -- "$STAGED_INSTALLER" "$STAGED_WORKSTATION"' \
    'runbook removes only the separately verified retained media targets'
  assert_contains "$runbook" 'set -euo pipefail' \
    'retained-media cleanup stops at the first failed identity check'
  assert_contains "$runbook" 'EXPECTED_MEDIA_ACL=' \
    'retained-media cleanup derives the exact expected qemu ACL'
  assert_contains "$runbook" \
    'test "$(getfacl -cpn -- "$STAGED_MEDIA")" = "$EXPECTED_MEDIA_ACL"' \
    'retained-media cleanup asserts rather than prints the effective ACL'
  assert_not_contains "$runbook" 'at least 16 GiB of available' \
    'runbook no longer demands 16 GiB available solely for the proof guest'
  assert_contains "$runbook" '16 GB-class host' \
    'runbook describes the practical proof-host memory class'
  assert_contains "$runbook" '8 GiB transient guest' \
    'runbook documents the exact disposable guest allocation'
  assert_contains "$runbook" 'reasonable headroom for the host and libvirt' \
    'runbook preserves host memory headroom around the transient guest'
  assert_contains "$runbook" 'released after each transient domain stops' \
    'runbook explains that proof-guest RAM is temporary'
  assert_contains "$runbook" 'Docker is the normal daily development isolation boundary' \
    'runbook distinguishes daily Docker development from VM acceptance'
  assert_contains "$runbook" 'one-time clean Fedora Workstation' \
    'runbook scopes the VM to the clean Fedora acceptance boundary'
  assert_contains "$runbook" 'shipped S profile and the default Hyper-V appliance' \
    'runbook aligns the proof allocation with shipped deployment defaults'
  assert_contains "$runbook" 'hard 3600-second installation deadline' \
    'runbook documents the exact installer deadline'
  assert_contains "$runbook" 'caller-owned `0700` `.fedora-proof-logs/` directory' \
    'runbook documents the private retained-log directory boundary'
  assert_contains "$runbook" 'created atomically with no-clobber semantics' \
    'runbook documents atomic retained-log creation'
  assert_contains "$runbook" 'Prior proof releases may have created this exact directory as `0755`' \
    'runbook identifies the historical retained-log directory migration'
  assert_contains "$runbook" 'LOG_DIR="$(pwd -P)/.fedora-proof-logs"' \
    'runbook migration resolves only the exact repository retained-log directory'
  assert_contains "$runbook" 'serial text console and virt-install debug output' \
    'runbook explains the retained observable Anaconda progress'
  assert_contains "$runbook" 'bounded pre-delete diagnostic bundle' \
    'runbook explains failure diagnostics survive exact disk cleanup'
  assert_contains "$runbook" 'disables only libvirt DAC ownership relabeling' \
    'runbook documents retained-media ownership stability without weakening sVirt'
  assert_contains "$runbook" 'SELinux/sVirt labeling remains enabled' \
    'runbook preserves the SELinux media boundary explicitly'
fi

printf '%d Fedora proof contract checks passed; %d skipped; %d failed\n' \
  "$passed" "$skipped" "$failed"
(( failed == 0 ))

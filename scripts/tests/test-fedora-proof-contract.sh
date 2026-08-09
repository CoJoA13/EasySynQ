#!/usr/bin/env bash
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
HOST_SCRIPT=$ROOT/scripts/run-fedora-proof.sh
GUEST_SCRIPT=$ROOT/scripts/inside-fedora-proof.sh
KICKSTART=$ROOT/infra/dev/fedora-proof/ks.cfg
RUNBOOK=$ROOT/docs/runbooks/fedora-proof.md

passed=0
failed=0

pass() {
  passed=$((passed + 1))
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
storage_probe=$fixture_root/virsh-storage-probe
cleanup_fixture() {
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
  rm -f -- "$fixture_root/lock-ready" "$fixture_root/lock-release" "$storage_probe" \
    2>/dev/null || true
  rm -f -- "$failure_log" "$failure_repo/ks.cfg" 2>/dev/null || true
  rmdir "$failure_repo/.fedora-proof-logs" 2>/dev/null || true
  rmdir "$failure_repo" 2>/dev/null || true
  if [[ -n $failure_workdir \
      && $failure_workdir == "$failure_tmp"/easysynq-fedora-proof.* \
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
  rmdir "$failure_tmp" 2>/dev/null || true
  rmdir "$fixture_root/Fedora-Workstation-Live-44-directory.x86_64.iso" 2>/dev/null || true
  rmdir "$fixture_root/owned" 2>/dev/null || true
  rmdir "$fixture_root" 2>/dev/null || true
  if [[ -n $owned_cleanup \
      && $owned_cleanup == "${TMPDIR:-/tmp}"/easysynq-fedora-proof.* \
      && -d $owned_cleanup \
      && ! -L $owned_cleanup ]]; then
    rm -f -- \
      "$owned_cleanup/root.qcow2" \
      "$owned_cleanup/vm-name" \
      "$owned_cleanup/.easysynq-fedora-proof" 2>/dev/null || true
    rmdir "$owned_cleanup" 2>/dev/null || true
  fi
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

  host_source=$(<"$HOST_SCRIPT")
  assert_contains "$host_source" '--transient' 'both libvirt phases are transient'
  assert_contains "$host_source" '--location "$installer_iso"' \
    'Fedora Everything media is the Anaconda location'
  assert_contains "$host_source" \
    '--disk "path=$workstation_iso,device=cdrom,bus=sata,readonly=on"' \
    'Fedora Workstation media is attached read-only'
  assert_contains "$host_source" 'archive --format=tar "$evidence_commit"' \
    'guest source is archived from the recorded evidence commit'
  osinfo_uses=$(grep -cF -- '--osinfo "$osinfo"' <<<"$host_source")
  assert_status 2 "$osinfo_uses" 'both libvirt phases use the one validated osinfo selection'
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
    output=$(fedora_proof_check_libvirt_ready /usr/bin/virsh 2>&1)
    status=$?
    assert_status 0 "$status" 'libvirt test driver satisfies compute and storage readiness'

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
    rm -f -- "$storage_probe"
  else
    fail 'host script exposes behavior-level libvirt storage readiness'
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

    owned=$(mktemp -d "${TMPDIR:-/tmp}/easysynq-fedora-proof.XXXXXX")
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

    output=$(fedora_proof_validate_cleanup_identity "$owned" "$first_name" "$outside" 2>&1)
    status=$?
    assert_status 1 "$status" 'cleanup rejects a disk outside its owned mktemp directory'
    assert_contains "$output" 'outside owned work directory' 'outside cleanup refusal is explicit'

    output=$(fedora_proof_validate_cleanup_identity "$owned" 'unrelated-vm' "$disk" 2>&1)
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
      output=$(fedora_proof_remove_disk_exact "$owned" "$first_name" "$disk" 2>&1)
      status=$?
      assert_status 1 "$status" 'cleanup stops on a locked disk'
      assert_contains "$output" 'disk is locked' 'locked disk refusal is explicit'
      [[ -f $disk ]] && pass || fail 'locked disk remains present'
    else
      fail 'test acquired an exclusive disk lock'
    fi
    : >"$lock_release_file"
    wait "$lock_pid"

    output=$(fedora_proof_remove_disk_exact "$owned" "$first_name" "$disk" 2>&1)
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
    mkdir "$failure_repo" "$failure_tmp"
    cp "$KICKSTART" "$failure_repo/ks.cfg"
    original_connect=$FEDORA_PROOF_CONNECT
    FEDORA_PROOF_CONNECT=test:///default
    output=$(
      TMPDIR="$failure_tmp" XDG_CACHE_HOME="$fixture_root/virt-cache" \
        fedora_proof_run_lifecycle \
          "$failure_vm" "$installer" "$installer_sha" "$workstation" "$workstation_sha" \
          "$failure_repo" "$failure_repo/ks.cfg" 0000000000000000000000000000000000000000 \
          "$selected_fallback" 2>&1
    )
    status=$?
    FEDORA_PROOF_CONNECT=$original_connect
    failure_disk=${output#*Fedora proof disk: }
    failure_disk=${failure_disk%%$'\n'*}
    failure_workdir=${failure_disk%/root.qcow2}

    assert_status 1 "$status" 'failure after cleanup trap installation remains a proof failure'
    if [[ $failure_disk == "$failure_tmp"/easysynq-fedora-proof.*/root.qcow2 ]]; then
      pass
    else
      fail "failure fixture reports one exact owned disk path (actual=$failure_disk)"
    fi
    if [[ $output != *'unbound variable'* ]]; then
      pass
    else
      fail 'failure cleanup never reads function locals after their scope ends'
    fi
    assert_contains "$output" 'domain exited before identity capture' \
      'fixture reaches the real post-trap virt-install failure path'
    assert_not_exists "$failure_workdir" 'failure cleanup removes the exact owned work directory'
    assert_not_exists "$failure_disk" 'failure cleanup removes the exact qcow2 disk'
    assert_not_exists "$failure_workdir/id_ed25519" 'failure cleanup removes the exact private key'
    assert_not_exists "$failure_workdir/.easysynq-fedora-proof" \
      'failure cleanup removes the exact ownership marker'
    if ! /usr/bin/virsh --connect test:///default dominfo "$failure_vm" >/dev/null 2>&1; then
      pass
    else
      fail 'failure fixture leaves no libvirt test-driver domain'
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
fi

if [[ -f $RUNBOOK ]]; then
  runbook=$(<"$RUNBOOK")
  assert_contains "$runbook" \
    'sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket' \
    'clean proof-host setup enables the modular libvirt storage socket'
fi

printf '%d Fedora proof contract checks passed; %d failed\n' "$passed" "$failed"
(( failed == 0 ))

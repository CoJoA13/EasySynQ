#!/usr/bin/env bash
# QEMU boot test for the built appliance (developer tool, not shipped in the image).
# Boots the qcow2 + seed ISO headless (KVM, user-mode NAT), captures the serial console, and
# watches for the provision verdict. ⚠ Work dir MUST be on a real disk: the guest's docker
# build grows the qcow2 by several GB — a tmpfs /tmp will fill and take the host down with it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIST="$HERE/dist"
WORK="${BOOTTEST_WORK:-$HOME/.cache/easysynq-boottest}"
rm -rf "$WORK"; mkdir -p "$WORK"

# Prefer the qcow2 (a --qcow2-only dev build); otherwise round-trip the SHIPPED VHDX back to
# qcow2 — which both feeds QEMU and validates the converted artifact's content.
if [ -f "$DIST/EasySynQ-appliance.qcow2" ]; then
  cp "$DIST/EasySynQ-appliance.qcow2" "$WORK/disk.qcow2"
else
  qemu-img convert -O qcow2 "$DIST/EasySynQ-appliance.vhdx" "$WORK/disk.qcow2"
fi
qemu-img resize "$WORK/disk.qcow2" 40G >/dev/null

qemu-system-x86_64 \
  -enable-kvm -cpu host -smp 6 -m 6144 \
  -drive file="$WORK/disk.qcow2",if=virtio,format=qcow2 \
  -cdrom "$DIST/EasySynQ-seed.iso" \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:8443-:443 \
  -device virtio-net-pci,netdev=n0 \
  -display none -serial file:"$WORK/console.log" \
  -pidfile "$WORK/qemu.pid" -daemonize

echo "qemu started (pid $(cat "$WORK/qemu.pid")); console -> $WORK/console.log"
cleanup() { kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true; }
trap cleanup EXIT

# Success = the READY banner (serial) OR systemd's "Finished easysynq-provision" line — the
# latter only prints after every provision gate (readyz, account, secret) has passed.
wait_verdict() { # $1 = phase name, $2 = max 10s-iterations
  local phase="$1" cap="$2" i
  for i in $(seq 1 "$cap"); do
    sleep 10
    if grep -qaE "\[EasySynQ\] READY|Finished.*easysynq-provision" "$WORK/console.log" 2>/dev/null; then
      echo "$phase: provision verdict OK (~$((i * 10))s)"
      return 0
    fi
    if grep -qa "PROVISIONING FAILED" "$WORK/console.log" 2>/dev/null; then
      echo "$phase VERDICT: FAILED"
      tail -40 "$WORK/console.log"
      return 1
    fi
  done
  echo "$phase VERDICT: TIMEOUT"
  tail -30 "$WORK/console.log"
  return 1
}

probe_readyz() {
  local i
  for i in $(seq 1 30); do
    if curl -fsk --resolve easysynq.local:8443:127.0.0.1 \
        https://easysynq.local:8443/readyz 2>/dev/null | grep -q '"ready":true'; then
      echo " <- host-side readyz OK"
      return 0
    fi
    sleep 10
  done
  echo "host-side readyz probe failed"
  return 1
}

wait_verdict "fresh-provision" 270
probe_readyz

# Reboot-persistence proof: kill the VM, boot the SAME disk again — the provision unit must
# no-op (provisioned stamp) and the stack must come back green on its own.
echo "rebooting the same disk for the persistence check..."
kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true
sleep 3
: >"$WORK/console.log"
qemu-system-x86_64 \
  -enable-kvm -cpu host -smp 6 -m 6144 \
  -drive file="$WORK/disk.qcow2",if=virtio,format=qcow2 \
  -cdrom "$DIST/EasySynQ-seed.iso" \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:8443-:443 \
  -device virtio-net-pci,netdev=n0 \
  -display none -serial file:"$WORK/console.log" \
  -pidfile "$WORK/qemu.pid" -daemonize
probe_readyz || { echo "VERDICT: REBOOT-PERSISTENCE FAILED"; exit 1; }
echo "VERDICT: READY (fresh provision + reboot persistence)"
exit 0

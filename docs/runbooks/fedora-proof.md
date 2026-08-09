# Disposable Fedora Workstation acceptance proof

This runbook is the manual PR/release gate for the Fedora developer path. It creates an 80 GiB sparse
qcow2 disk, installs the official Fedora Workstation 44 live payload in a transient libvirt guest,
runs the repository acceptance contract with SELinux enforcing, and removes the exact VM and disk. It
does not replace the fast structural tests.

Docker is the normal daily development isolation boundary.
This transient VM is only the one-time clean Fedora Workstation acceptance boundary for SELinux,
systemd, bootstrap idempotence, system libvirt, and the complete repository gate; it is not a second
everyday development environment.

A structural-contract pass is not a Fedora proof pass. Record `PASS` only after this complete command
finishes on the immutable evidence commit and its retained log has been reviewed. When the media or a
usable libvirt host is unavailable, report the gate as `PENDING` or `BLOCKED`; do not invent a date,
checksum, evidence commit, or partial PASS.

## Why two Fedora media are required

Fedora Workstation is distributed as a Live ISO, not an Anaconda installation tree that
`virt-install --location` can reliably Kickstart. The proof therefore has two independently verified
inputs:

1. `Fedora-Everything-netinst-x86_64-44-<build>.iso` boots Fedora 44 Anaconda and receives the
   repository Kickstart through `--initrd-inject`.
2. `Fedora-Workstation-Live-44-<build>.x86_64.iso` is attached read-only. Kickstart mounts it and uses
   its exact `LiveOS/squashfs.img` as the `liveimg` payload.

The installed system must report `VARIANT_ID=workstation`, `VERSION_ID=44`, `x86_64`, and SELinux
`Enforcing`. A Fedora Cloud, Server, container, or prebuilt disk image is not an acceptable substitute.

## Proof-host prerequisites

Use a separate Fedora proof host with hardware virtualization enabled: a 16 GB-class host (or larger),
4 available CPUs, and roughly 100 GiB of free disk space. The harness assigns an exact
8 GiB transient guest for both installation and runtime. This matches the shipped S profile and the default Hyper-V appliance.
On a 16 GB-class host this leaves reasonable headroom for the host and libvirt, and the guest RAM is
released after each transient domain stops. These virtualization packages are deliberately not
installed by `scripts/bootstrap-fedora-dev.sh`:

```bash
sudo dnf install libvirt-daemon-kvm libvirt-daemon-config-network libvirt-client \
  qemu-kvm virt-install guestfs-tools acl
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket
sudo usermod -aG libvirt "$USER"
```

Log out and back in after the group change. Then verify the new session and the isolated default NAT
network:

```bash
id -nG | tr ' ' '\n' | grep -Fx libvirt
virsh --connect qemu:///system uri
virsh --connect qemu:///system pool-list --all
getent passwd qemu
command -v setfacl getfacl
virt-install --osinfo list | grep -E '^fedora(44|43)$'
sudo virsh --connect qemu:///system net-start default || true
sudo virsh --connect qemu:///system net-autostart default
virsh --connect qemu:///system net-info default
```

The read-only pool listing exercises the modular libvirt storage service that `virt-install` needs
to validate the install media and disk. The harness runs the same readiness check before it creates
its temporary directory or disk; it does not start host services or weaken permissions.

Do not weaken device, socket, ISO, or directory permissions to make libvirt work. The supplied ISO
files may remain in a caller-readable location, including the caller's home directory: the harness
copies them to two exact retained files directly under `/var/tmp` and never gives qemu an ACL on the
source files or any home-directory component. Fix the host's normal libvirt/SELinux configuration if
the preflight reports access denial; never use `chcon`, an `fcontext` override, `setenforce 0`, a broad
mode change, or a recursive/default ACL for this proof.

The harness prefers exact `fedora44` libosinfo metadata. Fedora 44 hosts whose packaged `osinfo-db`
does not yet list that identifier may use `fedora43` as the nearest device-default metadata only. The
Everything and Workstation media remain Fedora 44, and the installed guest must still pass every exact
Fedora 44 Workstation assertion. A database without either identifier fails closed.

## Acquire and verify both ISO files

Download Fedora Everything 44 Network Install from the official
[Fedora Everything page](https://fedoraproject.org/everything/download/) and Fedora Workstation 44
from the official [Fedora Workstation page](https://fedoraproject.org/workstation/download/). Download
the matching `Fedora-Everything-44-<build>-x86_64-CHECKSUM` and
`Fedora-Workstation-44-<build>-x86_64-CHECKSUM` files as well.

Follow Fedora's [download verification instructions](https://fedoraproject.org/security/) to verify
the clear-signed checksum files against Fedora's published OpenPGP certificates. Only after both
signatures are valid, verify each ISO and extract its 64-hex SHA-256 value from its own checksum file.
Keep the two values separate; the harness recomputes both full-file digests before it checks libvirt or
creates a VM.

Example local resolution after signature verification:

```bash
PROOF_MEDIA_DIR=/absolute/path/to/fedora-proof-media
INSTALLER_ISO="$PROOF_MEDIA_DIR/Fedora-Everything-netinst-x86_64-44-1.7.iso"
WORKSTATION_ISO="$PROOF_MEDIA_DIR/Fedora-Workstation-Live-44-1.7.x86_64.iso"
INSTALLER_SHA256="$(awk -v iso="$(basename "$INSTALLER_ISO")" \
  '$0 ~ iso {print $NF; exit}' \
  "$PROOF_MEDIA_DIR/Fedora-Everything-44-1.7-x86_64-CHECKSUM")"
WORKSTATION_SHA256="$(awk -v iso="$(basename "$WORKSTATION_ISO")" \
  '$0 ~ iso {print $NF; exit}' \
  "$PROOF_MEDIA_DIR/Fedora-Workstation-44-1.7-x86_64-CHECKSUM")"
test "${#INSTALLER_SHA256}" -eq 64
test "${#WORKSTATION_SHA256}" -eq 64
```

Use current Fedora 44 build names and values if they differ from the example.

## Validate, then run

Run from a committed tree. The proof records the exact commit after its clean-tree and site-data
guards, then sends a `git archive` of that immutable commit to the guest. Host `.env`, ignored import
trees, site data, credentials, later working-tree edits, and local build artifacts therefore cannot
enter the guest. It refuses tracked or staged changes because the evidence must name one reproducible
commit.

Checksum-only validation does not contact libvirt or create temporary files:

```bash
./scripts/run-fedora-proof.sh \
  --installer-iso "$INSTALLER_ISO" \
  --installer-iso-sha256 "$INSTALLER_SHA256" \
  --workstation-iso "$WORKSTATION_ISO" \
  --workstation-iso-sha256 "$WORKSTATION_SHA256" \
  --validate-only
```

The real proof rejects a caller `TMPDIR` other than `/var/tmp`; unset an inherited value before the
run. This is a fixed safety boundary, not a configurable location:

```bash
unset TMPDIR
```

Run the acceptance proof by removing only `--validate-only`:

```bash
./scripts/run-fedora-proof.sh \
  --installer-iso "$INSTALLER_ISO" \
  --installer-iso-sha256 "$INSTALLER_SHA256" \
  --workstation-iso "$WORKSTATION_ISO" \
  --workstation-iso-sha256 "$WORKSTATION_SHA256"
```

A fresh login after joining `libvirt` is preferred. If an operator deliberately uses `sg libvirt -c`
instead, unset `TMPDIR` inside that command string or set it to the literal `/var/tmp`; arbitrary
temporary roots are rejected. The printed disk path is the authoritative ephemeral cleanup target.

Before creating the VM workdir, the harness copies and re-hashes both verified inputs as these exact
caller-owned, regular, non-symlink, single-link retained files:

```bash
STAGED_INSTALLER="/var/tmp/easysynq-fedora-proof-media-$UID-installer.iso"
STAGED_WORKSTATION="/var/tmp/easysynq-fedora-proof-media-$UID-workstation.iso"
```

They are direct files, not a directory, so qemu needs no media-directory traversal grant. The harness
resolves the Fedora `qemu` service uid through both `getent` and `id`, resets each retained file to a
caller-only base ACL, grants only that uid effective `r--`, and validates the full SHA-256 again. A
different pre-existing file, symlink, hard link, owner, hash, or masked/extra ACL fails closed without
being overwritten. The files remain after success or failure so a rerun can reuse the same verified
bytes. For each retained ISO source, the domain XML disables only libvirt DAC ownership relabeling;
SELinux/sVirt labeling remains enabled. This keeps the two direct files caller-owned after both
transient domains without requiring `sudo`, `chown`, a home-directory grant, or broader permissions.

Allow approximately 60–120 minutes, depending on network, CPU, storage, and container-image caches.
The installer has a hard 3600-second installation deadline; exceeding it is a proof failure, not an
invitation to leave an unbounded guest running. The harness prints its unique VM name, exact temporary
disk path, and retained evidence log before VM creation. Logs are written with mode `0600` under
the caller-owned `0700` `.fedora-proof-logs/` directory, which must have no named or default ACL;
each unique log is created atomically with no-clobber semantics and is ignored by Git through the
repository's `*.log` rule. The detached
launcher keeps a private no-input stream open while the serial text console and virt-install debug output
append to that log, so Anaconda progress remains observable without a terminal or interactive stdin.

Prior proof releases may have created this exact directory as `0755`. The hardened harness refuses
that historical state before creating a VM. From the repository root, inspect the exact retained-log
directory, confirm it is the caller-owned non-symlink directory shown, and then harden only that target
without reading or removing any retained log:

```bash
(
  set -euo pipefail
  LOG_DIR="$(pwd -P)/.fedora-proof-logs"
  [[ -d "$LOG_DIR" && ! -L "$LOG_DIR" ]]
  [[ "$(readlink -e "$LOG_DIR")" == "$LOG_DIR" ]]
  [[ "$(stat -c '%u' "$LOG_DIR")" == "$EUID" ]]
  stat -c '%A %a %U:%G %n' -- "$LOG_DIR"
  getfacl -cpn -- "$LOG_DIR"
  setfacl -b -k -- "$LOG_DIR"
  chmod 0700 -- "$LOG_DIR"
  [[ "$(getfacl -cpn -- "$LOG_DIR")" == $'user::rwx\ngroup::---\nother::---' ]]
)
```

Kickstart preserves the installed resolver target, replaces it with Anaconda's DHCP resolver state
for the networked `%post`, and restores the original target afterward. Provisioning the SSH
prerequisites therefore does not depend on an incomplete or dangling chroot `resolv.conf`, and the
temporary handoff does not claim resolver ownership after boot. The guest login uses the rendered
one-run SSH public key and a generated random password hash whose
plaintext is immediately discarded; no static login secret exists. The guest explicitly authorizes
both disposable bootstrap `--apply` runs with the literal `yes`, starts Docker, adds only the disposable
guest account to the Docker group, and enters a fresh group session. It then runs setup,
contributor/test doctors, hello-world, a testcontainers Docker-client ping, API unit tests, web
lint/typecheck/tests, contract tooling, dev Compose configuration, the live S stack, stack doctor, and
`down -v`. SELinux is asserted before and after the proof. Generated `.env` secrets exist only inside
the disposable guest.

## Cleanup and safe reruns

The domain is transient. The cleanup trap will destroy a domain only when its exact generated name,
captured UUID, and qcow2 source all match the marker files in its owned `mktemp` directory. It removes
only its enumerated files under `/var/tmp/easysynq-fedora-proof.XXXXXX` and uses an exclusive lock plus
`qemu-img check` before deleting the exact disk. The workdir starts at `0700`; its only named ACL is
qemu `--x`, and the qcow2's only named ACL is qemu `rw-`. POSIX ACL masks may make the group-class mode
bits appear nonzero even though `group::---`; `getfacl -cpn` is the effective-access authority. No
qemu ACL is placed on the SSH keys, rendered Kickstart, markers, UUID, known-hosts file, or repository
manifest. Libvirt/sVirt labels the VM resources normally; the harness does not change SELinux labels.

While a domain is active, the exact disk may temporarily be owned by the resolved qemu uid. After the
exact domain is destroyed, cleanup requires libvirt to restore caller ownership before it revokes the
two lifecycle ACLs, takes the lock, runs `qemu-img check`, or deletes anything. A mismatch, unexpected
file, unrestored owner, active lock, or failed domain stop makes cleanup fail closed and retains the
exact target; it never escalates privileges or broadens the deletion.

On a timeout, signal, or other failed run, the same `0600` log retains the serial/debug stream and
Kickstart's deterministic phase or `%onerror` marker. Before destroying a safely identified active
domain, cleanup also appends a bounded pre-delete diagnostic bundle containing only capped libvirt
state, vCPU, memory, and exact disk statistics. It does not copy the rendered Kickstart, credentials,
private key, qcow2, or unbounded guest logs. The diagnostic evidence therefore survives ordinary exact
disk cleanup; an unsafe domain identity is recorded as unavailable and is never inspected by guess.

An ordinary rerun always receives a new name and directory, so it does not reuse a failed guest. If a
run reports a cleanup refusal, keep the printed log and inspect only the printed VM/disk:

```bash
virsh --connect qemu:///system dominfo '<printed-vm-name>'
virsh --connect qemu:///system domblklist '<printed-vm-name>' --details
qemu-img check '<printed-disk-path>'
```

Do not run a recursive cleanup or delete a disk while it is locked. Resolve the identity/lock cause,
then rerun the proof; ask for review before manually removing any retained artifact.

The two staged ISO copies are intentionally outside lifecycle cleanup. Remove them only after all
proof domains and workdirs for the caller are gone, and only after revalidating the two exact paths,
owners, link counts, hashes, and ACLs:

```bash
(
  set -euo pipefail
  STAGED_INSTALLER="/var/tmp/easysynq-fedora-proof-media-$UID-installer.iso"
  STAGED_WORKSTATION="/var/tmp/easysynq-fedora-proof-media-$UID-workstation.iso"
  QEMU_UID="$(id -u qemu)"
  EXPECTED_MEDIA_ACL="$(printf \
    'user::rw-\nuser:%s:r--\ngroup::---\nmask::r--\nother::---' "$QEMU_UID")"
  for STAGED_MEDIA in "$STAGED_INSTALLER" "$STAGED_WORKSTATION"; do
    test -f "$STAGED_MEDIA"
    test ! -L "$STAGED_MEDIA"
    test "$(readlink -e "$STAGED_MEDIA")" = "$STAGED_MEDIA"
    test "$(stat -c '%u:%h' "$STAGED_MEDIA")" = "$UID:1"
    test "$(getfacl -cpn -- "$STAGED_MEDIA")" = "$EXPECTED_MEDIA_ACL"
  done
  test "$(sha256sum "$STAGED_INSTALLER" | awk '{print $1}')" = "$INSTALLER_SHA256"
  test "$(sha256sum "$STAGED_WORKSTATION" | awk '{print $1}')" = "$WORKSTATION_SHA256"
  getfacl -cpn -- "$STAGED_INSTALLER" "$STAGED_WORKSTATION"
  rm -- "$STAGED_INSTALLER" "$STAGED_WORKSTATION"
)
```

If any check fails, stop and retain both exact files for inspection. Do not substitute a glob,
directory cleanup, recursive removal, sudo, or a broader path.

## PR evidence block

Paste this completed block into the PR. Do not attach the full log, generated secrets, SSH key, disk,
or any site data.

```text
Fedora Workstation proof: PASS | FAIL
Date (UTC): <YYYY-MM-DD>
Evidence commit: <full Git SHA>
Installer media: Fedora-Everything-netinst-x86_64-44-<build>.iso
Installer SHA-256: <64 hex>
Workstation media: Fedora-Workstation-Live-44-<build>.x86_64.iso
Workstation SHA-256: <64 hex>
Guest gates: VARIANT_ID=workstation; VERSION_ID=44; x86_64; SELinux Enforcing
Bootstrap gates: --check PASS; first --apply PASS; second --apply/idempotence PASS
Docker gates: fresh group session PASS; hello-world PASS; testcontainers ping PASS
Repository gates: setup PASS; API unit PASS; web lint/typecheck/test PASS; contracts PASS
Compose gates: S+dev config PASS; live stack doctor PASS; down -v PASS
Cleanup gate: exact transient VM/disk cleanup PASS
Local log reviewed for secret/site-data disclosure: PASS
Full log retained outside Git at: <local path>
```

GitHub-hosted Ubuntu runners are not a trustworthy Fedora Workstation + enforcing-SELinux VM boundary.
The checked-in contract tests validate structure, argument rejection, cleanup refusal, and rendered
Compose semantics; they do not turn an unexecuted VM into acceptance evidence.

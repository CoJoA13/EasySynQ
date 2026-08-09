# Disposable Fedora Workstation acceptance proof

This runbook is the manual PR/release gate for the Fedora developer path. It creates an 80 GiB sparse
qcow2 disk, installs the official Fedora Workstation 44 live payload in a transient libvirt guest,
runs the repository acceptance contract with SELinux enforcing, and removes the exact VM and disk. It
does not replace the fast structural tests.

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

Use a separate Fedora proof host with hardware virtualization enabled, at least 16 GiB of available
RAM, 4 available CPUs, and roughly 100 GiB of free disk space. These virtualization packages are
deliberately not installed by `scripts/bootstrap-fedora-dev.sh`:

```bash
sudo dnf install libvirt-daemon-kvm libvirt-daemon-config-network libvirt-client \
  qemu-kvm virt-install guestfs-tools
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket
sudo usermod -aG libvirt "$USER"
```

Log out and back in after the group change. Then verify the new session and the isolated default NAT
network:

```bash
id -nG | tr ' ' '\n' | grep -Fx libvirt
virsh --connect qemu:///system uri
virsh --connect qemu:///system pool-list --all
virt-install --osinfo list | grep -E '^fedora(44|43)$'
sudo virsh --connect qemu:///system net-start default || true
sudo virsh --connect qemu:///system net-autostart default
virsh --connect qemu:///system net-info default
```

The read-only pool listing exercises the modular libvirt storage service that `virt-install` needs
to validate the install media and disk. The harness runs the same readiness check before it creates
its temporary directory or disk; it does not start host services or weaken permissions.

Do not weaken device, socket, ISO, or directory permissions to make libvirt work. Put both ISO files
in a location the reviewed `qemu:///system` configuration can read, and fix the host's normal libvirt
ACL/SELinux configuration if the preflight reports access denial.

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

Run the acceptance proof by removing only `--validate-only`:

```bash
./scripts/run-fedora-proof.sh \
  --installer-iso "$INSTALLER_ISO" \
  --installer-iso-sha256 "$INSTALLER_SHA256" \
  --workstation-iso "$WORKSTATION_ISO" \
  --workstation-iso-sha256 "$WORKSTATION_SHA256"
```

A fresh login after joining `libvirt` is preferred. If an operator deliberately uses `sg libvirt -c`
instead, put any custom `TMPDIR=/absolute/owned/path` assignment inside the `sg` command string; do not
assume an unexported caller variable crosses the new shell. The printed disk path is the authoritative
cleanup target. This is invocation hygiene, not a way to bypass the harness's ownership checks.

Allow approximately 60–120 minutes, depending on network, CPU, storage, and container-image caches.
The harness prints its unique VM name, exact temporary disk path, and retained evidence log before VM
creation. Logs are written with mode `0600` under `.fedora-proof-logs/` and are ignored by Git through
the repository's `*.log` rule.

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
only its enumerated files and uses an exclusive lock plus `qemu-img check` before deleting the exact
disk. A mismatch, unexpected file, active lock, or failed domain stop makes cleanup fail closed and
leaves the target for inspection; it never broadens the deletion.

An ordinary rerun always receives a new name and directory, so it does not reuse a failed guest. If a
run reports a cleanup refusal, keep the printed log and inspect only the printed VM/disk:

```bash
virsh --connect qemu:///system dominfo '<printed-vm-name>'
virsh --connect qemu:///system domblklist '<printed-vm-name>' --details
qemu-img check '<printed-disk-path>'
```

Do not run a recursive cleanup or delete a disk while it is locked. Resolve the identity/lock cause,
then rerun the proof; ask for review before manually removing any retained artifact.

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

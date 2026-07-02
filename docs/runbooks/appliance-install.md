# Runbook — Hyper-V Appliance Install

> Audience: the Windows Server administrator standing up EasySynQ on the org's own
> infrastructure (D1). The appliance is a self-provisioning Ubuntu VM: two files + one
> PowerShell script; everything after that happens in the browser wizard.
> Built by `infra/appliance/build-appliance.sh`; this runbook covers the SERVER side.

## What you receive

| File | What it is |
|---|---|
| `EasySynQ-appliance.vhdx` | The OS disk — a pristine Ubuntu 24.04 cloud image (unmodified upstream, converted to VHDX) |
| `EasySynQ-seed.iso` | First-boot payload: cloud-init config + the pinned EasySynQ release + provisioning scripts |
| `Install-EasySynQ.ps1` | Creates + boots the Hyper-V VM |

All provisioning is on the seed ISO in plain text — auditable before anything runs.

## Prerequisites

- Windows Server with the **Hyper-V role**, ~10 GB free RAM and ~100 GB disk for the VM.
- An **external** virtual switch (LAN-bridged). The script guides you if none exists —
  an internal/default switch would hide the appliance from workstations.
- Outbound internet from the VM's network for first boot (Ubuntu packages + container
  registries). After provisioning, the appliance runs fully offline.
- Workstations on Windows 10/11 (they resolve the default `easysynq.local` mDNS name
  natively). For AD DNS instead, see *Custom hostname* below.

## Install

1. Copy the three files into one folder on the server.
2. Open an **elevated** PowerShell (right-click PowerShell → *Run as administrator* — the
   Explorer "Run with PowerShell" verb does **not** elevate), then:

   ```powershell
   .\Install-EasySynQ.ps1                       # defaults: 8GB RAM, 4 vCPU, 100GB disk
   .\Install-EasySynQ.ps1 -SwitchName "LAN" -MemoryGB 12
   ```

3. First boot self-provisions (**10–25 min**): installs Docker, builds/pulls the stack,
   generates unique secrets, mints the one-time setup secret. Watch progress on the VM
   console (Hyper-V Manager → Connect): `journalctl -fu easysynq-provision`.

4. When the console shows **`[EasySynQ] READY`**, log in on the console as
   `easysynq` / `EasySynQ-Setup-1` (you are forced to change it), then:

   ```
   cat ~/EASYSYNQ-SETUP.txt
   ```

   That sheet has the app URL, the initial sign-in account (`qmsadmin`, temporary
   password), and the **one-time bootstrap secret** the wizard asks for.

5. From any workstation: **https://easysynq.local** → sign in → paste the secret →
   complete the wizard (org profile → storage + WORM verify → backup drill → finalize).

## The certificate warning (fix once, via GPO)

The appliance serves TLS from Caddy's **internal CA** (no public CA can issue for a
LAN name). Browsers warn until that CA is trusted. One-time fix for the whole fleet:

1. On the VM console:

   ```
   easysynq-status --ca
   ```

   prints the root CA (PEM — a dozen lines). Copy the text from the console into a file
   `easysynq-root-ca.crt` on the server. (The cloud image ships **no SSH password
   auth**, so console copy/paste is the out-of-the-box path; add an SSH key later if
   you prefer.)
2. Distribute via Group Policy: *Computer Configuration → Policies → Windows Settings →
   Security Settings → Public Key Policies → **Trusted Root Certification Authorities*** →
   Import → `easysynq-root-ca.crt`. Workstations pick it up at the next `gpupdate`.

The CA is unique to this appliance (generated at first boot) — trusting it trusts only
this box.

## Connecting the QMS share (for the import)

The import engine reads your existing QMS tree **read-only**; the current mapped-drive
workflow is untouched. Recommended: a dedicated AD service account with read-only NTFS
permissions on the share. On the VM console:

```
sudo easysynq-mount-qms //YOURSERVER/QMS svc-easysynq-ro
```

(prompts for the password; persists across reboots; restarts the two containers that
see the mount). Then start an import run from the app's **Import** section.

## Custom hostname (AD DNS instead of mDNS)

```
sudo easysynq-reconfigure --host easysynq.corp.example.com
```

Rewrites the site/issuer/presign URLs, whitelists the redirect URI in Keycloak, and
recreates the affected containers. Create the matching **A record** in AD DNS first.
Do this **before** real usage starts — changing the OIDC issuer signs everyone out.

## Day-2 helpers (on the VM)

| Command | Purpose |
|---|---|
| `easysynq-status` | Container health + `/readyz` + provisioned version |
| `easysynq-status --remint` | Re-issue the one-time bootstrap secret (expired/lost) |
| `sudo easysynq-create-user <name>` | Add a Keycloak sign-in account (temporary password) |
| `sudo easysynq-mount-qms //srv/share [user]` | Attach the QMS share read-only |
| `sudo easysynq-reconfigure --host <fqdn>` | Move off mDNS to a real DNS name |
| `easysynq-status --ca` | Print the root CA for the GPO trust rollout |
| `easysynq-compose <args>` | Raw `docker compose` with the appliance overlay set — ⚠ never `down` casually: Keycloak has no volume, so `down` erases every sign-in account (`easysynq-create-user` recreates them; QMS data in Postgres/MinIO survives) |

## Troubleshooting

| Symptom | Check |
|---|---|
| Console shows `PROVISIONING FAILED at: <step>` | `journalctl -u easysynq-provision -e`; fix (usually network), then `sudo systemctl restart easysynq-provision` — every step resumes safely |
| `easysynq.local` doesn't resolve from a workstation | mDNS blocked on the subnet. A bare-IP URL will **not** work (TLS + the OIDC issuer are bound to the hostname) — create an AD DNS A record and run `sudo easysynq-reconfigure --host <fqdn>` |
| Sign-in loops / OIDC errors after a hostname change | The issuer must match the URL in the browser — re-run `easysynq-reconfigure --host <fqdn>` and hard-refresh |
| Upload/download fails from the browser | Port **9443** must be reachable (presigned object-store traffic) — check the Windows firewall isn't filtering the VM's ports |
| VM has no IP | External vSwitch wiring — confirm the switch is bound to the LAN-connected adapter |

## Sizing

Defaults match the **S profile** (≤25 users). For the M profile raise the VM to
12–16 GB RAM / 6 vCPU (`-MemoryGB 16 -CpuCount 6`) — profiles are described in
`docs/03-architecture-and-stack.md`. Backups: the wizard's backup step targets a
path **inside the VM** by default; point it at a mount that leaves the host
(the backup runbook covers targets).

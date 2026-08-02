# EasySynQ Production Deployment on `LAB` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up EasySynQ as a production QMS for <ORG>, reachable at
`https://easysynq.example.local`, with the existing `\\DC01\Quality` tree imported and a verified
backup chain reaching the offsite object store.

**Architecture:** Windows 11 Pro host `LAB` keeps its OS and domain membership. Hyper-V runs one
Generation-2 VM on Ubuntu 26.04 LTS, bridged to the LAN through an External vSwitch on the wired NIC
so it holds its own MAC and IP. Inside, the repository's own `bootstrap-ubuntu.sh` + `install.sh`
bring up the standard `s`-profile Compose stack behind Caddy with an internal CA.

**Tech Stack:** Hyper-V (Gen 2, UEFI) · Ubuntu 26.04 LTS "Resolute Raccoon" · Docker CE + Compose
≥ 2.24.4 · PostgreSQL 16 · MinIO · Redis · Keycloak · Caddy · Gotenberg · CIFS/SMB 3.0

**Design spec:** [`docs/superpowers/specs/2026-07-31-lab-production-deployment-design.md`](../specs/2026-07-31-lab-production-deployment-design.md)

---

## Global Constraints

These apply to **every** task. Values are exact; do not substitute.

- **App FQDN:** `easysynq.example.local` — **all lowercase**, fixed. Changing it after go-live
  invalidates the OIDC issuer and signs every user out.

> ⚠ **Use a lowercase FQDN.** This was originally set with the domain label capitalised — e.g.
> `easysynq.EXAMPLE.local`, mirroring how the AD zone is *displayed* — and it **broke login** with
> `Invalid parameter: redirect_uri`. DNS is case-insensitive, so resolution, TLS and `/readyz` all
> worked and the install looked healthy; but **Keycloak matches redirect URIs as a case-sensitive
> string**, and browsers normalise the hostname to lowercase before sending the request. The SPA
> therefore always sent `https://easysynq.example.local/` against a callback registered as
> `https://easysynq.EXAMPLE.local/*`, which could never match. Typing the URL in a different case
> makes no difference — the browser lowercases it regardless.
>
> Fixed 2026-07-31 by lowercasing all 7 host values in `.env` and re-registering the callback.
> Safe at that point because nothing was signed in and no data existed. **The Caddy root CA is
> unchanged**, so any GPO trust rollout already performed stays valid — only the leaf certificate
> was reissued.
- **Domain:** `example.local` · NetBIOS `EXAMPLE` · DC `dc01.example.local` @ `10.0.0.10`
- **VM IP:** **`10.0.0.20`** — currently a DHCP lease bound to the pinned MAC. Owner-accepted
  2026-07-31: proceed on the lease now, and have IT convert it to a reservation (or exclude it from
  the pool) when reachable. Edge-firewall credentials were unavailable and no saved config exists on
  DC01, so the pool range could not be determined and a safe static could not be chosen.
- **VM DNS:** **`10.0.0.10` only.** The edge firewall DHCP supplies `8.8.8.8`/`8.8.4.4`, which cannot
  resolve `example.local` — and both CIFS mounts would fail. Must be overridden post-install.
- **Sizing profile:** `s`
- **TLS mode:** `internal` (Caddy CA — no ADCS exists in this domain)
- **Org timezone:** `America/Chicago` (host reports Central Standard Time)
- **Import source:** `//DC01/Quality` → `/srv/easysynq/import` (**read-only**)
- **Backup destination:** `//DC01/easysynq-backup` → `/srv/easysynq/backup` (**read-write**)
- **Import service account:** `EXAMPLE\svc-easysynq-ro` · **Backup service account:** `EXAMPLE\svc-easysynq-bkp`
- **VM name:** `EasySynQ` · **vSwitch name:** `EasySynQ-LAN` · **Static MAC:** `00:15:5D:00:00:01`
- **VM spec:** 16 GB **static** memory · 8 vCPU · 200 GB dynamic VHDX · Secure Boot template **`MicrosoftUEFICertificateAuthority`** · `AutomaticCheckpointsEnabled = $false`
- **Repo commit:** `4f49c0f` (migration head `0083`)
- **Compose floor:** 2.24.4 — the production overlay uses the fail-closed `!reset` merge tag.
- **Never** run `docker compose down -v` on this stack. It destroys all named volumes, including the vault.

**Ordering constraint (load-bearing):** every CIFS mount must exist *before* any container that binds
it starts. A bind mount never sees a filesystem mounted over its source after container start — the
container captures the empty directory for the entire boot and the import silently reads nothing.

---

## Progress — 2026-07-31

| Task | Status |
|---|---|
| 1. Hyper-V + vSwitch | ✅ done |
| 2. Create the VM | ✅ done — MAC `00:15:5D:00:00:01` |
| 3. Active Directory | 🔶 **INCOMPLETE** — DNS record, both service accounts and both share grants done; **the deny-logon GPO (Step 7) is NOT applied.** Do not mark done until §Go-live gates confirms it. |
| 4. DHCP reservation | ⏸ **deferred to IT** — running on lease `10.0.0.20` |
| 5. Install Ubuntu 26.04 | ✅ done — reboot-validated |
| 6. Host bootstrap | ✅ done — *without* `--qms-share`; mounts moved to Task 7 |
| 7. Mounts + Compose override | ✅ done — import `ro` (write correctly refused), backup `rw` |
| 8. Install EasySynQ | ✅ done — `/readyz` green, all 11 services up, backup volume bind-swapped and **proven end to end** |
| 9. CA trust | ✅ exported + imported into a dedicated GPO |
| 10. Setup wizard | ✅ **`OPERATIONAL`** — org `<ORG_LEGAL_NAME>` / `EXAMPLE` / `America/Chicago`; `the owner` = System Administrator; **restore drill PASS** |
| 11. Import | ⬜ not started |
| 12. Final verification | 🔶 mostly pre-proven (reboot resilience, TLS floor, headers, backup chain) |

### Backup chain — PROVEN with real archives, not probes

Two genuine archives written to `\\DC01\easysynq-backup`:

```
easysynq-backup-20260731T194522Z-8850608c.tar.enc   686 KB   legs: realm_export ABSENT
easysynq-backup-20260731T194637Z-11d64a5e.tar.enc            legs: realm_export present
```

`verified: True, encrypted: True`. Nightly cron `0 2 * * *`. Destination `/var/lib/easysynq/backups`
(container path) → bind → `/srv/easysynq/backup` → CIFS → the volume the whole-disk image covers → the offsite object store.

⚠ **The restore-test drill leaves no archive behind** — it cleans up after itself. An empty backup
directory immediately after the wizard is expected and is *not* evidence the drill failed. Check
`backup_policy.last_restore_test_result` instead (it read `PASS`).

⚠ **`realm_export` failed on the first run and succeeded on the second and on the unattended nightly.**
Keycloak returned 500 on its own token endpoint, so the leg recorded `absent`. It never blocks the
backup by design, and on this Batch-13+ install the `keycloak` schema (100 tables) is inside the
`pg_dump`, so identity recovery does not depend on that leg.

**Cause not established.** An earlier revision of this document asserted that the backup terminates
PostgreSQL backends and severs Keycloak's pool. That is **wrong** and was withdrawn: there is no
`pg_terminate_backend` anywhere under `services/backup/`, and `_capture_and_dump()` opens read
transactions, runs an ordinary `pg_dump`, rolls back and closes its own connections. The
`terminating connection due to administrator command` entries in the log were **container
recreation** happening around the same time — including a `scripts/easysynq` invocation, which
recreates services (see the trap table below) — not the dump.

**If a nightly run reports `realm_export: absent` with no container churn nearby, investigate rather
than dismiss it.** Likely candidates are stale admin credentials or Keycloak availability, and both
persist silently until someone looks.


### Deviations from plan, all recorded in place

1. Bootstrap ran without `--qms-share` (AD wasn't ready); both mounts done by hand in Task 7.
2. **Not in the original design:** the edge firewall DHCP serves `8.8.8.8`/`8.8.4.4`, which cannot resolve
   `example.local`. Overridden to the DC on the VM *and* on LAB. Without it both CIFS mounts fail at boot.
3. **Not in the original design:** subiquity left ~98 GB unallocated; root LV extended to 194 GB.
4. Passwordless sudo enabled for deployment — **remove at go-live**:
   `sudo rm /etc/sudoers.d/90-easysynq-deploy`
5. `/srv/easysynq` was created 0700 by a leaked `umask 077`; corrected to 0755. Containers run as
   root so this never affected the app.
6. **Mixed-case FQDN broke login** — see the Global Constraints warning. Normalised to lowercase.

### Traps hit during first-run setup — all cost real time

| Symptom | Actual cause |
|---|---|
| `Invalid parameter: redirect_uri` | Mixed-case FQDN. Keycloak matches redirect URIs **case-sensitively**; browsers always lowercase the host. Typing the URL differently cannot help. |
| Correct password rejected | Keycloak **brute-force lockout** (`user_temporarily_disabled`) after failed attempts. Clear with `DELETE attack-detection/brute-force/users/{id}`. Looks identical to a wrong password. |
| `Invalid bootstrap secret` | The CLI prints the secret **indented four spaces**; a copy including that whitespace fails as `bootstrap_invalid`, indistinguishable from a genuinely wrong secret. Verify by character count. |
| Containers recreate unexpectedly | **`scripts/easysynq` uses ONLY `compose.yml`** (line 7) — not the profile, production, or site overlay. Every `backup`/`restore`/`upgrade`/`mirror` invocation recreates services whose config differs. Run the CLI via `docker compose <all four -f> run --rm worker uv run python -m easysynq_api.cli.<mod>` instead. |

**Schema names that are easy to get wrong:** `system_config` (holds `bootstrap_secret_hash`,
`bootstrap_expires_at`, `bootstrap_consumed_at`) · `role_assignment` is user→role
(`user_id, role_id, org_id, bound_scope`) while `role_grant` is role→permission · there is **no**
`backup_run` table; run state lives on `backup_policy.last_restore_test_*`.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `infra/compose/compose.lab.yml` | **Create.** Site override redefining the `backup` named volume as a bind onto the CIFS mount. | 7 |
| `/etc/fstab` (VM) | Modify. Adds the read-write backup mount beside the read-only import mount. | 7 |
| `/etc/easysynq-backup.cred` (VM) | Create, `0600` root. Backup share credentials. | 7 |
| `/etc/systemd/system/docker.service.d/10-easysynq-import.conf` (VM) | Modify. Extend `RequiresMountsFor` to cover the backup mount. | 7 |
| `.env` (VM, repo root) | Generated by `install.sh`, then edited for import/backup/alerting/timezone. | 8 |

---

## Task 1: Hyper-V role and External vSwitch

**Runs on:** `LAB`, **elevated** PowerShell (`LAB\colton` is a local admin; UAC will prompt).

**Interfaces:**
- Consumes: a live 1 Gbps link on `Ethernet 3` (Phase 0, owner-supplied).
- Produces: virtual switch `EasySynQ-LAN` bound to `Ethernet 3`.

- [x] **Step 1: Verify the precondition — wired link is up at 1 Gbps** — done 2026-07-31: `Up`, `1 Gbps`, full duplex (first cable had an open pin 1; re-terminated)

```powershell
Get-NetAdapter -Name "Ethernet 3" | Select-Object Name, Status, LinkSpeed
```

Expected: `Status = Up`, `LinkSpeed = 1 Gbps`.
**STOP if this shows `Disconnected` or `100 Mbps`.** 100 Mbps means the cable has only two pairs
crimped — remake it before continuing. Do not proceed on a degraded link.

- [x] **Step 2: Confirm Hyper-V is not already enabled** — done 2026-07-31, was `Disabled`

```powershell
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All | Select-Object FeatureName, State
```

Expected: `State = Disabled`. If already `Enabled`, skip to Step 4.

- [x] **Step 3: Enable Hyper-V, then reboot** — feature enabled 2026-07-31 11:14 (`RESULT: OK`, all sub-features `Enabled`); **reboot still pending**

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart
Restart-Computer
```

The reboot is required — the hypervisor loads at boot. Reconnect after the machine returns.

- [x] **Step 4: Verify Hyper-V is running** — done 2026-07-31 post-reboot: `vmms` Running, `HypervisorPresent: True`, no pre-existing switches or VMs

```powershell
Get-Service vmms | Select-Object Name, Status
Get-Command Get-VM | Select-Object Name, Source
```

Expected: `vmms` `Running`, and `Get-VM` resolves from the `Hyper-V` module.

- [x] **Step 5: Create the External vSwitch** — done 2026-07-31

`-AllowManagementOS $false` keeps the host off this NIC entirely. The host stays on Wi-Fi, so this
does not interrupt the current session and cleanly dedicates the wired NIC to the VM.

```powershell
New-VMSwitch -Name "EasySynQ-LAN" -NetAdapterName "Ethernet 3" -AllowManagementOS $false
```

- [x] **Step 6: Verify the switch** — done: External, `AllowManagementOS: False`, on Intel I226-V; host reverted cleanly to Wi-Fi

```powershell
Get-VMSwitch -Name "EasySynQ-LAN" | Select-Object Name, SwitchType, NetAdapterInterfaceDescription, AllowManagementOS
```

Expected: `SwitchType = External`, adapter description contains `I226-V`, `AllowManagementOS = False`.

---

## Task 2: Create the virtual machine

**Runs on:** `LAB`, elevated PowerShell.

**Interfaces:**
- Consumes: `EasySynQ-LAN` from Task 1.
- Produces: VM `EasySynQ` with static MAC `00:15:5D:00:00:01` (needed for the Task 4 reservation).

- [x] **Step 1: Confirm the chosen MAC is not already in use** — done 2026-07-31, no `00-15-5D` MACs in the ARP cache; `00:15:5D:00:00:01` free

```powershell
arp -a | Select-String "00-15-5d"
```

Expected: no line containing `00-15-5d-00-00-01`. Hyper-V's OUI is `00:15:5D`; a collision is
unlikely here (DC01 is physical) but must be ruled out before pinning a reservation to it.

- [x] **Step 2: Download the Ubuntu 26.04 ISO** — done 2026-07-31, 2.72 GB

Use `curl.exe` (native on Windows 11), **not** `Invoke-WebRequest`: PowerShell 5.1 buffers large
response bodies in memory and is dramatically slower for multi-GB files.

```powershell
New-Item -ItemType Directory -Force -Path C:\HyperV\ISO | Out-Null
curl.exe -L --fail --retry 3 --retry-delay 5 -# `
  -o "C:\HyperV\ISO\ubuntu-26.04-live-server-amd64.iso" `
  "https://releases.ubuntu.com/26.04/ubuntu-26.04-live-server-amd64.iso"
```

- [x] **Step 3: Verify the ISO checksum** — done 2026-07-31, **MATCH** on `dec49008a71f6098d0bcfc822021f4d042d5f2db279e4d75bdd981304f1ca5d9`

⚠ Fetch `SHA256SUMS` with `curl.exe`, not `Invoke-WebRequest`. Canonical serves it without a text
content type, so `.Content` comes back as a **byte array** — string operations against it silently
yield nothing, and the comparison then reports a false MISMATCH against an empty value.

```powershell
$sums = curl.exe -fsL "https://releases.ubuntu.com/26.04/SHA256SUMS"
$line = $sums | Where-Object { $_ -like "*ubuntu-26.04-live-server-amd64.iso*" }
$want = (($line -split '\s+') | Where-Object { $_ -match '^[0-9a-fA-F]{64}$' })
$got  = (Get-FileHash "C:\HyperV\ISO\ubuntu-26.04-live-server-amd64.iso" -Algorithm SHA256).Hash
"published : $($want.ToLower())"
"local     : $($got.ToLower())"
if ($want -and ($want.ToLower() -eq $got.ToLower())) { "RESULT: MATCH" } else { "RESULT: MISMATCH" }
```

Expected: `RESULT: MATCH`.
**STOP if they differ** — re-download rather than installing an unverified image.
An **empty** `published` value means the parse failed, not that the file is bad — fix the parse first.

- [x] **Step 4: Create the VM** — done 2026-07-31

```powershell
New-Item -ItemType Directory -Force -Path C:\HyperV\EasySynQ | Out-Null
New-VM -Name "EasySynQ" -Generation 2 -MemoryStartupBytes 16GB `
  -NewVHDPath "C:\HyperV\EasySynQ\EasySynQ.vhdx" -NewVHDSizeBytes 200GB `
  -SwitchName "EasySynQ-LAN" -Path "C:\HyperV"
```

- [x] **Step 5: Apply the settings that are easy to get wrong** — done 2026-07-31

```powershell
Set-VM -Name "EasySynQ" -StaticMemory -ProcessorCount 8 `
  -AutomaticStartAction Start -AutomaticStartDelay 60 `
  -AutomaticStopAction Shutdown -AutomaticCheckpointsEnabled $false

# Secure Boot: the DEFAULT template is Windows-only and will NOT boot Ubuntu.
Set-VMFirmware -VMName "EasySynQ" -EnableSecureBoot On `
  -SecureBootTemplate MicrosoftUEFICertificateAuthority

# Pin the MAC so the DHCP reservation stays valid across recreation.
Set-VMNetworkAdapter -VMName "EasySynQ" -StaticMacAddress "00155D000001"

# Boot from the installer ISO first.
Add-VMDvdDrive -VMName "EasySynQ" -Path "C:\HyperV\ISO\ubuntu-26.04-live-server-amd64.iso"
Set-VMFirmware -VMName "EasySynQ" -FirstBootDevice (Get-VMDvdDrive -VMName "EasySynQ")
```

- [x] **Step 6: Verify every setting took** — done: 16 GB static, 8 vCPU, Secure Boot `MicrosoftUEFICertificateAuthority`, MAC pinned, checkpoints off, boot order DVD→Network→Disk

```powershell
Get-VM EasySynQ | Select-Object Name, State, ProcessorCount, MemoryStartup, DynamicMemoryEnabled,
                                AutomaticStartAction, AutomaticStopAction, AutomaticCheckpointsEnabled
Get-VMFirmware  -VMName EasySynQ | Select-Object SecureBoot, SecureBootTemplate
Get-VMNetworkAdapter -VMName EasySynQ | Select-Object MacAddress, SwitchName, DynamicMacAddressEnabled
```

Expected: `DynamicMemoryEnabled = False`, `ProcessorCount = 8`, `MemoryStartup = 17179869184`,
`AutomaticStartAction = Start`, `AutomaticCheckpointsEnabled = False`,
`SecureBootTemplate = MicrosoftUEFICertificateAuthority`, `MacAddress = 00155D000001`,
`DynamicMacAddressEnabled = False`.

- [x] **Step 7: Record the MAC for Task 4** — `00:15:5D:00:00:01`, handed over 2026-07-31

The reservation in Task 4 must use `00:15:5D:00:00:01`. Hand this to whoever configures the edge firewall.

---

## Task 3: Active Directory preparation

**Runs on:** `DC01`, elevated PowerShell, as a confirmed Domain Admin.
**Owner:** the site owner. **Depends on:** the reserved IP chosen in Task 4.

**Interfaces:**
- Produces: `easysynq.example.local` A record; `svc-easysynq-ro`; `svc-easysynq-bkp`;
  share `easysynq-backup`; read grants on `Quality`.

- [ ] **Step 1: Create the DNS A record**

```powershell
Add-DnsServerResourceRecordA -Name "easysynq" -ZoneName "example.local" -IPv4Address "10.0.0.20"
```

`-CreatePtr` is omitted deliberately: no reverse lookup zone was found during discovery, and the
switch fails when one is absent. Add a PTR later if a reverse zone is created.

- [ ] **Step 2: Verify resolution FROM A WORKSTATION, not the DC**

Run this on `LAB`, not on DC01 — the DC answers from its own cache and will succeed even when
clients cannot resolve the name.

```powershell
Resolve-DnsName easysynq.example.local
```

Expected: one `A` record pointing at `<RESERVED-IP>`.

- [ ] **Step 3: Create the two service accounts**

```powershell
New-ADUser -Name "svc-easysynq-ro" -SamAccountName "svc-easysynq-ro" `
  -AccountPassword (Read-Host -AsSecureString "Password for svc-easysynq-ro") `
  -PasswordNeverExpires $true -CannotChangePassword $true -Enabled $true `
  -Description "EasySynQ read-only QMS import"

New-ADUser -Name "svc-easysynq-bkp" -SamAccountName "svc-easysynq-bkp" `
  -AccountPassword (Read-Host -AsSecureString "Password for svc-easysynq-bkp") `
  -PasswordNeverExpires $true -CannotChangePassword $true -Enabled $true `
  -Description "EasySynQ backup archive write"
```

**Record both passwords in your password manager now.** Each is typed exactly once, on the VM.

- [ ] **Step 4: Grant read access to the QMS share (BOTH layers)**

Share-level permission still defers to NTFS. Granting one but not the other is the single most common
cause of a mount that succeeds but shows an empty directory.

```powershell
Grant-SmbShareAccess -Name "Quality" -AccountName "EXAMPLE\svc-easysynq-ro" -AccessRight Read -Force

$acl  = Get-Acl "C:\quality"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  "EXAMPLE\svc-easysynq-ro","ReadAndExecute","ContainerInherit,ObjectInherit","None","Allow")
$acl.AddAccessRule($rule)
Set-Acl "C:\quality" $acl
```

- [ ] **Step 5: Create the backup share (write access, scoped to it alone)**

```powershell
New-Item -ItemType Directory -Force -Path "C:\easysynq-backup" | Out-Null

New-SmbShare -Name "easysynq-backup" -Path "C:\easysynq-backup" `
  -FullAccess "EXAMPLE\Domain Admins" -ChangeAccess "EXAMPLE\svc-easysynq-bkp"

$acl  = Get-Acl "C:\easysynq-backup"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  "EXAMPLE\svc-easysynq-bkp","Modify","ContainerInherit,ObjectInherit","None","Allow")
$acl.AddAccessRule($rule)
Set-Acl "C:\easysynq-backup" $acl
```

`C:\easysynq-backup` sits on the volume already covered by the whole-disk image plan (no exclusions), so
archives inherit the existing the offsite object store offsite path.

- [ ] **Step 6: Verify both shares**

```powershell
Get-SmbShareAccess -Name "Quality","easysynq-backup" | Format-Table -AutoSize
(Get-Acl "C:\quality").Access          | Where-Object IdentityReference -like "*svc-easysynq-ro*"
(Get-Acl "C:\easysynq-backup").Access  | Where-Object IdentityReference -like "*svc-easysynq-bkp*"
```

Expected: `svc-easysynq-ro` = Read on `Quality`; `svc-easysynq-bkp` = Change on `easysynq-backup`,
Modify on NTFS.

- [ ] **Step 7: Deny interactive logon to both service accounts**

These are long-lived credentials stored in files on the VM. `Domain Users` membership normally
permits local logon to member machines, and `-CannotChangePassword` does not restrict logon — so an
attacker who reads the credentials file would otherwise gain a usable domain logon.

Create a **new** GPO (do not edit Default Domain Policy), linked at the domain root:

*Computer Configuration → Policies → Windows Settings → Security Settings → Local Policies →
User Rights Assignment*

Add **both** accounts to:
- **Deny log on locally**
- **Deny log on through Remote Desktop Services**

Leave *Deny access to this computer from the network* **alone** — the share mounts need network logon.

- [ ] **Step 8: Apply and verify**

```powershell
gpupdate /force
```

---

## Task 4: DHCP reservation on the edge firewall — **DEFERRED to IT**

**Owner:** the site owner → IT. **Device:** edge firewall at `10.0.0.1` (confirmed by its TLS certificate).
**Status 2026-07-31:** deferred. Firebox credentials unavailable, IT unreachable, and no saved WSM
config exists on DC01 — so the DHCP pool range is unknown and no safe static could be chosen.

**Decision taken instead:** the VM keeps the DHCP lease **`10.0.0.20`** it received on first boot.
The MAC is pinned static, so the lease is stable in practice — DHCP servers reissue the same address
to the same MAC almost indefinitely. Everything downstream is built against `.183`.

**Residual risk:** if the lease expires while the VM is powered off long enough for `.183` to be
reassigned, the certificate and OIDC issuer would no longer match the address behind
`easysynq.example.local`, and sign-in would break. Slim for an always-on host; **closed entirely** by the
ask below.

- [ ] **Step 1: When IT is reachable, request ONE of the following**

Either is sufficient, and neither requires knowing the pool range:

- **Preferred** — a DHCP reservation binding `00:15:5D:00:00:01` → `10.0.0.20`, or
- **Equivalent** — exclude `10.0.0.20` from the DHCP pool so nothing else can be handed it.

⚠ If IT manages this Firebox from a saved Policy Manager configuration, ask them to record the change
in **that** master config — a Web-UI-only edit is overwritten on their next push.

- [ ] **Step 2: Verify the address survived**

```powershell
Resolve-DnsName easysynq.example.local     # must still be 10.0.0.20
Test-Connection 10.0.0.20 -Count 2
```

---

## Task 5: Install Ubuntu 26.04

**Runs on:** the VM console (Hyper-V Manager → Connect).

- [ ] **Step 1: Start the VM and connect**

```powershell
Start-VM -Name EasySynQ
vmconnect.exe localhost EasySynQ
```

- [ ] **Step 2: Run the installer**

- Hostname: `easysynq`
- Username: `easysynq` (this account gets `sudo` and the `docker` group)
- **Install OpenSSH Server: yes** — this is how the remaining tasks are driven without the console.
- Storage: use the entire 200 GB disk, default LVM layout.
- Install **no** snaps.
- Network: leave the **address** on DHCP (the reservation supplies it), but **override DNS to
  `10.0.0.10`** in the installer's network screen.

> ⚠ **Discovered 2026-07-31 — not in the original design.** The edge firewall's DHCP hands out
> **`8.8.8.8, 8.8.4.4`** as DNS. Public resolvers cannot resolve `example.local`, so a VM that accepts
> the DHCP-supplied DNS **cannot resolve `DC01`** — and *both* CIFS mounts (`//DC01/Quality` and
> `//DC01/easysynq-backup`) would fail at boot, silently taking the import and the entire backup
> chain with them. The DC forwards external queries correctly, so DC-only DNS costs nothing.
>
> If the installer's UI makes DNS override awkward, accept DHCP and fix it post-install in netplan:
>
> ```yaml
> # /etc/netplan/50-cloud-init.yaml  — then: sudo netplan apply
> network:
>   version: 2
>   ethernets:
>     eth0:
>       dhcp4: true
>       dhcp4-overrides:
>         use-dns: false
>       nameservers:
>         addresses: [10.0.0.10]
>         search: [example.local]
> ```
>
> Verify before Task 6: `resolvectl query dc01.example.local` must return `10.0.0.10`.

- [x] **Step 3: Reboot and remove the installer media** — done 2026-07-31. The installer had already
ejected the ISO; boot order additionally set to disk-first and **validated by a real reboot**
(boot_id changed, back in 9s, IP/DNS/docker/ufw all persisted).

```powershell
Get-VMDvdDrive -VMName EasySynQ | Set-VMDvdDrive -Path $null
```

- [ ] **Step 4: Verify identity and address**

On the VM console:

```bash
hostnamectl
ip -4 addr show scope global
```

Expected: hostname `easysynq`; the IPv4 address equals the Task 4 reservation.
**STOP if the address differs** — the reservation did not take, and the certificate plus OIDC issuer
will bind to the wrong host.

- [ ] **Step 5: Confirm SSH from `LAB`**

```powershell
ssh easysynq@easysynq.example.local
```

---

## Task 6: Host bootstrap

**Runs on:** the VM, over SSH.

**Interfaces:**
- Consumes: DNS record (Task 3), service account `svc-easysynq-ro` (Task 3).
- Produces: Docker CE, firewall, time sync, and `/srv/easysynq/import` mounted read-only.

- [ ] **Step 1: Clone the repository at the reviewed commit**

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/CoJoA13/EasySynQ.git
cd EasySynQ
git checkout 4f49c0f
git log --oneline -1
```

Expected: `4f49c0f feat(ops): Ubuntu 26.04 host bootstrap + Windows-LAN deployment runbook (#418)`

- [ ] **Step 2: Dry-run the bootstrap and read every command**

```bash
sudo ./scripts/bootstrap-ubuntu.sh \
  --host      easysynq.example.local \
  --profile   s \
  --qms-share //DC01/Quality \
  --qms-user  'EXAMPLE\svc-easysynq-ro' \
  --dry-run
```

Expected: preflight passes (8 vCPU ≥ 2, 16 GB ≥ 8, 200 GB ≥ 50) and every action is printed, none executed.

- [x] **Step 3: Run it for real** — done 2026-07-31, but **WITHOUT `--qms-share`/`--qms-user`**

> **Deviation.** Task 3 (AD) had not run, so `svc-easysynq-ro` did not exist and the mount would have
> failed. Bootstrap was run without the share arguments to get Docker, the firewall, time sync,
> sleep-masking and the hostname in place. **Both** CIFS mounts (import *and* backup) are therefore
> added by hand in Task 7, which already had to do the backup one. The `docker.service` drop-in that
> bootstrap normally writes for the import mount was also skipped — Task 7 Step 4 writes it covering
> both paths, so no coverage is lost. Verify `findmnt /srv/easysynq/import` before Task 8.

```bash
sudo ./scripts/bootstrap-ubuntu.sh \
  --host      easysynq.example.local \
  --profile   s \
  --qms-share //DC01/Quality \
  --qms-user  'EXAMPLE\svc-easysynq-ro'
```

It prompts once for the `svc-easysynq-ro` password and writes it to `0600` root-owned
`/etc/easysynq-qms.cred`. If mistyped, re-run with `--reset-credentials` — a stored file is
otherwise reused as-is.

- [ ] **Step 4: Log out and back in for the `docker` group**

```bash
exit
```

Then reconnect. Without a fresh session every `docker` command fails with a permission error.

- [ ] **Step 5: Verify the host**

```bash
docker compose version                 # >= 2.24.4
systemctl is-enabled docker            # enabled
systemctl is-enabled sleep.target      # masked   <- see note
timedatectl show -p NTPSynchronized --value   # yes
hostname -f                            # easysynq.example.local
findmnt /srv/easysynq/import           # present, flagged ro
ls /srv/easysynq/import | head
```

⚠ **`systemctl is-masked` does not exist on systemd 259** (Ubuntu 26.04) — it errors with
`Unknown command verb 'is-masked'`, which reads like sleep was never masked when in fact it was.
`docs/runbooks/install-ubuntu-server.md` §7 and §8 both carry this stale command. Use
`systemctl is-enabled <target>` (returns `masked`) or check for the `/dev/null` symlink in
`/etc/systemd/system/`.

Expected: the last command lists real QMS content. An **empty** listing means the share or NTFS grant
is missing — return to Task 3 Step 4. Both layers are required.

---

## Task 7: Backup mount and Compose override

**Runs on:** the VM. **This task is the reason backups leave the box.** Without it, `install.sh`
puts archives in a Docker named volume inside the VM, which is not a backup.

**Interfaces:**
- Consumes: `svc-easysynq-bkp` and share `easysynq-backup` (Task 3 Step 5).
- Produces: `/srv/easysynq/backup` mounted read-write; `infra/compose/compose.lab.yml` redefining the
  `backup` volume as a bind onto it.

- [ ] **Step 0: Create the IMPORT mount — bootstrap did not**

> ⚠ **Do not skip this.** Task 6 ran without `--qms-share`/`--qms-user`, so bootstrap created no
> import credentials file, no fstab entry and no mount. Without this step `/srv/easysynq/import`
> stays an ordinary empty directory, the worker binds *that*, and the import finds nothing while
> reporting success. (Alternative: re-run `bootstrap-ubuntu.sh` with the QMS arguments; these steps
> reproduce exactly what it would have written.)

```bash
sudo install -d -m 0755 /srv/easysynq/import
sudo install -m 0600 /dev/null /etc/easysynq-qms.cred
sudo tee /etc/easysynq-qms.cred >/dev/null <<'EOF'
username=svc-easysynq-ro
password=REPLACE_WITH_THE_READ_ONLY_ACCOUNT_PASSWORD
domain=EXAMPLE
EOF
sudo chmod 0600 /etc/easysynq-qms.cred && sudo chown root:root /etc/easysynq-qms.cred
sudo nano /etc/easysynq-qms.cred          # put the real password in

echo '//DC01/Quality /srv/easysynq/import cifs ro,_netdev,vers=3.0,noserverino,credentials=/etc/easysynq-qms.cred,uid=0,gid=0 0 0' \
  | sudo tee -a /etc/fstab
sudo mount /srv/easysynq/import
```

Verify — and check **both** directions, because a mount can succeed and still be wrong:

```bash
findmnt /srv/easysynq/import                        # present, flagged ro
ls /srv/easysynq/import | head                      # non-empty
sudo touch /srv/easysynq/import/.w 2>/dev/null && echo "⚠ WRITABLE — ro flag did not apply" || echo "correctly read-only"
```

An **empty** listing means a share-level or NTFS grant is missing for `svc-easysynq-ro` — Task 3
requires both layers. A **successful write** means the `ro` flag did not apply, and the import
engine could modify the master QMS tree.

- [ ] **Step 1: Create the backup mountpoint and credentials file**

```bash
sudo install -d -m 0755 /srv/easysynq/backup
sudo install -m 0600 /dev/null /etc/easysynq-backup.cred
sudo tee /etc/easysynq-backup.cred >/dev/null <<'EOF'
username=EXAMPLE\svc-easysynq-bkp
password=REPLACE_WITH_THE_PASSWORD_FROM_TASK_3
EOF
sudo chmod 0600 /etc/easysynq-backup.cred
sudo chown root:root /etc/easysynq-backup.cred
```

Then edit the password in place:

```bash
sudo nano /etc/easysynq-backup.cred
```

- [ ] **Step 2: Add the fstab entry**

Mirrors the read-only import line the bootstrap wrote, but `rw`. `_netdev` defers the mount until
networking is up so a reboot does not strand it.

```bash
echo '//DC01/easysynq-backup /srv/easysynq/backup cifs rw,_netdev,vers=3.0,noserverino,credentials=/etc/easysynq-backup.cred,uid=0,gid=0 0 0' \
  | sudo tee -a /etc/fstab
sudo mount /srv/easysynq/backup
```

`uid=0,gid=0` is correct: the api/worker images carry no `USER` directive, so containers run as root.

- [ ] **Step 3: Verify the mount is writable**

```bash
findmnt /srv/easysynq/backup
sudo touch /srv/easysynq/backup/.write-test && sudo rm /srv/easysynq/backup/.write-test && echo "WRITE OK"
```

Expected: `WRITE OK`. A failure here means the `Change`/`Modify` grants in Task 3 Step 5 are missing.

- [ ] **Step 4: Extend the Docker ordering drop-in to cover BOTH mounts**

The bootstrap wrote this file listing only the import path. `_netdev` orders the mount relative to
networking but does **not** make Docker wait for it, and the services are `restart: unless-stopped`.

```bash
sudo tee /etc/systemd/system/docker.service.d/10-easysynq-import.conf >/dev/null <<'EOF'
[Unit]
# EasySynQ: both CIFS bind sources must be mounted before Docker starts any container that
# binds them, or the container captures the empty underlying directory for the whole boot.
RequiresMountsFor=/srv/easysynq/import /srv/easysynq/backup
EOF
sudo systemctl daemon-reload
```

- [ ] **Step 5: Verify systemd recorded both**

```bash
systemctl show docker.service -p RequiresMountsFor
```

Expected output contains **both** `/srv/easysynq/import` and `/srv/easysynq/backup`.

- [ ] **Step 6: Create the site Compose override**

```bash
cat > ~/EasySynQ/infra/compose/compose.lab.yml <<'EOF'
# Site override — LAB / example.local.
#
# The base stack backs up into a NAMED VOLUME inside the VM, which is not a backup.
# Redefine that volume as a bind onto the CIFS share on DC01, so archives land on the
# volume already covered by the whole-disk image plan and reach the offsite object store.
#
# Archives are AES-256-GCM encrypted BEFORE leaving this VM, so neither the MSP nor
# the offsite object store can read them. Writing directly to the share (rather than syncing later)
# keeps failures inside EasySynQ's own BACKUP_FAILED alarm path.
volumes:
  backup:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/easysynq/backup
EOF
```

- [ ] **Step 7: Verify the override parses and resolves as intended**

```bash
cd ~/EasySynQ
docker compose -f infra/compose/compose.yml \
               -f infra/compose/compose.s.yml \
               -f infra/compose/compose.production.yml \
               -f infra/compose/compose.lab.yml config --volumes
```

Expected: no parse error, and `backup` is listed.

---

## Task 8: Install EasySynQ

**Runs on:** the VM.

- [ ] **Step 1: Run the installer**

```bash
cd ~/EasySynQ
./scripts/install.sh s --host easysynq.example.local --tls internal
```

`--tls internal` is correct: no public CA can issue for a `.local` name, and this domain has no ADCS.
The installer generates a `0600` `.env`, derives every browser-facing origin from the one hostname,
runs migrations, authorizes the SPA callback in Keycloak, and blocks until `/readyz` is green.

- [ ] **Step 2: ESCROW THE BACKUP KEY — do this now, not later**

```bash
grep '^BACKUP_ENCRYPTION_KEY=' .env
```

Copy the value into the password manager **immediately**, stored separately from the backups.
It exists **only** in this file. If it is lost, every archive in the offsite object store is permanently
unrecoverable. This is the single most consequential step in the plan.

- [ ] **Step 3: Point the import, backup and timezone settings at the real values**

```bash
sed -i 's|^IMPORT_SOURCE_PATH=.*|IMPORT_SOURCE_PATH=/srv/easysynq/import|' .env
sed -i 's|^BACKUP_PATH=.*|BACKUP_PATH=/var/lib/easysynq/backups|'          .env
sed -i 's|^EASYSYNQ_ORG_TIMEZONE=.*|EASYSYNQ_ORG_TIMEZONE=America/Chicago|' .env
grep -E '^(IMPORT_SOURCE_PATH|BACKUP_PATH|EASYSYNQ_ORG_TIMEZONE)=' .env
```

`BACKUP_PATH` stays at the container path — Task 7's override is what redirects that volume onto the
CIFS share. Do not point `BACKUP_PATH` at a host path.

- [ ] **Step 4: Replace the plain `backup` volume with the bind-backed one**

> **Why this step exists.** `install.sh` runs its own `docker compose up` **without**
> `compose.lab.yml`, so it has already created `backup` as an ordinary named volume living inside the
> VM. Docker will **not** retrofit `driver_opts` onto an existing volume — re-running with the
> override would silently reuse the plain volume and every archive would stay on this box. The volume
> is **empty** at this point (no backup has run yet; the wizard's drill is Task 10), so dropping it is safe.

Identify it, prove it is empty, then remove it:

```bash
cd ~/EasySynQ
docker volume ls --format '{{.Name}}' | grep backup      # e.g. easysynq_backup
VOL=$(docker volume ls --format '{{.Name}}' | grep -E '_backup$' | head -1)
echo "volume: $VOL"
sudo ls -la "$(docker volume inspect "$VOL" --format '{{.Mountpoint}}')"
```

Expected: the directory is empty (only `.` and `..`).
**STOP if it contains any `*.tar.enc`** — that would mean a backup already ran; copy it out first.

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml \
  down
docker volume rm "$VOL"
```

`down` without `-v` stops containers and preserves every other named volume — including the vault.
**Never add `-v` here.**

- [ ] **Step 5: Bring the stack up with the site override included**

Every Compose invocation from here on must include all four `-f` files, in this order.

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml \
  -f infra/compose/compose.lab.yml \
  up -d
```

- [ ] **Step 6: Verify the backup volume really resolves to the share**

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \
  exec -T worker sh -c 'touch /var/lib/easysynq/backups/.probe && echo CONTAINER-WRITE-OK'
ls -la /srv/easysynq/backup/.probe && sudo rm /srv/easysynq/backup/.probe
```

Expected: `CONTAINER-WRITE-OK`, and `.probe` visible on the **host** mount. If the file does not
appear at `/srv/easysynq/backup`, the override did not apply — backups would silently stay in the VM.

- [ ] **Step 7: Verify readiness**

```bash
curl -sk https://easysynq.example.local/readyz
```

Expected: a green/ready response.

---

## Task 9: Distribute the Caddy root CA

**Interfaces:** consumes the running proxy (Task 8); produces domain-wide trust for the internal CA.

> **Understand what this grants.** This root, once in the domain trust store, is trusted by every
> workstation for **any hostname it signs** — not only `easysynq.example.local`. Its private key lives in
> the Caddy volume on this VM, so `LAB`'s disk and root access become part of the domain's
> certificate trust boundary. This was accepted as risk **R-1** because no ADCS exists.

- [ ] **Step 1: Export the root certificate (on the VM)**

```bash
cd ~/EasySynQ
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \
  exec -T proxy cat /data/caddy/pki/authorities/local/root.crt > easysynq-root-ca.crt
head -1 easysynq-root-ca.crt
```

Expected: `-----BEGIN CERTIFICATE-----`

- [ ] **Step 2: Copy it to `LAB`**

```powershell
scp easysynq@easysynq.example.local:~/EasySynQ/easysynq-root-ca.crt C:\HyperV\easysynq-root-ca.crt
```

- [ ] **Step 3: Deploy via a NEW, dedicated GPO**

Create a GPO named `EasySynQ Root CA Trust` — **do not** edit Default Domain Policy. Scoping it
separately means the trust can be withdrawn in one action if the CA is ever compromised.

*Computer Configuration → Policies → Windows Settings → Security Settings → Public Key Policies →
Trusted Root Certification Authorities* → right-click → **Import** → select the file.

- [ ] **Step 4: Verify on a workstation**

```powershell
gpupdate /force
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Caddy*'
```

Expected: exactly one certificate. It must be in **LocalMachine\Root**, not CurrentUser.

---

## Task 10: First-run setup wizard

- [ ] **Step 1: Create the administrator sign-in identity in Keycloak**

The bootstrap secret grants EasySynQ administration but does **not** create a Keycloak password.
Create the intended admin identity first.

- [ ] **Step 2: Mint the one-time bootstrap secret (on the VM)**

```bash
cd ~/EasySynQ
docker compose --env-file .env \n  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \n  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \n  run --rm api uv run python -m easysynq_api.cli.setup mint-bootstrap
```

- [ ] **Step 3: Complete the wizard at `https://easysynq.example.local/setup`**

In order — each is a gate:

1. Paste the bootstrap secret → become the first **System Administrator**.
2. **Organization** — legal name, short code `EXAMPLE`, timezone **`America/Chicago`**.
3. **Storage** — *Verify storage* (WORM probe, gate **G-B**). The `documents` bucket must be
   object-lock-enabled; see [minio-object-lock-prereq.md](../../runbooks/minio-object-lock-prereq.md).
4. **Backup** — destination `/var/lib/easysynq/backups`, then *Run backup + restore-test drill*.
   **Finalize is blocked until this PASSES** (gate **G-C**). "Configured but unverified" does not count.
5. **Authentication** — pick a method, acknowledge MFA, *Verify authentication* (gate **G-D**).
6. **Finalize** → state becomes `OPERATIONAL` and the 423 setup latch lifts.

- [ ] **Step 4: Confirm the drill archive actually landed on DC01**

```bash
ls -la /srv/easysynq/backup/
```

⚠ **Do NOT expect the drill to leave an archive.** `backup restore-test` cleans up after itself, so
an **empty directory here is the normal result of a PASS**. Treating it as a failed backup chain
sends you diagnosing a working system. Check the recorded verdict instead:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \
  exec -T postgres psql -U easysynq -d easysynq \
  -c "SELECT last_restore_test_at, last_restore_test_result FROM backup_policy;"
```

Expected: `PASS`.

To prove the chain writes through to the share, run a **durable** backup — that one does leave a file:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \
  run --rm worker uv run python -m easysynq_api.cli.backup run
ls -la /srv/easysynq/backup/
```

Expected: at least one `*.tar.enc`. This proves the whole chain end to end — EasySynQ wrote an
encrypted archive through the CIFS mount onto the volume the whole-disk image plan sweeps to the offsite object store.

- [ ] **Step 5: Configure out-of-band alerting**

Until this is set, a failed nightly backup notifies **only in-app** — through the database that may
itself be the failure. Requires the SMTP relay identified in spec §9 item 1.

```bash
sed -i 's|^OPS_ALERT_CHANNELS=.*|OPS_ALERT_CHANNELS=smtp|' .env
sed -i 's|^OPS_ALERT_SMTP_TO=.*|OPS_ALERT_SMTP_TO=<operator-mailbox>|' .env
```

Then recreate `worker` and `beat` using the four-file Compose invocation from Task 8 Step 5.

- [ ] **Step 6: Invite the users**

Sign in as System Administrator → `/admin/users`. Paste each Keycloak `sub`; users go
`INVITED` → `ACTIVE` on first login. Assign seeded roles. Expect ~5.

---

## Task 11: Import the QMS tree

- [ ] **Step 1: Confirm the worker sees the source**

```bash
cd ~/EasySynQ
docker compose --env-file .env \
  -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml -f infra/compose/compose.lab.yml \
  exec -T worker ls /srv/import/source | head
```

Expected: real filenames. **An empty listing means the containers started before the mount existed** —
recreate `api` and `worker` with the same four-file invocation and re-check.

- [ ] **Step 2: Start an import run** from the app's **Import** section.

- [ ] **Step 3: Review the classification queue**

**Actual tree, surveyed 2026-07-31 through the live mount — 252 files, 56 MB:**

```
/srv/easysynq/import/
├── QMS/        195 files   137 .docx · 15 .xlsx · 4 .html · 2 .vsdx · 3 .pdf
│               organised by ISO 9001 clause: "1.0 Scope" … "10.0 Improvement",
│               plus "Quality Manual", "Standalone Documents",
│               "REFERENCE WORKING DOCUMENTS", "files"
├── QMS_DATA/    56 files    25 .pdf · 9 .xlsx · 9 .png · 7 .docx
│               same clause folders, but the CONTENT is dated evidence:
│               Calibration and Certifications/{Cross Calibration,KING Testers,
│               Crane Inspections}/{2023,2024,2025}/…
└── QMS.zip   18.5 MB       an archive snapshot, not a controlled document
```

⚠ **`QMS/` and `QMS_DATA/` are different KINDS, not duplicates** (basename overlap is nil).
This is the maintained-versus-retained split in ISO 9001 terms:

- **`QMS/` → `kind = DOCUMENT`.** Procedures, work instructions, forms, the Quality Manual. These
  belong in the 7-state lifecycle.
- **`QMS_DATA/` → `kind = RECORD`.** Calibration certificates, crane inspections, King Tester
  certifications — evidence that an activity happened on a date. These belong under retention and
  disposition, **not** a revision-and-approval workflow.

Kind is always human-confirmed at import (stakeholder-locked), so this is a review decision, not
something the classifier will get right unaided. Confirming `QMS_DATA` items as DOCUMENT would put
calibration certificates into an approval workflow they do not belong in.

**Exclusions to apply:**

| Item | Count | Why |
|---|---|---|
| `Thumbs.db` | 37 | Windows Explorer thumbnail caches — pure noise |
| `QMS.zip` | 1 | An 18.5 MB archive snapshot, not a controlled document |
| `*.xlsx#` | 1 | Excel lock artifact |
| `*.pdc` | 1 | Proprietary; confirm before deciding |

37 `Thumbs.db` + a lock artifact is exactly the **252 vs 215** gap between the live count and the
earlier Windows-side scan (`Get-ChildItem -Recurse -File` skips hidden/system files without `-Force`).

The clause-folder structure is a genuine asset: it maps onto EasySynQ's clause spine, so folder path
is strong evidence for clause mapping rather than a flat pile needing manual assignment.

- [ ] **Step 4: Commit the run** once the checklist is satisfied.

---

## Task 12: Final verification

- [ ] **Step 1: Host checks (on the VM)**

```bash
docker compose version
systemctl is-enabled docker
systemctl is-enabled sleep.target             # "masked" - NOT `is-masked`, see Task 6
timedatectl show -p NTPSynchronized --value
ufw status
hostname -f
findmnt /srv/easysynq/import
findmnt /srv/easysynq/backup
```

Expected: Compose ≥ 2.24.4 · docker enabled · sleep masked · clock synchronized · ufw allows
OpenSSH/80/443/9443 · FQDN `easysynq.example.local` · import mount `ro` · backup mount `rw`.

- [ ] **Step 2: Release-time security check**

```bash
curl -sI https://easysynq.example.local/ | grep -iE 'content-security|strict-transport|referrer|x-content-type|permissions-policy'
openssl s_client -connect easysynq.example.local:443 -tls1_1 </dev/null
```

Expected: all five headers present; the TLS 1.1 handshake **refused** (1.2 floor).

- [ ] **Step 3: Browser checks from a domain workstation**

- SPA loads at `https://easysynq.example.local` with **no certificate warning**
- A Keycloak login round-trips under the strict CSP
- A document upload **and** download both succeed — the download exercises port **9443**

- [ ] **Step 4: Reboot resilience — the test most deployments skip**

```powershell
Restart-Computer
```

After `LAB` returns, without touching anything:

```bash
findmnt /srv/easysynq/import
findmnt /srv/easysynq/backup
docker ps --format '{{.Names}}\t{{.Status}}'
curl -sk https://easysynq.example.local/readyz
```

Expected: VM auto-started, **both** mounts present, all containers up, `/readyz` green.
A missing mount here means the Task 7 Step 4 drop-in is wrong — and imports would silently read an
empty tree for that entire boot.

- [ ] **Step 5: Confirm escrow before declaring done**

Verify `BACKUP_ENCRYPTION_KEY` is in the password manager and that the value there matches `.env`.

---

## Go-live gates — two security controls the build deliberately left open

⚠ **The install is OPERATIONAL with both of these still open.** Neither breaks anything, neither
fails a test, and nothing else in this plan closes them — which is exactly why they need to be gates
rather than notes. Do not consider the deployment finished until both verify.

- [ ] **G-1: Remove the deployment sudo rule.** Passwordless sudo was enabled to drive provisioning
      non-interactively. While it stands, anyone holding the SSH private key on `LAB` has root on the
      QMS host without a password — and that key is stored unencrypted.

```bash
ssh easysynq@<vm-ip> "sudo rm -f /etc/sudoers.d/90-easysynq-deploy"
# Fail-closed verification: this MUST now prompt or refuse.
ssh -o BatchMode=yes easysynq@<vm-ip> "sudo -n true" \
  && echo "✘ STILL PASSWORDLESS — the file was not removed" \
  || echo "✔ password now required"
```

- [ ] **G-2: Apply and verify the deny-logon GPO** (Task 3 Step 7). Both service-account passwords
      live in `0600` files on the VM. Without this, reading either file yields a credential usable
      for interactive or RDP logon across every domain machine — the mount only ever needs *network*
      logon.

Create a **new** GPO (not Default Domain Policy) adding `svc-easysynq-ro` and `svc-easysynq-bkp` to
*Deny log on locally* **and** *Deny log on through Remote Desktop Services*. Leave *Deny access to
this computer from the network* alone. Then verify it actually applied, rather than assuming:

```powershell
gpupdate /force
gpresult /scope computer /r | Select-String "EasySynQ"     # the GPO must be listed as Applied
```

Only when both boxes are ticked may Task 3 be marked done.

---

## Post-go-live (tracked, not waived)

1. **Audit-checkpoint anchor (R13).** The install reports itself **NOT tamper-evident** until an
   off-host anchor is configured. Non-blocking for go-live, but real for an ISO 9001 audit trail —
   the anchor must live where this host's operator cannot rewrite it. Schedule it.
2. **Confirm the image backup swept the new folder** after the first image run. `C:\easysynq-backup` is on the
   imaged volume with no exclusions, so coverage is expected by construction — verify rather than assume.
3. **Consider raising the image backup to daily.** Current recurrence is Weekly + Monthly, so offsite RPO is up
   to a week (spec risk R-3).
4. **Review remote-access surface on DC01** (spec risk R-2) — the remote-access and RMM agents
   all reach the box holding source documents and backups.

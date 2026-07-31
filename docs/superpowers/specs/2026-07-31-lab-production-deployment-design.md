# Design — EasySynQ production deployment on `LAB` (AHT.local)

> **Status:** approved design, pending implementation.
> **Date:** 2026-07-31 · **Repo commit at design time:** `4f49c0f` · **Migration head:** `0083`
> **Site:** American Heat Treating (`AHT.local`), single-site heat-treat shop, ~5 QMS users.
>
> This is a site-specific deployment design. The generic procedure lives in
> [`docs/runbooks/install-ubuntu-server.md`](../../runbooks/install-ubuntu-server.md); this document
> records what is *different* here, the values discovered on site, and the trade-offs accepted.

---

## 1. Why this document exists

The existing runbooks assume a bare Ubuntu host on a LAN with a separate Windows file server. Neither
assumption holds at AHT:

- the intended host is a **domain-joined Windows 11 Pro workstation**, not a Linux box;
- there is **no file server** — `AHTDC` is simultaneously domain controller, file server, and SQL host;
- the deployment target must remain manageable by the existing IT provider.

Every fact below was **verified on the machine**, not taken from documentation. Several published
notes (notably `.claude/rules/windows-dev.md`) describe a *different* workstation and do not apply.

---

## 2. Discovered environment

### 2.1 Intended host — `LAB`

| Property | Value |
|---|---|
| Hostname / domain | `LAB` · joined to `AHT.local` |
| OS | Windows 11 Pro, build 26200 |
| CPU | AMD Ryzen 7 7735HS — 16 logical processors, SLAT + virtualization enabled in firmware |
| RAM | 29.7 GB |
| Disk | `C:` 951.8 GB total, 885.8 GB free |
| Chassis | Desktop (type 3), no battery; AC sleep timeout already `0` |
| Interactive account | `LAB\colton` — **local** account (SID `…-1002`), NTLM, only profile on the box |
| Installed tooling | Git only. **No Docker, no `bash` on PATH, no `uv`/`node`/`just`/`gh`.** No `.env`. |

### 2.2 Network

| Property | Value |
|---|---|
| Live link | `Wi-Fi 2` — 10.10.40.94/24, DHCP, 573 Mbps |
| Wired NIC | `Ethernet 3` — Intel I226-V gigabit, **currently `Disconnected`** |
| Gateway / DHCP server | `10.10.40.1` |
| Gateway identity | **WatchGuard Firebox** — confirmed by TLS cert `CN=Fireware web CA, O=WatchGuard` on :8080; WSM open on 4117/4118 |
| DNS | `10.10.40.222` (= `AHTDC`) |
| Also present | Ubiquiti UniFi controller |

### 2.3 Active Directory

| Property | Value |
|---|---|
| Domain | `AHT.local` · NetBIOS `AHT` · functional level `Windows2016Domain` |
| Domain controller | `AHTDC.AHT.local` @ 10.10.40.222 — Windows Server 2019 Standard, sole server |
| DNS zone | `AHT.local`, Primary, AD-integrated |
| `easysynq` A record | does not exist — name is free |
| Enterprise CA (ADCS) | **none published** |
| GPOs | only `Default Domain Policy`, `Default Domain Controllers Policy` |
| Enabled users | 9 (`Administrator`, `Chuck`, `Colton`, `Jennifer`, `Krystina`, `Maintenance`, `Michael`, `Production`, `Tyler`) |
| Service accounts | none — no `svc-*` exists yet |
| Operator rights | `AHT\Colton` ∈ **Domain Admins** + `BUILTIN\Administrators`; elevated on the DC |

### 2.4 The QMS source tree

`\\AHTDC\Quality` → `C:\quality` — **215 files, 53.4 MB**, newest 2026-07-28.

| Type | Count | Type | Count |
|---|---|---|---|
| `.docx` | 144 | `.html` | 4 |
| `.pdf` | 28 | `.vsdx` | 2 |
| `.xlsx` | 24 | `.pdc` / `.zip` / `.xlsm` / `.xlsx#` | 1 each |
| `.png` | 9 | | |

`AHTDC` also shares `Data`, `finances`, `Maintenance`, `SSI`, `ann-htsw`. Only `Quality` is in scope.

### 2.5 Existing backup posture

| Product | Covers | Schedule | Destination |
|---|---|---|---|
| **MSP360** ("Online Backup 7.9.3", Network Technology Inc) | `BackupDiskImagePlan` — whole 1.73 TB RAID-10 disk, all 4 partitions, **`<ExcludeRules />` empty** | `Weekly` + `Monthly` | **Backblaze B2**, bucket `Americanheat` |
| **SQLBackupAndFTP** 12.8.2 | SQL Server 2022 databases only | Nightly 00:08, ~2 min, succeeding | (not inspected) |

**`C:\quality` is therefore covered** by the image plan — implicitly, via whole-volume selection.

**Management/remote-access agents on `AHTDC`:** Atera RMM, Splashtop Streamer (installed 2026-07-22),
GoTo Opener, a TeamViewer updater task, Webroot SecureAnywhere.

---

## 3. Decision — hosting model

**Windows 11 remains the host OS. EasySynQ runs in a Generation-2 Hyper-V VM on Ubuntu 26.04 LTS,
provisioned by the repository's own `bootstrap-ubuntu.sh` + `install.sh`.**

### Rationale

`LAB` keeps its domain membership, IT manageability, and remote access; the VM is bridged onto the LAN
via an External vSwitch, so it holds its own MAC and IP and appears to workstations as an ordinary
host. This uses the newest and best-maintained install path in the repository, rather than a
Windows-native shape the project does not support.

### Alternatives considered and rejected

| Option | Rejected because |
|---|---|
| **Bare-metal Ubuntu** (wipe Windows) | Simplest technically, but destroys a managed domain workstation, needs USB media, and removes the machine from IT's control. |
| **Prebuilt Hyper-V appliance** (`Install-EasySynQ.ps1`) | Artifacts are not built (`infra/appliance/dist/` empty) and `build-appliance.sh` needs `bash` + `qemu-img` + `uv`, none present. Also ships Ubuntu 24.04, behind the 26.04 the current runbook targets. |
| **Docker Desktop on Windows** | Documented as the *dev* path only. Requires an interactive logged-in session, carries business licensing, and its bind-mount/path semantics differ from the production overlay. Would mean inventing an unsupported deployment shape. |

### Verified feasibility

- Ubuntu 26.04 LTS ("Resolute Raccoon") — `ubuntu-26.04-live-server-amd64.iso` is published.
- Docker CE publishes a `resolute` apt suite, so `bootstrap-ubuntu.sh`'s repo probe will pass.
- `bootstrap-ubuntu.sh` treats a non-26.04 version as a **warning**, not a hard failure.
- Profile `s` preflight floors are 2 CPU / 8 GB RAM / 50 GB disk — the VM clears all three.

---

## 4. Target architecture

```
Workstations ──┐
               ├─► https://easysynq.AHT.local        (Caddy :443 — SPA, API, Keycloak)
               └─► https://easysynq.AHT.local:9443   (Caddy — presigned MinIO/S3 origin)
                          │
                   [ WatchGuard Firebox 10.10.40.1 — gateway + DHCP ]
                          │
        ┌─────────────────┴──────────────────┐
        │                                    │
   AHTDC (DC / file / SQL)            LAB (Windows 11 Pro)
   ├── \\AHTDC\Quality  (RO) ────────► Hyper-V External vSwitch → "Ethernet 3"
   └── \\AHTDC\easysynq-backup (RW) ◄─┐        │
                                      │   ┌────▼──────────────────────────┐
                                      │   │ VM "EasySynQ" — Ubuntu 26.04  │
                                      │   │  Docker Compose, profile s:   │
                                      └───┤  caddy · api · worker · beat  │
                                          │  postgres · minio · redis     │
                                          │  keycloak · gotenberg         │
                                          └───────────────────────────────┘
```

### VM specification

| Setting | Value | Why |
|---|---|---|
| Generation | 2 (UEFI) | Required for modern Ubuntu |
| Secure Boot template | **Microsoft UEFI Certificate Authority** | The default Windows template will not boot Linux |
| Memory | **16 GB static** (dynamic memory OFF) | PostgreSQL and Keycloak behave badly under balloon pressure |
| vCPU | 8 | Leaves 8 threads for the host |
| Disk | 200 GB dynamic VHDX | Grows on demand; host has 886 GB free |
| `AutomaticStartAction` | `Start` (with delay) | Must return after a Windows Update reboot |
| `AutomaticStopAction` | `Shutdown` | Clean guest shutdown, not save-state |
| `AutomaticCheckpointsEnabled` | **`$false`** | Checkpointing a live database VM risks corruption and silent disk growth |

### Values worksheet

| # | Item | Value |
|---|---|---|
| 1 | AD DNS zone | `AHT.local` |
| 2 | App FQDN | `easysynq.AHT.local` |
| 3 | Short name | `easysynq` |
| 4 | VM IP | DHCP **reservation** on the WatchGuard, keyed to the VM's virtual MAC |
| 5 | QMS share (import) | `//AHTDC/Quality` → `/srv/easysynq/import` (read-only) |
| 6 | Share local path | `C:\quality` |
| 7 | Import service account | `svc-easysynq-ro` (read-only) |
| 8 | Backup service account | `svc-easysynq-bkp` (write, backup share only) |
| 9 | NetBIOS domain | `AHT` |
| 10 | Sizing profile | `s` |
| 11 | TLS mode | `internal` (Caddy CA) — no ADCS exists |

---

## 5. Backup design

EasySynQ's own logical backup is **not optional and not replaceable** by the MSP360 image: setup gate
**G-C** blocks finalization until a real backup **and** restore-test drill PASS. "Configured but
unverified" does not satisfy it. An image of a running PostgreSQL/MinIO is crash-consistent at best.

### Chain

```
EasySynQ nightly job (pg_dump + blob manifest + Keycloak realm + config + audit checkpoint)
   └─ AES-256-GCM encrypted → *.tar.enc
      └─ written to  /srv/easysynq/backup  (CIFS mount, RW, svc-easysynq-bkp)
         └─ lands on  \\AHTDC\easysynq-backup  (C:\easysynq-backup)
            └─ swept by the MSP360 whole-disk image (no exclusions)
               └─ offsite to Backblaze B2, bucket "Americanheat"
```

### Why this is sound

The archive is encrypted **before it leaves the VM**, so neither the MSP nor Backblaze can read AHT's
quality records. That makes an opaque, third-party-managed offsite path acceptable for confidentiality
— the trust requirement reduces to availability only.

### Consequences accepted

- **`BACKUP_ENCRYPTION_KEY` becomes existential.** It exists only in the `0600` `.env`. If `LAB` is
  lost and the key was never escrowed, every archive in B2 is unrecoverable noise. It must be copied
  into password-manager custody **the hour it is generated**, stored separately from the backups.
- **Offsite RPO is the image cadence (weekly/monthly), not nightly.** An archive written Monday may
  not reach B2 until the weekly image runs. Local copies on `AHTDC` are same-day; offsite is not.
- **`AHTDC` concentration.** It would hold the domain, the source QMS documents, and the QMS backups.
  The B2 copy is the only thing that survives its loss.

---

## 6. Implementation phases

| # | Phase | Owner | Gate to proceed |
|---|---|---|---|
| 0 | Ethernet cable made + tested; DHCP reservation prepared on the WatchGuard | Colton | `Ethernet 3` links at **1 Gbps** |
| 1 | Hyper-V role, External vSwitch, VM created | Claude (elevated) | VM MAC captured for the reservation |
| 2 | AD: A record · `svc-easysynq-ro` · `svc-easysynq-bkp` · share + NTFS ACLs · deny-logon GPO | Colton (Domain Admin) | `Resolve-DnsName easysynq.AHT.local` succeeds **from a workstation** |
| 3 | Ubuntu 26.04 install; clone at reviewed commit; `bootstrap-ubuntu.sh` | Claude + console | `findmnt /srv/easysynq/import` shows `ro` |
| 4 | `install.sh s --host easysynq.AHT.local --tls internal`; **escrow key**; `OPS_ALERT_CHANNELS` | Claude | `/readyz` green |
| 5 | Export Caddy root CA → **new** GPO → workstation trust | Colton | `Get-ChildItem Cert:\LocalMachine\Root` shows it after `gpupdate` |
| 6 | Setup wizard: bootstrap → org → WORM verify → **backup + restore drill** → auth → finalize | Colton | state `OPERATIONAL` |
| 7 | `IMPORT_SOURCE_PATH`; recreate `api`/`worker`; first import run | Claude | worker sees the 215 files |

**Ordering constraint (load-bearing):** the CIFS mount must exist *before* any container starts against
that path. A bind mount never sees a filesystem mounted over its source after container start — the
containers would see an empty directory and the import would silently find nothing.

### Sequencing note on Phase 1

Creating the External vSwitch binds `Ethernet 3`, which is currently disconnected and unused. The
active session runs over Wi-Fi, so this will **not** interrupt work in progress.

---

## 7. Risks and accepted trade-offs

| # | Risk | Severity | Disposition |
|---|---|---|---|
| R-1 | **Caddy root CA enters the domain trust store.** It can sign a certificate for *any* hostname; its key lives in the VM's Caddy volume, so `LAB`'s disk and root access join the domain's certificate trust boundary. | High | **Accepted** — no ADCS exists. Mitigate by scoping a dedicated GPO (not Default Domain Policy) and treating the VM as a tier-0-adjacent asset. Revisit if ADCS is ever deployed: a host-specific leaf would put nothing new in the root store. |
| R-2 | **Broad remote-access surface** — Atera, Splashtop, GoTo, TeamViewer all reach `AHTDC`, which holds source documents and would hold backups. Compounds R-1. | High | **Out of scope, flagged.** Owner decision; recorded so it is inherited knowingly. |
| R-3 | **Offsite RPO is weekly**, not nightly, for files. | Medium | Accepted for 5 users / 53 MB. Raising MSP360 to daily is an IT request, not a blocker. |
| R-4 | **Backup key loss** makes every offsite archive worthless. | Critical | Escrow at Phase 4; verified before Phase 6 finalize. |
| R-5 | **Single-host availability** — target is 99.0%/month inclusive of Keycloak + Beat (R14). No HA. | Medium | Accepted; matches shop scale. |
| R-6 | **Not tamper-evident** until an off-host audit-checkpoint anchor is configured (R13). Non-blocking for go-live but real for an ISO 9001 audit trail — the anchor must live where this host's operator cannot rewrite it. | Medium | **Scheduled, not waived.** Post-go-live item. |
| R-7 | `OPS_ALERT_CHANNELS` empty by default → a failed nightly backup notifies only in-app, via the database that may itself be the failure. | Medium | Configure at Phase 4. `syslog` requires a compose override to mount `/dev/log`; `smtp` needs a relay (to be identified); `webhook` needs a receiver. |
| R-8 | Hostname changes after go-live invalidate the OIDC issuer and sign everyone out. | Low | Name fixed at design time: `easysynq.AHT.local`. |
| R-9 | Host reboots (Windows Update) take the VM down. | Low | `AutomaticStartAction=Start`. |
| R-10 | An account able to write the backup share can also delete backups (ransomware path). | Medium | Separate least-privilege `svc-easysynq-bkp`; the B2 copy is the real mitigation. |

---

## 8. Definition of done

- `Ethernet 3` linked at 1 Gbps; VM holds its reserved address across a reboot.
- `Resolve-DnsName easysynq.AHT.local` resolves **from a workstation**, not just the DC.
- `docker compose version` ≥ 2.24.4 · `systemctl is-enabled docker` · `is-masked sleep.target` ·
  `timedatectl` synchronized · `findmnt /srv/easysynq/import` present and `ro`.
- `https://easysynq.AHT.local` loads with **no certificate warning** on a domain workstation.
- TLS 1.1 refused; CSP / HSTS / Referrer-Policy / X-Content-Type / Permissions-Policy headers present.
- A Keycloak login round-trips; a document upload **and** download both succeed (download exercises 9443).
- Setup state `OPERATIONAL`, reached through a **passing** backup + restore drill.
- `BACKUP_ENCRYPTION_KEY` escrowed in password-manager custody, verified separately from backups.
- An import run ingests the 215 files from `//AHTDC/Quality`.
- The VM survives a host reboot unattended.

---

## 9. Open items carried into implementation

1. **SMTP relay** for `OPS_ALERT_SMTP_TO` — not yet identified. Until set, backup-failure alarms reach
   only the container log and in-app notifications.
2. **Reserved IP address** — to be chosen on the WatchGuard, inside the correct scope.
3. **MSP360 coverage of the new backup folder** — `C:\easysynq-backup` sits on the imaged volume with
   no exclusions, so it should be covered by construction. Confirm after the first image run rather
   than assuming.
4. **Audit-checkpoint anchor** (R13) — schedule after go-live.
5. **`.xlsx#` and `.pdc`** in the QMS tree — likely a lock artifact and a proprietary file. Confirm
   handling during the import review step; neither should block.

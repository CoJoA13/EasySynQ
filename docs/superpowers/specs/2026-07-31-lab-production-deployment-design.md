# Design — EasySynQ production deployment on `LAB` (example.local)

> **Status:** approved design, pending implementation.
> **Date:** 2026-07-31 · **Repo commit at design time:** `4f49c0f` · **Migration head:** `0083`
> **Site:** <ORG> (`example.local`), single-site heat-treat shop, ~5 QMS users.
>
> This is a site-specific deployment design. The generic procedure lives in
> [`docs/runbooks/install-ubuntu-server.md`](../../runbooks/install-ubuntu-server.md); this document
> records what is *different* here, the values discovered on site, and the trade-offs accepted.

---

## 1. Why this document exists

The existing runbooks assume a bare Ubuntu host on a LAN with a separate Windows file server. Neither
assumption holds at this site:

- the intended host is a **domain-joined Windows 11 Pro workstation**, not a Linux box;
- there is **no file server** — `DC01` is simultaneously domain controller, file server, and SQL host;
- the deployment target must remain manageable by the existing IT provider.

Every fact below was **verified on the machine**, not taken from documentation. Several published
notes (notably `.claude/rules/windows-dev.md`) describe a *different* workstation and do not apply.

---

## 2. Discovered environment

> ⚠ **Sanitized.** Hostnames, addresses, account names, vendor products and versions are replaced
> with placeholders. **Do not restore real values into this repository** — a site inventory
> (user list, network layout, security-product versions, documented weaknesses) is exactly the
> reconnaissance material an attacker wants, and a code repository is the wrong place for it. Keep
> the concrete worksheet wherever the organization's other operational records live. Only the
> *shape* of the environment is recorded here, because the design decisions do not survive without it.

### 2.1 Intended host — `LAB`

A domain-joined **Windows 11 Pro** desktop-class workstation: 16 logical processors, ~30 GB RAM,
~880 GB free, SLAT and firmware virtualization enabled, no battery, AC sleep already disabled.

Two facts drove the plan: it was joined to the domain but the **interactive account was local**, so
the session had no domain rights at all; and it carried **Git only** — no Docker, no `bash` on PATH,
no `uv`/`node`/`just`/`gh`, no `.env`.

### 2.2 Network

The only live link at the outset was **Wi-Fi**; the gigabit wired NIC was **disconnected**, which
mattered because a Hyper-V External vSwitch cannot bridge reliably over a wireless adapter.

The site **edge appliance is both gateway and DHCP server**, and it hands out **public DNS
resolvers** — which cannot resolve the AD zone. That single fact would have broken both CIFS mounts
at boot, and with them the import and the entire backup chain. Its management interface was not
reachable with available credentials, so no DHCP reservation could be created during the build.

### 2.3 Active Directory

A single Windows Server domain controller — **also the file server and the SQL host**. Zone is
AD-integrated, the intended app name was free, only the two default GPOs existed, and **no
Enterprise CA (ADCS) is published**, which is what forces TLS mode `internal` and the CA-trust
rollout in §7 R-1. No service accounts existed prior to this deployment.

### 2.4 The QMS source tree

One read-only share, **~250 files / ~56 MB**, actively maintained. Composition matters for the
import classifier:

| Type | Count | Type | Count |
|---|---|---|---|
| `.docx` | 144 | `.html` | 4 |
| `.pdf` | 28 | `.vsdx` | 2 |
| `.xlsx` | 24 | `.pdc` / `.zip` / `.xlsm` / `.xlsx#` | 1 each |
| `.png` | 9 | `Thumbs.db` | 37 |

It splits into two trees of **different kinds**, both organised by ISO 9001 clause: controlled
**documents** (procedures, work instructions, forms, the Quality Manual) and dated **records**
(calibration certificates, inspection and tester certifications). See Task 11 in the plan.

The DC hosts several other unrelated shares; only the QMS share is in scope.

### 2.5 Existing backup posture

Two independent products were already running:

- A **whole-volume image backup** of the DC to **cloud object storage**, on a *weekly + monthly*
  recurrence with no exclusion rules — so the QMS share is covered implicitly rather than by name.
- A **database-only** nightly job covering the SQL instance. It does **not** cover files.

The owner believed the server was "backed up in whole nightly." What is verifiable is that the
*databases* are nightly and the *files* ride a weekly image. That gap shapes the backup design in §5.

**Several independent remote-access and management agents** are installed on the DC — the same host
that holds the source documents and would hold the QMS backups. Recorded as risk **R-2**.

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
               ├─► https://easysynq.example.local        (Caddy :443 — SPA, API, Keycloak)
               └─► https://easysynq.example.local:9443   (Caddy — presigned MinIO/S3 origin)
                          │
                   [ edge firewall 10.0.0.1 — gateway + DHCP ]
                          │
        ┌─────────────────┴──────────────────┐
        │                                    │
   DC01 (DC / file / SQL)            LAB (Windows 11 Pro)
   ├── \\DC01\Quality  (RO) ────────► Hyper-V External vSwitch → "Ethernet 3"
   └── \\DC01\easysynq-backup (RW) ◄─┐        │
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
| 1 | AD DNS zone | `example.local` |
| 2 | App FQDN | `easysynq.example.local` |
| 3 | Short name | `easysynq` |
| 4 | VM IP | DHCP **reservation** on the edge firewall, keyed to the VM's virtual MAC |
| 5 | QMS share (import) | `//DC01/Quality` → `/srv/easysynq/import` (read-only) |
| 6 | Share local path | `C:\quality` |
| 7 | Import service account | `svc-easysynq-ro` (read-only) |
| 8 | Backup service account | `svc-easysynq-bkp` (write, backup share only) |
| 9 | NetBIOS domain | `EXAMPLE` |
| 10 | Sizing profile | `s` |
| 11 | TLS mode | `internal` (Caddy CA) — no ADCS exists |

---

## 5. Backup design

EasySynQ's own logical backup is **not optional and not replaceable** by the whole-disk image: setup gate
**G-C** blocks finalization until a real backup **and** restore-test drill PASS. "Configured but
unverified" does not satisfy it. An image of a running PostgreSQL/MinIO is crash-consistent at best.

### Chain

```
EasySynQ nightly job (pg_dump + blob manifest + Keycloak realm + config + audit checkpoint)
   └─ AES-256-GCM encrypted → *.tar.enc
      └─ written to  /srv/easysynq/backup  (CIFS mount, RW, svc-easysynq-bkp)
         └─ lands on  \\DC01\easysynq-backup  (C:\easysynq-backup)
            └─ swept by the whole-disk image (no exclusions)
               └─ offsite to the offsite object store, bucket "<offsite-bucket>"
```

### Why this is sound

The archive is encrypted **before it leaves the VM**, so neither the IT provider nor the offsite store can read the organization's
quality records. That makes an opaque, third-party-managed offsite path acceptable for confidentiality
— the trust requirement reduces to availability only.

### Consequences accepted

- **`BACKUP_ENCRYPTION_KEY` becomes existential.** It exists only in the `0600` `.env`. If `LAB` is
  lost and the key was never escrowed, every archive in B2 is unrecoverable noise. It must be copied
  into password-manager custody **the hour it is generated**, stored separately from the backups.
- **Offsite RPO is the image cadence (weekly/monthly), not nightly.** An archive written Monday may
  not reach B2 until the weekly image runs. Local copies on `DC01` are same-day; offsite is not.
- **`DC01` concentration.** It would hold the domain, the source QMS documents, and the QMS backups.
  The B2 copy is the only thing that survives its loss.

---

## 6. Implementation phases

| # | Phase | Owner | Gate to proceed |
|---|---|---|---|
| 0 | Ethernet cable made + tested; DHCP reservation prepared on the edge firewall | Owner | `Ethernet 3` links at **1 Gbps** |
| 1 | Hyper-V role, External vSwitch, VM created | Claude (elevated) | VM MAC captured for the reservation |
| 2 | AD: A record · `svc-easysynq-ro` · `svc-easysynq-bkp` · share + NTFS ACLs · deny-logon GPO | Owner (Domain Admin) | `Resolve-DnsName easysynq.example.local` succeeds **from a workstation** |
| 3 | Ubuntu 26.04 install; clone at reviewed commit; `bootstrap-ubuntu.sh` | Claude + console | `findmnt /srv/easysynq/import` shows `ro` |
| 4 | `install.sh s --host easysynq.example.local --tls internal`; **escrow key**; `OPS_ALERT_CHANNELS` | Claude | `/readyz` green |
| 5 | Export Caddy root CA → **new** GPO → workstation trust | Owner | `Get-ChildItem Cert:\LocalMachine\Root` shows it after `gpupdate` |
| 6 | Setup wizard: bootstrap → org → WORM verify → **backup + restore drill** → auth → finalize | Owner | state `OPERATIONAL` |
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
| R-2 | **Broad remote-access surface** — several independent remote-access and RMM agents reach `DC01`, which holds source documents and would hold backups. Compounds R-1. | High | **Out of scope, flagged.** Owner decision; recorded so it is inherited knowingly. |
| R-3 | **Offsite RPO is weekly**, not nightly, for files. | Medium | Accepted for 5 users / 53 MB. Raising the image backup to daily is an IT request, not a blocker. |
| R-4 | **Backup key loss** makes every offsite archive worthless. | Critical | Escrow at Phase 4; verified before Phase 6 finalize. |
| R-5 | **Single-host availability** — target is 99.0%/month inclusive of Keycloak + Beat (R14). No HA. | Medium | Accepted; matches shop scale. |
| R-6 | **Not tamper-evident** until an off-host audit-checkpoint anchor is configured (R13). Non-blocking for go-live but real for an ISO 9001 audit trail — the anchor must live where this host's operator cannot rewrite it. | Medium | **Scheduled, not waived.** Post-go-live item. |
| R-7 | `OPS_ALERT_CHANNELS` empty by default → a failed nightly backup notifies only in-app, via the database that may itself be the failure. | Medium | Configure at Phase 4. `syslog` requires a compose override to mount `/dev/log`; `smtp` needs a relay (to be identified); `webhook` needs a receiver. |
| R-8 | Hostname changes after go-live invalidate the OIDC issuer and sign everyone out. | Low | Name fixed at design time: `easysynq.example.local`. |
| R-9 | Host reboots (Windows Update) take the VM down. | Low | `AutomaticStartAction=Start`. |
| R-10 | An account able to write the backup share can also delete backups (ransomware path). | Medium | Separate least-privilege `svc-easysynq-bkp`; the B2 copy is the real mitigation. |

---

## 8. Definition of done

- `Ethernet 3` linked at 1 Gbps; VM holds its reserved address across a reboot.
- `Resolve-DnsName easysynq.example.local` resolves **from a workstation**, not just the DC.
- `docker compose version` ≥ 2.24.4 · `systemctl is-enabled docker` · `systemctl is-enabled
  sleep.target` returns `masked` (systemd 259 has no `is-masked` verb) ·
  `timedatectl` synchronized · `findmnt /srv/easysynq/import` present and `ro`.
- `https://easysynq.example.local` loads with **no certificate warning** on a domain workstation.
- TLS 1.1 refused; CSP / HSTS / Referrer-Policy / X-Content-Type / Permissions-Policy headers present.
- A Keycloak login round-trips; a document upload **and** download both succeed (download exercises 9443).
- Setup state `OPERATIONAL`, reached through a **passing** backup + restore drill.
- `BACKUP_ENCRYPTION_KEY` escrowed in password-manager custody, verified separately from backups.
- An import run ingests the 215 files from `//DC01/Quality`.
- The VM survives a host reboot unattended.

---

## 9. Open items carried into implementation

1. **SMTP relay** for `OPS_ALERT_SMTP_TO` — not yet identified. Until set, backup-failure alarms reach
   only the container log and in-app notifications.
2. **Reserved IP address** — to be chosen on the edge firewall, inside the correct scope.
3. **image-backup coverage of the new backup folder** — `C:\easysynq-backup` sits on the imaged volume with
   no exclusions, so it should be covered by construction. Confirm after the first image run rather
   than assuming.
4. **Audit-checkpoint anchor** (R13) — schedule after go-live.
5. **`.xlsx#` and `.pdc`** in the QMS tree — likely a lock artifact and a proprietary file. Confirm
   handling during the import review step; neither should block.

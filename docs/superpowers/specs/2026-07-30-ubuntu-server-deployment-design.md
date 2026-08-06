# Ubuntu 26.04 production host bootstrap + Windows-LAN deployment runbook

> Status: **APPROVED, ready to plan.** Design calls resolved below.
> **Ops/docs only** — no API, web, migration, permission key, or OpenAPI change.
> Driver: provide a repeatable production-host bootstrap for **Ubuntu 26.04 LTS (`resolute`)**,
> including the common case where an organization's file server is Windows.

## Goal

Close the gap between "a bare Ubuntu 26.04 box" and "the state `install.sh` already assumes",
and document the Windows-server side of the same deployment. Today
[`install-online.md`](../../runbooks/install-online.md) step 2 jumps straight to `git clone` +
`install.sh`, silently assuming Docker, a Compose ≥ 2.24.4 plugin, a firewall, a stable FQDN, and
accurate time already exist. Nothing in the repo provisions a **production** host —
[`fresh-linux-setup.md`](../../runbooks/fresh-linux-setup.md) is explicitly developer-facing and
installs `uv`/Node/`just`, which a production host must not carry.

## Context (verified 2026-07-30)

- **Docker on 26.04 is not a risk.** Docker 29 shipped day-one `resolute` packages; the official
  `download.docker.com/linux/ubuntu` repo carries the suite. Ubuntu's own `docker.io` /
  `docker-compose-v2` lag, so production must use Docker's repo — the dev runbook's
  `apt install docker.io` is **not** sufficient here, because the production overlay's `!reset`
  merge tag needs Compose **≥ 2.24.4** (enforced by
  [`require-compose-version.sh`](../../../scripts/require-compose-version.sh)).
- `fresh-linux-setup.md` is already 26.04-aware (it notes 26.04 ships PostgreSQL 18 only and that
  PGDG publishes a `resolute` suite). It needs no correction.
- `install.sh` requires no root today and is reused by two other callers — the appliance provisioner
  (`EASYSYNQ_ENV_ONLY=1`) and the air-gapped installer. That constrains where `apt` work may live.

## Non-goals

- **Do not re-pin the Hyper-V appliance to 26.04.** `UBUNTU_SERIES="noble"`
  ([`build-appliance.sh:20`](../../../infra/appliance/build-appliance.sh)) and the "Ubuntu 24.04 cloud
  image" line in [`appliance-install.md:12`](../../runbooks/appliance-install.md) are a deliberate,
  boot-proven pin validated by `infra/appliance/boot-test.sh`. They are **accurate as written** for
  the appliance path, which this deployment does not use. Bumping the series means rebuilding the
  VHDX and re-running the QEMU/KVM harness — a separate slice with real risk. Editing the prose
  without rebuilding would make the docs *less* true. Filed, not touched.
- No change to `install.sh` behavior, secret generation, or the Compose overlays.
- No L profile, Kubernetes, or OpenSearch path.

## Component 1 — `scripts/bootstrap-ubuntu.sh` (new)

Host provisioner. Takes `sudo`, runs **before** `install.sh`, and never invokes it. Two units with
one purpose each: this provisions the **host**, `install.sh` provisions the **app**.

**Why separate:** `install.sh` needs no root, generates the secrets, and is
shared by the appliance provisioner and the air-gapped installer. Folding `apt` into it would force
`sudo` onto the secret-generating path and risk both other callers.

### Interface

```
sudo ./scripts/bootstrap-ubuntu.sh --host <fqdn> [options]

  --host <fqdn>          Static FQDN; must match the AD DNS A record (required)
  --profile s|m          Sizing floor for preflight (default: s)
  --qms-share <unc>      e.g. //FILESRV/QMS — mount the import source read-only
  --qms-user <account>   Service account for that share (implies --qms-share)
  --skip-firewall        Leave ufw untouched (a managed/external firewall)
  --skip-upgrades        Do not enable unattended-upgrades
  --force                Proceed despite a failed sizing preflight
  --dry-run              Print every command; execute nothing
```

### Steps

Every step is **idempotent and skip-if-done**, mirroring the appliance provisioner, so a failed run
resumes by simply re-running.

| # | Step | On failure |
|---|---|---|
| 0 | Preflight: root; `/etc/os-release` `ID=ubuntu`; x86_64; vCPU/RAM/disk vs profile floor (S: 2 / 8 GB / 50 GB · M: 4 / 16 GB / 200 GB) | **Fail**; `--force` overrides. Warn-only if `VERSION_ID != 26.04` |
| 1 | `apt-get update`; install `ca-certificates curl git openssl gnupg ufw cifs-utils unattended-upgrades` | fail |
| 2 | Docker CE repo: keyring → `/etc/apt/keyrings/docker.asc` (0644), suite from `VERSION_CODENAME`; install `docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` | **Probe `dists/<codename>/Release` with `curl -fsI` first**; fail with the explicit remedy if absent |
| 3 | Verify Compose floor by delegating to `require-compose-version.sh` | fail |
| 4 | `systemctl enable --now docker`; `usermod -aG docker "$SUDO_USER"` | fail |
| 5 | Mask `sleep.target suspend.target hibernate.target hybrid-sleep.target`; logind `HandleLidSwitch*=ignore`; restart logind | fail |
| 6 | `timedatectl set-ntp true`; `hostnamectl set-hostname <fqdn>`; ensure the `127.0.1.1 <fqdn> <short>` line in `/etc/hosts` | fail |
| 7 | ufw: **`allow OpenSSH` first**, then 80/443/9443, then `--force enable` | fail |
| 8 | unattended-upgrades: security origins only, `Unattended-Upgrade::Automatic-Reboot "false"` | fail |
| 9 | *(only with `--qms-share`)* `/etc/easysynq-qms.cred` (0600, root) → `/etc/fstab` entry `ro,_netdev,vers=3.0,noserverino,credentials=…` → `mount` → assert readable | fail |
| 10 | Print the next command and the exact `IMPORT_SOURCE_PATH` to set | — |

### Design constraints (each closes a specific failure)

- **Generates no secrets.** `install.sh` remains the single origin of `.env`, so there is exactly one
  artifact to escrow (`BACKUP_ENCRYPTION_KEY`).
- **Fixed import root `/srv/easysynq/import`**, mirroring the appliance's fixed root, so the share
  mount never rewrites `.env`. The script prints the value; the operator sets it.
- **SSH is allowed before ufw is enabled.** Reversed, this severs a remote install session.
- **Suite probed, never guessed.** Silently falling back to another Ubuntu suite would install a
  mismatched Docker build that fails much later and far less legibly.
- **Mount before the containers bind it.** A bind mount never sees a filesystem mounted over its
  source after container start — the containers would see an empty directory and the import would
  find nothing. The invariant is *filesystem mount exists before a container starts against that
  path*, so the runbook orders it: bootstrap mounts the share (§2) → `install.sh` runs and brings the
  stack up with the default import path (§3) → the operator sets `IMPORT_SOURCE_PATH` and recreates
  `api` + `worker` (§6). Because the mount already exists at recreate time, the bind resolves
  correctly. Setting `IMPORT_SOURCE_PATH` cannot happen before §3 — `install.sh` is what creates
  `.env`.

## Component 2 — `docs/runbooks/install-ubuntu-server.md` (new)

End-to-end for this topology: an Ubuntu host serving a Windows LAN. Links to `install-online.md` for
browser-edge detail rather than duplicating it.

### §0 Values worksheet

An operator may not have the domain, hostname, or share UNC until deploy time. The runbook therefore
opens with a **fill-once table** — every later command references these names, so nothing is
retyped and no placeholder survives into a real command:

| Name | Value | How to discover it (run on the Windows server) |
|---|---|---|
| AD DNS zone | | `Get-DnsServerZone \| Where-Object { -not $_.IsReverseLookupZone }` |
| App FQDN | | your choice, e.g. `easysynq.<zone>` |
| Ubuntu host IP | | `ip -4 addr show scope global` on the Ubuntu box |
| QMS share UNC | | `Get-SmbShare \| Where-Object Name -notlike '*$'` |
| Share local path | | same output, `Path` column |
| Service account | | your choice, e.g. `svc-easysynq-ro` |
| NetBIOS domain | | `(Get-ADDomain).NetBIOSName` |

### Sequence

Windows prep (§1) → bootstrap, incl. the share **mount** (§2) → `install.sh` (§3) → CA export + GPO
(§4) → setup wizard (§5) → point `IMPORT_SOURCE_PATH` at the mount + recreate `api`/`worker`, then
run the import (§6) → verification checklist (§7) → troubleshooting (§8).

## Component 3 — Windows server section (§1, §4)

Prose with a verified copy-paste PowerShell block per step (design decision: AD writes stay
deliberate and reviewable, one step at a time — not one opaque script).

- **DNS A record** — `Add-DnsServerResourceRecordA -CreatePtr`, then verify with `Resolve-DnsName`
  from a workstation, not from the DC.
- **Service account** — `New-ADUser` with `-PasswordNeverExpires -CannotChangePassword`; note that
  it needs no logon rights beyond the share.
- **Share + NTFS** — `Grant-SmbShareAccess -AccessRight Read`, plus an NTFS `ReadAndExecute` ACE via
  `Get-Acl`/`Set-Acl`. Both layers are required; share-level Read alone still defers to NTFS.
- **GPO trusted root** — GUI path (*Computer Configuration → Policies → Windows Settings → Security
  Settings → Public Key Policies → Trusted Root Certification Authorities*), plus
  `certutil -dspublish -f easysynq-root-ca.crt RootCA` as the enterprise-store alternative.
- **Verify on a workstation** — `gpupdate /force`, then assert the cert is present:
  `Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Caddy*'`.

## Component 4 — Edits to existing docs

| File | Edit |
|---|---|
| `docs/runbooks/install-online.md` | Prereqs: point at `bootstrap-ubuntu.sh` for an unprovisioned host |
| `docs/manuals/installation-guide.md` | §2 path table + §3 host prereqs: same pointer |
| `docs/runbooks/00-index.md` | List the new runbook |

## Verification

No unit harness — this is host provisioning, and the repo's only precedent for testing a shell
provisioner is the appliance's QEMU boot test, which does not apply to a script run on an existing
host. Verification is therefore `--dry-run` plus the runbook's own §7 checklist:

```
docker compose version              # >= 2.24.4
systemctl is-enabled docker         # enabled
systemctl is-masked sleep.target    # masked
ufw status                          # 80, 443, 9443, OpenSSH
timedatectl                         # NTP synchronized: yes
hostname -f                         # the app FQDN
findmnt /srv/easysynq/import        # ro, if a share was configured
```

Then the release-time security check already in `install-online.md` (CSP/HSTS headers, TLS 1.1
refused).

## Risks / known traps carried into the plan

1. **9443 omitted** → app loads, every upload/download fails. Covered by step 7 and asserted in §7.
2. **Bare IP instead of FQDN** → TLS and the OIDC issuer are name-bound; unrecoverable without
   reconfigure. `--host` is required and DNS-validated.
3. **ufw enabled before SSH allowed** → remote lockout. Ordering is fixed in step 7.
4. **Share mounted after `compose up`** → empty import source. Ordering is fixed in the runbook.
5. **`BACKUP_ENCRYPTION_KEY` not escrowed** before the first backup → every encrypted backup is
   unrecoverable. Called out in §3 and §5.
6. **No out-of-band ops channel** (`OPS_ALERT_CHANNELS` empty by default) → a failed nightly backup
   notifies admins only in-app, which needs the database that may be the thing that failed. Called
   out in §5.
7. **R13**: the install self-reports **NOT tamper-evident** until an off-host audit-checkpoint anchor
   is configured. Non-blocking for go-live; recorded in §5 as a named follow-up rather than left to
   be read as noise.

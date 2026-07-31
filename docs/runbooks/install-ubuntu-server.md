# Runbook — Ubuntu server install on a Windows LAN

> Audience: the administrator standing up EasySynQ on a dedicated Ubuntu host (a repurposed
> workstation or a small server) on a LAN whose main file server is Windows. Covers the Windows
> side (AD DNS, service account, share, GPO certificate trust) as well as the Linux side.
> For browser-edge and TLS-mode detail see [install-online.md](install-online.md); this runbook
> does not duplicate it.

**The main Windows server is unaffected by this install.** EasySynQ runs on its own host with its
own DNS name and its own ports. Workstations keep reaching the file server exactly as they do today.
The only coupling is one **read-only** mount of the existing QMS tree, used by the import engine.

Do **not** reverse-proxy EasySynQ through the Windows server or point a CNAME at it — TLS and the
OIDC issuer are bound to EasySynQ's own hostname, and that shape breaks both.

## 0. Values worksheet — fill this in first

Every command below refers to these names. Fill the table once, then substitute consistently.

| # | Name | Your value | How to discover it |
|---|---|---|---|
| 1 | AD DNS zone | | `Get-DnsServerZone \| Where-Object { -not $_.IsReverseLookupZone }` (on the DC) |
| 2 | App FQDN | | your choice — e.g. `easysynq.` + row 1 |
| 3 | App short name | | the first label of row 2 — e.g. `easysynq` |
| 4 | Ubuntu host IP | | `ip -4 addr show scope global` (on the Ubuntu box) |
| 5 | QMS share UNC | | `Get-SmbShare \| Where-Object Name -notlike '*$'` (on the file server) |
| 6 | Share local path | | same output, `Path` column |
| 7 | Service account | | your choice — e.g. `svc-easysynq-ro` |
| 8 | NetBIOS domain | | `(Get-ADDomain).NetBIOSName` |
| 9 | Sizing profile | `s` | `s` ≤ 25 users, `m` ≤ 100 — see doc 03 §7 |

Give the Ubuntu host a **static IP or a DHCP reservation** before you start. Row 4 must not change
after install: the certificate and the OIDC issuer are bound to the name that resolves to it.

## 1. Windows server preparation

Run these on the domain controller / file server as appropriate, in an elevated PowerShell.

### 1.1 Create the DNS A record

```powershell
Add-DnsServerResourceRecordA `
  -Name       "<row 3: app short name>" `
  -ZoneName   "<row 1: AD DNS zone>" `
  -IPv4Address "<row 4: Ubuntu host IP>" `
  -CreatePtr
```

Verify **from a workstation, not from the DC** — the DC often answers from cache and will succeed
even when clients cannot resolve the name:

```powershell
Resolve-DnsName <row 2: app FQDN>
```

Expected: an `A` record pointing at row 4.

### 1.2 Create the read-only service account

This account only reads the QMS share. It needs no interactive logon rights and no group membership
beyond `Domain Users`.

```powershell
New-ADUser `
  -Name                 "<row 7: service account>" `
  -SamAccountName       "<row 7: service account>" `
  -AccountPassword      (Read-Host -AsSecureString "Password for the service account") `
  -PasswordNeverExpires $true `
  -CannotChangePassword $true `
  -Enabled              $true `
  -Description          "EasySynQ read-only QMS import"
```

Record that password — you will type it once, on the Ubuntu host, in step 2.

### 1.3 Grant read-only access to the QMS share

**Both layers are required.** Share-level permission still defers to NTFS, and granting one but not
the other is the most common cause of a mount that succeeds but shows an empty directory.

Share level:

```powershell
Grant-SmbShareAccess `
  -Name        "<share name from row 5>" `
  -AccountName "<row 8: NetBIOS>\<row 7: service account>" `
  -AccessRight Read `
  -Force
```

NTFS level:

```powershell
$path = "<row 6: share local path>"
$acl  = Get-Acl $path
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  "<row 8: NetBIOS>\<row 7: service account>",
  "ReadAndExecute",
  "ContainerInherit,ObjectInherit",
  "None",
  "Allow")
$acl.AddAccessRule($rule)
Set-Acl $path $acl
```

Confirm both landed:

```powershell
Get-SmbShareAccess -Name "<share name from row 5>"
(Get-Acl "<row 6: share local path>").Access |
  Where-Object IdentityReference -like "*<row 7: service account>*"
```

### 1.4 Confirm the Ubuntu host can reach SMB

TCP 445 must be open from row 4 to the file server. If the workstations already use this share, it
almost certainly is — but confirm rather than assume, since the Ubuntu host may sit in a different
subnet or firewall zone than the workstations.

## 2. Bootstrap the Ubuntu host

Install **Ubuntu 26.04 LTS**, then clone the repository at a reviewed release tag:

```bash
git clone https://github.com/CoJoA13/EasySynQ.git
cd EasySynQ
git checkout <release-tag-or-approved-commit>
```

Provision the host. This installs Docker CE and the Compose plugin from Docker's official
repository, configures the firewall, disables sleep, enables time sync, sets the hostname, and
mounts the QMS share read-only:

```bash
sudo ./scripts/bootstrap-ubuntu.sh \
  --host       <row 2: app FQDN> \
  --profile    <row 9: profile> \
  --qms-share  <row 5: QMS share UNC, forward slashes — e.g. //FILESRV/QMS> \
  --qms-user   '<row 8: NetBIOS>\<row 7: service account>'
```

It prompts once for the service-account password and writes it to a root-owned `0600` credentials
file. Add `--dry-run` first if you want to read every command before anything executes.

**Then log out and back in** so your account picks up the `docker` group. Without this, every
subsequent `docker` command fails with a permission error.

The script deliberately does **not** install EasySynQ — that is the next step.

## 3. Install EasySynQ

```bash
./scripts/install.sh <row 9: profile> --host <row 2: app FQDN> --tls internal
```

`--tls internal` is correct for a private/LAN name: no public CA can issue a certificate for it.
Use `--tls acme` only if row 2 is publicly resolvable and reachable by a public CA.

The installer generates a `0600` `.env` with role-separated secrets, derives every browser-facing
origin from the one hostname, runs migrations, authorizes the SPA callback in Keycloak, and blocks
until `/readyz` is green.

> ⚠️ **Escrow the backup key now.** `BACKUP_ENCRYPTION_KEY` exists **only** in that `0600` `.env`.
> Copy it into your password manager or key custody process **before** the first backup runs. If it
> is lost, every encrypted backup is permanently unrecoverable. Keep custody of the key separate
> from custody of the backups themselves.

## 4. Distribute the certificate authority

Because `--tls internal` uses Caddy's own CA, browsers warn until that CA is trusted. Do this before
users see the app.

Export the root certificate on the Ubuntu host:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.<row 9: profile>.yml \
  -f infra/compose/compose.production.yml \
  exec -T proxy cat /data/caddy/pki/authorities/local/root.crt > easysynq-root-ca.crt
```

Copy `easysynq-root-ca.crt` to the Windows server, then distribute it by **either** route:

**Group Policy** — *Computer Configuration → Policies → Windows Settings → Security Settings →
Public Key Policies → Trusted Root Certification Authorities* → right-click → *Import* → select the
file. Workstations pick it up at the next `gpupdate`.

**Enterprise store** (alternative, applies domain-wide):

```powershell
certutil -dspublish -f easysynq-root-ca.crt RootCA
```

Verify on a workstation:

```powershell
gpupdate /force
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Caddy*'
```

Expected: one certificate. The CA is generated uniquely on your host at install, so trusting it
trusts only this box.

## 5. First-run setup wizard

Mint the one-time bootstrap secret on the Ubuntu host:

```bash
./scripts/easysynq setup mint-bootstrap
```

Create the intended administrator's sign-in identity in Keycloak first — the bootstrap secret grants
EasySynQ administration but does **not** create a Keycloak password. See
[Installation Guide §4.3](../manuals/installation-guide.md#43-create-the-first-sign-in-identity).

Then browse to `https://<row 2: app FQDN>/setup` and complete the gates in order:

1. Paste the bootstrap secret → you become the first **System Administrator**.
2. **Organization** — legal name, short code, IANA timezone.
3. **Storage** — *Verify storage* (the WORM probe, gate **G-B**). The `documents` bucket must be
   object-lock-enabled; see [minio-object-lock-prereq.md](minio-object-lock-prereq.md).
4. **Backup** — set a destination, then *Run backup + restore-test drill*. Finalize is **blocked**
   until this passes (gate **G-C**). Point it at storage that leaves this host — a backup living
   only on the EasySynQ box is not a backup. See [backup-restore.md](backup-restore.md).
5. **Authentication** — pick a method, acknowledge MFA, *Verify authentication* (gate **G-D**).
6. **Finalize** → the install becomes `OPERATIONAL` and the setup latch lifts.

Two items to handle at go-live rather than later:

- **Set `OPS_ALERT_CHANNELS`** (`syslog`, `smtp`, and/or `webhook`) in `.env`. It is empty by
  default, which means a failed nightly backup notifies administrators only **in-app** — a path that
  needs the database that may be the very thing that failed. An out-of-band channel is the only one
  that survives the failure it reports.
- The install reports itself **NOT tamper-evident** until an off-host audit-checkpoint anchor is
  configured (R13). This is non-blocking for go-live, but for an ISO 9001 audit trail it is real:
  the anchor must live somewhere this host's operator cannot rewrite. Schedule it; do not read the
  warning as noise.

## 6. Point the import engine at the QMS share

The share is already mounted read-only at `/srv/easysynq/import` from step 2. Now tell EasySynQ to
use it and recreate the two containers that bind it:

```bash
sed -i 's|^IMPORT_SOURCE_PATH=.*|IMPORT_SOURCE_PATH=/srv/easysynq/import|' .env
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.<row 9: profile>.yml \
  -f infra/compose/compose.production.yml \
  up -d api worker
```

Order matters: **the filesystem mount must exist before a container starts against that path.** A
bind mount never sees a filesystem mounted over its source after the container has started — the
containers would see an empty directory and the import would find nothing. Because step 2 mounted
the share first, the bind resolves correctly on this recreate.

Confirm the containers see the tree:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.<row 9: profile>.yml \
  -f infra/compose/compose.production.yml \
  exec -T worker ls /srv/import/source | head
```

Then start an import run from the app's **Import** section.

## 7. Verification checklist

On the Ubuntu host:

```bash
docker compose version              # >= 2.24.4
systemctl is-enabled docker         # enabled
systemctl is-masked sleep.target    # masked
ufw status                          # OpenSSH, 80, 443, 9443 allowed
timedatectl                         # "System clock synchronized: yes"
hostname -f                         # row 2
findmnt /srv/easysynq/import        # present, and flagged ro
```

Release-time security check:

```bash
curl -sI https://<row 2>/ | grep -iE 'content-security|strict-transport|referrer|x-content-type|permissions-policy'
openssl s_client -connect <row 2>:443 -tls1_1 </dev/null   # MUST be refused (TLS 1.2 floor)
```

From a workstation: the SPA loads without a certificate warning, a Keycloak login round-trips, and a
document upload **and** download both succeed (the latter exercises port 9443 — see §8).

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| App loads, but every upload/download fails | TCP **9443** blocked. Presigned object-store traffic uses its own HTTPS origin. `ufw status` on the host; any hardware firewall between workstations and the host. |
| Certificate warning persists on a workstation | GPO not applied (`gpupdate /force`) or the cert landed in the wrong store — it must be **Local Machine → Trusted Root**, not Current User. Re-check with the `Get-ChildItem Cert:\LocalMachine\Root` command in §4. |
| Sign-in loops, or OIDC errors after any rename | The issuer must match the URL in the browser exactly. Changing the hostname after install signs everyone out and requires reconfiguring the issuer — pick the name before go-live. |
| `docker: permission denied` | The `docker` group needs a fresh login session. Log out and back in, or `newgrp docker` for the current shell. |
| Import finds nothing | Either `IMPORT_SOURCE_PATH` was never set (§6), or `api`/`worker` were started **before** the mount existed. Confirm with `findmnt /srv/easysynq/import`, then recreate the containers per §6. |
| Mount fails at boot | Missing `_netdev` in `/etc/fstab` (the mount raced networking), or the service-account password changed. Re-run `sudo mount /srv/easysynq/import` to see the error. |
| Mount succeeds but the directory is empty | Share-level **or** NTFS permission missing for the service account — §1.3 requires both. |
| Host went offline overnight | Sleep/suspend re-enabled, or the machine was shut down manually. `systemctl is-masked sleep.target` should report `masked`. |
| `/readyz` never goes green | `docker compose logs` on the host; most often a dependency container failed to start or a `.env` origin tuple is inconsistent. |

# EasySynQ Installation Guide

## 1. Purpose and support boundary

This guide installs the current EasySynQ release on infrastructure controlled by one organization.
It covers online Linux, air-gapped Linux, the Hyper-V appliance, and a developer workstation.

Current production support is:

| Item | Shipped support |
|---|---|
| Topology | One Linux host or VM, Docker Compose |
| Profiles | S and M |
| Search | PostgreSQL full-text search in both profiles |
| Identity | Keycloak local accounts and Keycloak-managed federation |
| TLS | Public ACME or Caddy internal CA |
| Browser origins | App/Keycloak on `https://<host>`; S3 uploads/downloads on `https://<host>:9443` |
| Upgrade | Backup-first, migration, readiness gate |

There is no shipped L profile, OpenSearch service, Kubernetes package, or bundled observability
stack. Do not create local overlays that imply those paths are supported without separately
validating them.

## 2. Choose an installation path

| Environment | Procedure |
|---|---|
| Bare Ubuntu host on a Windows LAN | Follow [Install Ubuntu server](../runbooks/install-ubuntu-server.md) — bootstraps the host and covers the Windows-side DNS/account/GPO work, then §4. |
| Linux host with internet | Follow §4, then [Install online](../runbooks/install-online.md) for edge details. |
| Linux host without internet | Build and transfer a pinned bundle using [Install air-gapped](../runbooks/install-airgapped.md). |
| Windows Server / Hyper-V | Use the [Hyper-V appliance runbook](../runbooks/appliance-install.md). |
| Developer workstation | Follow §8 and the [fresh Linux developer setup](../runbooks/fresh-linux-setup.md). |

## 3. Production prerequisites

### Host

- A supported Linux host/VM with Docker Engine and Docker Compose **2.24.4 or newer**. On an
  unprovisioned Ubuntu host, `sudo ./scripts/bootstrap-ubuntu.sh --host <fqdn>` installs and
  configures all of the host prerequisites in this section.
- Standard Linux command-line tools used by the installer, including Bash, OpenSSL, `curl`, `sed`,
  `grep`, and GNU coreutils.
- S profile: 2 vCPU, 8 GB RAM, and 50 GB SSD minimum.
- M profile: 4 vCPU, 16 GB RAM, and 200 GB SSD minimum.
- Accurate DNS and time synchronization. Use a stable FQDN; changing the OIDC issuer later signs
  users out.
- Host-volume encryption is strongly recommended.
- An absolute non-root POSIX backup path intended to be visible to the worker and backed by approved
  persistent/off-host storage. Setup does not certify the backing; verify it independently.

Verify the platform before installing:

```bash
docker --version
docker compose version
git --version
openssl version
curl --version
```

### Network and names

Create the DNS record before starting. Allow workstation access to:

| Port | Purpose |
|---|---|
| TCP 80 | ACME challenge / HTTPS redirect where applicable |
| TCP 443 | EasySynQ SPA, API, and Keycloak realm |
| TCP 9443 | Dedicated HTTPS MinIO origin for presigned browser transfers |

Do not expose PostgreSQL, Redis, Keycloak's internal port, or plaintext MinIO `:9000` to the LAN.

Choose one TLS mode:

- `acme`: the FQDN is publicly resolvable/reachable for certificate issuance.
- `internal`: private DNS or air-gapped network; distribute Caddy's root CA to every client.

### Decisions to make before setup

- Organization legal name, short code, and IANA timezone.
- S or M profile.
- WORM lock mode: `GOVERNANCE` is the normal choice; `COMPLIANCE` prevents deletion even by root
  until retention expires.
- Backup target and separate custody for the backup encryption key.
- An optional later identity-provider and federation policy; it is not a first-install prerequisite.
- An optional SMTP relay and at least one out-of-band operations alert channel.
- Read-only source path for an existing QMS import, if applicable.

## 4. Online Linux installation

### 4.1 Obtain a reviewed release

Use a release tag or reviewed commit, not an arbitrary moving branch:

```bash
git clone https://github.com/CoJoA13/EasySynQ.git
cd EasySynQ
git checkout <release-tag-or-approved-commit>
```

### 4.2 Run the production installer

For S:

```bash
./scripts/install.sh s --host qms.example.com --tls acme
```

For M:

```bash
./scripts/install.sh m --host qms.corp.example --tls internal
```

The installer:

- creates `.env` with mode `0600` and generated, role-separated secrets;
- writes the app, OIDC, QR/share-link, notification, and S3 browser origins from the FQDN;
- validates the origin tuples before migration;
- applies the base, selected sizing, and production Compose files;
- runs migrations before API/worker startup;
- authorizes the SPA callback in Keycloak; and
- waits for `https://<host>/readyz`.

If `.env` already exists, the installer preserves its secrets and operator settings. Review the
file rather than assuming it was regenerated.

### 4.3 Create the first administrator in EasySynQ

```bash
./scripts/easysynq setup mint-bootstrap
```

The secret is shown once and expires. Open `https://<host>/setup` without signing in, enter the
secret, and create the first administrator profile with the **System Administrator** role. EasySynQ
creates the sign-in identity; the operator does not visit Keycloak or handle an identity subject.
Next, copy the shown-once temporary password and acknowledge the active credential generation. Only
then sign in and change the temporary password when prompted. SMTP is not required.

### 4.4 Complete the six setup screens

1. **Create administrator** — enter the one-time secret and administrator profile, save the
   shown-once temporary password, acknowledge the active credential generation, then sign in and
   change the temporary password.
2. **Organization** — legal name, short code, and IANA timezone. The timezone governs effective
   dates and business-day scheduling.
3. **Storage** — choose WORM mode and run the live object-lock probe. Finalization is blocked until
   the probe proves that an early delete is denied.
4. **Backup** — provide an absolute non-root POSIX destination. Save performs a preliminary API-context
   check only; run the backup/restore-test worker drill, then separately prove the path's approved
   persistent backing and survival across container restart/recreation. The drill proves current
   worker access and source-dependent integrity only. A configured backup without a successful
   restore test does not pass.
5. **Authentication** — select Local or Federated, acknowledge the MFA recommendation, and verify
   the current non-bootstrap login plus issuer reachability. Federation itself is configured in
   Keycloak.
6. **Finalize** — review the gates and move the instance to `OPERATIONAL`.

The “Not yet tamper-evident” warning is non-blocking but meaningful. The organization must configure
a genuinely off-host/append-only audit checkpoint sink before claiming tamper-evidence.

### 4.5 Complete post-finalize onboarding

In the application:

1. Open Account → **Administration**.
2. In **Users**, create later accounts. `user.create` creates an account, `user.update` edits it,
   and role assignment requires the separate `permission.grant` permission. Password reset remains
   a system-tier operation. Each temporary password is shown once and must be changed at first
   sign-in.
3. Assign seeded roles and only the minimum required overrides.
4. In **Processes**, assign accountable process owners.
5. In **Config**, set the working calendar, email policy, quiet-hours escalation policy, and review
   notification health.
6. If importing existing files, mount the source read-only, set `IMPORT_SOURCE_PATH`, and start the
   run from **Import**.

The Roles tab is an inspector for seeded bundles; it is not a custom-role editor.

## 5. Internal CA rollout

For `--tls internal`, export the Caddy root:

```bash
EASYSYNQ_PROFILE_NAME="$(sed -n 's/^EASYSYNQ_PROFILE=\([^[:space:]#]*\).*/\1/p' .env)"
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f "infra/compose/compose.${EASYSYNQ_PROFILE_NAME}.yml" \
  -f infra/compose/compose.production.yml \
  exec -T proxy cat /data/caddy/pki/authorities/local/root.crt \
  > easysynq-root-ca.crt
```

Distribute it through the organization's trusted-root mechanism. Trust the CA before user testing;
otherwise login, S3 transfers, QR verification, and notification deep links may appear broken for
certificate reasons.

## 6. Installation acceptance checklist

Do not hand the service to QMS users until all applicable items pass:

- [ ] `https://<host>/healthz` returns HTTP 200.
- [ ] `https://<host>/readyz` returns HTTP 200 and every dependency is ready.
- [ ] App login round-trips through the expected Keycloak realm.
- [ ] Port 9443 is reachable from a workstation and a controlled file can upload/download.
- [ ] The Storage screen reports WORM verified.
- [ ] The backup + restore-test drill reports PASS.
- [ ] `.env` is `0600`, excluded from version control, and its encryption/signing secrets are held
      out-of-band.
- [ ] Internal CA trust is deployed where applicable.
- [ ] At least two System Administrators are provisioned before disabling any administrator.
- [ ] A QMS Owner is assigned separately from the System Administrator role.
- [ ] SMTP and at least one independent operations-alert path are tested, if required.
- [ ] Off-host audit witness status is documented honestly.
- [ ] A first backup is copied to the intended recovery location.

Security header and TLS checks:

```bash
curl -sI "https://<host>/" \
  | grep -iE 'content-security|strict-transport|referrer|x-content-type|permissions-policy'
openssl s_client -connect <host>:443 -tls1_1 </dev/null
```

The TLS 1.1 attempt must be refused.

## 7. Air-gapped and Hyper-V notes

For air-gapped installation, build the bundle on a connected release host only after every
non-development image is pinned by digest. The bundle carries the images this repository builds as
well as the pinned third-party ones, so the target never compiles anything. Verify the transferred
checksum before `docker load`; then install with `--tls internal --offline`, which forbids every
pull and build and names any image the transfer missed. See
[Install air-gapped](../runbooks/install-airgapped.md).

For Hyper-V, use the supplied VHDX, seed ISO, and elevated PowerShell installer. The appliance uses
an internal CA, exposes day-2 helper commands, and still requires port 9443. See the
[appliance runbook](../runbooks/appliance-install.md).

## 8. Developer workstation quick start

Production `install.sh` is not the developer bootstrap. Follow the detailed
[developer runbook](../runbooks/fresh-linux-setup.md). After copying `.env.example`, set the
localhost OIDC values before `just up`:

```bash
cp .env.example .env
chmod 600 .env
```

```dotenv
OIDC_ISSUER=http://localhost/realms/easysynq
OIDC_JWKS_URL=http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs
OIDC_DISCOVERY_URL=http://keycloak:8080/realms/easysynq/.well-known/openid-configuration
```

Then run:

```bash
just setup
just up s
```

### 8.1 Create the first administrator

Mint the setup secret from the running stack:

```bash
./scripts/easysynq setup mint-bootstrap
```

Open `http://localhost/setup` without signing in. Enter the secret and create the first
administrator profile. EasySynQ creates the sign-in identity; do not create a fixed or demo account
first. Next, copy the shown-once temporary password and acknowledge the active credential generation.
Only then sign in and change the temporary password when prompted, and complete the remaining setup
gates through `OPERATIONAL`.

Review the developer runbook's DB-role, Keycloak DB, and audit-sink requirements too.

### 8.2 Optional post-bootstrap development fixtures

Run these only after first-administrator setup is complete. They are fixed local development
fixtures, not supported first-install identities or credentials:

```bash
just demo-user       # demo / Demo-Password-1
just seed-personas   # priya / ken / mara separation-of-duties fixtures
```

`just seed-personas` additionally creates the development author/approver/releaser identities needed
to demonstrate separation of duties.

## 9. Uninstall and reset warning

Stopping the stack or running Compose `down` preserves named volumes. `down -v`, deleting `pgdata`,
or deleting MinIO volumes is a destructive data reset. This guide does not authorize a reset;
follow the organization's retention and recovery controls before removing data.

## 10. Next documents

- [Administrator & IT Manual](administrator-it-manual.md)
- [User Manual](user-manual.md)
- [Backup / restore / upgrade runbook](../runbooks/backup-restore.md)
- [MinIO object-lock prerequisite](../runbooks/minio-object-lock-prereq.md)

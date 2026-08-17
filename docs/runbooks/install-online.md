# Install (online)

A single Linux host with Docker Compose **2.24.4 or newer**. The minimum is enforced before any
legacy Keycloak migration because the production overlay uses Compose's fail-closed `!reset` merge
tag. Profiles: **S** (≤25 users) or **M** (≤100 users, with 2 API + 2 worker + 2 renderer
replicas). Both shipped profiles use PostgreSQL FTS; OpenSearch and the L profile are reserved,
not deployed. See doc 03 §7 for sizing.

> On a host that is not yet provisioned (no Docker, firewall, or NTP), run
> [`scripts/bootstrap-ubuntu.sh`](../../scripts/bootstrap-ubuntu.sh) first — see
> [install-ubuntu-server.md](install-ubuntu-server.md), which also covers the Windows-side AD DNS,
> service account and GPO certificate trust. This runbook assumes those prerequisites are met.

## Steps

1. **DNS + firewall** — create the app's DNS record before installation. Allow inbound TCP
   **80/443** for the app/Keycloak site and **9443** for the separately secured MinIO S3 origin.
   Browsers use both origins; MinIO itself remains private on the Compose network.

2. **Clone + first-run installer** — generates a `0600` `.env` (random, role-separated DB
   credentials, MinIO keys, app/backup keys, Keycloak admin + database credentials), writes every
   browser-facing URL from one hostname, and brings up the production overlays:

   ```bash
   # Publicly resolvable DNS where Caddy can obtain an ACME certificate:
   ./scripts/install.sh s --host qms.example.com --tls acme

   # Private/corporate DNS with Caddy's internal CA:
   ./scripts/install.sh s --host qms.corp.example --tls internal
   ```

   `--tls acme` is the default; use `internal` when the DNS name is not reachable by a public CA.
   The installer authorizes `https://<host>/*` on Keycloak's `easysynq-web` client, blocks until
   `https://<host>/readyz` is green, and prints the URL. `beat` remains exactly one replica.

   The generated URLs are intentionally paired:

   - `SITE_ADDRESS`, `PUBLIC_BASE_URL`, `APP_BASE_URL`, `KEYCLOAK_HOSTNAME` →
     `https://<host>`;
   - `MINIO_SITE_ADDRESS`, `S3_PUBLIC_ENDPOINT` → `https://<host>:9443`;
   - browser OIDC issuer → `https://<host>/realms/easysynq`, with internal discovery/JWKS URLs for
     the API container.

   Do not point `S3_PUBLIC_ENDPOINT` at the app origin: the app site's catch-all serves the SPA, not
   S3. Do not expose MinIO's plaintext `:9000`; that port exists only in `compose.dev.yml` and is
   loopback-bound. The installer validates both complete tuples before migration/start, then appends
   the selected callback without replacing redirect URIs added by an operator or identity provider.

   The ingestion source defaults to the repository-root `.import-source/` directory. Set
   `IMPORT_SOURCE_PATH` to an absolute host path for a real source tree; Compose resolves relative
   bind-source paths from `infra/compose/`, regardless of where `.env` lives.

3. **Trust the CA when using `--tls internal`.** Export Caddy's root certificate and distribute it
   through the organization's trusted-root mechanism before workstation use:

   ```bash
   docker compose --env-file .env \
     -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
     -f infra/compose/compose.production.yml \
     exec -T proxy cat /data/caddy/pki/authorities/local/root.crt > easysynq-root-ca.crt
   ```

4. **Create the first administrator in the setup wizard** at `https://<host>/setup`.

   1. Run `./scripts/easysynq setup mint-bootstrap`, then enter its one-time secret, the
      administrator username, and profile in the browser. EasySynQ creates the first identity; do
      not visit Keycloak or copy an identity subject.
   2. Save the shown-once temporary password, continue to sign in, and replace that password when
      Keycloak requires it. SMTP is not required.
   3. **Organization** profile (legal name / short code / timezone).
   4. **Storage** — *Verify storage* (the WORM probe, gate **G-B**). The `documents` bucket MUST be
      object-lock-enabled — see [minio-object-lock-prereq.md](minio-object-lock-prereq.md).
   5. **Backup** — set a destination, then *Run backup + restore-test drill*; finalize is blocked
      until it PASSES (gate **G-C** / AC#5). See [backup-restore.md](backup-restore.md).
   6. **Authentication** — pick a method + ack MFA, then *Verify authentication* (gate **G-D**).
   7. **Finalize** → `OPERATIONAL`; the 423 setup latch lifts.

   An **upgrade of a running install** seeds `OPERATIONAL` automatically — no wizard.

5. **Users & Roles** — sign in as System Administrator → **Administration → Users** to create later
   users. `user.create` creates the account; `user.update` edits it; assigning roles separately
   requires `permission.grant`, and a system-tier guard controls password reset. Each temporary
   password is shown once and changes at first sign-in. `POST /users` and host subject tools are
   break-glass/orphan-adoption paths, not the normal install flow.

## First upgrade from the legacy H2 Keycloak service

Batch 13 moves live identity state into the durable PostgreSQL `keycloak` schema. Before the first
Compose recreation from an older `start-dev` install, run:

```bash
./scripts/migrate-keycloak-h2.sh
```

This stops only the legacy Keycloak container, performs Keycloak's offline full realm export
(including users, stable IDs and credential hashes), validates it, and stages it for first
PostgreSQL boot. It restarts the untouched legacy container if export fails. `install.sh` invokes
this automatically and reads a custom `COMPOSE_PROJECT_NAME` from `.env`; run it explicitly before
any raw `docker compose up` during that one transition. Afterward, ordinary Keycloak recreation
preserves accounts and client edits in the PostgreSQL `pgdata` volume. The temporary import volume
is scoped to that Compose project, so multiple installations on one Docker host cannot consume one
another's exported identities.

## Verify it works (release-time security check)

```bash
curl -sI https://<host>/ | grep -iE 'content-security|strict-transport|referrer|x-content-type|permissions-policy'
openssl s_client -connect <host>:443 -tls1_1 </dev/null   # MUST be refused (TLS 1.2 floor)
```
Confirm the SPA loads and a Keycloak login round-trips under the strict CSP (the `style-src` check —
see the Caddyfile note). A non-blocking warning flags the install as **NOT tamper-evident** until an
off-host audit-checkpoint anchor is configured (R13).

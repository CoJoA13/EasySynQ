# Install (online)

A single Linux host with Docker (Compose v2). Profiles: **S** (≤25 users, Postgres-FTS only) or
**M** (≤100 users, full stack). See doc 03 §7 for sizing.

## Steps

1. **Clone + first-run installer** — generates a `0600` `.env` (random DB password, MinIO keys,
   `APP_MASTER_KEK`, `BACKUP_ENCRYPTION_KEY`, `KEYCLOAK_ADMIN_PASSWORD`) and brings the stack up:
   ```bash
   ./scripts/install.sh s      # or: m
   ```
   It blocks until `/readyz` is green, then prints the URL. (`beat` must be **exactly one** replica.)

2. **Point the app at the non-owner DB role** (S6 role separation). The installer sets
   `DATABASE_URL` to the owner by default; for production set it to `easysynq_app` and keep
   `DATABASE_URL_SYNC` (Alembic + the backup/restore/upgrade CLIs) on the owner `easysynq`. Also set
   `AUDIT_LINKER_DATABASE_URL` (the `easysynq_linker` DSN). See `.env.example`.

3. **First-run setup wizard** at `http://<host>/setup`:
   1. Operator runs `easysynq setup mint-bootstrap` → paste the one-time secret to become the first
      **System Administrator**.
   2. **Organization** profile (legal name / short code / timezone).
   3. **Storage** — *Verify storage* (the WORM probe, gate **G-B**). The `documents` bucket MUST be
      object-lock-enabled — see [minio-object-lock-prereq.md](minio-object-lock-prereq.md).
   4. **Backup** — set a destination, then *Run backup + restore-test drill*; finalize is blocked
      until it PASSES (gate **G-C** / AC#5). See [backup-restore.md](backup-restore.md).
   5. **Authentication** — pick a method + ack MFA, then *Verify authentication* (gate **G-D**).
   6. **Finalize** → `OPERATIONAL`; the 423 setup latch lifts.

   An **upgrade of a running install** seeds `OPERATIONAL` automatically — no wizard.

4. **Users & Roles** — sign in as System Administrator → `/admin/users` to invite users (paste their
   Keycloak `sub`; they go `INVITED`→`ACTIVE` on first login), assign seeded roles, enable/disable.

## Verify it works (release-time security check)

```bash
curl -sI https://<host>/ | grep -iE 'content-security|strict-transport|referrer|x-content-type|permissions-policy'
openssl s_client -connect <host>:443 -tls1_1 </dev/null   # MUST be refused (TLS 1.2 floor)
```
Confirm the SPA loads and a Keycloak login round-trips under the strict CSP (the `style-src` check —
see the Caddyfile note). A non-blocking warning flags the install as **NOT tamper-evident** until an
off-host audit-checkpoint anchor is configured (R13).

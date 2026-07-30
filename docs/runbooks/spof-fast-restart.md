# SPOF fast-restart (Keycloak & Beat)

On the single-host profile, **Keycloak** (auth) and **Beat** (scheduler) are explicit single points
of failure (R14). The availability target is **99.0%/month inclusive of both**; 99.5%+ needs the
documented HA path. Both fail safely and self-heal on restart.

## Keycloak is down
**Symptom:** no one can log in (`/readyz` shows `keycloak` unready); existing JWTs keep working until
they expire (auth fails *closed*, P9). The QMS stays readable to anyone with a valid token.
```bash
docker compose -f infra/compose/compose.yml restart keycloak
# wait for health, then confirm:
curl -fsS http://<host>/readyz | grep keycloak       # ready:true
```
Keycloak runs the optimized production server against its dedicated `keycloak` schema in the
existing PostgreSQL service. Accounts, credential hashes, stable subjects, federation settings and
client edits therefore survive a Keycloak restart, image bump, container recreation and ordinary
`compose down`; the authoritative state is in the durable `pgdata` volume. The committed realm file
is imported only when the realm does not yet exist.

Deleting named volumes (`compose down -v`) is a destructive full-stack reset. Recover identity state
with the PostgreSQL backup/cutover procedure in [backup-restore.md](backup-restore.md); the encrypted
realm export remains an additional recovery leg, not the live store.

## Beat is down
**Symptom:** scheduled jobs stall — effectivity-cutover sweep, chain-linker, chain-verify, blob
re-hash, monthly audit-partition roll, nightly backup, mirror reconcile. No data loss; work resumes
and self-heals on restart.
```bash
docker compose -f infra/compose/compose.yml up -d beat
docker compose -f infra/compose/compose.yml ps beat        # MUST be exactly ONE replica
```
A growing **written-but-not-yet-chained** audit tail (chain-linker stalled) is itself alarmed; once
Beat is back the linker catches up. To force a sweep immediately, the relevant CLIs are
`./scripts/easysynq mirror rebuild` (mirror) and `./scripts/easysynq backup run` (backup); chain-linking resumes on the
next Beat tick.

## Budget note
A nightly backup quiesces the DB↔blob snapshot for a short window; counted within the 99.0% budget.
Avoid running many rapid backups/restores in business hours (the aggregate quiesce eats the budget).

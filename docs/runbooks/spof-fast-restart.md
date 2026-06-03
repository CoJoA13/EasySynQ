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
The realm is imported from `infra/compose/keycloak/` on start (`--import-realm`); user accounts live
in Keycloak's own Postgres volume and survive a restart. If the realm itself was lost, restore it
from a backup's realm export (see [backup-restore.md](backup-restore.md)).

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
`easysynq mirror rebuild` (mirror) and `easysynq backup run` (backup); chain-linking resumes on the
next Beat tick.

## Budget note
A nightly backup quiesces the DB↔blob snapshot for a short window; counted within the 99.0% budget.
Avoid running many rapid backups/restores in business hours (the aggregate quiesce eats the budget).

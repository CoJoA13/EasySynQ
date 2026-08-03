# Handoff — EasySynQ at the site, continuing work

> **Read this first if you are a new session picking up the LAB deployment.**
> Written 2026-08-02. The install is **live and operational**; what follows is what remains.
>
> Background, in order of usefulness:
> [implementation plan](2026-07-31-lab-production-deployment.md) (what was done, and every deviation) ·
> [design spec](../specs/2026-07-31-lab-production-deployment-design.md) (why, and the accepted risks) ·
> [install-ubuntu-server.md](../../runbooks/install-ubuntu-server.md) (the generic procedure)

---

## 1. What exists right now

**EasySynQ is in production at `https://easysynq.example.local`** for <ORG>
(`example.local`), a ~5-user heat-treat shop. State `OPERATIONAL`, restore drill `PASS`.

| | |
|---|---|
| Host | `LAB` — Windows 11 Pro, domain-joined, Hyper-V |
| Guest | VM `EasySynQ`, Ubuntu 26.04, **`10.0.0.20`**, MAC `00:15:5D:00:00:01` |
| Reach it | `ssh easysynq@10.0.0.20` (key auth from LAB, no password) |
| Repo on VM | `~/EasySynQ` at commit `4f49c0f` |
| Domain controller | `dc01.example.local` @ `10.0.0.10` — also file server **and** SQL host |
| Import source | `//DC01/Quality` → `/srv/easysynq/import` (**ro**), 252 files |
| Backup target | `//DC01/easysynq-backup` → `/srv/easysynq/backup` (**rw**) → whole-disk image → the offsite object store |
| Admin | `qmsadmin` / the owner — System Administrator |

**Always use all four compose files, in this order.** Omitting `compose.lab.yml` resolves a
different backup volume:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml \
  -f infra/compose/compose.lab.yml <cmd>
```

### Proven, not assumed

Full host-reboot resilience was tested 2026-08-02: LAB rebooted → VM auto-started → both CIFS
mounts landed → containers restarted → `/readyz` green, all inside ~1 minute. Network identity,
DNS override and beat's schedule state all survived. Four encrypted archives sit on the DC01
share, at least one written unattended by the nightly job.

---

## 2. The work queue

### A. Import the QMS tree — **do this first, needs the owner**

The plumbing is correct and verified (worker sees 252 files, read-only). Nothing has been imported;
that is a deliberate, reviewed action, not an automatic one.

Pipeline: **scan → extract/classify → human review → commit.** Stages 1–2 are mechanical and can be
run unattended. Stage 3 needs the owner's judgment:

| Path | Files | Confirm as | Why |
|---|---|---|---|
| `QMS/` | 195 | **DOCUMENT** | SOPs, work instructions, forms, Quality Manual — the 7-state lifecycle |
| `QMS_DATA/` | 56 | **RECORD** | Calibration certs, crane inspections, King Tester certs — retention/disposition |

**Exclude:** 37 × `Thumbs.db`, `QMS.zip` (18.5 MB archive snapshot), one `*.xlsx#` lock artifact.
Confirm the single `.pdc` before deciding.

⚠ **Open CARs are a third case.** Owner's framing: `QMS_DATA` is "audit data, certifications, CARs".
Historical *closed* CARs are records. Anything still **open** is better recreated as a live CAPA so
it inherits due dates, escalation and the overdue sweep — a static PDF gets none of that. Decide
before commit; moving a record into the CAPA workflow afterwards is not a rename.

The clause-folder structure (`1.0 Scope` … `10.0 Improvement`) maps onto EasySynQ's clause spine, so
folder path is strong evidence for clause mapping.

### B. PR 1 — clause IA + Library polish — **approved, not started**

1. **Clause 7 (Support) → DO.** Currently seeded `PLAN` for clause 7 *and its whole subtree*.
   Needs: **R62 decisions-register entry (owner-approved; R61 is now the site-data rule)** → seed edit in
   `apps/api/src/easysynq_api/db/seeds/iso9001_clauses.py` → migration **`0084`** (head is `0083`;
   this install already holds the old values) → `docs/02` §3.2 **and** its section heading, which
   currently reads `### Clause 7 — Support (PLAN/DO)`.
   ⚠ Leave `import_classification.pdca_phase` alone — it is a *derived stored snapshot* of what the
   classifier concluded; rewriting it would falsify an analysis record. Moot anyway: zero import runs.
   Side effect: the hardcoded `PLAN: Cl 4–6` / `DO: Cl 7–8` labels become *correct*.
2. **Remove the clause dropdowns from the nav rail** (`apps/web/src/app/shell/LeftRail.tsx`). They
   are only `/library?clause=N` links, they render **top-level clauses only**, and the filter is
   exact-match — so **every one returns zero documents**. Removal also kills `activeClauseTop`,
   `openedPhases` and `useClauses()` in that file, dropping an API call from every page load.
3. **Fix the button height** in `apps/web/src/features/library/ClauseTree.tsx:57`. `size="compact-sm"`
   has a fixed height while `whiteSpace: "normal"` lets labels wrap, so wrapped titles overflow into
   the row below. Add `height: "auto"`, `minHeight: var(--button-height-compact-sm)`, vertical
   padding, `lineHeight: 1.35`, plus a hanging indent so wrapped text aligns under the title rather
   than the clause number.
4. **Collapse sub-clauses** to the selected top-level clause only (~40 rows → ~12).

Owner decided **against** truncating clause titles: two lines is readable once the height is fixed,
and clause titles are the ISO vocabulary an auditor scans for. If it still feels dense afterwards,
use a **two-line clamp**, never a one-line ellipsis, and Mantine `Tooltip` rather than `title`.

Triggers the `migrations` CI job; run the `migration-reviewer` agent.

### C. PR 2 — clause subtree rollup — **approved, own review**

`?clause=8` should match `8` **and its descendants**, at every level. Today it is an exact string
match, so top-level clauses always return zero.

⚠ Match `number = '8' OR number LIKE '8.%'` — **not** `LIKE '8%'`, which makes clause 1 match 10.

Not blocked by the deferred per-clause counts: counts are an authz-sensitive aggregation, rollup is
just a wider `WHERE` with the existing per-row filter still applying. Touches the query, its tests,
`packages/contracts/openapi.yaml` and `docs/15-api-design.md`, so it runs the `contracts` job.

### D. Deployment loose ends

| Item | Detail |
|---|---|
| **Remove deployment sudo** | `sudo rm /etc/sudoers.d/90-easysynq-deploy` — passwordless sudo was for provisioning; leaving it means the SSH key on LAB is root on the QMS host |
| **SMTP relay** | `OPS_ALERT_CHANNELS` / `OPS_ALERT_SMTP_TO` are empty, so a failed nightly backup notifies **only in-app** — via the database that may be what failed |
| **DHCP reservation** | Ask IT to reserve `10.0.0.20` for `00:15:5D:00:00:01`. An exclusion alone is **not** equivalent — the VM runs `dhcp4: true`, so excluding `.20` denies it to this VM at renewal; pair any exclusion with a static netplan address (plan Task 4). Device is a **edge firewall** at `10.0.0.1` (mgmt on `:8080`, WSM on 4117/4118). If IT manages it from a saved Policy Manager config, ask them to record it there or a push will erase it. |
| **Deny-logon GPO** | Both service accounts (`svc-easysynq-ro`, `svc-easysynq-bkp`) need *Deny log on locally* + *Deny log on through Remote Desktop Services* in a **new** GPO. Leave *Deny access from the network* alone — the mounts need it. |
| **R13 anchor** | Install reports **NOT tamper-evident** until an off-host audit-checkpoint anchor exists somewhere this host's operator cannot rewrite. Non-blocking, real for an ISO 9001 audit trail. Scheduled, not waived. |

### E. Defects found, logged, not fixed

1. **`scripts/easysynq` uses only `compose.yml`** (line 7). Every `backup`/`restore`/`upgrade`/
   `mirror` call resolves without the profile, production and site overlays, and recreates services
   whose config differs. Harmless so far only because Docker will not mutate an existing volume.
   Workaround: `docker compose <all four -f> run --rm worker uv run python -m easysynq_api.cli.<mod>`.
2. **`backup_policy.cron` is write-only.** The wizard stores `0 2 * * *`; the scheduler uses a
   hardcoded `86400.0` interval (`apps/api/src/easysynq_api/tasks/app.py:58`) and never reads it.
   Backups fire 24 h after beat last **started**, so they currently run ~2:18 PM Chicago, and every
   container *recreation* (not reboot — the writable layer survives a restart) moves the time.
3. **Realm export once recorded `absent`.** Observed once; succeeded on retry and on the unattended
   run. Cause NOT established — do not repeat the earlier "pg_dump kills Keycloak backends" theory:
   `_capture_and_dump()` only holds a read snapshot and rolls back, it terminates nothing. Suspect
   admin credentials, Keycloak availability, or container churn, and investigate if it recurs. Not
   recovery-critical here: the `keycloak` schema (100 tables) is inside the dump.

---

## 3. Traps already paid for — do not rediscover these

| Symptom | Cause |
|---|---|
| `Invalid parameter: redirect_uri` | Mixed-case FQDN. Keycloak matches redirect URIs **case-sensitively**; browsers lowercase the host. Typing the URL differently cannot help. **Use lowercase everywhere.** |
| Correct password rejected | Keycloak brute-force lockout (`user_temporarily_disabled`). Survives a password reset. → `./scripts/clear-keycloak-lockout.sh <user>` |
| `Invalid bootstrap secret` | The CLI prints it **indented four spaces**; copying the whitespace fails identically to a wrong secret. Verify by character count. |
| Containers recreate unexpectedly | `scripts/easysynq` overlay bug — see D/E above. |
| `systemctl is-masked` errors | Not a verb on systemd 259. Use `systemctl is-enabled` (reports `masked`). |
| Import finds nothing | Check `IMPORT_SOURCE_PATH=/srv/easysynq/import` in `.env` — it defaults to `../../.import-source`, an empty dir. This was missed once already. |

**Schema names that mislead:** `system_config` holds the bootstrap fields · `role_assignment` is
user→role, `role_grant` is role→permission · there is **no** `backup_run` table; run state lives on
`backup_policy.last_restore_test_*`.

---

## 4. Owner's backlog — captured, not yet specified

Raised 2026-08-02 from real friction during this deployment. **Not designed or scoped** — recorded
so they are not lost. Each needs its own brainstorm before any work.

1. **Easier user creation.** Today it is two disconnected steps: create the Keycloak account, then
   paste its `sub` into Administration → Users. `scripts/new-keycloak-user.sh` reduces the friction
   but does not remove the seam.
2. **Permission-key visibility under "Manage" on a user.** What a user can actually do is not
   legible from the user surface.
3. **Browse/picker when importing**, instead of typing a source path by hand.

---

## 5. Git state

- Branch **`feat/ops-lab-deployment`** — pushed, PR open, CI running. Contains the two Keycloak admin
  scripts, two runbook fixes, and the deployment spec + plan.
- This handoff is **uncommitted**.
- `gh auth login` now works on LAB. Earlier sessions could not push (permission classifier); if a
  push is blocked, the owner runs it from a terminal on LAB.

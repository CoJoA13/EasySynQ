# Handoff — EasySynQ at the site, continuing work

> **Read this first if you are a new session picking up the site deployment.**
> Written 2026-08-02. The install is live; what follows is what remains, generalized.
>
> **Where the concrete state lives (R61):** the current-state worksheet — host/guest identity,
> addresses, access paths, the admin account, the source-tree inventory, and the outstanding
> go-live security gates — is a site record and lives in the organization's own operational
> documentation, **not** in this repository. This file keeps only what transfers: procedures,
> judgment calls, traps, and the product work queue.
>
> Background, in order of usefulness:
> [implementation plan](2026-07-31-lab-production-deployment.md) (what was done, and every deviation) ·
> [design spec](../specs/2026-07-31-lab-production-deployment-design.md) (why, and the accepted risks) ·
> [install-ubuntu-server.md](../../runbooks/install-ubuntu-server.md) (the generic procedure)

---

## 1. The operating rule that bites first

**Always use all four compose files, in this order.** Omitting the untracked site overlay
(`compose.lab.yml`) resolves a different backup volume:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml \
  -f infra/compose/compose.lab.yml <cmd>
```

---

## 2. The work queue

### A. Import the QMS tree — **do this first, needs the owner**

The mount plumbing is verified read-only; nothing has been imported. That is a deliberate,
reviewed action, not an automatic one.

Pipeline: **scan → extract/classify → human review → commit.** Stages 1–2 are mechanical and can
be run unattended. Stage 3 needs the owner's judgment. The source tree splits into a documents
branch (SOPs, work instructions, forms, the Quality Manual — confirm **DOCUMENT**, the 7-state
lifecycle) and a data branch (calibration and inspection certificates — confirm **RECORD**,
retention/disposition). The exact folder inventory, counts, and exclusions are in the site
worksheet and in plan Task 11.

⚠ **Open CARs are a third case.** Historical *closed* CARs are records. Anything still **open**
is better recreated as a live CAPA so it inherits due dates, escalation and the overdue sweep — a
static PDF gets none of that. Decide before commit; moving a record into the CAPA workflow
afterwards is not a rename.

A clause-numbered folder structure (`1.0 Scope` … `10.0 Improvement`) maps onto EasySynQ's clause
spine, so folder path is strong evidence for clause mapping.

### B. PR 1 — clause IA + Library polish — **approved, not started**

1. **Clause 7 (Support) → DO.** Currently seeded `PLAN` for clause 7 *and its whole subtree*.
   Needs: **R62 decisions-register entry (owner-approved; R61 is now the site-data rule)** → seed edit in
   `apps/api/src/easysynq_api/db/seeds/iso9001_clauses.py` → migration **`0084`** (head is `0083`;
   a live install already holds the old values) → `docs/02` §3.2 **and** its section heading, which
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

### D. Go-live security gates — tracked in the site worksheet

The outstanding gates (deployment-sudo removal · out-of-band alerting · DHCP reservation, never a
bare exclusion · the deny-logon GPO · the R13 off-host anchor) are **site state** and are tracked
in the organization's operational documentation. Their generic procedures stay in this repo: plan
**G-1/G-2**, **Task 4 Step 1**, **Task 10 Step 5**; the R13 anchor is the named audit
checkpoint-lineage residual in `docs/slice-history.md`.

### E. Product defects found here, logged, not fixed

1. **`scripts/easysynq` uses only `compose.yml`** (line 7). Every `backup`/`restore`/`upgrade`/
   `mirror` call resolves without the profile, production and site overlays, and recreates services
   whose config differs. Harmless so far only because Docker will not mutate an existing volume.
   Workaround: `docker compose <all four -f> run --rm worker uv run python -m easysynq_api.cli.<mod>`.
2. **`backup_policy.cron` is write-only.** The wizard stores `0 2 * * *`; the scheduler uses a
   hardcoded `86400.0` interval (`apps/api/src/easysynq_api/tasks/app.py:58`) and never reads it.
   Backups fire 24 h after beat last **started**, and every container *recreation* (not reboot —
   the writable layer survives a restart) moves the time.
3. **`realm_export: absent` in a backup is not pg_dump's fault.** `_capture_and_dump()` only holds
   a read snapshot and rolls back — it terminates nothing. Suspect admin credentials, Keycloak
   availability, or container churn, and investigate if it recurs. A single absence is not
   recovery-critical: the `keycloak` schema (100 tables) is inside the dump.

---

## 3. Traps already paid for — do not rediscover these

| Symptom | Cause |
|---|---|
| `Invalid parameter: redirect_uri` | Mixed-case FQDN. Keycloak matches redirect URIs **case-sensitively**; browsers lowercase the host. Typing the URL differently cannot help. **Use lowercase everywhere.** |
| Correct password rejected | Keycloak brute-force lockout (`user_temporarily_disabled`). Survives a password reset. → `./scripts/clear-keycloak-lockout.sh <user>` |
| `Invalid bootstrap secret` | The CLI prints it **indented four spaces**; copying the whitespace fails identically to a wrong secret. Verify by character count. |
| Containers recreate unexpectedly | `scripts/easysynq` overlay bug — see §2E above. |
| `systemctl is-masked` errors | Not a verb on systemd 259. Use `systemctl is-enabled` (reports `masked`). |
| Import finds nothing | Check `IMPORT_SOURCE_PATH=/srv/easysynq/import` in `.env` — it defaults to `../../.import-source`, an empty dir. This was missed once already. |

**Script availability:** a checkout that predates PR #419 lacks
`scripts/clear-keycloak-lockout.sh` and `scripts/new-keycloak-user.sh`. Update with
`git -C ~/EasySynQ pull --ff-only` once that PR is on `main` — safe for site-local state: the
site compose overlay and `.env` are untracked, and neither a fast-forward pull nor a checkout
touches untracked files.

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

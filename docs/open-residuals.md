# Open residuals

This is the sole current, owner-visible ledger for deliberately deferred work. Each stable `RES-*` record
stays open until its closure contract ships with linked evidence. Dated `Named residuals` prose in
[`slice-history.md`](slice-history.md) is historical snapshot evidence, not a second live ledger.
Each record is mirrored by a GitHub issue labelled `residual` (title `[RES-…]`) so the project board
can track it; the record here, not the issue, is authoritative, and the issue closes when the record
is removed with its closure evidence.

## RES-SOURCE-INDEPENDENT-RECOVERY

Status: OPEN
Owner: Repository owner
Source: Recovery reconciliation, 2026-09-08, against `6077e8a45b5942daf803220765b326f82ddd4417`;
the [current recovery runbook](runbooks/backup-restore.md), pinned
[archive](https://gitlab.com/synqsuite-group/EasySynQ/-/blob/6077e8a45b5942daf803220765b326f82ddd4417/apps/api/src/easysynq_api/services/backup/archive.py),
[backup](https://gitlab.com/synqsuite-group/EasySynQ/-/blob/6077e8a45b5942daf803220765b326f82ddd4417/apps/api/src/easysynq_api/services/backup/drill.py), and
[restore](https://gitlab.com/synqsuite-group/EasySynQ/-/blob/6077e8a45b5942daf803220765b326f82ddd4417/apps/api/src/easysynq_api/services/backup/restore.py)
source; historical
[C-01/C-01b/M-01 contracts](superpowers/plans/2026-08-04-audit-remediation-v2.md#11-integrity-and-recovery).
Current execution is tracked by [GitHub issue #557](https://github.com/CoJoA13/EasySynQ/issues/557);
the archived [GitLab issue #3](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/3) keeps its history.
Reason: Current archives contain the database and an object locator/hash manifest, but not the referenced
object bytes; restore verification reads and copies those bytes from the configured source store. The
shipped integrity path is therefore useful but source-dependent, and no closed recovered stack has been
proven from a complete independent generation. Server-verified staged digests, the non-root API runtime,
and monotone retention extension already ship and are not reopened by this record.
Closure contract: Produce one sealed, complete, encrypted generation that binds every referenced object to
its exact version and to matching database, identity/configuration, and audit-checkpoint state. Require
separately scoped ordinary and recovery/administrative capabilities, a certified worker-owned durable
destination, and fresh role-preserving restore targets with durable terminal
disposition. With source-store reads denied, restore and boot the closed recovered stack and prove document,
record, sealed-pack, and rendition reads before access reopens. Link the exact evidence before evaluating
production recovery or upgrade eligibility. The scratch-target guard is closed with
[dated evidence](slice-history.md#s-restore-scratch-worm-guard--protected-target-rejection-before-copy).
Audit task coverage is also closed with
[dated evidence](slice-history.md#s-audit-verify-orchestrator--durable-alarms-and-engine-lifecycle).
Migration lock-wait coverage is closed with
[dated evidence](slice-history.md#s-upgrade-lock-timeout--bounded-online-migration-lock-waits) under R74.
The narrower [RES-AUDIT-CHECKPOINT-LINEAGE](#res-audit-checkpoint-lineage) and
[RES-AUDIT-KEY-ROTATION](#res-audit-key-rotation) records keep their separate ownership and closure
contracts.
Raw-transport progress on 2026-09-09 (R79): an inactive API preserves exact retained checkpoint
bytes and version identity, with actual-image/provider consumption by unchanged R78. It changes no
archive, restore or operational verifier. Complete per-witness collection, scalable global history,
SDK resource/lifetime containment, actual snapshot agreement and a source-denied recovered-stack
proof remain required; this record stays OPEN.
Isolated-read progress on 2026-09-09 (R80): the inactive exact-read worker now enforces resource
limits and post-cleanup result admission, with actual-image hostile-stream and lifecycle proof.
No archive, restore or operational verifier changes. Complete per-witness page collection, bounded
spooling/global reconciliation, snapshot agreement and source-denied recovered-stack proof remain
required; this record stays OPEN.
Supplied-page progress on 2026-09-09 (R81): original version-list bytes can now be decoded under
strict UTF8/XML admission without losing opaque labels, duplicate records or delete markers.
Actual-image supplied-byte tests do not prove collection. Isolated exact page transport, complete
required-witness collection, bounded spool/global reconciliation and the unchanged source-denied
recovered-stack closure contract remain required; this record stays OPEN.
Original-page transport progress on 2026-09-11 (R83): the inactive isolated API now admits one
original version page only after exact request/response validation and worker cleanup. Genuine TLS
provider pagination and hostile response/IPC/resource acceptance passed. R81 additionally admits
three bounded discarded DeleteMarker metadata scalars; no archive, restore or operational verifier
is activated. Whole required-witness binding/traversal, a fresh private bounded spool, sticky gaps
and global cursor cycles, global reconciliation, actual snapshot agreement and the source-denied
recovered-stack proof remain required. Finite fixture pagination does not satisfy those contracts;
this record and GitLab issue #3 remain OPEN.
Required-witness traversal progress on 2026-09-11 (R84 candidate): the inactive collector binds
every required namespace before I/O, retains observed original pages/bodies and sticky gaps in one
fresh bounded private spool, and publishes diagnostics only after cleanup. Root verified the full
new-case 1,003-GET/three-page provider proof and real resource/storage controls as part of the
passing ten-case mandatory harness. The MEMORY spool is abandoned after interruption
and cannot be resumed. This supplies neither global R77/R78 reconciliation nor an authenticated
snapshot, archived object generation or actual recovered-stack proof. Database comparison,
independent custody/delivery, protected rollback memory and the source-denied boot/read closure
contract remain required. This record and issue #3 stay OPEN; no restore or activation is enabled.
Last reviewed: 2026-09-11

Global reconciliation progress on 2026-09-12 (R85 candidate): the inactive API now checks collected
legacy/bootstrap and v2 history globally in one owned lifetime, with whole-path coverage at each
required witness and compact output after cleanup. Eleven mandatory image cases passed, including
genuine TLS missing-copy/consistent/late-conflict cases and separate large synthetic closure.
This supplies observed-history consistency only; no archive generation, actual database snapshot
comparison, independent custody/delivery, protected rollback memory or source-denied recovered-stack
boot/content-read proof is added. The closure contract and issue #3 remain OPEN. See
[current status](current-status.md) for candidate integration state. Last reviewed: 2026-09-12.
Delivery, 2026-09-18: MR [!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46) merged R85 on 2026-09-12 as `9158a5d`; its final
[source](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843299684) and
[merged-main](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970) pipelines passed all fourteen required jobs,
including eleven mandatory runtime cases. Nothing above is activated
or changed by the merge; this record stays OPEN. Last reviewed: 2026-09-18.
Exact-version progress, 2026-09-20 (candidate): the first bounded step of this record's
exact-version boundary ships persistent object-version binding. Migration `0093` adds
`blob.object_version_id`/`object_version_source` under a CHECK; the four WORM write paths bind the
version their verified promotion already read back and discarded, and the four renditions paths
record `unversioned` because that bucket has none. Manifest v3 carries the binding and a generation
state (`sealed`/`observed`/`partial`/`absent`), v2 archives still restore unchanged, the copy and
stored-locator legs resolve the bound version instead of the current one, and the re-hash now also
checks the recorded length. `backup bind-versions` binds pre-0093 rows as `backfill`, attesting only
the version observed at backfill time. Proven against real PostgreSQL and MinIO: an object
overwritten AFTER its generation was written restores from the sealed version, and reverting the
version-aware copy turns that case red; a bound version that cannot be resolved and a size
disagreement each FAIL, and each has its own mutation. This closes NOTHING in the contract above:
the archive still carries no object bytes, restore stays source-dependent and non-cutover, and
service-capability separation, complete encrypted generations (still whole-archive-in-memory),
certified destinations, fresh role-preserving targets and the source-denied boot-and-read proof all
remain required. See the
[design](superpowers/specs/2026-09-20-recovery-exact-version-binding-design.md).
Delivery, 2026-09-22: merged in
[!63](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/63) (squash `909fef6`); its
[merged-results pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2869629501) passed
all eleven jobs it ran, including the migration suite and eleven mandatory runtime cases. Review
added the manifest-version input to the generation state (a v2 archive now reports `absent`, a
never-backfilled v3 generation `partial`) and closed the backfill command's never-raise contract.
The contract above is untouched by the merge; this record stays OPEN. Last reviewed: 2026-09-22.

## RES-CONTAINER-SECURITY-TRIAGE

Status: OPEN
Owner: Repository owner
Source: GitLab repository setup audit, 2026-09-08. Tracked since 2026-09-22 by
[GitHub issue #558](https://github.com/CoJoA13/EasySynQ/issues/558); archived GitLab issue #4 keeps its history.
Reason: Actual built-image scans still contain high/critical OS findings without a reported fixed
version. Their package-level applicability, alternative remediation and disposition remain open.
The required security job now scans both built application images and blocks HIGH/CRITICAL findings
with a reported fixed version while reporting no-fix findings as OPEN. The live npm policy remains
enforced; filesystem and pip-audit findings remain advisory. This bounded threshold is not release
image security clearance. Keep detailed vulnerability inventories outside Git under R61.
Closure contract: Triage findings against the actual built release images, apply available fixes
with runtime verification, record justified exceptions in the approved external security evidence,
scan the built web image, and adopt an explicit reviewed gate for actionable high/critical findings.
Link fresh scan and runtime evidence when closing this record; do not blanket-ignore findings or
describe report-only scanner success as a clean result.
Progress, 2026-09-08: Fresh built API/web scans and unprivileged offline runtime smokes were collected
against main `5577178`; compatible web tooling candidate `dcd22d7` in
[!15](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/15) was rebuilt and rescanned.
Its baseline fix-available tooling findings are absent, actual npm/Node/runtime and Renovate
extraction were verified, and the live application-lock audit returned `blocked: 0`.
The fixed-version image gate retains no-fix findings and fails closed on scanner/report errors.
Detailed evidence remains external; no exception, risk acceptance, production exposure conclusion,
or full residual closure has been created. [Issue #4](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/4)
tracks the remaining work.
Progress, 2026-09-12: MR
[!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46)'s first source pipeline
blocked two fix-available base-runtime findings. A targeted API-image package update passed a
fresh built-image scan with zero blocking findings and 85 no-fix findings still OPEN. All eleven
mandatory runtime cases and 290 affected tests passed; scan/runtime filesystem identity and
owned cleanup were independently verified. The unchanged web image's latest source-pipeline
scan reports 55 no-fix findings OPEN and zero blocking findings; it was not rescanned locally
for the API-only update. Fresh source/main gates remain pending. Detailed inventories remain
external, with no exception, exposure conclusion or residual closure.
Subsequent progress, 2026-09-12: The next source pipeline at `2113200` confirmed the API threshold
passed but found twelve fix-available web base-package findings. Targeted web-image upgrades passed
a fresh built-image scan with zero blocking findings / 43 no-fix findings OPEN, 123 affected tests,
and offline preview/page/SPA checks plus all 29 referenced JS/CSS assets on that same scanned image. Node 26,
the npm pin, application locks and scanner policy remain unchanged; owned cleanup was verified.
API no-fix findings remain 85, with its build/proof inputs unchanged. Fresh source/main gates are
still required; this progress does not close the residual or establish image-security clearance.
Delivery, 2026-09-18: Both updates merged with R85 in
[!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46). The final
[source](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843299684) and
[merged-main](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970) security jobs report
API zero blocking / 85 no-fix OPEN and web zero blocking / 43 no-fix OPEN. The no-fix findings are
untriaged, so this record and issue #4 stay OPEN.
Progress, 2026-09-19 (candidate): Every remaining no-fix finding in both images was an OS package
from the base image, and the API image was the only one still on Debian 12. Moving its base to
`python:3.12-slim-trixie` with trixie-pgdg (Python 3.12.14 and `pg_dump` 18.x unchanged; SQLite
3.40.1 → 3.46.1) cut the API image from **85 to 47 no-fix findings, with none CRITICAL**, under
the repository gate (zero blocking in both images; web unchanged at 43). At `80ab9b2`, all **11
mandatory runtime cases** passed in 582 seconds and **4,939 API unit tests** passed with the built-image
proof enabled. The remaining findings are the base-OS set both images now share. Their
applicability triage belongs in a confidential GitLab issue rather than in Git, and the record and
issue #4 stay OPEN until that triage and any justified dispositions exist.
Progress, 2026-09-19 (merged and candidate): !51 merged the trixie base. The remaining findings
reduce to eight base-OS CVEs; their per-package applicability triage is in confidential issue #5,
outside Git under R61. None is reachable in the shipped configuration, and none is risk-accepted.
The triage also found that the shipped Compose services set no capability or privilege-gain limits,
and that both images carried setuid/setgid tools. The candidate hardening clears those bits in both
images and runs `migrate`, `api`, `worker`, `beat` and `web` with `cap_drop: [ALL]` and
`no-new-privileges`. The proxy keeps its bind capability. A live stack under those settings passed
health checks, a verified encrypted backup, the restore drill and a mirror rebuild. Scanner counts
are unchanged, because this removes attack surface rather than packages. This record and issue #4
stay OPEN until Debian fixes land or the dispositions are otherwise closed.
Delivery, 2026-09-19: The hardening merged in
[!52](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/52) (squash `c5cc68d`). The
[merged-main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2863960314) at `6017f1a`
passed all fourteen jobs, with eleven mandatory runtime cases and a security job reporting API 0
blocking / 47 no-fix and web 0 / 43. Remaining to close: the Debian fixes for the eight triaged CVEs,
and an optional read-only root filesystem for the application services, which needs a writable-path
inventory and is not done.
Last reviewed: 2026-09-19

## RES-IP-REGISTER-COLUMN-JUMP

Status: OPEN
Owner: Repository owner
Source: S-ui-5c, 2026-08-29 (owner-deferred at the S-ui-5 walkthrough close)
Reason: The interested-parties register's columns change width when the filter selection changes, so
rows appear to shift sideways between two views of the same data. The cause is
`table-layout: auto` sizing each enum column to the widest value currently rendered: filtering to a
subset removes the widest `Category`, `Influence`, `Interest` or `Status` value, the column shrinks,
and every column after it moves. The known fix is `layout="fixed"` on the register's `Table` with
pinned pixel widths on those four columns. The adversarial reviewer called pinned widths fragile —
they rot the moment a label changes or a new enum value is added — and required that the widths be
harvested LAST, because S-ui-5c's `white-space: nowrap` on `SortableTh` changed the header
min-content of every register and any width measured before it is stale. The owner reviewed this
against the other two walkthrough items and deferred it as the lowest-value of the three.
Closure contract: Either harvest the post-S-ui-5c column widths in a real browser, pin them under
`layout="fixed"`, and add a Playwright case to `apps/web/e2e/register-table-legibility.spec.ts` that
measures one column's left edge in two filter states and fails when it moves; or establish that a
min-width floor per enum column is stable enough without pinning exact widths, and prove that
instead. jsdom cannot see either, so a Vitest assertion is not acceptable evidence. If neither is
worth the fragility, record that the columns stay fluid and remove this record.
Last reviewed: 2026-08-29

## RES-CAPA-LIST-TABLE-NO-SCROLL-CONTAINER

Status: OPEN
Owner: Repository owner
Source: S-ui-6, 2026-08-30
Reason: S-ui-6 gave `/capa` its first browser coverage, but that coverage measures the BOARD view
only. The page's `List` view renders a bare `<Table>` with no `Table.ScrollContainer`, which is why
`CapaBoardPage.tsx` is absent from the nine files pinned by
`apps/web/src/lib/responsiveRegisterContract.test.ts` and why the shared table-shaped specs cannot
reach it — the same fact that made a `REGISTER_CASES` entry the wrong unlock in the first place. So
after a slice titled "give the CAPA board browser coverage", the one table on `/capa` is still the
unmeasured surface: its five columns can overflow a narrow viewport with no scroll affordance and no
gate would see it, which is exactly the defect S-ui-5c fixed for the other ten registers by giving the
theme `ScrollArea` a `type: "auto"` that this table never receives because it has no ScrollArea. The
open question is whether the CAPA list should join the nine-page cohort at all, or whether a kanban
board with a secondary list is a different shape that wants a different answer — which is a design
call, not a defect to fix silently inside a coverage slice.
Closure contract: Decide whether `/capa`'s List view joins the `responsiveRegisterContract` cohort.
If it does, wrap its `<Table>` in a `Table.ScrollContainer` with a `minWidth`, add the file to that
test's nine-file list (making it ten), and extend `apps/web/e2e/capa-board.spec.ts` with a case that
switches to the List view and asserts localized horizontal scrolling at a width where the table
overflows — probing a RANGE of widths first, since the other registers reproduce at 1000 and 1115
rather than at 1280. If it does not, record why a board's secondary list is exempt and remove this
record. `/capa` also has no 320px case and no denied/granted header pair, unlike the ten
`REGISTER_CASES` routes; 320px was measured clean by hand during S-ui-6 but is unpinned, and folding
it in belongs with whichever answer is taken here.
Last reviewed: 2026-08-30

## RES-REGISTER-PAGE-FRAME

Status: OPEN
Owner: Repository owner
Source: S-ui-4, 2026-08-29
Reason: Twelve register pages still hand-roll the same four-branch scaffold — a forbidden branch, a
loading branch, an error branch and the loaded page, each wrapping its body in its own `Container`.
S-ui-4 shared the header inside those branches but not the scaffold around them, and three findings
from its adversarial review are why. An always-taken return destroys TypeScript control-flow
narrowing on the five pages that currently guard with a narrowing early return, so a frame would
need a generic render-prop body rather than `children`. Rendering the page title during loading —
which the frame would do, and which is the better behaviour — breaks two suites that identify the
loaded state by its heading alone (`AuditsListPage.test.tsx` and `DcrsRegisterPage.test.tsx`'s
equal-width contract). The third blocker is CLOSED: `CapaBoardPage` was the one page whose
branches disagreed on container size, `md` in three branches against `xl` in the loaded page.
S-capa-width-railfoot-order unified every `/capa` container at `xl` while fixing a separate defect
(the tab strip shifting between faces), so that unification has already happened and IS the intended
visual change this record's closure contract asked a frame slice to record: the board's forbidden,
loading and error branches widened from 960 to 1320 pixels. Two blockers remain — the narrowing
early return and the two heading-gated suites.
Closure contract: Either build the frame with a render-prop body and re-anchor the two heading-gated
suites on a load-only sentinel; or record that the scaffold stays per-page and remove this record. A
shared table wrapper is separately blocked and must not be attempted:
`apps/web/src/lib/responsiveRegisterContract.test.ts` is a source-text contract requiring each of
nine page files to contain its own literal `<Table.ScrollContainer minWidth={N}>`.
⚠ A SECOND source-text contract now constrains the frame the same way, and it was added by the same
slice that closed the third blocker, so it is recorded here rather than discovered mid-implementation:
`apps/web/src/lib/tabSectionWidthContract.test.ts` requires each tabbed section's layout AND every
face to carry its own literal `<Container size="…">`. A frame that owns the Container leaves one
width where the contract expects one per face, and its per-section assertion fails. Relaxing that
contract for a section whose faces delegate to a frame is part of this record's work, not a surprise
against it.
Last reviewed: 2026-09-02 (third blocker closed by S-capa-width-railfoot-order)

## RES-DOC11-TOKEN-DRIFT

Status: OPEN
Owner: Repository owner
Source: S-ui-1 to S-ui-3, 2026-08-29
Reason: `docs/11-ui-ux-design-system.md` now contradicts the shipped design tokens on four points. It
names Inter as the self-hosted sans and JetBrains Mono as the monospace, where the shipped stack is
Archivo plus a system monospace; it gives the accent and the focus ring as `#2A6FDB`, where the
shipped accent is the brand mark's teal and the focus ring is a solid teal token corrected for WCAG
2.2 SC 1.4.11; and it states shell metrics of a 56px top bar and a 264px rail, where the shipped
layout tokens are 58px and 244px and `AppShell` now reads them. The accent and focus-ring
divergences predate this program, but were inert while nothing consumed the tokens; S-ui-1 is the
slice that made them authoritative, so the divergence is now load-bearing rather than latent. The
program plan schedules the correction under its final sweep slice, which has not shipped, and a
deferral recorded only in a plan file is not in the live ledger this document owns.
Closure contract: Bring doc 11 into agreement with `apps/web/src/theme/tokens.css` for typography,
accent, focus ring and shell metrics, citing the token names rather than restating literal values so
the two cannot drift again; or, if a value in doc 11 is the intended design and the token is wrong,
change the token instead and prove the contrast gate still passes. Either way the fix must name
which of the two documents is authoritative for a design value.
Last reviewed: 2026-08-29

## RES-ARCHIVO-SYMBOL-GLYPHS

Status: OPEN
Owner: Repository owner
Source: S-ui-1, 2026-08-29
Reason: The self-hosted Archivo subsets ship Google's standard `latin` and `latin-ext` unicode
ranges, which contain no glyph for the canonical non-colour status vocabulary in `lib/status.ts`
(`checkmark`, `quarter-circle`, `cross`, `filled` and `hollow circle`, `star`) nor for the left and
right arrows, although they do contain the up and down arrows. Those characters therefore render
from the fallback stack while the surrounding label renders in Archivo. Measured in the browser
against the built bundle: every affected glyph renders at a normal advance width, so this is a
typeface inconsistency, not missing or tofu output, and the status vocabulary falls back uniformly
so the DP-5 non-colour channel stays internally consistent within itself. Widening the declared
`unicode-range` cannot fix it, because the subset files do not contain the glyphs to begin with.
Closure contract: Either accept the mixed rendering and record it against DP-5 in doc 11 with the
measured evidence; or ship a wider Archivo cut built from the upstream variable font, proving the
added glyph coverage, the resulting file size, and that the air-gap bundle and the `font-src 'self'`
CSP still hold.
Last reviewed: 2026-08-29

## RES-IP-ALLOW-EXACT-MATCH

Status: OPEN
Owner: Repository owner
Source: S-proxy-trust, 2026-08-28
Reason: The `ip_allow` grant predicate compares the resolved client address to its list by exact
string, while doc 07 described it as restricting to source ranges. The divergence was inert while
every request resolved to the reverse proxy's own address and the predicate therefore matched
nothing; now that a real client address reaches it, an administrator who enters a CIDR gets a grant
that silently denies everything instead of narrowing. Doc 07 has been corrected to the implemented
semantics, so the ledger carries the capability gap rather than a documentation error.
Closure contract: Either accept exact-address matching and reject a range-shaped entry at the point
an administrator submits it, with a proof that the refusal names the offending value; or implement
containment matching and prove it preserves the lossless-representation contract that Evidence Pack
build replay depends on (R58), including the expanded-IPv6 spelling that its integration proof pins.
Last reviewed: 2026-08-28

## RES-POSTGRES-MCP-REPLACEMENT

Status: OPEN
Owner: Repository owner
Source: Program 0 security stop, 2026-08-08;
[GHSA-w48q-cv73-mx4w](https://github.com/advisories/GHSA-w48q-cv73-mx4w)
Reason: The deprecated PostgreSQL MCP package selected by the approved plan resolves a high-severity
advisory with no compatible fix, so the repository PostgreSQL connector and its owner-database port
overlay are disabled.
Closure contract: Select or build a maintained PostgreSQL MCP implementation, pin it through a committed
lock, pass a high/critical dependency audit with no unaccepted finding, provision a dedicated dev-only
read-only login, and prove reads succeed while DML, DDL, role switching, sequence access, privileged
functions, owner credentials, production data, and site data remain unavailable.
Last reviewed: 2026-08-08

## RES-AUDIT-RUNTIME-ACCEPTANCE-FAILURE

Status: OPEN
Owner: Repository owner
Source: [Main API job 16412434417](https://gitlab.com/synqsuite-group/EasySynQ/-/jobs/16412434417),
2026-09-10, commit `f411d06a59818ae00a7e328a8d2546e03994075b`
Reason: The API unit checks passed, then the mandatory external-audit runtime harness failed.
The original runner deleted its private JUnit and child output without naming the failed case.
One unchanged local run with matching build/proof manifests passed all eight mandatory image checks;
the local Docker/runtime environment differed from CI, so the original cause remains unestablished.
Progress, 2026-09-10: A subsequent local actual-image run identified an uncaught memory-sampler
thread exception that polluted the isolated probe's JSON result. A deterministic real-child regression
reproduced `ProcessLookupError` when the worker exited between opening and reading its procfs status.
The sampler now treats that vanished-process condition like the already-handled missing status file;
permission and other I/O failures still propagate, and the mandatory memory/resource assertions remain.
The original CI report did not preserve the thread exception, so attribution of that historical
failure remains unproven.
Closure contract: Capture actionable, privacy-preserving evidence of the failing check in the affected
environment, reproduce its mechanism, fix it at the owning boundary, and verify the mandatory acceptance
without skips, weaker resource/time limits, or reliance on retrying failed gates. A diagnostic summary
or a later green pipeline alone does not close this record.
Occurrence, 2026-09-19: On !51's trixie API-image candidate `9dc60c1`, [API job
16602276811](https://gitlab.com/synqsuite-group/EasySynQ/-/jobs/16602276811) passed the unit suite
and ten mandatory cases, but failed `test_version_page_transport_runtime_preserves_original_observations_and_limits`.
A single diagnostic retry, [job 16603495378](https://gitlab.com/synqsuite-group/EasySynQ/-/jobs/16603495378),
passed all eleven on the same commit and the same runner class (`saas-linux-small-amd64`). Local
evidence showed no trixie-specific mechanism. The full eleven-case run and two isolated runs of this
case passed on trixie, and the isolated bookworm and trixie runs recorded equivalent peak worker
memory and elapsed times with the same bundled expat. The runner still withholds the failed
assertion, so the cause remains unestablished; this occurrence is intermittent, and the retry does
not close this record.
Last reviewed: 2026-09-19

## RES-INTEGRATION-SETUP-ORDER-FAILURE

Status: OPEN
Owner: Repository owner
Source: S-recovery-exact-version-binding verification, 2026-09-22, clean `main` worktree `bdfdf95`
Reason: A full single-process run of the integration suite (`pytest -m integration`) fails
`tests/integration/test_setup.py::test_authenticated_setup_surface_requires_credential_acknowledgment`
while every other case passes (1,258 passed / 1 failed on `bdfdf95`); the same tree passes the
case in CI because the four-way duration-balanced sharding never places it after the test whose
leftover state it trips on. The failure is therefore order-dependent, masked by shard composition,
and a `.test_durations` refresh that moves the chunk boundary can surface it in CI without any
code change.
Closure contract: Identify the earlier test (or fixture) whose shared-database or process state
the setup case depends on, make the case self-provide or isolate that precondition so it passes
in any order, prove the fix with a full single-process integration run and with the specific
ordering that reproduced the failure, and confirm CI sharding is not relied on. A green sharded
run alone does not close this record.
Last reviewed: 2026-09-22

## RES-DEPENDABOT-BACKLOG-TRIAGE

Status: OPEN
Owner: Repository owner
Source: S-ci-github-primary (R86), 2026-09-22, after PR #554 merged
Reason: Dependabot replaced Renovate on 2026-09-21 and refreshed the eight dependency pull
requests that were open on GitHub from before the 2026-09-08 GitLab move (#478, #541, #543, #547,
#548, #550, #551, #553). They are live proposals under the adopted updater, not superseded work,
and none has been reviewed against the current `main`: two pass `gate`, three fail it (the Compose
image updates redden `compose-images-lock` because `infra/images.lock` digests are manual under
R86; the web and Redocly groups need a look), and three have no run on the R86 workflow yet.
Leaving them unreviewed means the accepted-manual losses in R86 are exercised by nobody.
Closure contract: Each of the eight pull requests is either merged through the gate (with `just
images-update` run on the branch where the update touches a Compose image) or closed with the
reason on the thread; the count of open Dependabot pull requests older than the move is zero;
and the outcome is recorded in dated slice history. A later Dependabot pull request opened after
this record is ordinary work, not part of it.
Last reviewed: 2026-09-22

## RES-TYPESCRIPT-7-UPGRADE

Status: OPEN
Owner: Project maintainer
Source: Closed [!8](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/8) and its measured
evaluation on main `f991f89`, 2026-09-20
Reason: The SPA pins `typescript ^6.0.3` while 7.0.2 is the released `latest`. The application code
is already compatible: with `typescript@7.0.2` installed in a throwaway worktree, both strict
projects (`tsconfig.json` and `tsconfig.browser.json`) type-check clean, `npm run build` succeeds and
the whole suite passes at **2,357 tests across 283 files**. `npm run lint` is the blocker and fails
before linting a file: `typescript-eslint` refuses TS 7.0 outright and tracks support for TS >= 7.1
upstream. Lint is a required job, so the upgrade cannot land. The vendor's side-by-side workaround
(retaining TypeScript 6 for the ESLint API while building with 7) is rejected here: it puts two
compilers in the lockfile and the air-gap bundle and lints against a compiler the build does not use.
Closure contract: Upgrade once `typescript-eslint` supports the installed TypeScript major, with one
compiler in the lockfile, and verify the complete web loop — ESLint, both strict `tsc` projects, the
production build, the full Vitest suite and the Playwright browser job — on the upgraded tree. Record
the measured evidence. Do not disable or downgrade the lint job, pin a second compiler, or treat a
passing type-check and build as closure while lint is skipped.
Last reviewed: 2026-09-20

## RES-TESTCONTAINERS-IMPORT-DEPRECATIONS

Status: OPEN
Owner: Project maintainer
Source: [S-audit-required-witness-collection runtime evidence](slice-history.md#s-audit-required-witness-collection--fresh-bounded-traversal-diagnostics),
2026-09-11
Reason: `apps/api/tests/integration/conftest.py` still imports PostgreSQL, MinIO and Redis fixtures
from deprecated `testcontainers.postgres`, `testcontainers.minio` and `testcontainers.redis` paths.
The dated ten-case mandatory runtime evidence reports the three inherited deprecation warnings; there
is no evidence that the required-witness collection branch introduced them.
Closure contract: Migrate the three fixture imports to `testcontainers.community.postgres`,
`testcontainers.community.minio` and `testcontainers.community.redis`, then verify the affected
PostgreSQL, MinIO and Redis fixtures and mandatory runtime paths complete without those warnings.
Do not suppress the warnings or treat an unaffected subset as closure evidence.
Last reviewed: 2026-09-11

## RES-INGEST-PROGRESS

Status: OPEN
Owner: Repository owner
Source: Batch 10, PR [#367](https://github.com/CoJoA13/EasySynQ/pull/367)
Reason: Ingestion reaper — long dedup/propose stages have no incremental progress signal.
Closure contract: Add a heartbeat-written progress stamp that advances per batch, covering long
dedup/propose computation and re-delivered scanning, with its required migration and focused reaper proof.
Last reviewed: 2026-08-08

`reap_stalled_runs`' backstop is anchored on `repo.max_stage_progress`, but only `import_file` (scan) /
`import_extract` / `import_classification` are written **per batch**. `import_dupe_cluster` and
`import_proposal_node` are written **once at stage completion** (`replace_dedup_groups` /
`replace_proposals`), so a long-running `Deduping`/`Proposing` computation still rides the last classify
row and could in principle be reaped while alive. Bounded in practice: the ~30-minute source-root lock TTL
is the effective liveness signal against a 6-hour backstop, so lock-liveness protects a genuinely live
worker first. **Closing it needs** a heartbeat-written progress stamp (a new column or a per-batch row
write) + a migration — a slice, not a remediation fix. Raised by both Codex and diff-critic on #367;
documented at `max_stage_progress`.

⚠ Same root cause, also unclosed: a **re-delivered `Scanning` run** re-walks existing paths via
`upsert_file`, whose conflict-update does NOT touch `created_at`, so the anchor does not advance on a
replay either. Every one of these needs the same fix — a timestamp that advances per BATCH, not per
first-insert or per stage-completion.

## RES-INGEST-PARTIAL-OPTIN

Status: OPEN
Owner: Repository owner
Source: Batch 10, PR [#367](https://github.com/CoJoA13/EasySynQ/pull/367)
Reason: Resuming a PartiallyCommitted run can retry a FAILED opted-in family member.
Closure contract: Add the partial-state clear/acknowledge operation and owner-reviewed state contract
needed before any resume-side opt-in gate.
Last reviewed: 2026-08-08

The R10 commit gate is start-only (gating resumes strands the run — see the R10 amendment), so if a run
went `PartiallyCommitted` *because the effective member itself failed*, a resume retries and commits it
without honoring the opt-in. Note this corrects the in-code rationale's assumption that the effective
member is always already in the vault by then (`claim_commit_result` lets a failed ledger row later
succeed, and `_finalize` marks PartiallyCommitted on ANY item failure). **Closing it needs** the same
partial-state clear/acknowledge operation the resume gate would require — a new endpoint + a review-state
decision. Raised by Codex on #367.

## RES-R10-RECONSTRUCTION

Status: OPEN
Owner: Repository owner
Source: Batch 10, PR [#367](https://github.com/CoJoA13/EasySynQ/pull/367); R10 amendment, 2026-07-25
Reason: Revision-chain reconstruction is unimplemented and refused at commit.
Closure contract: Ship the owner-approved provenance-materialization slice while retaining honest
amendment, API-contract, and SPA refusal behavior until it lands.
Last reviewed: 2026-08-08

Revision-chain reconstruction (R10) is unimplemented and now refused at commit. The per-family opt-in is
still accepted and stored, but a run carrying one is refused with
`422 revision_chain_reconstruction_unsupported`. **Closing it needs** the actual provenance
materialization slice; until then the amendment, the contract and the SPA must keep saying so.

## RES-CAPA-REJECT

Status: OPEN
Owner: Repository owner
Source: Batch 9, PR [#366](https://github.com/CoJoA13/EasySynQ/pull/366)
Reason: CAPA `reject`/`changes_requested` are untested, and a multi-approver stage wedges on one reject.
Closure contract: Obtain an owner decision on decisive-negative behavior, implement that contract, and
add the missing CAPA action-plan tests.
Last reviewed: 2026-08-08

An ANY quorum only FAILs once no candidate remains undecided, so one reject leaves the instance PENDING
and the CAPA in `RootCause` with a live approval instance — which blocks re-propose until every approver
rejects. `decide_dcr_approval` force-terminates on a negative; `decide_capa_action_plan` does not.
**Closing it needs** an owner decision on whether CAPA should mirror DCR's decisive-negative behaviour,
plus the missing tests.

## RES-AUDIT-CHECKPOINT-LINEAGE

Status: OPEN
Owner: Repository owner
Source: Batch 7, PR [#364](https://github.com/CoJoA13/EasySynQ/pull/364)
Reason: Legacy checkpoints do not commit to a trusted predecessor. The explicit protected-file verifier
now preserves enrolled witnesses independently of the database, while scheduled/API/no-option checks
still discover their inventory from the database.
Closure contract: Define and ship a Merkle-chained checkpoint format in which each anchor commits to the
prior anchor hash, with a binding register entry and migration/compatibility proof.
Last reviewed: 2026-09-11

All retained eligible legacy object versions are checked, including older contradictions after a
genuine producer re-anchors. The explicit protected-file CLI now checks the owner's enrolled
organizations, public keys and witnesses even after database sink retargeting or organization removal;
[R73](decisions-register.md#r73--external-legacy-audit-verification-uses-an-owner-controlled-public-enrollment-file--2026-09-08)
and the [runbook](runbooks/audit-external-verification.md) define that manual custody boundary.
Scheduled/API/no-option callers still use the mutable database inventory. Neither path proves an
expected predecessor: legacy anchors contain no predecessor commitment, evidence can expire before
observation, and descriptor compromise or rollback remains outside cryptographic enforcement.
**Closing it needs** Merkle-chained anchors, trusted lineage/key-era bootstrap and the corresponding
compatibility/restore proofs. The static external enrollment closes only the explicit CLI's database
selection gap; this record remains OPEN.

Historical-target progress on 2026-09-09 (R75): the explicit consumer now checks a closed older
inspection database using one database snapshot, authenticates ahead witnesses separately and
requires complete coverage from every enrolled witness. Retained pre-rewrite contradictions still
fail. This does not prove the intended recovery point, predecessor continuity or archive provenance;
the lineage closure contract is unchanged.

Versioned-envelope progress on 2026-09-09 (R76): the pure codec freezes predecessor, stream,
sequence, key identity/epoch and planned transition-proof bytes. Current issuance and all existing
consumers remain legacy. An authenticated envelope does not establish pinned bootstrap, complete
predecessor history, durable delivery or restore compatibility; this record remains OPEN.

Supplied-graph progress on 2026-09-09 (R77): the pure v2 reader evaluates externally pinned
predecessor/sequence/key continuity over all supplied observations and rejects usable output for
forked, invalid or incomplete history. Current operational consumers remain unchanged. Opaque
bootstrap contents, complete per-witness collection, scalable retained-history processing and
DB-chain comparison remain separate obligations; this foundation does not close this record.

Pinned-bridge progress on 2026-09-09 (R78): the pure reader now validates an externally committed
legacy package, all of its declared pages and exact supplied bodies, retained signatures and each
required witness's positive boundary. An authentic unlisted observation remains a discrepancy;
missing pages cannot establish body membership. Shared manifest/input omissions remain undetectable,
and no current consumer is activated. Complete raw per-witness collection, scalable global history,
actual DB-chain comparison, durable delivery and compatibility/restore proofs remain required.
This record remains OPEN.

Raw-transport progress on 2026-09-09 (R79): exact explicit-version GETs now preserve original
bytes, enforce returned identity and feed the unchanged R78 evaluator in disposable actual-image
acceptance. This is an inactive prerequisite, not full history collection or a witness-custody claim.
The new API strictly rejects the pinned provider's literal-null responses with absent VersionId.
Complete per-witness enumeration, global reconciliation, SDK resource/lifetime containment and the
existing database/delivery/restore obligations remain OPEN. Current legacy consumers are unchanged.

Isolated-read progress on 2026-09-09 (R80): one exact R79 request can now run behind a bounded
Linux process/protocol boundary with verified resource limits and watchdog/cleanup admission.
This supplies no page collector, witness-completeness result or global-history evaluator. Complete
per-witness collection, bounded spooling, scalable reconciliation and existing database/delivery/
restore obligations remain OPEN. No current legacy consumer is activated.

Supplied-page progress on 2026-09-09 (R81): the inactive decoder rejects lossy or ambiguous original
XML and retains every admitted version/delete marker/duplicate and provider cursor as untrusted
observations. A rejected document yields no page. The actual-image proof covers supplied bytes only;
private exact page transport, complete per-witness enumeration, persistent gaps, global cursor cycles,
bounded fresh spooling and scalable global reconciliation remain unimplemented. A terminal flag
cannot establish non-omission, snapshot atomicity, custody or required-witness coverage. Existing
snapshot/delivery/restore obligations and this record remain OPEN.

Original-page transport progress on 2026-09-11 (R83): one original page can now cross an exact
request and isolated worker boundary with bounded admitted bytes, both opaque cursors preserved
and success only after cleanup. Actual-image TLS provider evidence retains every independently
created version/delete marker; a narrow bounded metadata extension admits the provider's marker
ETag/Size/StorageClass without interpreting them or rewriting XML. No terminal page or finite test
multiset establishes whole-witness completeness. Required-witness namespace binding and complete
traversal, sticky gaps, cross-page cursor-cycle/duplicate accounting, a fresh bounded private spool
and scalable global reconciliation remain unimplemented. Database-chain agreement, custody,
freshness/rollback continuity, durable delivery and activation/restore proofs remain required.
Current operational consumers are unchanged and this record stays OPEN.

Required-witness traversal progress on 2026-09-11 (R84 candidate): one inactive attempt now binds
the entire externally required witness set before I/O, traverses original R83 pages and every
eligible R80 version serially, and preserves duplicate deliveries, conflicts, markers, unavailable
reads and cursor gaps in a fresh bounded storage-only spool. A `traversed` report is unauthenticated
observation accounting after cleanup, not a verified global history. Root verified full new-case
acceptance with 1,003 actual GETs and three original pages, separate 5,002-body synthetic scaling,
actual resource/SQL/IPC controls and owned cleanup. The unchanged ten-case harness also passed;
integration state is tracked in [current status](current-status.md). The spool is discarded and never reopened after
success or failure; a later reconciler must work within that lifetime. Global R77/R78 equivalence,
larger bridge/key/edge indexes, actual DB-chain agreement, non-omission/atomicity, protected custody
and rollback memory, durable delivery and activation/restore compatibility remain required.
Current operational consumers remain unchanged; this record stays OPEN.

Global reconciliation progress on 2026-09-12 (R85 candidate): indexed R77/R78-equivalent kernels now
consume complete collected original evidence within one fresh worker, beyond the older 4,096-node/
eight-page supplied limits. All required witnesses must cover the full accepted v2 path, and late
cross-format signed-head contradictions suppress usable output. Independent large/truncation
controls, eleven-case actual-image acceptance and owned cleanup passed. This is an inactive
observed-history foundation; actual DB-chain agreement, non-omission/atomicity, protected custody/
rollback memory, durable delivery and operational/restore integration remain required. This record
stays OPEN; [current status](current-status.md) tracks candidate delivery. Last reviewed: 2026-09-12.
Delivery, 2026-09-18: MR [!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46) merged R85 on 2026-09-12 as `9158a5d`; its final
[source](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843299684) and
[merged-main](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970) pipelines passed all fourteen required jobs,
including eleven mandatory runtime cases. The foundation remains
inactive; this record stays OPEN. Last reviewed: 2026-09-18.

## RES-MINIO-VERSION-LIST-DENY

Status: OPEN
Owner: Repository owner
Source: Historical witness pinned-provider permission proof, 2026-09-08
Reason: Pinned MinIO `RELEASE.2024-09-13T20-26-02Z` permits `ListObjectVersions` when
`s3:ListBucketVersions` is explicitly denied but `s3:ListBucket` remains allowed. It also permits
version listing or reading when only the corresponding version-specific allow is omitted and the
ordinary `s3:ListBucket` or `s3:GetObject` allow remains. The shipped witness reader still grants
both ordinary and explicit version actions for portable behavior and remains denied every tested
write, delete, retention, and governance-bypass operation. This provider limitation does not block
the intended read-only history scan, and this record grants no security exception or deployment risk
acceptance.
Closure contract: Ship a reviewed provider update or backport and prove against the deployed provider
that an explicit `s3:ListBucketVersions` deny blocks version listing while separately allowed ordinary
listing remains usable. Repeat the effective reader/writer permission matrix and WORM retention
regressions, including public-verifier failure without current-object fallback when version access is
actually denied.
Last reviewed: 2026-09-08

## RES-AUDIT-LONG-SCOPE-REF

Status: OPEN
Owner: Repository owner
Source: Batch 12; `apps/api/src/easysynq_api/api/audit.py::document_scope_match`
Reason: Pre-cap audit rows for a document with a >512-character identifier are unreachable through the
per-document history endpoint.
Closure contract: Add a discriminator that separates legacy raw keys from capped keys, using an
owner-reviewed append-only/hash-chain-safe backfill or schema migration.
Last reviewed: 2026-08-08

Batch 12 caps `audit_event.scope_ref` on write so it cannot break its new btree index, and
`api/audit.py::document_scope_match` searches the ONE canonical capped key. Rows written *before* that
cap, for a document whose `identifier` exceeds `_SCOPE_REF_MAX_CHARS`, are stored under the raw value and
no longer match. **Why the obvious fix is wrong:** an intermediate revision also searched the raw
identifier as a compatibility operand, and Codex round 4 showed that reopens a cross-document merge — a
capped key is exactly `_SCOPE_REF_CAPPED_CHARS` characters, and a raw identifier of that length is itself
capped on write, so such a value in `scope_ref` is irreducibly ambiguous: EITHER this document's own
pre-cap row OR another document's post-cap key. Nothing in the row distinguishes them, so searching it can
return a **different document's audit events**. A completeness gap on a pathological identifier is
strictly preferable to a cross-document leak. **Blast radius is nil for normal documents** — below the
threshold the cap is the identity function, so every ordinary row is untouched; this reaches only
documents carrying a pathologically long *imported legacy* identifier. **Closing it properly needs** a
discriminator that separates legacy raw keys from capped keys (e.g. a one-off backfill re-keying pre-cap
rows, or a `scope_ref_kind` column) — a migration over append-only, hash-chained rows, so a slice with its
own decision, not a remediation fix. Pinned by
`test_history_query_never_searches_another_documents_key`.

## RES-AUDIT-KEY-ROTATION

Status: OPEN
Owner: Repository owner
Source: Batch 7, PR [#364](https://github.com/CoJoA13/EasySynQ/pull/364)
Reason: Active legacy checkpoints contain no key identifier or activation history. Scheduled/API/no-option verification
uses one key; explicit protected-file verification supports a static legacy public-key allowlist.
Closure contract: Add a key identifier to checkpoints and retain a public-key verification history, with
rotation and pre-rotation restore proofs.
Last reviewed: 2026-09-11

Restoring a pre-rotation backup still verifies its historical signature against the current key.
The unattended retained-version scan also uses that single key, so retaining an old public key alone
does not enable automatic selection. The explicit external verifier accepts 1–8 owner-enrolled legacy
public keys per organization and rejects unknown keys, without private-key access. That static
allowlist supplies no key activation, revocation era, compromise cutoff or rotation/restore protocol.
**Closing it needs** a checkpoint key identifier, retained authorized public-key history and actual
rotation plus pre-rotation restore proofs. This record remains OPEN.

Historical-target progress on 2026-09-09 (R75): the explicit legacy reader now accepts authentic
newer off-host evidence for an independently selected older target while checking all applicable
anchors against the static enrolled public-key set. Real two-key and unknown-key cases are covered,
but no key identifier, activation/revocation era, compromise cutoff or rotation procedure is added.
The existing restore path and this record's rotation/pre-rotation proof requirements are unchanged.

Versioned-envelope progress on 2026-09-09 (R76): the pure codec authenticates explicit current and
next key identities, consecutive transition epochs and both keys' signatures, with strict public-key
admissibility. Its immutable result does not activate keys or establish authorized history. Existing
signing-key loading, issuance and pre-rotation restore behavior are unchanged; this record remains OPEN.

Supplied-graph progress on 2026-09-09 (R77): the pure reader separates available historical material
from a predecessor edge's permitted signing key/epoch. Detached or rejected transitions cannot
introduce next material, and failed/incomplete results expose no usable key history. A terminal
transition is not operational activation. Current key loading, issuance and restore stay unchanged;
complete history, independent delivery/activation confirmation and pre-rotation restore remain OPEN.

Pinned-bridge progress on 2026-09-09 (R78): exact supplied legacy evidence can now be bound to an
external bootstrap pin without changing historical normalization or retained-key admission. This
adds no legacy signer epoch, key activation or trust-file update. The bounded package result does
not prove full retained history, rollback continuity, independent transition confirmation or an
actual pre-rotation restore. Current signing and restore behavior and this closure contract remain
unchanged; this record remains OPEN.

Raw-transport progress on 2026-09-09 (R79): unchanged retained bytes and exact version metadata
can now reach R78 through an explicit reader. No key is activated or enrolled and no legacy key era
is inferred. Complete independent witness history, protected rollback continuity, durable delivery,
activation confirmation and actual pre-rotation restore remain required; this record stays OPEN.

Isolated-read progress on 2026-09-09 (R80): resource/lifetime containment is available for one
inactive exact-version read. It changes no key selection, epoch, custody, issuance or restore path.
Full independent history, protected rollback continuity, durable delivery, activation confirmation
and pre-rotation restore remain required; this record stays OPEN.

Original-page transport progress on 2026-09-11 (R83): exact isolated page observations and their
opaque cursors are available to future collection. They activate no key, infer no signer epoch and
change no enrollment, custody or restore path. Complete required-witness history, bounded spooling,
global reconciliation, protected rollback memory, independent key delivery/activation confirmation
and an actual pre-rotation restore remain required; this record stays OPEN.

Required-witness traversal progress on 2026-09-11 (R84 candidate): the inactive bounded collector
preserves observed namespace evidence without choosing legacy/v2 membership, authenticating bodies
or inferring key eras. Full new-case and ten-case harness acceptance are verified; integration
state is tracked in [current status](current-status.md). The private diagnostic spool is discarded after its lifetime; it is
not a retained key-history store or activation authority. Global history reconciliation, protected
rollback knowledge, independent transition delivery/activation confirmation and actual rotation
plus pre-rotation restore proofs remain open. Existing signing, key loading, enrollment and restore
behavior are unchanged; this record stays OPEN.

Global reconciliation progress on 2026-09-12 (R85 candidate): the inactive global kernel distinguishes
known authenticating key material from predecessor-authorized key/epoch edges across complete
observed history. Detached/rejected transitions cannot enroll successor authority; whole-path
witness coverage and late contradictions are checked before compact post-cleanup output. Actual
image and independent scale controls passed. No key is activated, delivered or loaded differently;
protected rollback knowledge, independent transition delivery/activation confirmation and real
rotation/pre-rotation restore proof remain open under the unchanged closure contract. Candidate
integration is tracked in [current status](current-status.md). Last reviewed: 2026-09-12.
Delivery, 2026-09-18: MR [!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46) merged R85 on 2026-09-12 as `9158a5d`; its final
[source](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843299684) and
[merged-main](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970) pipelines passed all fourteen required jobs,
including eleven mandatory runtime cases. No key is activated;
this record stays OPEN. Last reviewed: 2026-09-18.

## RES-RISK-CLAUSE-PICKER

Status: OPEN
Owner: Repository owner
Source: `apps/web/src/features/risk/NewRiskModal.tsx`
Reason: The backend accepts an optional per-risk `clause_id`, but the v1 risk-creation UI has no clause
picker.
Closure contract: Design and ship the clause picker with the required authorization, form, API-contract,
and browser behavior proofs.
Last reviewed: 2026-08-08

## RES-WORM-EVENT-BASIS-REEXTENSION

Status: OPEN
Owner: Repository owner
Source: C5 worm_lock_period enforcement review, 2026-08-27
Reason: An `event:*`-basis capture locks objects from the CAPTURE date (the basis is unknown until
the event fires), and nothing re-extends the object lock when a later basis-fill lands.
Closure contract: When the event-basis fill mechanism ships, extend the affected records' sealed
object locks to the recomputed `basis + worm_lock_period` horizon (upward only) in the same slice,
and prove the recomputed floor on both the blob row and the storage layer.
Last reviewed: 2026-08-27

`worm_lock_until` falls back to the capture date for an unfired `event:*` basis — that can only
under-shoot the eventual basis-derived horizon. In v1 nothing writes `retention_basis_date` after
capture, so the gap is vacuous today; the basis-fill slice must inherit the re-extension duty or
the storage floor silently stays at the capture-derived horizon.

## RES-AUDIT-EXPORT

Status: OPEN
Owner: Repository owner
Source: `apps/api/src/easysynq_api/api/audit.py`; OpenAPI operation `exportAuditEvents`
Reason: The documented async audit CSV/JSON export shape is deferred and not mounted.
Closure contract: Ship the D-9 async-job implementation with authorization, privacy-bounded output,
durable job state, OpenAPI response behavior, and affected audit/evidence-pack proofs.
Last reviewed: 2026-08-08

## RES-REST-STATE-PAGE-HEADING

Status: OPEN
Owner: Repository owner
Source: S-ui-a11y-outline, 2026-09-02
Reason: Every routed page now renders exactly one `h1` in its LOADED state, but the detail routes
render no heading at all in their other rest states. `DocumentDetailPage`, `AuditDetailPage`,
`ObjectiveDetailPage`, `ManagementReviewDetailPage`, `RecordDetailPage` and `DcrDiffPage` each guard
with two to six early returns and carry their title in only one of them, so a reader who is denied the resource, or who hits a load error, meets a document with no
heading. The `403` case is the one that matters: it is a permanent state for an ungranted reader,
not a flicker. The eleven registers do not have this shape — `RegisterPageHeader` is rendered in the
forbidden and error branches too — but they do drop the title in their LOADING branch, which is the
same defect and is already named inside [`RES-REGISTER-PAGE-FRAME`](open-residuals.md). This record
exists because that one is scoped to the register scaffold and names no detail route.

`/imports/:runId` WAS the sharpest case and is now closed, on the owner's call, for consistency:
`IngestionRunPage` carried `Import review` only in its 404/403 branch, so five of its six faces —
the review cockpit among them, the primary human-paced surface of the whole ingestion flow —
presented a document with no heading. The title is now chosen once above the face dispatch, so every
face carries it. That is the pattern this record's closure contract should follow for the remaining
routes: hoist the existing title above the branch, rather than add a different one per branch.
This was deliberately excluded from S-ui-a11y-outline: that slice changed which heading level each
existing title renders at and added no title anywhere, so folding in seven pages of new
branch-rendering would have mixed a second defect into a diff whose whole claim is that nothing
moved. Note the interaction with the gate the same slice added — `expectSoundHeadingOutline`
asserts exactly one `h1`, so it cannot be pointed at a forbidden or loading branch until this is
closed, and that is the honest limit of the slice's coverage rather than an oversight.
Closure contract: Give every routed page a title in each of its rest states, or record that a
denied/erroring detail route is exempt and say what a screen-reader user is expected to land on
instead. Prove it by extending `expectSoundHeadingOutline` to at least one forbidden branch and one
error branch per affected page, and confirm the assertion fails before the change. Sequence this
against [`RES-REGISTER-PAGE-FRAME`](open-residuals.md), whose closure contract already has to decide
whether a shared frame renders the title during loading; the two records should be answered together
rather than one silently constraining the other.
Last reviewed: 2026-09-02 (narrowed the same day: `/imports/:runId` closed)

## RES-DATE-TIME-DISPLAY-CONVERGENCE

Status: OPEN
Owner: Repository owner
Source: S-capa-width-railfoot-order, 2026-09-02
Reason: **R70** locks three things for user-facing display — a six-digit `MM/DD/YY` date, 24-hour
time, and both resolved in the organization timezone — and exactly one surface conforms: the
rail-foot clock, via `formatOrgClock`. Everything else violates at least one rule today. This record
tracks the conformance work; the standard itself is decided in
[`decisions-register.md`](decisions-register.md) and is not re-opened here.

The measured gap, which is wider than the date format that prompted the decision:

- `apps/web/src/lib/time.ts::formatTimestamp` calls `Intl.DateTimeFormat(undefined, …)` with **no
  `timeZone`** and no `hour12`/`hourCycle`. Executed, it renders `Sep 2, 2026, 01:45 PM CDT` under
  `en-US` and `2 Sept 2026, 13:45 GMT-5` under `en-GB` — so it breaks rule 2 (12-hour with a
  meridiem), breaks rule 3 (the viewer's browser zone, not the organization's), emits a THIRD date
  format, and renders differently per viewer locale. It reaches `lib/AsOf.tsx`, which
  `RegisterPageHeader` places on every register page, plus `RecordsTable`, `RecordDetailSections`,
  `DriftStatusPage`, `SupersededCopiesPage`, `NotificationItem`, `ReviewInputsSection` and
  `NotificationHealthPanel`. `formatRelativeTime` falls through to it beyond a week and inherits all
  of it.
- `formatDateInTimeZone` is org-zone-correct but emits `YYYY-MM-DD`, so it breaks rule 1 only. It is
  the `useOrgDate` path behind every register and timeline date.

⚠ The hazard is ORDERING, not formatting. `YYYY-MM-DD` sorts lexically and `MM/DD/YY` does not, so
any consumer that sorts, compares or parses a RENDERED date rather than the timestamp behind it
breaks with no type error and no failing render — just a register in the wrong order.

⚠ `formatTimestamp`'s browser-zone behaviour is a correctness defect independent of R70, not merely
a style mismatch: a record captured at 23:30 organization time is stamped with the *next day* for a
viewer east of the organization, beside a `useOrgDate` column that shows the correct day. The two
already disagree in the same table today.

This was not folded into the slice that created the divergence. Changing every date and time display
in the product is far larger and more visible than a rail-foot clock, and belongs in a diff a
reviewer can read as being about exactly that.
Closure contract: Bring every user-facing date and time onto R70 — six-digit `MM/DD/YY`, 24-hour,
organization timezone — or record which surfaces are exempt and why. Before changing either
formatter, enumerate every caller and prove none sorts, compares or parses its OUTPUT rather than the
timestamp behind it; a rendered value reaching a sort comparator is the failure this record exists to
prevent. Prove the result with an executable assertion per changed surface, and confirm each fails
against the current output. `formatTimestamp`'s missing `timeZone` should be fixed first and can be
proven on its own: pin a fixed instant and a non-UTC organization zone and assert the rendered day.
Last reviewed: 2026-09-02

## RES-RESTORE-DRILL-PLAINTEXT-ARCHIVE

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #420](https://github.com/CoJoA13/EasySynQ/issues/420), filed 2026-08-03 and
deferred by owner decision; verified against `3d8613a` on 2026-09-22.
Reason: `run_drill()` in `apps/api/src/easysynq_api/services/backup/drill.py` proves that the backup
destination round-trips by packing an unencrypted `easysynq-backup-{stamp}.tar` holding the full
`pg_dump` into the policy destination, restoring from it, then deleting it best-effort. Only the
durable backup path produces the AES-256-GCM `*.tar.enc` operators expect there. Every setup-gate and
operator-triggered drill therefore places the complete database in plaintext on the backup target for
the drill's duration, and a cleanup failure strands it. Where that target is itself swept by other
backup tooling, the plaintext copy can leave the host.
Closure contract: Make the drill write no plaintext database bytes to the destination while it
still proves the destination can hold and return a full-size archive, for example by encrypting
the transient archive with the backup key. A small non-database probe does not satisfy this: a
destination whose quota is below the real archive size would pass it. Prove it with a pg_dump-gated
integration test that finds no plaintext `easysynq-backup-*.tar` in the destination during or after
both a passing and a failing drill.
Last reviewed: 2026-09-22

## RES-SITE-DATA-GUARD-GAPS

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #421](https://github.com/CoJoA13/EasySynQ/issues/421), a deferred review batch
from 2026-08-03; verified against `3d8613a` on 2026-09-22.
Reason: `scripts/check-no-site-data.sh` (the R61 mechanical backstop) still misses four shapes: IPv6
addresses that begin with `::` compression (deliberately unmatched because Python slice syntax such
as `a[::2]` has the same shape), textual lockfiles (skipped by the `\.lock$` exclusion), lowercase
MD5 fingerprints (the pattern is uppercase-only), and file names beginning with `-` (most matchers
pass the file list without a `--` separator).
Closure contract: Close all four with a context-aware rule for leading `::` that keeps the slice
false positive out, an evidence-backed lockfile policy, a case-insensitive fingerprint match, and
`--` on every matcher. Prove each with a failing fixture in `scripts/tests/test-check-no-site-data.sh`
and a clean run over the full tree.
Last reviewed: 2026-09-22

## RES-KEYCLOAK-SCRIPT-DOTENV-ESCAPES

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #422](https://github.com/CoJoA13/EasySynQ/issues/422), filed 2026-08-03;
verified against `3d8613a` on 2026-09-22.
Reason: The `env_val` helper copied into `scripts/new-keycloak-user.sh` and
`scripts/clear-keycloak-lockout.sh` ends a double-quoted value at the first `"`, escaped or not.
Compose resolves `KEYCLOAK_ADMIN_PASSWORD="abc\"def"` to `abc"def`; the helper returns `abc\`, so
after a rotation to such a value both scripts authenticate with the wrong password. This is the third
dotenv production the `sed` approximation has had to chase.
Closure contract: Read the value Compose itself resolves (for example from `docker compose config`)
or implement the escape grammar once in a shared helper, and extend the extraction test matrix with
escaped-quote and trailing-backslash cases run against both scripts.
Last reviewed: 2026-09-22

## RES-SITE-ARTIFACT-GITIGNORE

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #423](https://github.com/CoJoA13/EasySynQ/issues/423) item 3, filed
2026-08-03; items 1 and 2 were mooted when the deployment record they corrected was removed. Verified
against `3d8613a` on 2026-09-22.
Reason: The install runbooks tell operators to create site-specific Compose overlays and to export the
installation root CA as `easysynq-root-ca.crt` inside the working tree, but `.gitignore` covers
neither. A routine `git add -A` on an operator checkout can therefore stage site data that R61
forbids in the repository.
Closure contract: First name one supported location and filename for a site overlay in the install
runbooks (today they show an inline override fragment without prescribing a path, so no ignore rule
can be targeted safely, and a wildcard broad enough to cover every valid Compose filename would hide
tracked files). Then ignore exactly that path and the exported root CA, and prove both with
`git check-ignore` plus a check that every tracked `infra/compose/compose.*.yml` stays tracked.
Last reviewed: 2026-09-22

## RES-BACKUP-CRON-IGNORED

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #425](https://github.com/CoJoA13/EasySynQ/issues/425), filed 2026-08-03;
verified against `3d8613a` on 2026-09-22.
Reason: The setup wizard stores `backup_policy.cron`, but the Beat entry `backup-nightly` in
`apps/api/src/easysynq_api/tasks/app.py` runs on a hardcoded `86400.0` interval and nothing reads the
stored cron. Backups fire 24 hours after Beat last started, so the configured time is never honoured
and each container recreation moves it.
Closure contract: Drive the backup schedule from `backup_policy.cron` evaluated in the organization
timezone (`resolve_org_tz`, R56), fall back safely with a warning on a malformed value, keep the task
idempotent under redelivery, and prove with a test that the schedule follows the stored cron rather
than process start time.
Last reviewed: 2026-09-22

## RES-CREDENTIAL-RESET-R64-ALIGNMENT

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #430](https://github.com/CoJoA13/EasySynQ/issues/430), deferred from PR #429 on
2026-08-04; the contract text (item 3) has since been aligned. Verified against `3d8613a` on 2026-09-22.
Reason: R64 rule 5 requires a system-tier caller to reset another user's credential. The roster still
offers "Issue new temp password" to any caller holding `user.create` (`UsersAdmin.tsx`), so a
granular-override holder sees an action that always returns `422 two_tier_violation`. The PEP check
in `services/authz/pep.py` is unconditional (no self exception), while R64 rule 5, its denial
message and doc 07 §351 say "another user"; doc 07 then justifies system tier for "every reset", and
the published contract describes the guard as unconditional. The authorities disagree, and choosing
the weaker reading would change a security boundary.
Closure contract: Gate the roster action on the same system-tier rule the server enforces, with a
component test for a granular `user.create` holder. Keep the unconditional guard and correct R64
rule 5, the denial text and doc 07 to say every reset needs system tier; a self-reset exception is
out of scope for this record and needs its own owner-approved register amendment first.
Last reviewed: 2026-09-22

## RES-KEYCLOAK-ERROR-CLASSIFICATION

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #431](https://github.com/CoJoA13/EasySynQ/issues/431), deferred from PR #429 on
2026-08-04; verified against `3d8613a` on 2026-09-22.
Reason: `KeycloakProvisioningClient` turns every 4xx on create, including admin-credential 401/403,
into `KeycloakRejected` (reported as invalid operator input), and turns a 404 from
`set_temporary_password` into `KeycloakUnavailable` (reported as an outage). A stale identity link
therefore reads as Keycloak being down, and a broken admin credential reads as a typo.
Closure contract: Classify a missing subject distinctly, route 401/403 to the
configuration/availability path, and unit-test each status class. Because no supported operation
replaces a dead `keycloak_subject` today (`POST /users` creates a separate `app_user`, and the only
user PATCH changes status), the closure must also either ship an audited relink operation for a
stale subject, with authorization and a test, or return a response naming the documented manual
recovery. An error class alone does not close this record.
Last reviewed: 2026-09-22

## RES-KEYCLOAK-LOCATION-SUBJECT

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #432](https://github.com/CoJoA13/EasySynQ/issues/432), deferred from PR #429 on
2026-08-04; verified against `3d8613a` on 2026-09-22.
Reason: After creating a Keycloak user, `keycloak_provisioning.py` trusts any non-empty last path
segment of the `Location` header as the new subject. The exact-lookup fallback runs only when the
header is absent, so a malformed `.../users/` yields the subject `users`, and `provision_user`
commits an `app_user` bound to an account that does not exist.
Closure contract: Validate the `Location` path itself rather than subject syntax (the repository
treats `keycloak_subject` as opaque): accept it only when it is the expected
`.../admin/realms/<realm>/users/<id>` shape with a non-empty final segment, otherwise use the exact
username lookup, and test the `.../users/` and foreign-path cases.
Last reviewed: 2026-09-22

## RES-PROVISION-POST-COMMIT-READ-FAILURE

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #433](https://github.com/CoJoA13/EasySynQ/issues/433), deferred from PR #429 on
2026-08-04; verified against `3d8613a` on 2026-09-22.
Reason: In `provision_user` (`apps/api/src/easysynq_api/api/users.py`), the `session.refresh(user)`
and role-name reads run after the first commit with no error handling. If either fails, the account
and `app_user` exist without a credential and the caller receives a bare 500 without the "user
created; reissue rather than retry" guidance the credential path gives.
Closure contract: Map a failure of those reads to the same recoverable response, or build the
response from values already held, and prove it with a fault-injection test.
Last reviewed: 2026-09-22

## RES-CREATE-USER-PENDING-EDITS

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #434](https://github.com/CoJoA13/EasySynQ/issues/434), deferred from PR #429 on
2026-08-04; verified against `3d8613a` on 2026-09-22.
Reason: `CreateUserModal.tsx` leaves the identity fields editable while the create request is
pending, and the collision-recovery link reads the live form instead of the submitted values. An
operator who edits the name or email while waiting can link an existing Keycloak identity to another
person's metadata.
Closure contract: Snapshot the submitted values when the create starts and have the link request use
the snapshot (or disable the fields while pending), with a test that edits during a pending create and
asserts the link payload.
Last reviewed: 2026-09-22

## RES-ROLE-PICKER-ROLE-READ

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #435](https://github.com/CoJoA13/EasySynQ/issues/435), deferred from PR #429 on
2026-08-04; verified against `3d8613a` on 2026-09-22.
Reason: The Create user role picker is gated on `permission.grant`, but its data comes from
`GET /api/v1/roles`, which requires `role.read`. A granular-override caller without `role.read` sees
an enabled, silently empty dropdown. The roster's Manage drawer (`UsersAdmin.tsx`) issues the same
roles query unconditionally for a roster reader, so its "Assign a role" selector fails the same way. The seeded System Administrator holds all three keys, so a
default install does not hit this.
Closure contract: In both the Create user modal and the Manage drawer, include `role.read` in the
role selector's gate or render the denied query as a calm no-access state (`forbidden` flag,
`retry: false`), with a component test for the denied case on each surface.
Last reviewed: 2026-09-22

## RES-UNBOUND-SCOPE-TEMPLATE-ROLES

Status: OPEN
Owner: Repository owner
Source: [GitHub issue #436](https://github.com/CoJoA13/EasySynQ/issues/436), filed 2026-08-04;
verified against `3d8613a` on 2026-09-22.
Reason: Five of the eight seeded roles carry parameterized scope templates (`:assignment_process`,
`:assigned_folder`, `:assigned_doc_class`). `_grant_from_role` in `services/authz/repository.py`
falls back to the raw template when an assignment has no `bound_scope`, the assignment API accepts
that, and the roster's Manage drawer posts only `role_id`. Provisioning has the same gap:
`CreateUserModal` submits `role_ids` to `/users/provision`, which creates each assignment without a
`bound_scope`. The user appears to hold the role and receives none of its access. Only the
process-owner flow binds a scope.
Closure contract: Enforce the rule for every assignment path (post-creation assignment,
provisioning, and the `grant-role` break-glass CLI, which also inserts `bound_scope=None` when its
optional `--bound-scope` is omitted): either refuse an unbound parameterized role with a named 422, or
collect the binding in both UIs and require it in the CLI. Prove that no path can silently create an
unbound Author or Approver.
Last reviewed: 2026-09-22

## RES-TIKA-4-OCR-CONTROL

Status: OPEN
Owner: Repository owner
Source: Dependabot PR #478 (Tika 3.3.1 → 4.0.0), closed after a measured comparison on
2026-09-22 against real 3.3.1 and 4.0.0 `-full` sidecars using the extractor's exact request shape.
Reason: The import OCR ladder (`services/ingestion/extractor_tika.py`, doc 09 §5.2) sends
`X-Tika-PDFOcrStrategy` (`no_ocr` for the native pass, `ocr_only` for the OCR pass) and
`X-Tika-OCRLanguage`. Tika 4.0.0 silently ignores both: a `no_ocr` request OCRs a scanned PDF, so
OCR cannot be switched off for an import and `ocr_used` would report `False` for OCR-derived text,
and a `deu` request OCRs a German scan as English (`Ma&nahme: GréBenprufung` instead of
`Maßnahme: Größenprüfung`). Nothing errors, and no CI job runs a real Tika (the unit suite mocks
the transport; the integration suite substitutes a fake extractor), so the upgrade would pass
`gate`. Tika majors are refused in `.github/dependabot.yml` until this closes.
Closure contract: Move per-request OCR strategy and language to the mechanism Tika 4 supports
(per-request parse context or server configuration), keep the OCR-off contract and truthful
`ocr_used` provenance, and add a real-Tika test (a pinned sidecar image) that fails on the current
headers against 4.x: a native PDF, a scanned PDF with OCR off and on, and a non-English scan. Then
upgrade the pinned sidecar and remove the Dependabot refusal and its test.
Last reviewed: 2026-09-22

## RES-HARNESS-PROBE-REQUESTFAILED-RACE

Status: OPEN
Owner: Repository owner
Source: Dependabot PR #587, CI run 35822550793 (2026-09-23): `web browser (Chromium)` failed in
`e2e/harness-fail-closed-meta.spec.ts` ("default fail-closed interceptor has exact abort and fatal
outcomes") on a change that touches nothing Playwright executes. It is the only `web-browser`
failure in the preceding 60 CI runs.
Reason: The probe (`e2e/harness-fail-closed.probe.spec.ts`) arms
`page.waitForEvent("requestfailed")` and then triggers a request that the fail-closed interceptor
aborts and answers with a fatal `throw`. When the fatal ends the test before the pending
`waitForEvent` settles, Playwright records its "Test ended" rejection as a second error. The meta
spec then sees two errors instead of exactly one and fails. The interceptor behaves correctly; the
self-test's exactly-one-error assertion is timing-dependent.
Closure contract: Make the probe's error set deterministic, for example by settling or explicitly
handling the pending `requestfailed` wait before the fatal can end the test, without weakening the
meta spec's exact-outcome assertions (a length check relaxed to "at least one" is not acceptable).
Prove it by forcing the losing order (delay the event) and showing the meta spec still passes, and
show that removing the fix reproduces the two-error result.
Last reviewed: 2026-09-23

## RES-REDIS-8-UPGRADE

Status: OPEN
Owner: Repository owner
Source: Dependabot PR #593 (redis-py 6.4.0 → 8.1.0), closed 2026-09-23 after review.
Reason: The latest stable kombu (5.6.2, Celery's transport) requires `redis<6.5`. The proposed lock
therefore moved kombu to the pre-release 5.7.0a1, which no CI job exercises because no test runs a
Celery worker. redis-py 8 also changes defaults this code relies on: socket timeouts default to 5s
while the SSE stream waits up to 20s on `get_message`, the wire protocol defaults to RESP3 (pub/sub
is its most-changed path), and its new type overloads make the two `# type: ignore` comments in
`redis_client.py` fail mypy. redis-py is capped below 6.5 in `.github/dependabot.yml`, and
`[tool.uv] prerelease = "disallow"` refuses the alpha outright.
Closure contract: Once a stable kombu (and Celery) release accepts redis-py 8: lift the Dependabot
ceiling; remove the stale `# type: ignore` comments; set the SSE client's `socket_timeout`
explicitly, or prove with an integration test that a subscription idle past 5s still receives
events; decide RESP2 versus RESP3 deliberately; pin the integration `RedisContainer` image to the
Compose major; and pass a live check of Beat, a worker consuming a task, and an SSE stream held
open for over a minute.
Last reviewed: 2026-09-23

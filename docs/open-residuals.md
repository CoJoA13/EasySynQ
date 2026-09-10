# Open residuals

This is the sole current, owner-visible ledger for deliberately deferred work. Each stable `RES-*` record
stays open until its closure contract ships with linked evidence. Dated `Named residuals` prose in
[`slice-history.md`](slice-history.md) is historical snapshot evidence, not a second live ledger.

## RES-RENOVATE-GITHUB-METADATA

Status: OPEN
Owner: Repository owner
Source: Post-merge setup verification, 2026-09-08, scheduled job 16358301239
Reason: The initial authenticated run reported GitHub metadata/tool failures and generated npm
updates without refreshed lockfiles despite a successful updater exit. The owner-approved GitLab-only
setup has since removed those dependencies, and actual npm lock generation and coordinated Mailpit
image updates are verified. Real uv lock refresh remains unverified because no suitable Python update
has been selected. This record remains open until that part of the existing closure contract is proved.
Closure contract: The owner declined GitHub services and hosting on 2026-09-08. Replace GitHub
metadata/tool dependencies with registry lookups and preinstalled tools, explicitly disable GitHub
requests and changelog fetching, and preserve the GitLab-only credential setup. Run the protected
main schedule, verify actual npm/uv lockfile refreshes and coordinated image-manifest updates, and
inspect remaining lookup/artifact errors. Successful dependency pipelines remain necessary before
merging individual updates; deliberate version-review assertions may still require scoped work.
The earlier token-based closure proposal is superseded by this owner decision. Do not merely
suppress warnings or treat a green updater exit as proof that lockfiles were refreshed.
Progress, 2026-09-08: Setup !11 merged; its [main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2829943288)
passed 14/14 and the [protected schedule](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2829947451)
passed 15/15 without GitHub lookup/authentication errors. [Mailpit !5](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/5)
merged with Compose and images.lock synchronized. The later scheduled Renovate job 16372131561
produced the real npm lock update in [contract-tools !6](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/6);
clean installation, live audit, full generation without drift and reviewed exact-version guards passed,
then its [14 required jobs](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2830246394) passed before merge.
No Python update is manufactured to close the remaining uv evidence gap. Current execution and token
rotation are tracked in [issue #2](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/2).
Last reviewed: 2026-09-08

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
Current execution is tracked by [GitLab issue #3](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/3).
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
Last reviewed: 2026-09-09

## RES-CONTAINER-SECURITY-TRIAGE

Status: OPEN
Owner: Repository owner
Source: GitLab repository setup audit, 2026-09-08
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
Last reviewed: 2026-09-08

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
Last reviewed: 2026-09-10

## RES-WEB-QUERY-TEARDOWN-NOTIFICATION

Status: OPEN
Owner: Repository owner
Source: S-auth-startup-boundary full-web verification, 2026-08-09
Reason: A full Vitest run has nondeterministically emitted a post-jsdom `window is not defined` error
from a queued TanStack Query notification after every test assertion had already passed. The observation
is test-runner evidence only: production causality has not been reproduced or established.
Closure contract: Produce a deterministic minimal reproduction, identify and fix the root cause at its
owning boundary, and complete repeated clean full-suite runs without depending on reruns or retrying a
failed gate.
Last reviewed: 2026-08-09

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
Last reviewed: 2026-09-09

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
Last reviewed: 2026-09-09

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

# Open residuals

This is the sole current, owner-visible ledger for deliberately deferred work. Each stable `RES-*` record
stays open until its closure contract ships with linked evidence. Dated `Named residuals` prose in
[`slice-history.md`](slice-history.md) is historical snapshot evidence, not a second live ledger.

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

## RES-RULEPACK-BRITISH-KEYWORDS

Status: OPEN
Owner: Repository owner
Source: S-ui-5a, 2026-08-29
Reason: S-ui-5a adopted American-US spelling as the house standard for user-facing text and swept the
SPA, the API's user-visible strings and the current-authority documents, but did not reach
`apps/api/src/easysynq_api/domain/ingestion/rule_packs/iso9001_rule_pack_v1.yaml`, which still carries
the British form in two places with different consequences. The `explanation` string "Header reads
like an audit programme/plan" is user-visible — `apps/web/src/features/ingestion/ItemDetailDrawer.tsx`
renders each fired matcher's explanation beside its weight — so the old spelling still reaches a
reader on the ingestion surface. More consequentially, two matchers key on `"audit programme"` as a
case-insensitive substring NEEDLE (`rule_classifier.py`, `any(kw in low for kw in m.keywords)`), so a
document titled per the newly adopted standard — "Audit Program 2026" — does not fire them at all: it
loses the weight-30 header signal on the audit-schedule rule and the weight-55 signal on the Clause
9.2 rule, and may land in a lower confidence band than the identical British-spelled document. The
house-standard change therefore has a classification consequence, not only a spelling one. It was not
fixed inside S-ui-5a because editing a versioned, weight-calibrated resource is out of scope for a
spelling slice: even a change that looks additive has to be shown not to disturb the calibration
before it ships, which is what the versioned-rule-pack design exists to force.
Closure contract: Replace the needle `"audit programme"` with `"audit program"` in both matchers, and
correct the `explanation` string to the house standard. Replacement rather than addition is correct
and is the cheaper proof: the US form is a strict prefix of the British one, so the shorter needle
matches BOTH spellings, so nothing that fires today stops firing. Replacement also sidesteps the
double-count question that ADDING a second needle would raise: with one needle there is nothing to
double-count, and the change is monotone — it can only add matches, never remove one. Re-run the calibration test that reproduces the doc 09 §6.5 worked examples, confirm
the published accuracy band is unchanged, and add a case asserting that a header reading
"Audit Program" and one reading "Audit Programme" score identically.
Last reviewed: 2026-08-29

## RES-CAPA-BOARD-NO-BROWSER-COVERAGE

Status: OPEN
Owner: Repository owner
Source: S-ui-5c, 2026-08-29
Reason: `/capa` has no browser-test coverage at all, so layout defects on it cannot be proved or
fixed under the rule this repository applies everywhere else. `apps/web/e2e/support/registers.ts`
lists ten register cases and CAPA is not among them, and `apps/web/e2e/support/api.ts` fulfils no
`/api/v1/capas` route, so no Playwright scenario can reach the board. Two defects the owner reported
from a live walkthrough are consequently unfixed: the row of Source/Severity/State filter Selects is
narrower than the summary card above it, so the block edges do not line up, and a narrow vertical
gap sits between the "Open CAPAs" and "By source" cards. A one-line fix for the first
(`grow preventGrowOverflow={false}` on the filter Group) was written, verified against every named
gate, and then REVERTED rather than shipped unmeasured — the CAPA board is a size-and-position
change and jsdom resolves no layout, which is exactly the blind spot that let three earlier S-ui
slices ship a visible defect behind a fully green suite.
⚠ The obvious closure — adding a `capa` entry to `REGISTER_CASES` — does NOT work, and was checked
rather than assumed. `RegisterCase` is table-shaped (`floor`, `headers`, `finalHeader`), and the
generic specs consume those: `register-geometry.spec.ts` asserts `containerScrollWidth` and
`tableWidth` are at least the case's `floor`. `CapaBoardPage` renders a kanban `ScrollArea` of six
260-pixel columns AND a `<Table>` beneath it, but that table has **no** `Table.ScrollContainer` — the
file is not among the nine in `responsiveRegisterContract.test.ts` — so those assertions would measure
something that is not there. Joining the shared cohort would either fail or need conditionals that
weaken it for the ten registers that do fit.
Closure contract: Add a `/api/v1/capas` fixture route to `apps/web/e2e/support/api.ts` — hoisted into
the always-fulfilled block if the shell requests it, since that file's tail is a fail-closed `throw` —
and give `/capa` its OWN spec rather than a `REGISTER_CASES` entry, in the idiom of
`e2e/risk-matrix-legend.spec.ts`. Then fix the filter-row alignment and the inter-card gap with
assertions that fail against the current code and pass after. Two changes S-ui-5b and S-ui-5c already
made to this uncovered route should be pinned by the same spec while it is being written: the
`RegisterFilterBar` bottom margin, and the theme's `ScrollArea type: "auto"`, which gives the board's
always-overflowing kanban a permanently visible horizontal scrollbar.
Last reviewed: 2026-08-29

## RES-REGISTER-HEADING-LEVELS

Status: OPEN
Owner: Repository owner
Source: S-ui-4, 2026-08-29
Reason: The register pages do not agree on a heading level, and most of them render no `h1` at all.
`AppShell` contributes no page heading; `HomePage`, `LibraryPage`, `ReportsRegisterPage` and
`IngestionRunsPage` title at `order={1}`; eleven registers (the ten in
`apps/web/e2e/support/registers.ts` plus `CapaBoardPage`) title at `order={2}`; the two CAPA
sub-registers title at `order={3}`; and `ProgramPage` titles at `order={3}` with an `order={4}`
sub-heading. So a reader landing on a register meets a document whose
outline begins at `h2` or `h3` with no `h1` above it, and the level a given register uses carries no
meaning beyond how it was written. S-ui-4 centralised the markup in `lib/RegisterPageHeader.tsx`
but deliberately kept `order` a caller prop rather than normalising it. One existing suite pins a
register heading level — `AuditsListPage.test.tsx` asserts `{ level: 2, name: "Internal audit" }`
and uses it as its load gate — and the component's `order?: 2 | 3` union covers only what the
eleven adopters use, so the `order={1}` and `order={4}` pages each widen it when they adopt.
Levelling the registers is an accessibility change with its own test surface and does not belong
inside a retheme slice. Centralising it first is what makes the later fix a one-file change.
Closure contract: Decide the intended outline for a register route and apply it in
`RegisterPageHeader`, updating every suite that pins a level, and prove the result with an
axe assertion for a single `h1` and no skipped level on at least one register route in
`apps/web/e2e/register-accessibility.spec.ts`. The program's final sweep slice already owes an
a11y pass per route; this record names the specific defect that pass must close.
Last reviewed: 2026-08-29

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
equal-width contract). And `CapaBoardPage` is the one page whose branches disagree on container
size, `md` in three branches against `xl` in the loaded page, so adopting a frame there is a
deliberate visual change rather than a mechanical one.
Closure contract: Either build the frame with a render-prop body, re-anchor the two heading-gated
suites on a load-only sentinel, and record the CapaBoardPage container unification as an intended
change; or record that the scaffold stays per-page and remove this record. A shared table wrapper
is separately blocked and must not be attempted: `apps/web/src/lib/responsiveRegisterContract.test.ts`
is a source-text contract requiring each of nine page files to contain its own literal
`<Table.ScrollContainer minWidth={N}>`.
Last reviewed: 2026-08-29

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
Reason: Only the newest off-host audit checkpoint is verified.
Closure contract: Define and ship a Merkle-chained checkpoint format in which each anchor commits to the
prior anchor hash, with a binding register entry and migration/compatibility proof.
Last reviewed: 2026-08-08

A DB owner who rewrites the chain and lets the 15-minute beat re-anchor over the rewritten head passes
verification, while the older immutable objects that would expose it are never read. **Closing it needs**
a Merkle-chained anchor lineage (each checkpoint commits to the prior anchor's hash) — a checkpoint
payload/format change + a register entry.

## RES-AUDIT-VERIFY-ORCHESTRATOR

Status: OPEN
Owner: Repository owner
Source: Batch 11, PR [#368](https://github.com/CoJoA13/EasySynQ/pull/368)
Reason: `_run_verify_chain` has no orchestrator-level test coverage.
Closure contract: Add an orchestrator harness that can inject the sessionmaker or a task-honored settings
override, then cover the dirty commit, missing-key alarm, and engine/disposal branches end to end.
Last reviewed: 2026-08-08

The nightly chain-verify task has had **zero** tests since S6 — this is pre-existing, not introduced by
Batch 11 — but Batch 11 added real logic to it that is therefore unverified at the orchestrator level:
(a) the `emitted` → **`dirty`** commit decision (the commit must now also fire for `integrity.alarm` rows,
which write no audit row by design — keying it on the audit rows alone would silently discard the
missing-verify-key notifications); (b) the **missing-verify-key alarm** itself (an `integrity.alarm` per
org + the out-of-band channel, deliberately writing NO `CHAIN_VERIFY_FAIL` row); (c) the
**engine-inside-`try`** move with its conditional `dispose()` (Codex P2 — a malformed DSN otherwise exits
without the promised "could not run" alarm).

The individual pieces *are* covered: `_should_alarm_offhost` has a unit decision table including the new
`witness_required` / `unanchored_overdue` cases, `unanchored_is_overdue` has a mutation-verified boundary
test, and `emit_integrity_alarm` has integration cover. It is only the orchestration that is untested.
**Why it is not just an oversight:** `_run_verify_chain` builds its own engine from
`settings.database_url`, while the integration harness (`app_under_test`) only repoints
`get_sessionmaker()` — so no existing fixture can reach it. **Closing it needs** an orchestrator harness
(inject the sessionmaker, or a settings override the task honours) — a slice, not a remediation fix.
Raised and named by Codex + this batch's own review on #368.

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

## RES-UPGRADE-LOCK-TIMEOUT

Status: OPEN
Owner: Repository owner
Source: Batch 12 migration-reviewer finding; mitigated in `docs/runbooks/backup-restore.md`
Reason: `easysynq upgrade` has no `lock_timeout`, so a migration can convoy the live write path.
Closure contract: Add an owner-reviewed Alembic connection lock-timeout contract and prove its global
failure semantics under a populated database with a concurrent writer.
Last reviewed: 2026-08-08

The in-place upgrade runs on a one-off worker **while api/worker/beat stay up** (`scripts/easysynq:82` →
`cli/upgrade.py`), no `lock_timeout`/`statement_timeout` is set anywhere in the repo, and
`migrations/env.py:108` wraps the whole run in ONE transaction — so any migration that takes a table lock
and queues behind an open writer holds everything until the entire `upgrade head` commits. Surfaced by
`0075`, the first revision to index the hottest, largest, monotonically growing table: its build takes
`ShareLock` on `audit_event` and every partition, and since nearly every mutating request writes an
`audit_event` in the same transaction, the write path convoys (reads are unaffected — `AccessShareLock`
does not conflict). CI is structurally blind to this: the `migrations` job round-trips an **empty,
single-connection** DB, so there is neither data to index nor a concurrent writer. **Mitigated for now,
not closed:** `docs/runbooks/backup-restore.md` § Upgrade documents stop/start steps and a row-count
pre-check. **Closing it needs** a `lock_timeout` on the alembic connection — deliberately NOT taken inside
a contract-housekeeping PR, since it changes *every* migration's failure mode from "wait" to "abort".
Found by the `migration-reviewer` pass on Batch 12.

## RES-AUDIT-KEY-ROTATION

Status: OPEN
Owner: Repository owner
Source: Batch 7, PR [#364](https://github.com/CoJoA13/EasySynQ/pull/364)
Reason: Audit checkpoints support only one verification key.
Closure contract: Add a key identifier to checkpoints and retain a public-key verification history, with
rotation and pre-rotation restore proofs.
Last reviewed: 2026-08-08

v1 is single-key; restoring a pre-rotation backup after a future rotation would verify the historical
signature against the current key. **Closing it needs** a key-id on the checkpoint + a retained public-key
history.

## RES-RISK-CLAUSE-PICKER

Status: OPEN
Owner: Repository owner
Source: `apps/web/src/features/risk/NewRiskModal.tsx`
Reason: The backend accepts an optional per-risk `clause_id`, but the v1 risk-creation UI has no clause
picker.
Closure contract: Design and ship the clause picker with the required authorization, form, API-contract,
and browser behavior proofs.
Last reviewed: 2026-08-08

## RES-RESTORE-SCRATCH-WORM-GUARD

Status: OPEN
Owner: Repository owner
Source: `apps/api/src/easysynq_api/services/backup/restore.py`
Reason: Restore verification rejects the configured documents bucket but does not reject every possible
WORM/object-locked bucket role as a scratch target.
Closure contract: Define all prohibited WORM bucket roles, fail closed before any scratch copy, and prove
the guard without weakening the current documents-bucket protection.
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

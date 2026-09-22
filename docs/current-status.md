---
easysynq_status_schema: 1
as_of: "2026-09-22"
baseline_commit: "c62ce09d0442b6b3d5df0ebb57ee0efb60dd3e52"
last_shipped_slice: "S-recovery-exact-version-binding"
migration_head: "0093"
next_migration: "0094"
api_unit_tests: 4977
web_test_files: 283
web_tests: 2357
contract_tests: 285
integration_passed: 1262
integration_skipped: 2
ci_jobs: 13
ci_checks: 15
---

# Current execution snapshot

This is the dated coordination snapshot for contributors and repository automation. It is not product
authority and it is not runtime discovery: binding decisions live in
[`decisions-register.md`](decisions-register.md), while current deferred work lives only in
[`open-residuals.md`](open-residuals.md).

The verified shipped baseline is MR !63's merged main `c62ce09d0442b6b3d5df0ebb57ee0efb60dd3e52` (merged 2026-09-22
01:48 UTC). Its [merged-results pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2869629501)
passed all eleven jobs it ran — the web suites were path-skipped because no web file changed —
including all eleven mandatory runtime cases. Frontmatter counts are read from that pipeline's job
logs: **4,977 API passes** with one existing release-only skip, **1,262 integration passes / 2
skips**, 285 response-contract tests, five migration tests; web (283 files / 2,357 tests) is
carried from the local gate. Between S-runtime-trixie-hardening and this baseline, `main` also
received Redis 8 (!43), the js-yaml major refusal (!57), the TypeScript 7 residual (!58) and the
CI compute policy (!53, !54, !56); none changed a migration or contract.

**Shipped: S-recovery-exact-version-binding, September 20–22 (!63).** Migration `0093` adds the
binding columns, and every `blob` row written from this slice on carries the exact object version
it was sealed as, recorded by the WORM promotion that already read it back; renditions record
`unversioned` because their bucket has no versions. Rows written before `0093` stay unbound (their
generations report `partial`) until the operator runs `backup bind-versions`, which records the
version observed at backfill time as `backfill` — never presented as sealed. Manifest v3
carries the binding and a generation state (`sealed` / `observed` / `partial` / `absent`); restore
resolves the bound version and now also checks recorded length; `backup bind-versions` binds older
rows as `backfill`. The motivating case — an object overwritten after its generation was written
restores from the sealed version — is proven against real MinIO with the version-aware copy
mutated red. **Nothing in the recovery contract closes:** the archive still carries no object
bytes, restore stays source-dependent and non-cutover, and `RES-SOURCE-INDEPENDENT-RECOVERY` and
issue #3 stay OPEN. See [dated evidence](slice-history.md#s-recovery-exact-version-binding--every-blob-bound-to-the-version-it-was-sealed-as).

The previous shipped baseline was MR !54's merged main `7f6174b854c7a5c02047266cb068bb6abeb61e2c`
(merged 2026-09-20), whose [merged-results pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2864107244)
passed all fourteen jobs after two retries inside an npm maintenance window.

**Shipped: S-runtime-trixie-hardening, September 19 (!51, !52).** The API image now uses Debian 13
(trixie), like the web image. Both images clear setuid/setgid bits, and the five application services
run with `cap_drop: [ALL]` and `no-new-privileges`; the proxy keeps its capabilities. The built-image
gate reports **API 0 blocking / 47 no-fix, none CRITICAL (was 85 no-fix, 14 CRITICAL)** and web
0 / 43. The remaining findings are eight shared base-OS CVEs, triaged in confidential issue #5 under
R61: none is reachable in the shipped configuration and none is risk-accepted. The runtime SQLite is
now 3.46.1. A read-only root filesystem is not done; `RES-CONTAINER-SECURITY-TRIAGE` and issue #4
stay OPEN. See
[dated evidence](slice-history.md#s-runtime-trixie-hardening--one-base-release-no-capabilities-no-setuid).

The previous shipped baseline was MR !46's merged main `9158a5dab6daff6a4ba7f9493f6588e9081eb835`
(merged 2026-09-12). Its final [source pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843299684)
at `7ee474e` and [merged-main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970)
passed all fourteen required jobs, including all eleven mandatory runtime cases.

**Shipped: S-audit-global-history-reconciliation (R85), September 12.** One inactive
entry point admits external enrollment, collects original R84 evidence, globally reconciles legacy
bootstrap and v2 material/edges, checks the entire path at every required witness, and returns a
compact result only after complete closure and owned cleanup. Its first supported success requires
a positive pinned legacy boundary and nonempty v2 history. Unknown bodies, gaps and late signed-head
contradictions remain visible; a healthy copy or included early pin never repairs them. Existing
R77/R78 supplied evaluators and R84 storage-only diagnostics retain their contracts and limits.

The complete local repository check now passes **4,939 API tests** (one existing release-only
skip), API Ruff/format/mypy, web lint/build and **2,357 web tests across 283 files**. Contracts,
authority and site-data guards pass. Fresh actual-image acceptance at test-fix commit
`8a8d8003fef38b34776f2e92e17f3232af01219b` passed **all eleven mandatory cases in 965.223
seconds**, zero failures/errors/skips. The new case took **114.373 seconds** within its
300-second cap. Genuine TLS cases used the same enrollment and **33 GETs / six LISTs**;
separate synthetic acceptance checked **4,097 v2 nodes, ten bridge pages and 12,809 GETs**,
plus a late conflict. Installed Python 3.12.14 / SQLite 3.40.1, 58 indexed plans,
resource/IPC controls, non-root/read-only execution and owned cleanup were verified.
Complete independent branch review found no material issues. GitLab Duo completed its review
without findings and disclosed partial diff coverage. MR
[!46](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/46) has since merged (above).
Its first source pipeline passed thirteen jobs but the built-image security gate found two
fix-available findings in a base runtime library. A targeted API-image package upgrade now
passes a fresh scan with **zero blocking findings / 85 no-fix findings still OPEN**.
The updated image passed **290 affected tests** and **all eleven mandatory runtime cases in
632.551 seconds**, zero failures/errors/skips; the new case took **55.791 seconds**. Scan and
acceptance image filesystem layers, all 39 installed module identities, complete receipt
semantics and owned cleanup were independently checked. Existing limits remain unchanged.
Current build digest: `67002eaaf7c5e8bc4f9f015b863ee1d8a4e7420adbe7dbeaa3995745d8c17672`;
proof digest: `8c591a931c34fd8a9178b4d5d9fbd07ca11a072bf65d99ecae78b53f691e2aba`.
The subsequent source pipeline at `2113200` again passed thirteen required jobs, including all
eleven runtime cases, and confirmed zero API-image blocking findings. Its web image had twelve
newly fix-available base-package findings. Targeted web-image upgrades now pass a fresh scan with
**zero blocking findings / 43 no-fix findings OPEN**, plus **123 affected tests in 3.09 seconds**.
The exact scanned image ran its normal preview command offline as UID 1000 on Node 26.8.2 /
npm 11.19.1. The page, SPA fallback and all 29 referenced JS/CSS assets matched packaged bytes; package
checks and owned cleanup passed. Application locks and API build/proof inputs are unchanged.
The final source and merged-main pipelines above confirmed both image thresholds: **API zero
blocking / 85 no-fix OPEN, web zero blocking / 43 no-fix OPEN**.

The first full check's 56 failures were attributed to a new test leaking a synthetic reader
across teardown and older CLI fixtures depending on host file permissions. Both test-only
fixes passed their focused regressions and the fresh complete check above. Dated earlier
receipts remain evidence for their recorded inputs; they are not substituted for this run.
[Dated evidence](slice-history.md#s-audit-global-history-reconciliation--bounded-global-closure) and
[the approved design](superpowers/specs/2026-09-12-audit-global-history-reconciliation-design.md)
state the exact limits and evidence scope.

No operational consumer, enrollment, signing, IAM, deployment or restore was activated. A consistent
observed history leaves database agreement, provider non-omission, custody, freshness/rollback
memory, delivery, key activation and source-independent recovery unproved. Issue #3 remains open
at 3/5; the historical runtime failure, Testcontainers warnings, MinIO permission limitation and
85 API / 43 web no-fix security findings retain their existing open records. Both counts are
confirmed by the merged-main security job. Passing the fixed-version threshold is not image-security clearance.

A recovery reconciliation completed on 2026-09-08 against
`6077e8a45b5942daf803220765b326f82ddd4417`. Server-verified staged upload digests, the non-root API
runtime, and monotone retention extension are shipped. The current archive and restore verification remain
source-dependent: exact snapshot object-version binding and service-capability separation are partial, while
mandatory complete encrypted generations, durable target disposition, and a source-denied recovered-stack
boot/read proof have not shipped.

[`RES-SOURCE-INDEPENDENT-RECOVERY`](open-residuals.md#res-source-independent-recovery) now owns that
overall recovery boundary, with current execution tracked in
[GitLab issue #3](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/3). Its narrower related records
retain separate closure contracts. S-restore-scratch-worm-guard closes the scratch-target record;
S-audit-verify-orchestrator closes the task-coverage record; S-upgrade-lock-timeout closes the lock-wait
record. Checkpoint lineage and key rotation remain open.
Production recovery and upgrade safety are unproven.

S-audit-historical-witnesses now checks every retained eligible legacy checkpoint object version at
each currently configured off-host witness. An older conflicting signed checkpoint remains a failure
after a genuine producer writes a newer consistent anchor. Delete markers and incomplete or denied
scans fail closed. External readers need the portable version permissions documented in the
[key-rotation runbook](runbooks/key-rotation.md); merging the Compose policy does not update live IAM.
The pinned provider's version-list denial limitation is OPEN as
[`RES-MINIO-VERSION-LIST-DENY`](open-residuals.md#res-minio-version-list-deny). Checkpoint lineage,
key-history selection and source-independent recovery remain open, and the database/store observations
are not one snapshot.

S-audit-external-trust adds explicit verification against an owner-controlled public descriptor on a
separate verifier machine. Enrolled organizations, public keys and witnesses remain required after
changes to the checked database's inventory. The public CLI uses dedicated reader credentials and a
fresh process environment; its separate read-only container acceptance passed. Existing scheduled/API
and no-option CLI verification retain their prior behavior. See the
[external-verification runbook](runbooks/audit-external-verification.md) and R73 for manual custody and
remaining limits. This does not close checkpoint lineage, key rotation or source-independent recovery.

S-upgrade-lock-timeout limits each online Alembic lock acquisition wait to five seconds (R74).
The populated migration and affected upgrade suite passed **19 tests without skips**, including
real blocked writers, truthful partial-commit behavior and migration-stage failure auditing. A
separate **20-test focused unit run** covered setup/offline/CI/CLI behavior. The runbook keeps writers
closed after failure and distinguishes earlier commits from the rolled-back active segment. This
closes only the lock-wait record; the broader recovery and production upgrade blockers remain.

S-audit-historical-target adds the explicit `--historical-target` consumer under R75. It checks a
closed older inspection database against the owner's enrolled retained legacy witnesses, using one
read-only REPEATABLE READ database snapshot. Authentic newer off-host anchors are reported as ahead
evidence; applicable contradictions remain failures. Every required witness must cover the complete
linked head and pending rows prevent success. The operator still selects the target independently;
lineage, key eras, archive provenance and recovery eligibility are not established.

S-audit-checkpoint-v2-codec freezes the pure signed-envelope representation under R76. It binds
organization/stream identity, predecessor, sequence, audit head and key epoch; a planned transition
also binds the next key and its proof of possession. Canonical bytes, strict input bounds and
canonical nonidentity prime-order Ed25519 keys are required. The codec returns immutable evidence.
Existing writers, scheduled/API/CLI verification, R73 enrollment, R75 historical reports, backup
and restore remain legacy. Envelope authentication does not establish lineage, key activation,
witness delivery, archive provenance or recovery eligibility; the related residuals remain open.

S-audit-lineage-reader adds the pure supplied-v2-graph evaluator under R77. It separates authenticated
public key material from predecessor-authorized edges, explores admissible forks, and returns stable
bounded diagnostics for contradictions and incomplete graphs. A required checkpoint pin can expose
a missing or conflicting supplied history. Only a globally consistent result exposes an ordered
path, used key epochs and a tip. Bootstrap assurance remains `external-pin-only`; bootstrap contents,
legacy bridge coverage, witness collection completeness, witness custody, audit-chain comparison,
freshness and operational key activation remain explicitly unproved. Current consumers remain legacy.

S-audit-bootstrap-bridge adds the pure supplied-legacy-bootstrap-package evaluator under R78.
An externally pinned root binds the exact required witness namespaces, retained-key inventory and
positive audit boundary. Every committed page and exact raw legacy body must reconcile; authentic
unlisted older records, conflicting locators and signed-head contradictions cannot be hidden by a
newer boundary or healthy sibling witness. Missing dependencies remain incomplete. Only a consistent
supplied package exposes the original external R77 bootstrap pin and per-witness summaries.

The result explicitly leaves operational legacy-history completeness, witness collection
completeness, custody, database-chain comparison, v2 lineage consistency, freshness, rollback-memory
continuity and operational key activation unproved. Matching omissions from the package and supplied
observations remain undetectable here. Current readers, writers, enrollment descriptors, grants,
backup and restore are unchanged; complete raw version collection, scalable global reconciliation,
actual snapshot comparison and independent activation/restore proofs remain prerequisites.

S-audit-raw-transport adds the inactive exact-version byte reader under R79. One explicit retained
key/version is fetched through a fresh private SDK client with verified TLS and a one-send exact-target
guard. Returned identity must match before bounded successful-body reads; unchanged bytes are exposed
only after body/client cleanup. They remain unauthenticated until R78. Actual provider evidence
preserves opaque retained versions and rejects missing returned identity for literal `null`; marker
and missing-version responses map to provider failure. Current operational callers remain unchanged.

S-audit-isolated-read adds the inactive Linux supervisor under R80. One unchanged R79 request runs
in a private worker with verified address-space, CPU, descriptor, core and regular-file limits.
Ready/request/result frames are bounded, credentials stay out of worker argv/environment, and exact
bytes are admitted only after stdout EOF, zero child exit, cleanup and final cancellation/deadline
checks. Existing readers, writers, protected enrollment, dependencies and restore remain unchanged.

S-audit-version-page-decoder adds the inactive supplied-byte interface under R81. It admits one
strict UTF8/XML version-list document with exact scope, unique fields, canonical flags and bounded
structure. One strict percent decode preserves key identity and literal plus; version IDs stay
opaque. Every admitted version, delete marker and duplicate remains an untrusted observation.
Malformed late content yields no page. Complete witness collection remains a separate prerequisite;
current operational callers remain unchanged.

The **2026-09-11 development record for S-audit-version-page-transport (R83)** adds one inactive original
version-page read through a private Linux worker. Its exact request binds reader, bucket,
organization and both opaque cursors; original XML reaches the R81 decoder before SDK parsing.
No page is admitted before client close, exact IPC, stdout EOF, zero worker exit, owned cleanup
and final cancellation/deadline checks. Genuine provider evidence required a narrow R81 extension:
optional DeleteMarker `ETag`, `Size` and `StorageClass` are bounded discarded singletons. Existing
strict structure, identity and limit rules remain; provider XML is never rewritten.

Scoped verification passed **814 affected unit tests in 97.79 seconds**, API Ruff/format, mypy over
**465 source files** and runner static checks. The metadata extension separately passed **210
decoder tests** after seven intended admission failures; runner membership passed **135 tests**
after its seven intended failures.

Full local `just check` passed at `1ef5edbd3aa7d8fa84327f30593c8e05a66f7780`: **3,737 API tests
passed with two intentional skips**, **2,357 web tests passed**, and Ruff, formatting over **819
files**, mypy over **465 source files**, ESLint, TypeScript and the Vite build passed. The skips are
the release digest check (`test_images_lock_pinned.py`, requires `EASYSYNQ_RELEASE=1`) and the
built-image/offline-start check (`test_infra_hardening.py`, requires `EASYSYNQ_IMAGE_PROOF=1`).

The **2026-09-11 pre-correction run** passed all **nine mandatory actual-image tests** in
**377.97 seconds**, with zero skips, errors or
failures; three pre-existing Testcontainers deprecation warnings remain. The new case used genuine
verified provider TLS and a distinct read-only identity,
collecting **1,007 independently created observations, including three delete markers**, in pages
of 1,000 and seven. It matched the complete creation multiset, opaque cursor chain, exact physical
queries and original trace-body bytes. The installed API ran at UID **10001** without development
packages. Fourteen body cases, five routing cases, the late-hook wrong-design control, five
containment cases, nine adversarial IPC cases and four actual resource experiments passed.
The CPU limit terminated its finite fixture at **19,998 ms of child CPU**; oversized SDK buffering
failed within the worker's 512MiB address-space limit and the parent remained usable.
Source/proof manifests and execution hashes matched; owned containers, image and runtime directory
were removed. Earlier failing fixture runs are not counted as acceptance passes.

Task review found that `coercible-latest` also changed the truncation flag. The fixture now changes
only the complete `IsLatest` element. Historical local canonical acceptance at
`94af759731fa01ac2d237fa77076820496ae8bab` passed **all nine mandatory installed-image cases in
382.108 seconds**, with zero skips, errors or failures and the same three pre-existing warnings.
The corrected latest-only vector returned `RESPONSE_INVALID`; genuine TLS, all 1,007 observations
over two pages, and the remaining body, routing, containment, IPC and resource cases passed.
Source, output, script, manifest and image hashes and all owned cleanup were verified.

Source CI **2840588422** then failed the new fixture's DinD reachability assumptions: the job and
daemon have separate loopbacks. The other **13 checks passed**; API units passed **3,738 with one
release-only skip**, and the eight older runtime cases passed. The fixture now uses Testcontainers'
resolved Docker host, a reachable publish address and a matching DNS/IP certificate SAN with TLS
verification retained. Provider-local endpoints remain unchanged. Independent scoped review marked
the correction **ADDRESSED with zero new findings**.

The latest local canonical proof at `61a9c7c891049a314ec3bc54d867b5b1fa761979` passed **all nine
mandatory actual-image cases in 384.010 seconds**, with zero skips, errors or failures and the same
three existing warnings. Genuine TLS, all **1,007 observations over 1,000/seven-row pages**, exact
trace bytes/requests and the remaining acceptance cases passed. All **823 source hashes** were
unchanged; command, output and artifact custody and owned cleanup were verified, including absence
of owned containers and image. Earlier local runs remain evidence for their earlier source states;
fresh source/DinD and merged-main CI were pending at that checkpoint. The verified R83 merged-main
baseline for the next increment is `f08b8b0ba96f049531e01a59f31427d9156a61d3`; its file tree equals
the previously verified clean `e26cb14` tree. The earlier run counts remain historical evidence.

The 16MiB body cap applies after SDK buffering; it is not a pre-buffer download/allocation bound.
Real network workers, substituted adversarial IPC producers and direct installed-limit experiments
are identified separately in the proof. Accepted review costs retain repeated reader admission
bounds and separate worker cleanup logic to keep R79/R80 unchanged; future fixes must synchronize
those paths. At the R83 boundary, required-witness traversal, sticky gaps/global cursor cycles and
fresh bounded spooling remained future work. The inactive R84 candidate below adds that foundation;
global reconciliation, actual database comparison, rollback continuity, key delivery and independent
activation/recovery proofs remain OPEN. GitLab issue #3 is not closed.

Authority checks (`AUTHORITY_OK`) and site-data checks passed. Whole-branch review approved the
source with zero new findings, carrying the two previously accepted Important maintenance costs
and pre-existing Minor warnings. Source/merged-main CI were pending in that earlier record. At that earlier checkpoint, the schema's shipped slice,
commit and suite counts retained their earlier verified baseline; the R81 dated evidence remains in
[slice history](slice-history.md). No migration,
dependency, CI configuration, operational caller, deployment or restore is changed.

The **2026-09-11 local candidate, S-audit-required-witness-collection (R84)**, binds all externally
required namespaces before I/O and traverses them through the existing serial R80/R83 workers.
One fresh storage-only SQLite worker retains every admitted original page, repeated body delivery,
marker, unavailable version and traversal gap. Its MEMORY journal/temp database is discarded after
finish or interruption and is never reopened. The API returns only post-cleanup traversal diagnostics;
`traversed` is not authentication, globally consistent history, database agreement or activation.
Existing protected enrollment, operational callers, writers and restore paths are unchanged.

Independent local verification passed **921 affected unit tests in 58.95 seconds**, zero failures,
errors or skips, plus scoped Ruff, format and mypy gates. The unchanged mandatory actual-image
harness then passed **all ten cases in 814.87 seconds**, with zero failures, errors or skips. Its
new collection case took **606,102 ms**, following a separate focused pass. It exercised synthetic
5,002-body scaling, actual resource/SQL/IPC controls, and **1,003 real provider GETs across three LIST
pages of 1,000/one/two observations**. Every downloaded locator/length/SHA256 tuple matched literal
seeded data independently. Trace validated exact GET metadata and its `<BLOB>` representation;
LIST XML bytes remained length/hash checked. Actual CPU consumption was **119,981 ms**;
seven adversarial IPC and ten actual-worker cases passed. All **2,055 source identities**, build,
proof, driver/log/JUnit hashes and fresh owned container/image/directory cleanup were verified.

The provider collector took **443,338 ms** within its unchanged 450-second allowance—a margin of
about **6.7 seconds**, not a general performance guarantee. Provider fixture/setup/cleanup took
**452,043 ms**; the other phases were synthetic **9,018**, resources **144,247** and certifi **767 ms**.
The proof stayed below the new-case 780-second and full-harness 1,200-second caps.

Full local `just check` then passed in **494.432 seconds**: **4,078 API tests in 152.51 seconds**,
**2,357 web tests across 283 files in 328.64 seconds**, API/web lint, formatting, type checks and
the web production build. The production-image runtime check ran with `EASYSYNQ_IMAGE_PROOF=1`.
The only skip was release-ceremony image digest pinning, since `EASYSYNQ_RELEASE` was not enabled.
`just authority-check`, site-data and whitespace checks also passed, with all **2,056 source
identities** unchanged during the gates. This checkpoint records local acceptance before Task 4
and whole-branch reviews and GitLab source/main CI and merge.

Fresh-worker startup required build-time checked-hash bytecode with assertions enabled and no
runtime cache writes, plus removal of unused database/settings imports. The Protocol keeps shared
runtime identity through re-exports, with defining-module/repr/new-pickle metadata changes. The
sink getter remains patchable and delegates to the original settings cache; callable metadata and
cache-management attributes are not identical. The bytecode-only comparison added **74,659,361
Docker-reported image bytes**; that measurement is not the final candidate's image-size delta.
See [the dated history](slice-history.md#s-audit-required-witness-collection--fresh-bounded-traversal-diagnostics)
for scope and costs. Global reconciliation, full bridge/key indexes, DB comparison, provider
non-omission, custody, freshness/rollback memory, key delivery/activation and independent recovery
remain open. No migration, dependency version, live IAM, deployment or restore is changed.

Those earlier local candidate runs did not define a shipped baseline. Current frontmatter now
records the verified R85 merge above.

Other full-stack counts are inherited from
[MR !26](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/26)'s source pipeline
`2834420570` and merged-main pipeline `2834469617`, both passing all fourteen required checks.
The merged baseline is `475490e45b1bef34253c878b26a0ebc97f751d4f`: 1,259 integration passes with two
existing skips, 285 response contracts, 2,352 web tests and 80 browser tests. Its image security gate
reported no blocking fix-available findings and retained **85 API / 53 web no-fix findings OPEN**;
this is baseline evidence, not a fresh security clearance. No live enrollment, grant, rotation,
restore, deployment or upgrade was performed.

## Shipped boundary

The original MVP foundation and the ISO 9001 workflow families are delivered. The routed SPA covers the
main document, workflow, compliance, reporting, audit, ingestion, drift, objective, management-review,
DCR, improvement, risk, context, interested-party, identity-provisioning, first-run setup, and read-only
Records surfaces. Retention Policy and Evidence Pack management remain without dedicated SPA routes.

The completed S-ui interface program spans slices S-ui-1 to S-ui-6. Routes and information
architecture are unchanged, as is every permission and gating behaviour, and no migration or permission key
was added. It is otherwise a surface and layout rework, with two exceptions that are not cosmetic. S-ui-2
corrected cache invalidation for the caller's own task list, which was previously refreshed only by the
change-request, improvement and leadership decision branches, so a document approval, a periodic-review
completion, every CAPA decision and both acknowledgement paths left it stale. And S-ui-5a is the one slice
in the program that is not front-end-only: it changes API `ProblemException` titles, OpenAPI descriptions
and the contract lock.

S-ui-4 shared the register page header across eleven register routes and the scorecard band shell across
four, and answered the accepted-duplication question the decisions register had parked. That entry's
measurement was corrected there: the risk, context and interested-parties lifecycle panel and publish
modal are far closer to identical than recorded, with no structural divergence, and the owner's decision
was to collapse the scorecard band only and leave the panel and modal accepted. A shared four-branch page
frame, the Library and Records registers, and register heading-level normalisation were all deliberately
left out; the first and last are tracked in [`open-residuals.md`](open-residuals.md).

S-ui-5a, S-ui-5b and S-ui-5c are corrections found by the owner walking through the running
application, not by a gate. S-ui-5a adopted American-US spelling as the house standard for user-facing
product text; the rule, its exact scope, and the reasons it knowingly diverges from ISO's own English
are **R68** in [`decisions-register.md`](decisions-register.md), which is where that standard first
acquired an authority home — until this pass it existed only in a pull-request body. It renamed no
field, enum or key, but it did change two user-visible API strings and the OpenAPI descriptions, so the
contract lock moved `e66fa80c…` → `041da029…`. S-ui-5b set a 16-pixel gap at each seam after the page
header, where every seam had measured zero, and stopped Mantine truncating status badge labels in
squeezed cells; `e2e/register-rhythm.spec.ts` bounds each gap to 8-24 pixels rather than pinning 16, so
what is proven is separation without double-spacing, not the exact rhythm. S-ui-5c fixed two distinct
mechanisms the owner had reported as one: sortable column headers wrapping, fixed with `nowrap` on the
shared `SortableTh`, and table overflow being invisible, fixed by defaulting `ScrollArea` to
`type: "auto"`.

S-ui-5d then closed the three of those the owner chose to act on. The controlled-document surface had
acquired four different names, one of which — the user manual's — was a wrong navigation instruction
naming a rail entry that no longer existed; every one of them is now **"Master document list"**,
including the API's `report_name`, which is the formal title in the provenance stamp and is therefore
Title Case where the interface labels are sentence case. That reached the OpenAPI document, so the
contract lock moved `041da029…` → `2b8c2503…`. `src/lib/shellLabelContract.test.ts` now pins the rail,
breadcrumb and document-title map to one string per destination, which is the guard whose absence let
the breadcrumb disagree with the rail for a whole slice. And the risk-matrix legend is capped to the
grid it keys.

What remains deferred is [`RES-IP-REGISTER-COLUMN-JUMP`](open-residuals.md) (owner-deferred).
S-rulepack-audit-program closed the rule-pack half of the spelling standard and left a narrower
predicate behind it; S-rulepack-approval-block has now closed that too, and opened nothing in its
place, so the classifier carries no known spelling gap. S-ui-6 closed the CAPA board's
coverage gap and replaced it with the narrower
[`RES-CAPA-LIST-TABLE-NO-SCROLL-CONTAINER`](open-residuals.md): that slice's spec measures the board
view, and the page's secondary List table has no scroll container, so the one table on `/capa` is still
unmeasured. The owner's rail-foot idea — a colour-scheme toggle, perhaps a clock — is now DELIVERED
and no longer an idea: its three open questions were put to the owner on 2026-08-31 and answered, the
answers are binding as **R69**, S-railfoot-pref shipped the account persistence and S-railfoot-ui
shipped the control and the organization clock. Nothing about it remains deferred.

S-ui-a11y-outline then closed the register heading-level record S-ui-4 opened and deliberately did
not act on; its identifier is retired from the ledger and survives only in the dated
[`slice-history.md`](slice-history.md) entries that raised it. Twenty-six of the thirty-three routed
leaf pages rendered no `h1`; `/admin/users` and `/admin/processes` jumped from an `h1` to an `h4`;
and `/documents/:id` topped out
at `h3` while its own suite carried three passing axe assertions. Every routed page now renders
exactly one `h1` in its loaded state and no route skips a level. The change is semantic only —
Mantine's `Title` takes the tag and the font size as independent props, so every promoted heading
carries a `size` equal to its previous `order` and renders identically; all seventy-eight Playwright
tests, including every geometry and rhythm spec, passed unchanged. `RegisterPageHeader` no longer
takes a level at all, which is what makes the eleven registers unable to diverge again.

Neither existing accessibility gate could see any of that, on two independent grounds measured
against axe-core rather than assumed: `page-has-heading-one` matches only the `<html>` element, so it
is INAPPLICABLE for every container-scoped run — which is every `axe(container)` call in the suite —
and `heading-order` fires at `moderate`, which the browser spec's `serious`/`critical` filter drops.
The closed record's own closure contract asked for exactly the assertion that cannot fire. Three
layers replace it and each was mutation-verified: a direct DOM walk, a source-text cohort contract
checked against the route table, and an unscoped unfiltered browser run that failed on the pre-fix
tree and passes on this one. What the slice does NOT do is add a title where a page has none, so the
detail routes' non-loaded branches and the five headingless faces of `/imports/:runId` are recorded
as `RES-REST-STATE-PAGE-HEADING`.

Evidence for the slice: ESLint, both strict `tsc` projects, the production build, Vitest at **280
files and 2,339 tests** and the Playwright Chromium suite at **78** were run locally and passed. The
API, migration, integration and response-contract suites were not run locally — this slice changes no
Python — but its pull-request CI exercised them and passed all fifteen executing checks with
`release-gate` skipped as designed. Their frontmatter figures are re-read from that run rather than
carried: API 2,003 with two skips and 285 contract schemas both unmoved, and `integration_passed`
corrected **1,224 → 1,231** (shards of 274, 213, 387 and 357, two skips), a figure that had been
carried since S-ui-4 without re-measurement. Firefox, WebKit, assistive-technology sessions, SMTP
delivery, deployment, live acceptance and the disposable Fedora proof did not run and are not
described as passed. No authenticated walkthrough of the changed routes was performed.

S-capa-width-railfoot-order then closed two defects the owner found by walking the running
application, neither of them introduced by the slice above. The `/capa` tab strip shifted 90 pixels
whenever the user changed face, because `CapaLayout` sized it by the active tab and a Mantine
`Container` is centred: `lg` and `xl` differ by 180 pixels. CAPA was the only one of the three tabbed
sections that varied — `AuditsLayout` and `DriftLayout` each pin one width across their layout and
every child — so every CAPA face is now `xl`, which also closes the `CapaBoardPage` branch-width
discrepancy that `RES-REGISTER-PAGE-FRAME` names as one of its blockers. ⚠ The old behaviour was
TESTED rather than accidental, so those assertions were rewritten, not deleted, and a cross-face
guard added: a per-face assertion structurally cannot observe movement between faces, which is how
three green tests coexisted with the defect. The rail-foot clock now sits above the theme control and
carries a six-digit date resolved in the organization zone — at 23:00 in a UTC-5 zone the UTC date is
already tomorrow, so a browser-local date under that label would name the wrong day. The clock row's
fit is MEASURED in a browser rather than computed, and `src/lib/tabSectionWidthContract.test.ts`
guards the width invariant across all three tabbed sections rather than only the one that broke.

Evidence: ESLint, both strict `tsc` projects, the production build, Vitest at **281 files and 2,352
tests** and the Playwright Chromium suite at **80** were run locally and passed. No Python changed,
so the API, migration, integration and response-contract figures above stand from the run that
produced them.

S-retire-fedora-dev then made **Ubuntu 26.04 the supported developer host** and retired the Fedora
developer path, recorded as **R71**. This was forced rather than chosen: Fedora 44 publishes no Node
26 package — `nodejs22-bin` and `nodejs24-bin` are present, `nodejs26-bin` is not — so the Fedora
bootstrap could not follow the tracked Node major forward, and its hard `.node-version != 22` gate
blocked the bump outright. The retirement therefore had to land BEFORE the Node bump, not after.
Removed: the Fedora bootstrap, the disposable two-media Workstation proof, their two contract test
suites, the injected Kickstart, and the runbook — 202 KB and two CI steps. There is now no manual
host-acceptance gate: `./scripts/doctor.sh` is the host contract and CI is the acceptance evidence.
Fedora warns rather than fails in the doctor, so a contributor mid-migration still gets a usable
report. API unit held at **2,003**; all ten shell contracts, `AUTHORITY_OK` and the site-data
backstop pass. No application code, migration, contract or permission key changed.

S-node-26 then moved the tracked Node major from **22 to 26**, which S-retire-fedora-dev had
unblocked. The pin itself was mechanical; the load-bearing change is
`scripts/lib/npm-audit-policy.mjs`, whose `SUPPORTED_NPM_VERSION` accepted only npm `10.9.x` — and
Node 22 is the last major shipping npm 10.9, so the `security` job would have failed immediately.
Widening it to npm 11 was safe because three assumptions were measured first: npm 11.19.0 reproduces
both `package-lock.json` files byte-for-byte, `lockfileVersion` stays 3, and `auditReportVersion`
stays 2. **No lockfile churn.** API unit stayed at **2,003** and web at **281 files / 2,352 tests** —
unmoved by design, since this changes the runtime rather than behaviour.

S-postgres-18 then moved the **database server from 16 to 18** across Compose, the digest-pinned
`infra/images.lock`, both testcontainers and CI's `migrations` service container, with the
`pg_dump`/`pg_restore` client moved to match. No schema change, no migration, no contract. No data
migration was required because there is no live deployment — the only PostgreSQL data is a
rebuildable dev volume. The load-bearing coupling is the CLIENT: `pg_dump` refuses a newer server, so
a 16 client against an 18 server fails 19 backup/restore tests with a message naming a version
mismatch rather than its cause. `apps/api/Dockerfile` installs the client **by package name**, so it
is invisible to a `postgres:1[0-9]` search and would have broken the worker's in-container drill
while CI stayed green; the rebuilt image was verified to carry pg_dump 18.6. Integration is
**1,231 passed / 2 skipped** — identical to the PG16 baseline, which is the point.

S-python-312-ceiling then refused a Dependabot base-image bump that every check had passed.
[`#448`](https://github.com/CoJoA13/EasySynQ/pull/448) moved `apps/api/Dockerfile` to
`python:3.14-slim-bookworm` and was CLEAN on all sixteen checks, yet ships an image that cannot
start: `uv sync --locked` cannot satisfy `requires-python` from `/usr/local/bin`, so it builds the
venv on a CPython 3.12 downloaded into root-owned `/root/`, and the CMD dies as uid 10001 with a
permission error. The refusal is a `versions: [">=3.13"]` Dependabot ceiling — **not** a
`semver-major` rule, which would not have matched a bump whose tag major is unchanged — plus
`test_api_image_python_major_matches_requires_python` in the required `api` job. ⚠ The runtime proof
that would have caught it already existed and had never run: it is `skipif`-gated on
`EASYSYNQ_IMAGE_PROOF`, set nowhere in the repository, which is the `EASYSYNQ_RELEASE` inertness
recurring one proof over. S-image-proof-enabled closed that gap by wiring the existing test up in
the `api` job rather than writing another.

The design tokens are now authoritative. The Mantine theme reads the `--es-*` typography, spacing, radius
and elevation scales instead of its own defaults, and `AppShell` separately reads the layout tokens rather
than hardcoding its dimensions. Before this the theme already took its font families and its status colour
pairs from tokens, but the typography, spacing, elevation and layout ramps each had zero consumers, so
those four rendered at Mantine's defaults. The accent is the brand mark's teal, so the mark and the interface agree.
`Archivo` is self-hosted under `apps/web/public/fonts/` under the SIL Open Font License 1.1, because the
Caddy CSP sets `font-src 'self'` and the air-gap bundle has no egress. Muted text reaches WCAG AA by
remapping Mantine's own `--mantine-color-dimmed` onto the token, which corrects roughly 331 `c="dimmed"`
call sites without component edits; correcting the token alone would not have changed them.

The left rail leads each destination with a hand-rolled inline SVG glyph, marks each PDCA section with
that phase's hue beside the phase name, and shows one navigation count: the caller's own open tasks. That
is the only count the RAIL can state honestly, because no aggregate counts endpoint exists and the
per-register numbers are derived by scanning capped register windows; the top bar's notification bell
carries the shell's other count. It follows the established
never-a-confident-zero rule, so a failed count renders as unavailable rather than as zero.

The Home quadrants render a tinted header band carrying the phase, its clause range and that quadrant's
current signal. The signal is folded from the same observations the tiles beneath it render, so a header
states an observed count and the label that count belongs to, never a compliance verdict, and it cannot
drift from the tile it summarises. Dark-scheme quadrant tints are designed rather than derived from the
light values.

Earlier first-run provisioning removes normal first-install dependence on the Keycloak console, a Keycloak
subject, or the retired fixed-`qmsadmin` helper. While setup is `UNINITIALIZED`, `/setup` accepts the
one-time EasySynQ bootstrap proof, creates and links the first Keycloak identity and EasySynQ user, assigns
the seeded System Administrator role, and displays a generated temporary password once. The SPA retains
the associated credential receipt only in volatile component memory and submits it during acknowledgment.
Provisioning authority ends outside `UNINITIALIZED`; the public acknowledgment route additionally
accepts only the narrowly fenced matching replay of a completed claim while setup is `IN_SETUP`. The active
receipt, current setup proof, complete claim, exact administrator assignment, and absence of an unrelated
administrator are all required before consumption. A reminted setup proof can acknowledge the same
still-displayed password generation; a superseded receipt consumes nothing and requires explicit password
reissue.
Keycloak forces a password replacement at first sign-in. Incomplete or mismatched replays fail closed.

Public bootstrap refuses an unrelated System Administrator assignment without exposing whether one exists
before the setup proof is validated. A host operator may recover only while setup is `UNINITIALIZED` by
running `easysynq setup release-administrator-blocker` for one exact Keycloak subject. The command refuses
the claim owner, removes only that user's System Administrator assignment, preserves the identity, user,
other roles, and history, and requires an independent incident/change record. Supported fresh-install
instructions never create or sign in as a demo identity before the browser creates the first administrator.

All supported usernames are trimmed and canonicalized to lowercase before claim binding, Keycloak
lookup/create, response projection, and ordinary `/users/provision` handling. Display names retain their
case. Later users continue to be created from Administration → Users with `user.create`; role assignment
still additionally requires `permission.grant`, editing uses `user.update`, and every credential reset of
another linked user requires both `user.create` and the unconditional system-tier guard under R64. SMTP and
activation email are not required.

Migrations `0087_first_admin_bootstrap` and `0088_bootstrap_credential` add nullable,
upgrade-compatible claim/link and credential-receipt digest state. The plaintext receipt, bootstrap proof,
and temporary password are never persisted or logged. The cross-system workflow never deletes a Keycloak
user as compensation and serializes proof admission, exact administrator-set checks, marker-owned profile
reconciliation, and credential issuance. Failed-proof accounting uses one atomic expiring Redis update,
rejects malformed negative reader state, and is rechecked inside the PostgreSQL singleton lock so racing
invalid attempts share the same limit. Detailed behavior and evidence remain in
[`slice-history.md`](slice-history.md#s-first-admin-provisioning--first-administrator-without-keycloak-administration).

The application API has exactly nine bearer-free operations in three bounded authorization categories:
public health/metadata/setup routing (`GET /healthz`, `GET /readyz`, `GET /auth/config`, and
`GET /setup/state`); bootstrap-secret-authorized mutations (`POST /setup/administrator` and
`POST /setup/administrator/acknowledge`); and signed-capability-authorized verification/share access
(`GET /verify`, `GET /evidence-packs/shared`, and `GET /evidence-packs/shared/download`). The capability
routes remain authorized and scope-bounded, not anonymous QMS-content access; ordinary operations, QMS
content, and customer/site data remain authenticated and authorized.

## Runtime truth

Before creating a migration, run:

```bash
cd apps/api && uv run alembic heads
```

The frontmatter value above is a dated snapshot. `alembic heads` is the migration authority; do not infer
the head from filename sorting or prose.

The permission catalog is defined in [`07-authorization-model.md`](07-authorization-model.md) and
enforced by the executable catalog assertion in `apps/api/tests/unit/test_authz.py`. The complete decision
set is defined by the headings and self-range declarations in [`decisions-register.md`](decisions-register.md).

## Verification baseline

Fresh 2026-09-22 evidence for S-recovery-exact-version-binding (!63), read from its
[merged-results pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2869629501) job
logs. It moves `api_unit_tests` 4,957 → **4,977**: one from !57's js-yaml refusal pin, which landed
between baselines, and nineteen from this slice (seventeen binding tests, two never-raise tests;
a manifest assertion re-pinned from v2 to v3 adds none), and `integration_passed` 1,259 →
**1,262** (three restore cases), `migration_head` `0092` → **`0093`** and `next_migration` to
**`0094`** (`uv run alembic heads` reports `0093_blob_object_version_binding (head)`). Contracts
(285), the eleven runtime cases and the migration suite (5) were measured again at the same values;
web was path-skipped in that pipeline and is carried at 283 files / 2,357 tests from the local
gate. CI topology was unchanged at that date (13 defined jobs; 14 instances on a tag or manual run
of the GitLab pipeline — under the R86 workflow the same 13 jobs expand to 15, or 16 on a `v*` tag).

Fresh 2026-09-20 evidence for the pipeline compute policy (!53, !54), read from !54's
[merged-results pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2864107244) job
logs. It moves `api_unit_tests` 4,942 → **4,954** (!53's twelve policy tests) → **4,957** (!54's net
three), and `ci_jobs` 12 → **13** (`docs-tests`). Web (283 files / 2,357 tests), Playwright Chromium
(80), response contracts (285), integration (1,259 passed / 2 skipped) and the eleven mandatory
runtime cases were measured again at the same values; `uv run alembic heads` still reports
`0092_user_color_scheme (head)`. `ci_checks` stays **14**: that is a tag or manual pipeline, and now
also the widest merge-request pipeline. The first post-merge main pipeline under the new policy ran
its **5** backstop jobs.

Fresh 2026-09-19 evidence for S-runtime-trixie-hardening (!51, !52), read from the
[merged-main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2863960314) job logs
at `6017f1a`. It moves `api_unit_tests` 4,939 → **4,942** (+3 hardening pins; measured against the
R85 merged-main pipeline). Web (283 files / 2,357 tests), Playwright Chromium (80), response contracts
(285), integration (1,259 passed / 2 skipped) and the eleven mandatory runtime cases were measured
again at the same values. `uv run alembic heads` still reports `0092_user_color_scheme (head)`. CI
topology is unchanged (12 defined jobs, 14 branch instances).

Fresh 2026-09-18 evidence for S-audit-global-history-reconciliation (R85, MR !46), read from job logs
rather than inferred. It moves `api_unit_tests` 4,181 → **4,939** (+758), measured in the R84
[merged-main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2842504738) and the
R85 [merged-main pipeline](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines/2843322970), and
the mandatory runtime cases 10 → **11**, which is not a frontmatter field. Web (283 files / 2,357
tests), Playwright Chromium (80), response contracts (285) and integration (1,259 passed / 2 skipped)
were measured again at the same values. `uv run alembic heads` still reports
`0092_user_color_scheme (head)`; the slice adds no migration, permission key or contract change. CI
topology is unchanged (12 defined jobs, 14 branch instances).

S-audit-external-trust was checked on 2026-09-08 against source base
`98357eaebcc653dea234abdf8efd774cddc7b54a`. Local API units passed **2,333 with two existing opt-in
skips** in **63.19 seconds**; the API unit frontmatter now reflects that fresh count. Both mandatory
current-image custody tests passed through the repository runner, including healthy and hostile-PG
public CLI cases, actual reader/mount denials and the genuine selection attack. Eight new component
integration tests and seven affected audit integration cases passed in separately attributed runs.
Ruff, **784-file** formatting, **452-source-file** mypy, runner static, authority, site-data and
whitespace checks passed. Three inherited Testcontainers namespace warnings remain. The older
image-start proof and release digest-pin check were the local unit skips; required GitLab CI enables
the former, and the new container proof ran independently. Migration head, web/contracts figures,
CI topology and the historical full-integration baseline are unchanged; no new full-integration
aggregate is inferred from the selected local modules. Published-commit CI remains the merge gate.

S-audit-historical-witnesses was checked on 2026-09-08 against source base
`02587988766e4ea7e0acdc76c665428261c33971`. Final local API units passed **2,172 tests with two
expected opt-in skips** in **35.83 seconds**. Ruff, formatting over **775 files**, mypy over **449
source files**, authority, site-data and whitespace checks passed. The two genuine producer
re-anchor regressions first failed on the old reader and then passed. The initial 59-case integration
run passed 58 cases, including durable historical alarms and existing audit/backup/restore behavior;
its remaining storage test needed provider-specific assertion corrections. That corrected test then
passed separately in **16.43 seconds**, completing all nine effective-permission identities and WORM
controls. Production and the other integration files were unchanged between those runs; these are
attributed runs, not a claim of one clean 59-case rerun. Source/log identities and zero owned fixture
containers were verified. Three pre-existing Testcontainers namespace warnings remain. The local
skips are the release digest-pin check and the opt-in built API image check; required GitLab CI runs
the image proof. See the [dated slice evidence](slice-history.md#s-audit-historical-witnesses--retained-checkpoint-version-verification).
Frontmatter figures retain their recorded baseline attribution; this paragraph records the new
affected-suite evidence without claiming a new full application baseline.

S-audit-verify-orchestrator adds coverage of the existing nightly task without changing production
behavior. Six unit cases cover missing-key fanout, accumulated notification dirtiness, no-commit,
engine-construction/read failures, re-raise and disposal. Three PostgreSQL cases run the real task,
settings hook, verification services and notification emitter against a private migrated database;
fresh sessions prove notification-only commits, a clean run and a required missing witness on an
empty linked chain. They capture out-of-band payloads at the sender boundary, not transport delivery.
The corrected affected neighborhood passed **19 integration tests**. The initial candidate's unchanged
unit inputs passed **2,089 tests / 2 expected local opt-in skips**, and five isolated behavior faults
were detected. The only correction was an integration assertion for the existing absent-checkpoint
reason. See [dated evidence](slice-history.md#s-audit-verify-orchestrator--durable-alarms-and-engine-lifecycle).
These bounded results do not replace the historical full integration baseline or close key/recovery work.

S-restore-scratch-worm-guard rejects configured documents, records and checkpoint buckets, all
manifest source buckets, restored custom checkpoint bucket declarations and any destination with
Object Lock enabled or unverifiable. Rejected destinations are not copied into or cleaned up.
The existing storage principal now needs read-only `s3:GetBucketObjectLockConfiguration`; unsupported
or denied metadata lookup fails closed. See the [operator runbook](runbooks/backup-restore.md) and
[dated closure evidence](slice-history.md#s-restore-scratch-worm-guard--protected-target-rejection-before-copy).
Fresh corrected-candidate verification on source base `6491eca` passed **2,083 unit tests** with two
expected local opt-in skips and **47 affected PostgreSQL/MinIO integration tests**. Ruff, format,
mypy, repository authority and site-data checks passed. These bounded fresh results do not replace
the historical full integration baseline below or establish source-independent recovery.

The built-image security gate scans the actual API and web artifacts using their production build
contexts. HIGH/CRITICAL findings with a reported fixed version block the required `security` job;
findings without a reported fix remain visible as OPEN. Scanner or report-processing failures also
block merging. The live npm policy remains enforced; filesystem and pip-audit findings remain
advisory. Passing this threshold does not establish image-security clearance.

Private image evidence collected on 2026-09-08 covers main `5577178` and the compatible web npm
candidate `dcd22d7` in [!15](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/15).
Actual image identities, unprivileged offline runtime entry paths and the web default preview
command were verified. The web image pins npm **11.19.1**; its local Renovate extraction and scoped
`<12` rule passed, as did the live source-lock audit with `blocked: 0`. The baseline fix-available
tooling findings are absent in the rebuilt candidate. OS applicability and remediation work remain
open under [`RES-CONTAINER-SECURITY-TRIAGE`](open-residuals.md#res-container-security-triage);
detailed inventories stay outside Git under R61. These artifact smokes do not prove production
deployment, recovery, or upgrade readiness.

The numeric frontmatter records each suite's latest verified result, with fresh or inherited evidence
attributed above. It is consumed by repository automation and must remain parseable, unique-keyed and
comma-free. `baseline_commit` identifies the source base for the current slice; it is not the published
candidate's test identity. Earlier S-ui-6 evidence used compatibility anchor
`1dcbc2bc12b14e11f037a657d44659412a7a39c0` and remains dated history. Update only freshly verified facts;
partial or unavailable checks must stay explicit.

Owner clarification on 2026-09-08: use no GitHub services or hosting. The setup migration replaces
GitHub-hosted CI tools, configures Renovate to use preinstalled Node/npm and uv/Python, blocks its
GitHub hosts, and omits GitHub release-note and interpreter-metadata lookups. Contributor hooks,
artifact retrieval, and active setup instructions move to non-GitHub distribution paths. Historical
PR links and local security policy files remain evidence/data. The protected dry-run job **16364818159** authenticated with the GitLab token, ran Renovate
**44.69.8**, Node **26.8.1** / npm **11.19.0**, uv **0.12.10**, and Python **3.12.14**, and extracted
**106** dependencies across **22** manager/file entries (42 retired Actions dependencies are no
longer extracted). GitHub/tool lookup failures were absent. It processed existing update branches;
TypeScript 7 still produced a real npm peer conflict with typescript-eslint's `<6.1.0` ceiling.
The optional RE2 native addon falls back to JavaScript RegExp because npm lifecycle scripts are
disabled. Subsequent protected-main evidence passed 14/14 jobs in pipeline **2829943288** and
15/15 in scheduled pipeline **2829947451**, with no GitHub lookup/authentication errors. Mailpit
!5 merged with its Compose and image-lock references synchronized. Scheduled Renovate job
**16372131561** then wrote the contract-tools npm lock: Redocly **2.51.2** and js-yaml **4.3.2**.
Clean install/live audit (zero vulnerabilities), full contract regeneration without tracked drift,
and the updated independent exact-version guards passed. Reviewed head
`6a3555527755552b8c2db66af425b7a36dfe8534` passed all **14** required jobs in pipeline
**2830246394**, and !6 merged as `d07570448ce53ff0e9fb82e71a996947394dda9b`.

Actual npm write proof is complete. Real uv lock refresh remains unverified until a suitable Python
update exists; the residual recorded for the Renovate GitHub-metadata gap remained open at that
date (closed as superseded by R86 on 2026-09-21, when Renovate was retired). Four existing
OpenAPI composition warnings and the unchanged Python generator's formatter FutureWarning remain;
this is not a warning-free toolchain claim. Major MRs !7–!9 remain Draft for their separate
compatibility/teardown gates. The planned rotation date is **2026-09-30**, ahead of the current
token's **2026-10-07** expiry: replace the masked/protected project variable using the existing
least-privilege role/scopes, verify a protected scheduled run, then retire the old credential.
Rotation has not occurred; [issue #2](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/2) tracks it.
Local contributor verification: doctor **75/0**, timing-artifact fixtures **9 passed**, hosting
and distribution guards passed, `AUTHORITY_OK`, and a clean site-data scan.

Fresh 2026-09-08 repository setup audit. GitLab now protects `main` against direct and force
pushes, limits merging to Maintainers, requires a successful pipeline and resolved discussions,
and rejects skipped pipelines as merge evidence. Release tags matching `v*` are protected for
Maintainer creation. The [GitLab setup runbook](https://github.com/CoJoA13/EasySynQ/blob/c62ce09/docs/runbooks/gitlab-repository-setup.md) (historical; superseded by R86) records the setup.

The Renovate credential failure is now reproduced rather than inferred from a generic error.
The audit first found a protected token on unprotected `main`. Protecting the branch corrected
that mismatch, but scheduled pipeline `2828253975` still failed. Two otherwise identical
protected-branch diagnostic jobs using Renovate **39.264.0** isolated the remaining wiring issue:
`16358067495` omitted the YAML self-reference and authenticated (`user` and `version`: HTTP 200);
`16358132692` included it, observed the literal `$RENOVATE_TOKEN`, and received HTTP 401. Neither
printed the credential. The earlier blanket claim about job variables outranking project variables
was wrong according to GitLab's documented precedence; the observed effective value is the evidence
for removing this declaration in this project. The existing bot token did not need rotation.

The authenticated dry run then found invalid `_comment` and `_schedule_comment` configuration
keys and exited successfully without extracting dependencies. Those unsupported fields are removed;
all update policy values are preserved. A new `renovate-config` job runs the same image's strict
repository validator on ordinary branches, so invalid configuration now blocks a merge. Local
GitLab structural guards pass **58/0** (the new validator assertion failed before its job existed).
Final branch pipeline `2828291882` passed all **14** jobs at `c4889eb2` and MR `!4` merged as
`2a1d614a` with an identical source tree. Real scheduled job `16358301239` then authenticated,
extracted **134** dependencies from **17** files and opened MRs `!5` through `!9`. It exited
successfully but reported missing GitHub authentication, tool/version lookup failures and
unrefreshed lockfile artifacts. Automation is therefore only partially configured; the remaining
credential and verification work was recorded as the Renovate GitHub-metadata residual in
[`open-residuals.md`](open-residuals.md) (superseded by R86 on 2026-09-21), not a clean updater claim.

The successful main pipeline `2828169447` at `66675359` supplied fresh audit evidence: API unit
**2,011 passed / 1 release-only skip**, integration **1,231 passed / 2 skipped**, **285** contracts,
web **2,352 tests across 281 files**, **80** browser tests, and migration round-trip checks passed.
The application counts are unchanged. The additional `renovate-config` job moves `ci_jobs` to
**12** and ordinary `ci_checks` to **14**. At that setup baseline, the security job succeeded with
pip-audit and Trivy findings still report-only; the later built-image gate is described above.
See `RES-CONTAINER-SECURITY-TRIAGE` in
[`open-residuals.md`](open-residuals.md). This is CI evidence, not deployment acceptance.

Fresh 2026-09-07 evidence for S-renovate-adopt. It moves `ci_jobs` 10 -> **11** and nothing else.
`api_unit_tests` stays **2,011** and `ci_checks` stays **13**: the slice adds assertions inside
existing tests rather than new ones, and the Renovate job is schedule-gated so it never runs on a
branch push.

⚠ Dependency automation had been UNATTENDED since the GitLab move — Dependabot does not run there,
and GitLab's own Dependency Scanning reports vulnerable packages rather than raising routine
version-bump MRs, so it is not a replacement. `renovate.json` is a faithful port of
`.github/dependabot.yml`, deliberately not a redesign, and carries the python ceiling forward.
No automerge: this repository has repeatedly found bumps that were green in CI and still wrong.

Measured: api unit **2,011 passed / 1 skipped** under `EASYSYNQ_IMAGE_PROOF=1`, `ruff check` and
`ruff format --check` clean, `mypy` strict clean across 449 source files, gitlab-ci-hardening
**53/0** (up from 49 — four new Renovate assertions), ci-hardening 85/0, `AUTHORITY_OK`,
`check-no-site-data` clean. The web, contract and integration figures are carried unchanged and are
NOT restated: no TypeScript, no OpenAPI, no migration.

Fresh 2026-09-07 evidence for S-gitlab-ci-port. It moves `api_unit_tests` 2,008 -> **2,011** (three
semantic pins for the new pipeline) and, more consequentially, the CI-topology fields: `ci_jobs`
12 -> **10** and `ci_checks` 16 -> **13**.

⚠ Those two figures now describe **GitLab**, which is the gate. The drop from 12 jobs is not a
reduction in coverage: GitHub's `integration` and `web` AGGREGATOR jobs exist only to give branch
protection one stable required-check name over a matrix, and GitLab gates on the whole pipeline, so
porting them would assert something already guaranteed. Every suite they aggregated still runs.
13 is the instance count on a branch push -- 10 defined jobs, with `integration-shards` fanning to
four and `web-tests` to two, and the tag-only `release-gate` not firing.

`.github/workflows/ci.yml` still exists and is still pinned by its own harness, but it is no longer
the gate; GitHub is read-only for the transition and its 135 historical PR links must keep
resolving.

Measured on the branch: api unit **2,011 passed / 1 skipped** with `EASYSYNQ_IMAGE_PROOF=1` as CI
runs it, `ruff check` and `ruff format --check` clean, `mypy` strict clean across 449 source files,
gitlab-ci-hardening 49/0, ci-hardening 85/0, `AUTHORITY_OK`, `check-no-site-data` clean. The web,
contract and integration figures are carried unchanged and are NOT restated: no TypeScript, no
OpenAPI and no migration changed.

Fresh 2026-09-03 evidence for S-image-proof-enabled. It moves `api_unit_tests` 2,006 → **2,008**
and the api unit skips 2 → **1**: one added CI pin, plus the built-image runtime proof itself
un-skipping and passing now that `EASYSYNQ_IMAGE_PROOF` is set on the `api` job. The remaining skip
is the release-ceremony digest check, which still needs `EASYSYNQ_RELEASE=1`. It touches no
TypeScript, no OpenAPI and no migration, so the web, contract and integration figures are carried
unchanged and are NOT restated as freshly verified. `ruff check`, `ruff format --check` and `mypy`
strict clean across 449 source files; the CI-workflow shell contract 85 passed / 0 failed;
`AUTHORITY_OK`; `check-no-site-data` clean.

⚠ The `api` job now builds the API image, so it costs a cold docker build it did not before. That
was the deliberate trade: `security` already builds the image but is non-required, and a guard that
cannot fail a merge is the gap this closed.

Fresh 2026-09-03 evidence for S-tika4-content-key. It moves `api_unit_tests` 2,004 → **2,006**
(one parametrized test, two cases) and nothing else. It touches no TypeScript, no OpenAPI and no
migration, so the web, contract and integration figures are carried unchanged and are NOT restated
as freshly verified. `ruff check`, `ruff format --check` and `mypy` strict are clean across 449
source files; `AUTHORITY_OK`; `check-no-site-data` clean.
Fresh 2026-09-03 evidence for S-python-312-ceiling. It moves `api_unit_tests` 2,003 → **2,004**
(one added test) and nothing else. It touches no TypeScript, no OpenAPI and no migration, so
`web_test_files`, `web_tests`, `contract_tests`, `migration_head`, `next_migration` and the
integration figures are carried unchanged and are NOT restated as freshly verified. Measured on the
branch with `ruff check` and `ruff format --check` clean, `mypy` strict clean across 449 source
files, `AUTHORITY_OK` and `check-no-site-data` clean. ⚠ The squash `6f5ca65` did **not** preserve the
branch tree byte-for-byte and this entry does not claim it did: `git diff 038ee31 6f5ca65` is the
[`#536`](https://github.com/CoJoA13/EasySynQ/pull/536) back-fill that merged in between, verified by
`git diff`.

Fresh 2026-09-01 evidence for S-railfoot-ui, which completes the rail-foot feature. Web-only: it
moves `web_test_files` 277 → **278** (one new file) and `web_tests` 2,257 → **2,273**, and the
Playwright Chromium suite 76 → **78**, which is not a frontmatter field. It touches no Python, no
migration and no contract, so `api_unit_tests`, `migration_head`, `next_migration`, `contract_tests`
and the integration figures are carried unchanged and are NOT restated as freshly verified.

⚠ **The browser gate caught a regression this slice introduced, and vitest could not have.** A
Mantine `SegmentedControl` renders `role="radiogroup"`, and the audits register's first filter is an
UNNAMED radiogroup, so `register-geometry.spec.ts`'s page-wide `getByRole("radiogroup")` began
matching two elements and failed Playwright's strict mode. It is the duplicate-`aria-label` trap one
role over: adding ANY control to the shell can collide with an unnamed page-level role query. The
repair scopes that query to `main`, which is the correct scope for a spec measuring register
geometry regardless, and leaves the shell free to grow. It was confirmed as this slice's regression
rather than a flake by stashing: 24 of 24 pass on the same tree without these changes.

⚠ **A second browser-only finding, in the new spec itself.** Mantine visually hides the real radio
`<input>`, so Playwright refuses to click it — "element is not visible", sixty retries — while jsdom
has no visibility model and `userEvent.click` on that same input succeeds. The component tests were
therefore clicking something a person cannot reach. The browser spec clicks the label instead, and
asserts on `document.documentElement.dataset.mantineColorScheme` rather than on the radio's checked
state, because the latter would only prove that a radio is a radio.

Measured locally on the branch, whose tree the squash preserved byte-for-byte as `5ef71c2` —
verified by `git diff`, not asserted. Vitest passed **278 files and 2,273 tests**; the full
`npm run test:browser` — build included, the only form that proves anything — passed **78 of 78
Chromium tests**; ESLint over `src` and `e2e`, both strict `tsc` projects and the production build
were clean. The API, migration, integration and response-contract suites were NOT run and are not
described as passed: this slice changes no Python.

Fresh 2026-09-01 evidence for S-railfoot-pref. It moves `api_unit_tests` 2,001 → **2,003**,
`migration_head` `0091` → **`0092`** and `next_migration` to **`0093`**. It is the first half of the
rail-foot feature: the account-level colour-scheme preference the control will write to, decided as
**R69**, which the register records along with its own range bump. The contract gains
`ColorScheme`, `MePreferencesUpdate` and `PATCH /me/preferences`, and `AppUser.color_scheme` is
required, so the lock moved `2b8c2503…` → **`786f6782…`**. No permission key: the route takes no user
id and can reach no other account, riding the authentication-only `GET /me` precedent.

Migration `0092` was round-tripped on a throwaway PG16 — `upgrade head`, `downgrade base`,
`upgrade head`, and `alembic check` clean — and then, separately, on a POPULATED database, because a
fresh-DB round trip cannot see a backfill or a populated-downgrade abort. Three `app_user` rows were
inserted at `0091`; the upgrade backfilled all three to `AUTO`, `is_nullable` became `NO`, the
downgrade dropped the column and the enum type with all three rows surviving, and the re-upgrade and
`alembic check` were clean again. The column carries NO `server_default` on either side, which is why
`alembic check` is clean: an enum default reflects back as `'AUTO'::color_scheme`.

Measured locally on the branch, rebased onto `f0959fa` so it carries the partition-runway repair,
and whose tree the squash preserved byte-for-byte as `b679efc` — verified by `git diff`, not asserted. API unit passed **2,003 with the same 2 expected
skips**; Ruff lint and format-check were clean over 769 files and mypy found no issue in 449 source
files; `gen-contracts.sh --check` reports the contract in sync. The eleven tests in
`tests/integration/test_auth_me.py` passed, seven of them new, and the authenticated
response-contract sweep passed **285**, up one for the new operation — so `contract_tests` moves
284 → **285** as freshly verified rather than carried. `integration_passed` and `integration_skipped`
stay CARRIED and are explicitly not claimed for this tree. A whole-suite local run is not the
supported mode — CI runs `integration-shards (1..4)`, while one process shares a single database
across every file — and the local toolchain additionally cannot pass the restore drill at all,
because its `pg_restore` is PostgreSQL 17+ and emits `SET transaction_timeout = 0` against the
postgres:16 testcontainer. That was confirmed as an environment limit rather than a regression by baselining it:
`test_setup.py` plus `test_restore.py` produce an identical **7 failed, 101 passed** with this
slice's changes stashed and applied. The web gate was run as a precaution and is NOT
load-bearing here. ⚠ Two earlier versions of this paragraph claimed otherwise and both were wrong;
the adversarial review caught it. The contract regeneration does rewrite
`apps/web/src/api/_generated/schema.d.ts`, but that file has **zero importers** and `tsconfig.json`
sets `skipLibCheck: true`, so tightening `AppUser.required` cannot reach `tsc` at all. The real and
only coupling is by hand: `meFixture` and the e2e `/me` fixture are pinned with `satisfies` against
the HAND-WRITTEN `Me` interface in `useMe.ts`, not the generated schema — which is precisely why a
field added server-side stays invisible to them. This slice adds `color_scheme` to `Me` and to both
fixtures. Nothing structurally ties them to `_represent`; that gap is real, is not closed here, and
is the reason the check mattered even though the gate could not have failed. ESLint over `src` and
`e2e`, both strict `tsc` projects and the production build are clean, and Vitest passed **277 files
and 2,257 tests**. Those two figures are therefore freshly verified
rather than carried, even though neither moved — this slice adds no web test, and the point of
running them was the regenerated schema, not a new assertion. `npm run test:browser` was NOT run:
this slice renders nothing. Four
mutations were run and each reddened only the
proof it should — dropping the commit, turning the partial update into a reset, using the wrong
transient fallback, and widening the write to every user in the org, which is caught solely by the
cross-account isolation test.

Fresh 2026-09-01 evidence for S-partition-runway-test. A one-line test repair with no frontmatter
movement: no test is added or removed, so `api_unit_tests` stays 2,001, and nothing else in the
numeric block is touched.

⚠ **It fixes a time bomb that had already gone off.** `audit_event` is partitioned by month and
migration `0010` seeds a FIXED runway of 2026-06/07/08. The rolling top-up is the application's job —
the daily `roll_partitions` Beat plus the `ensure_partitions` boot hook in `main.py` — and the
integration conftest does its own top-up. `tests/migration/test_migration_coherence.py` has neither:
it drives Alembic against its own scratch database, so it sees exactly those three months. It
inserted a `PACK_INVALIDATED` row at `now()`, which worked until 2026-08-31 and from 2026-09-01
failed with `no partition of relation "audit_event" found` on EVERY branch, `main` included. That was
confirmed on `main` in a clean worktree before anything was changed, so it is dated repository
breakage rather than a slice regression. The instant is incidental to what the test asserts, so it is
pinned inside the seeded runway and the test no longer depends on the wall clock.

Production was never affected: `main.py`'s lifespan already calls `ensure_partitions` on boot for
exactly this reason, and the Beat keeps the runway ≥2 months ahead.

Measured locally on the branch, whose tree the squash preserved byte-for-byte as `f0959fa`
(verified by `git diff`, not asserted). `tests/migration` passes; API unit passed
**2,001 with the same 2 expected skips**; Ruff lint and format-check were clean over 769 files and
mypy found no issue in 449 source files. The web, integration and response-contract suites were not
run and are not described as passed.

Fresh 2026-08-31 evidence for S-rulepack-approval-block. It moves `api_unit_tests` 1,998 →
**2,001** (three new tests in an existing file) and nothing else in the frontmatter: it touches no
TypeScript, so the web figures are carried unchanged and NOT restated as freshly verified, and it
adds no migration and no contract. It moves `classifier_version` `rule-heuristic-1.1` →
**`rule-heuristic-1.2`** — the second bump in two slices, for the same cause both times: R68's
US-spelling standard reaching a needle that is a classification signal rather than a label. Two
things differ from the `1.1` bump and both matter to a reader resuming an import. The edit is in
`rule_classifier.py::_eval_predicate`, not the pack YAML, and the pin still moves because
`classifier_version` denotes the whole scoring behaviour, cutoffs and named predicates included. And
it is an ADDITION rather than a prefix shortening, because "authorised by" and "authorized by" share
no substring — so this slice also had to establish that a block spelling it both ways scores once.

The published INTERIM accuracy band was re-measured against `1.2` and is unchanged at kind 0.911 /
type 1.000 / clause precision 0.889 / clause recall 1.000 over 45 entries. That is measured, not
argued from the corpus text: all 45 entries were classified under both the old and the new predicate
and zero of them differ in any field. The corpus keeps only "Approved by" phrasings, which is why
nothing moved; it was not edited to make the band hold.

Measured locally on the branch, whose tree the squash preserved byte-for-byte as `7ec59ca` —
verified by `git diff`, not asserted. API unit passed **2,001 with the same 2
expected skips**; Ruff lint and format-check were clean over 769 files and mypy found no issue in 449
source files. The web, integration and response-contract suites were not run LOCALLY and no local
result is claimed for them; `#520`'s own CI did exercise them, settling at **15 passing checks of 16**
with `release-gate` reporting "skipping" as usual. Their frontmatter counts stay carried rather than
refreshed, because fresh counts were not captured from those runs. Five mutations were run against the tree the tests execute, each reddening for its predicted
reason: reverting the fix, deleting BOTH needles outright, storing the new needle capitalised,
deleting `_eval_predicate`'s `.lower()`, and implementing the US spelling as a separate pack matcher
instead of extending the predicate. Two are isolating. The `.lower()` deletion reddens the
case-insensitivity case and nothing else across the suite (1 failed, 2,000 passed), which is what
that case uniquely covers — the capitalised-needle mutation reddens two tests and does not isolate
it. The separate-matcher mutation is the only shape that can genuinely double-count, and it failed
at `assert 90 == 75`.

Fresh 2026-08-31 evidence for S-rulepack-audit-program. It moves `api_unit_tests` 1,996 → **1,998**
(two new parametrize cases in an existing file) and nothing else in the frontmatter: it touches no
TypeScript, so the web figures are carried unchanged and NOT restated as freshly verified, and it adds
no migration or contract. It does move `classifier_version` `rule-heuristic-1` → **`rule-heuristic-1.1`**,
which is not a frontmatter field but is the fact most likely to be needed next: the pin is written into
permanent vault import provenance, and a resumed import run adopts it and re-classifies under one
matcher set. The published INTERIM accuracy band was re-measured against the new pin and is unchanged
at kind 0.911 / type 1.000 / clause precision 0.889 / clause recall 1.000 over 45 entries.

Measured locally on the branch, whose tree the squash preserved byte-for-byte as `bb12945`. API unit passed **1,998 with the same 2 expected skips**; Ruff lint and
format-check were clean over 769 files and mypy found no issue in 449 source files. The web, integration
and response-contract suites were not run and are not described as passed.

Fresh 2026-08-30 evidence for S-ui-6. The numeric frontmatter above describes `main` after that slice,
whose four new Vitest tests land in two EXISTING files — so `web_tests` moves 2,253 → **2,257** while
`web_test_files` stays 277 — and whose seven new browser cases (`e2e/capa-board.spec.ts`) land in the
Playwright figure, which is not frontmatter. S-ui-6 changes no Python, no migration and no contract, so
`api_unit_tests`, `integration_passed`, `integration_skipped`, `contract_tests`, `migration_head` and
`next_migration` are carried unchanged and are NOT restated as freshly verified. The S-ui-5d, S-ui-5c,
S-ui-4 and 2026-08-17 paragraphs below are retained as the evidence for their own trees and are not
restated as current — in particular the "69 of 69 Chromium tests" figure below belongs to tree
`bdd8f2f`, not to this one.

Measured locally on the S-ui-6 branch, whose tree the squash preserved byte-for-byte as `c28040f`. Web Vitest passed **277 files and 2,257 tests** in 343.86
seconds. The full `npm run test:browser` script — build included, which is the only form that proves
anything, since a bare `playwright test` serves a stale `.playwright-dist` — passed **76 of 76 Chromium
tests** in 1.4 minutes, up seven. ESLint over `src` and `e2e`, both strict `tsc` projects and the
production build were clean. Every layout claim in the new spec was mutation-verified individually;
two assertions in the first draft were found INERT against their own mutations and were rewritten
until they reddened, which is recorded in the slice-history entry rather than smoothed over. The API
unit, integration and response-contract suites were not run locally and are not described as passed;
neither were Firefox, WebKit, assistive-technology sessions, SMTP delivery, deployment, live
acceptance or the disposable Fedora proof.

Measured locally on the merged S-ui-5d tree `bdd8f2f`. API unit passed **1,996 tests with the same 2 expected skips**
in 35.41 seconds; Ruff lint and format-check were clean over 769 files and mypy found no issue in 449
source files. Web Vitest passed **277 files and 2,253 tests** in 428.77 seconds — up two tests and one
file, the new `src/lib/shellLabelContract.test.ts`. The full `npm run test:browser` script passed **69
of 69 Chromium tests** in 1.4 minutes, up two: `e2e/risk-matrix-legend.spec.ts`. ESLint, both strict
`tsc` projects and the production build were clean, as were `check-repo-authority.sh`,
`check-no-site-data.sh` and the 91-fixture agent-authority test. `gen-contracts.sh --check` reports the
contract in sync at SHA-256 `2b8c25038de77414cf0d2ef1473611dd2a9c5e335915a54a3bcca2304bf45f3b`,
regenerated rather than hand-edited and superseding `041da029…`; the Alembic head stayed
`0091_documents_list_index`, so `0092` is still next.

Every claim S-ui-5d makes about layout is mutation-verified, because jsdom can see none of it. Removing
the `maxWidth` cap reddens both new browser cases — including the one asserting the scorecard band sits
beside the matrix at 1200 pixels, which is what establishes that the cap *widens* the side-by-side range
rather than narrowing it. Reverting only the breadcrumb, which is precisely the S-ui-5a defect, reddens
both cases in the new label contract; leaving a stale label beside the new one reddens only the second,
so neither assertion is redundant.

The integration and published response-contract suites were NOT run locally for S-ui-5d, and the
then-frontmatter's 1,224 integration and 284 contract figures were still carried from the S-ui-4 CI run
(both have since been re-read from a later run; the figures in this paragraph are the ones that stood
when it was written and are deliberately not updated). That
carry is weaker here than it was for S-ui-5b and S-ui-5c, which were front-end-only: S-ui-5d changes an
API response value and the OpenAPI document, so those suites are exactly the ones with something new to
say about it. Its pull-request CI run `33289631692` exercised them, passing all fifteen executing
pull-request checks with `release-gate` skipped as designed; no local run was performed. Firefox, WebKit,
assistive-technology sessions, SMTP delivery, deployment, live acceptance and the disposable Fedora
proof did not run and are not described as passed.

Measured locally on the merged S-ui-5c tree `6f0e0fd`. API unit passed **1,996 tests with the same 2
expected skips** in 32.86 seconds, the release-ceremony image-digest and image-build opt-ins; S-ui-5a
edited API docstrings, comments and two `ProblemException` titles, and no **API** unit test asserts
those strings. One web unit test does, `ProgramPage.test.tsx:249`, but against its own MSW fixture, so
it pins nothing about the API. Web Vitest passed **276 files and 2,251 tests** with exit code 0 in 353.26 seconds — unchanged,
because S-ui-5a renamed `ProgrammePage.test.tsx` to `ProgramPage.test.tsx` without changing its
assertion count and the other two slices added browser specs rather than unit ones. The full
`npm run test:browser` script, build included, passed **67 of 67 Chromium tests** in 1.3 minutes with one
worker and zero retries — but only after fixing a defect in one of the specs being recorded. S-ui-5c's
scroll cases reddened `web browser (Chromium)` twice on trees that passed locally, and the failing case
moved between runs, which is what identified it as a race rather than the wrong selector first
suspected: `ScrollAreaScrollbarAuto` renders nothing until one of its `ResizeObserver`s fires, so a
single synchronous snapshot can read `barShown: false` while the behaviour is correct. The assertion is
now polled, and the mutation was re-verified against the polling form because a poll that merely times
out would be a tautology. That is a **false failure**, the mirror of the false pass this repository
normally guards against. Playwright is up from 52: S-ui-5a added none, S-ui-5b's `e2e/register-rhythm.spec.ts` added
ten and S-ui-5c's `e2e/register-table-legibility.spec.ts` added five, across nine spec files once
`playwright.config.ts`'s `testIgnore` excludes the two harness probes. `uv run alembic heads` reported
`0091_documents_list_index (head)`, making `0092` next; none of the three slices added a migration.
Contract checking is in sync on this tree at SHA-256
`041da0299dde316f1a6c2ca8acb1106171960ea44e52c19b8b88afd3bf5f7958`, regenerated by
`scripts/gen-contracts.sh` in S-ui-5a and superseding the S-ui-4 hash `e66fa80c…`. Each of the three
pull requests passed all fifteen executing pull-request checks with `release-gate` skipped as designed.

The integration and published response-contract suites were NOT run locally for these three slices; the
then-frontmatter's 1,224 integration and 284 contract figures were carried from the S-ui-4 CI run, which was
sound for S-ui-5b and S-ui-5c because they are front-end-only, but is NOT fresh evidence for S-ui-5a,
which changed API strings and the OpenAPI document. S-ui-5a's own pull-request CI exercised those
suites; no local re-run was performed here. Firefox, WebKit, assistive-technology sessions, SMTP
delivery, deployment, live acceptance and the disposable Fedora proof did not run and are not described
as passed.

Three coverage limits of the new browser specs are worth stating, because a count of 67 reads as broader
than it is. First, of `register-table-legibility.spec.ts`'s five cases only three are load-bearing —
objectives' "Current / target" wrap and both scroll cases — and showing it took two mutations, not one.
Restoring the pre-fix `RegisterToolbar.tsx` and re-running the full `test:browser` script reddened
exactly one wrap case; removing the theme's `ScrollArea` entry reddened both scroll cases, each having
asserted its `overflows` precondition first. Context's "Last reviewed" and risks' "Risk / opportunity"
each measured a single line box before the fix too, so they are belt-and-braces rather than evidence. Second, the master document list — the surface
whose silent horizontal scrolling prompted the `ScrollArea` change, and which S-ui-5d has since
renamed from "Controlled register" — is reachable by no Playwright
scenario at all, because `reports` is not among the ten register cases in `e2e/support/registers.ts`; its
fix is inferred from the shared theme default, not measured. Third, that theme default also reaches two
bare `ScrollArea` consumers no test covers and no commit message mentions: the navigation rail
(`AppShell.tsx:67`), which now shows a persistent bar on a short viewport, and the CAPA board
(`CapaBoardPage.tsx:232`), whose six 260-pixel columns always overflow, so it now carries a permanently
visible horizontal scrollbar. `ScrollArea.Autosize` in the notification bell is unaffected — it resolves
under a separate theme key. Both are plausibly the intended improvement, but neither is measured, and the
second lands on the one route S-ui-5c declared unmeasurable.

Measured on the merged S-ui-4 tree `2626ba9`. API unit passed 1,996 tests with 2 expected skips in 35.65
seconds, both skips being the release-ceremony image-digest and image-build opt-ins — unchanged, because
S-ui-4 touched no API code. Web Vitest passed 276 files and 2,251 tests with exit code 0, up from 273 and
2,230: S-ui-4 added the shared header and band-shell unit suites and a source-text adoption contract. The
Playwright browser suite passed 52 of 52 Chromium tests with one worker and zero retries, up from 42; the
ten new cases measure each shared register header both for a granted caller, whose action renders, and for
a denied one, whose header must carry no element standing in for the affordance they lack. Web ESLint
exited 0 and strict `tsc --noEmit` passed for both the application and browser projects; the production
build completed. `check-repo-authority.sh`, `check-no-site-data.sh` and the 91-fixture agent-authority
test all passed. Alembic reported `0091_documents_list_index (head)`, making `0092` next — S-ui-4 added no
migration.

One measurement caveat worth carrying forward. `npm run test:browser` is `build:browser && playwright
test`; invoking `playwright test` alone serves a previously built bundle, so a source change — including a
deliberate mutation used as proof — does not reach the browser and the suite reports a stale result. Any
Playwright evidence must come from the full script.

Pull-request CI run `33264035498`, on the pre-merge S-ui-4 branch head `ddd893d`, passed all fifteen
pull-request checks with `release-gate` skipped as designed. Its four integration shards passed 272, 209,
389 and 354 tests — 1,224 with the two expected shared-database skips — and the published
response-contract job passed all 284 schemas. Those figures are unchanged from the S-ui-3 run
(`33239332445`, branch head `05cd1663`), which is what a front-end-only slice should produce; they are
re-read here rather than carried forward. The slice merged to `main` as `2626ba9`. Contract checking was in
sync on that tree at SHA-256 `e66fa80c1e7c6ffec3f0a1321a59fea2440cf1f48beb6677c1dbe26cd87243cf`, which
superseded the 2026-08-17 hash recorded below. (That figure was briefly overwritten here with the later
S-ui-5a hash by an authority-document spelling sweep, and is restored: `git show
2626ba9:packages/contracts/.contract.lock` is `e66fa80c…`, and the lock's history shows `041da029…` first
appearing at `030c89d`.)

`docs/11-ui-ux-design-system.md` was not updated by this program and now disagrees with the shipped
tokens on typography, accent, focus ring and shell metrics; that gap is recorded as
`RES-DOC11-TOKEN-DRIFT` in [`open-residuals.md`](open-residuals.md).

The integration and response-contract suites were NOT run locally for this program; their counts above
are taken from that CI run rather than from a local execution, because the local Docker-backed fixtures
were not exercised. Firefox, WebKit, assistive-technology sessions, SMTP delivery, deployment, live
acceptance and the disposable Fedora proof did not run and are not described as passed.

The three UI slices were additionally reviewed by an adversarial multi-lens pass whose confirmed findings
were folded before merge. Two are worth recording because they defeated the automated gates entirely: a
CSS custom-property override that lost on specificity and was inert in the light scheme, and a card layout
that clipped the Home quadrants' only navigation action out of view at every breakpoint. Neither was
visible to ESLint, strict TypeScript or the complete Vitest suite, because jsdom performs no layout and
resolves no stylesheet cascade. Both are now covered by executable guards — a token contrast gate that
derives its pairs from the token naming convention, and `apps/web/e2e/home-geometry.spec.ts`, which
measures each quadrant's action against its card at 320 and 1280 pixels in a real browser.

Fresh 2026-08-17 durable evidence measured the complete affected inventories after the first-administrator
PR review fixes.
API unit job `job-mswq4zse-b59b5405` passed 1,835 tests with one expected release-ceremony image-digest
skip in 30.33 seconds. API integration job `job-msx8jger-0e1559da` passed 1,163 tests with two expected
shared-database skips, 284 contract-marked tests deselected, and three registered Testcontainers import
deprecations in 548.61 seconds. Published response-contract job `job-mswlweou-9b17e09e` remained valid
because the later fixes changed no API/OpenAPI/response surface; it passed all 284 schemas with the same
three deprecations in 251.84 seconds. Web job `job-msx9sx1s-0e95eae6` passed all 267 Vitest files and
1,946 tests in 308.83 seconds with the known Node
`localStorage` diagnostics.

The bounded PR review fixes cover the first-administrator blocker host-recovery guidance, separate bound
username/email collision guidance, and trusted-remint admission-budget reset/rollback; the fresh complete
inventories exercised their owning web and setup integration suites without changing API unit, contract,
migration, hash, CI-topology, or residual evidence.

The populated migration gate passed 1/1 and independently exercised `0087 -> 0086 -> 0087` and
`0088 -> 0087 -> 0088`, with the registered PostgreSQL Testcontainers deprecation. Final
recovery/install cohorts passed 8 recovery integrations, 3 CLI/wrapper dispatch tests, and 15
supported-install/first-admin guards.
The earlier synthetic browser job `job-mswm9e51-7a34ed2b` passed 40/40 Chromium tests in 16.4 seconds
with one worker and zero retries for its then-tested tree. After responsive first-administrator corrections
through `d091ee58aa05b5590f42fe395e22062137ccb38e`, the final local
`npm --prefix apps/web run test:browser` cohort again passed 40/40 Chromium tests with one worker and zero
retries. CI run `32040002549` corroborated that final tree: all fifteen expanded checks, including
`web browser (Chromium)` and aggregate `web`, completed successfully.

The separate narrow live job `job-mswlg4ft-b05efb99` remained valid because the later fixes did not change
its exercised identity/provider flow. It passed 1/1 Chromium test in 2.6 seconds with one worker and zero
retries against fresh Docker-backed API, PostgreSQL, object store, Redis, and Keycloak services. It proved
mixed-case input returned and bound the canonical lowercase username, mandatory password replacement
succeeded, and the obsolete temporary password was rejected from a clean browser context. Teardown removed
the exact `easysynq-first-admin-32b1b175ba35` project's containers, volumes, network, and six local images.

Static and contract gates were also fresh: Ruff format reported 750 files already formatted; Ruff lint
passed; mypy found no issues in 444 source files; web ESLint exited 0; and the production TypeScript/Vite
build transformed 1,107 modules with only the existing large-chunk advisory. Contract checking is in sync
at SHA-256 `bec600ffcc53e6f73871c46e0a52ae520902a50e78e1b1e72d1265927cebb90b`;
Alembic reported only `0088_bootstrap_credential (head)`, making `0089` next. Executable workflow parsing
found eleven job definitions and fifteen expanded aggregate/leaf checks at that date; it now finds
twelve and sixteen. The one added job and check is `release-gate`, from the release-gate slice.

The final requirements/security whole-branch review found no Critical issue, fixed its authority,
current-comment, negative-Redis-state, and exact bearer-free allowlist findings in commits
`4ea6e78c5edf52a2666a6bee1130ace34ab19b56` and
`1f6f12def9ddb539711ceb4b382e3e28f4c1f87e`, and the scoped final re-review reported no unresolved
Critical or Important finding. No owner-visible residual closure contract changed.

Firefox, WebKit, actual assistive-technology sessions, SMTP delivery, deployment, general live acceptance
beyond the narrow first-administrator flow, and disposable Fedora proof did not run and are not described
as passed. Docker-backed pytest fixtures and both populated migration boundaries are claimed only to their
exact gates. Known passing diagnostics are the registered Testcontainers deprecations, expected suite
skips, Node `localStorage` warnings, the Vite large-chunk advisory, and `NO_COLOR`/`FORCE_COLOR` warnings
from the synthetic browser job.

## CI topology

The frontmatter records the current workflow topology snapshot. The workflow files themselves are
executable truth. Contributor workflow and evidence expectations live in [`../AGENTS.md`](../AGENTS.md)
and [`dev-workflow.md`](dev-workflow.md).

The dependency-light `contracts` job runs repository-authority, site-data, doctor, disabled
PostgreSQL MCP, CI-hardening and Compose-image-lock guards before dependency hydration. These
checks prove tracked interfaces and failure propagation; they do not emulate a developer host or
live application stack. The retired Fedora bootstrap/proof is not part of the current pipeline.

`.github/workflows/ci.yml` defines **13** jobs (R86). Pull requests run on the **merge commit** of
the head onto main, and the `main` ruleset requires the branch to be up to date and squash-only
merging, so the PR's final check run on its head SHA tests the exact tree main receives and **it is
the merge evidence**; plain branch pushes run nothing. `gate` is the single required status check:
it needs every job and fails if any job failed, was cancelled, or was skipped while its own condition
said it should run, so a path-conditional suite is never listed as a required check of its own. The
`changes` job decides which suites a run owes from the path lists in `.github/ci-paths.yml` (`code`,
`api_suites`, `web_suites`), and every conditional job keys off its outputs. In a pull request,
`integration-shards` and `contract-responses` run when API-side inputs differ from main,
`web-shards` and `web-browser` when `apps/web` or the contract differs, and `api`, `security` and
`migrations` when anything outside `docs/**` and root `*.md` differs; editing `.github/**` runs every
suite. A docs-only PR runs the guards (`contracts`, `compose-images-lock`) plus `docs-tests`, the unit
tests that read documentation (selected by content). The workflow defines **13** jobs. A manually
started run on main executes **15** instances (`integration-shards` expands to four, `web-shards` to
two, and both `docs-tests` — the run is not docs-only — and tag-only `release-gate` are skipped); a
`v*` tag run adds the release gate for **16**. A pull
request runs the subset its changed paths select plus `changes`, `gate` and the unconditional guards;
a post-merge push to main runs `changes`, `gate`, `contracts`, `compose-images-lock`, `migrations` and
`security`. A newer commit cancels an in-flight run of the same pull request (`concurrency` keyed on
the PR ref); main, tag and manual runs are never auto-cancelled. There is no scheduled pipeline:
Dependabot opens its own pull requests, which run the same gate. `web-browser` runs the Chromium suite
and retains failure diagnostics as an artifact for seven days.

The `security` job must finish successfully. It enforces the npm policy and blocks HIGH/CRITICAL
vulnerabilities with a reported fixed version in either built application image. No-fix image
findings remain OPEN; filesystem and pip-audit findings retain their advisory treatment. Scanner
and report-processing failures fail the job. A green pipeline does not establish that the shipped
images are free of high/critical findings or close the wider security residual.

## Program 0 acceptance status

- PostgreSQL MCP: **disabled** after the reviewed package failed the high-severity advisory gate. No
  connector, launcher, package lock, owner-database port overlay, or orphan database role ships. The sole
  re-enablement contract is [`RES-POSTGRES-MCP-REPLACEMENT`](open-residuals.md#res-postgres-mcp-replacement).
- Disposable Fedora Workstation proof: **RETIRED, never run**. It was `PENDING` from 2026-08-09 to its
  retirement and no media checksum, evidence commit, or PASS was ever recorded, so retiring it discards
  no evidence. R71 removed the Fedora developer path it existed to gate; `./scripts/doctor.sh` is now
  the host contract and CI is the acceptance evidence.
- Local focused Python acceptance: **PASS on 2026-08-08 with CPython 3.12.13**. The Program 0
  dependency-tooling, deployment-configuration, and CI-workflow matrix completed 80 tests. This does not
  replace the full repository suites or live deployment acceptance.

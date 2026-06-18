# Gaps, Contradictions, Risks & Open Questions — Completeness & Consistency Audit

This document is an adversarial completeness-and-consistency review of the EasySynQ specification (files `01`–`15`). It is organized as the brief requested: **(A) Gaps**, **(B) Contradictions / Inconsistencies**, **(C) Risks & Hard Problems**, and **(D) Open Questions (each with a recommended default)**. Items are prioritized within each section (P1 = must-fix before build, P2 = should-fix, P3 = nice-to-have / watch-item). Every finding cites the section file(s) it concerns.

The spec is, overall, unusually coherent and self-cross-referencing (the data model `14` even includes an explicit reconciliation table, §14, that pre-empts several conflicts). This audit therefore concentrates on the residual holes, the places the reconciliation did *not* reach, and the hard real-world problems the prose asserts as "solved" but that carry genuine engineering risk.

---

## A. GAPS — required ISO 9001:2015 capabilities or obvious product needs not yet specified

### A1 (P1) — No "controlled folder / scope-path" entity, yet FOLDER scope is load-bearing everywhere
`07-authorization-model.md` §5.1 makes `FOLDER` one of the five scope levels, and roles like Author are scoped to `FOLDER=/SOPs/Purchasing`. But `14-data-model.md` §14 R6 explicitly resolves that "folder is a **scope selector** (a logical path on documents), not a first-class storage folder." **Nothing in the spec defines where that logical path comes from, how it is assigned to a document, how it is edited, or how the subtree-inheritance in `07` §5.3 is computed.** A document's `documented_information` row (`14` §5.1) has no `folder_path` column. Folder-scoped permissions cannot be evaluated against data that does not exist. Either add a `folder_path`/`folder_id` attribute (and a UI to manage it) or remove FOLDER as a scope level. This is a direct data-model-vs-permission-model gap.

### A2 (P1) — Quality Manual / document hierarchy levels ("Level-1/2/3") are referenced but never modeled
`07` §9 and §10 repeatedly use a doc-class scheme "Level-1 Policy, Level-2 Procedure, Level-3 Work Instruction" and Ken approves "DOC_CLASS = Level-2." `10` routes by `document_class`. But `14` §5.2 `document.document_type` is a flat enum with **no level attribute**, and DOC_CLASS scope in `07` §5.1 is defined as "`kind` + `type`," not a level. The "Level-N" tiering is used as a permission scope and a routing key but has no field. Add an explicit `document_level` (or define DOC_CLASS precisely as a derived mapping from `document_type`).

### A3 (P1) — Effective-date timezone / "what counts as now" for scheduled cutover is underspecified for a global org
`04` §4.5 and `05` §2.4–2.5 schedule releases by `effective_from` and cut over via a Beat sweep. `05` R-A4 says the server clock (UTC) is authoritative, but `08` §6 stores a per-org display timezone and "cron interpretation." A document scheduled "effective 2026-07-01" almost always means *local* midnight, not UTC. The spec never states whether `effective_from` is a `date` interpreted in org tz or a UTC `timestamptz`. `14` §5.3 types it `timestamptz`. This will produce off-by-one-day effectivity (and audit-defensibility) bugs. Specify the conversion rule explicitly.

### A4 (P2) — No specification of the "review-only" / metadata-only signature and its ISO/Part-11 meaning set
`04` §9.2 and `05` §9.3 allow a periodic review to conclude "no change needed," producing a `review_event` / "candidate `signature_event`." `04` §3.6 introduces "metadata-only revisions." But the `signature_event.meaning` enum (`14` §8, reconciled to `review|approval|release|obsolete|verify|disposition|import_baseline`) does **not** include a `review_confirmed` meaning, while `10` §4.1 emits `signature_event(meaning=REVIEW_CONFIRMED)`. The "reconsidered but unchanged" evidence (an ISO 7.5 expectation) has no canonical home in the enum. Add `review_confirmed` to the meaning enum and `14` §8.

### A5 (P2) — Outsourced-process control (ISO 9001 §8.4 + §4.4) is only partially modeled
`02` covers Suppliers/External Providers (8.4) as `Supplier` + `SupplierEvaluation`. But ISO 9001 §8.4.1 explicitly requires control of *outsourced processes*, not merely purchased product/services. The Process Map (`02` §5.3) has no notion of an outsourced/external process node, and there is no link between a `process` and the `supplier` that performs it. For many real QMSs this is a graded nonconformity area. Add an `is_outsourced` flag + supplier link on `process`.

### A6 (P2) — Customer communication & customer-complaint intake (§8.2.1, feeds 10.2) has no entity or flow
`02` models Customer Requirements review (8.2.3) and Customer Satisfaction (9.1.2), and `10` §6.1 lists "complaint" as a CAPA `source`. But there is **no complaint/feedback intake entity** and no flow from a customer complaint to an NCR/CAPA — the `source=complaint` value points at nothing. Customer complaints are a primary improvement trigger in ISO 9001. Add a lightweight complaint capture (a Record type) that can spawn an NCR/CAPA, or state explicitly that complaints are captured as `EVIDENCE`/`NCR` manually.

### A7 (P2) — Risk register has no methodology / scoring fields, yet it "feeds the Check dashboard"
`02` §6.1 and `14` §6 model `risk_opportunity` with `description`, `treatment`, `effectiveness` — but no likelihood/severity/risk-rating fields. Yet `10` §2.3/§2.5 routes workflows on `subject.risk_rating`, and `13` §5.1 surfaces "open high-risk register items." `risk_rating` is consumed but never stored or derived. Add risk-scoring attributes (or define `risk_rating` precisely).

### A8 (P2) — No "preview/rendering of arbitrary source formats" fallback policy
The render pipeline (`03` §6.1, `04` §5.1) normalizes Office→PDF via Gotenberg/LibreOffice. The spec never states what happens for formats LibreOffice cannot render (CAD/DWG, MS Project, proprietary engineering formats, very large spreadsheets, video). For an engineering/manufacturing QMS (the IATF 16949 extension target) this is common. Define a graceful "no preview available; download source" path and confirm such documents are still controllable (versioned, watermarked-on-download is impossible without a rendition — see C4).

### A9 (P2) — Acknowledgement obligations on personnel change are unspecified
`04` §8 pins acknowledgements to a `document_version_id` and re-triggers on re-release. But there is no rule for **new joiners / role changes**: when Sam joins Production, must he acknowledge the *already-effective* current revisions of his area's docs? Distribution targets resolve dynamically (`04` §8.1), but acknowledgement tasks are only created "on release/re-release." A new hire would never get acknowledgement tasks for existing docs. Specify "on entry into a distribution target, create acknowledgement tasks for current effective versions."

### A10 (P2) — Calibration / measuring-resource "out-of-tolerance impact" loop is missing
`02`/`06` model calibration records and `next_due`. ISO 9001 §7.1.5.2 requires that when measuring equipment is found unfit, the org assess the validity of *previous results*. There is no link from a failed calibration (`result=fail`) to the records/releases that depended on that instrument, and no triggered review. Add a "fail → impact assessment / NCR" trigger, or note it as out of scope.

### A11 (P3) — No nonconforming-output *disposition* states (use-as-is, rework, scrap, concession)
`02`/`08.7` models `Nonconformity` (product/output type) feeding CAPA, but ISO §8.7 requires recording the *disposition* of nonconforming output (correction, segregation, return, concession, customer authorization). The `ncr` entity (`14` §9) has no disposition field. Add disposition typing to `ncr`.

### A12 (P3) — No explicit "QMS scope change → re-evaluate exclusions / IA visibility" flow
`02` §6.1 and `08` §12 let exclusions (e.g., Design 8.3) hide IA sections. When the Scope Statement is revised to remove an exclusion, the spec never says the previously-hidden IA section/entities are re-surfaced or that coverage is re-checked. Define the scope-revision side effect on IA visibility.

### A13 (P3) — Email deliverability, bounce handling, and "notification failed" visibility are deferred but never owned
`10` §13 explicitly defers "email deliverability/bounce handling" to "the API/Worker docs," but no such doc exists in `01`–`15`. Since approval requests and overdue escalations ride email, silent email failure is an operational risk. Assign an owner section or specify bounce surfacing on the Health dashboard (`08` §15.6).

### A14 (P3) — No data-export / tenant-offboarding ("get all my data out") capability
Self-hosted orgs will eventually migrate or decommission. There is `easysynq backup` (`03` §9) but no documented *portable* full-content export (documents + records + audit in open formats) independent of a restore into another EasySynQ. Evidence Packs are scoped, not whole-QMS. Consider a whole-vault export.

---

## B. CONTRADICTIONS / INCONSISTENCIES between sections

### B1 (P1) — Document lifecycle has two different state machines across sections
`01` §7 (UJ-4 diagram) and the `01` glossary define the lifecycle as **`Draft → In Review → Approved → Released/Effective → Obsolete`** (5 states, no `Under Revision`, no `Superseded`). `04` §3.1 and `05` §2 add **`Under Revision`** and **`Superseded`** as first-class states. `03` §6.2 uses `UnderRevision` but omits `Superseded` from its diagram (it goes `Released → Obsolete`). `14` §14 R1 reconciles to seven canonical tokens (`Draft, InReview, Approved, Effective, UnderRevision, Superseded, Obsolete`). The reconciliation exists but `01`'s normative glossary and UJ diagrams were **not updated to match** — `01` is the document everything else claims to inherit "verbatim," yet it is now the outlier. Fix `01` to reference the 7-state canonical machine (or annotate that `01` shows the simplified user-facing view).

### B2 (P1) — `signature_event.meaning` enum is inconsistent and incomplete across five sections
Casing and value sets diverge: `04` §4.2 uses lowercase `approval|review|release|obsolete`; `10` (entities + YAML) uses uppercase `APPROVE|REJECT|RELEASE|VERIFY|DISPOSITION` and `REVIEW_CONFIRMED`; `12` §11.2 uses `authored|reviewed|approved|released|responsibility`; `09` uses `IMPORT_BASELINE`; `06` references `verify`. `14` §14 R2 reconciles to lowercase `review|approval|release|obsolete|verify|disposition|import_baseline` — but this set **omits** `authored`/`responsibility` (from `12` §11.2's Part-11 table) and `review_confirmed` (from `10` §4.1 and the periodic-review flow). The reconciled enum is itself incomplete. Converge `04`, `10`, `12` on one enum and ensure it contains every meaning actually emitted (see A4).

### B3 (P1) — The AuthZ resolution algorithm differs materially between `07` and `12`
`07` §6.2 specifies **"deny-wins, and specificity is NOT used to rescue an ALLOW"** — a narrow DENY beats a broad ALLOW, and specificity only breaks ALLOW-vs-ALLOW ties. `12` §3.2 specifies **"most specific scope wins; explicit DENY override beats ALLOW; per-user override beats role-derived"** as an ordered precedence ladder where "most specific scope wins" is listed first. These are not the same rule: under `12` a *more specific ALLOW* could be read as beating a *less specific DENY*, which `07` explicitly forbids. The PDP is one component; it cannot implement two precedence rules. Pick one (recommend `07`'s deny-wins-regardless) and make `12` cite it verbatim.

### B4 (P1) — First-run wizard step count and ordering disagree (`08` vs `11`)
`08` §3 specifies a **10-step** wizard (Step 0 Bootstrap … Step 10 Finalize) with backup+restore-test as a *blocking* gate (G-C) before authentication. `11` §5.8 depicts an **8-step** wizard (Welcome/License → Admin → Org/realm → Storage → Backup → Identity → Import → Review) with **no separate bootstrap step, no restore-test gate, and a different order** (license appears in `11` but not in `08`'s blocking steps; `08` puts org profile before storage, `11` puts realm before storage). The UI spec and the setup spec describe different wizards. Reconcile to one canonical step list (recommend `08`'s, as it is the authoritative setup section, and update `11`'s wireframe).

### B5 (P2) — DCR is classified as both a Record and "a small workflow," with two different lifecycles
`05` §5 and `14` §7 model the DCR as **a Record (immutable once closed)** with states `Open → Assessed → Routed → InApproval → Approved → Implemented → Closed/Cancelled`. `10` §3.1 calls the DCR "a small workflow" with states `Raised → Triage → Accepted/Rejected → Linked`. The state names and count differ entirely. Also: if a DCR is a `Record` (immutable, `kind=RECORD`), how does its `state` mutate through 8 states? That conflicts with the records-are-immutable invariant unless DCR uses the CAPA-style append-only stage-block pattern — which is never stated for DCR. Reconcile the DCR lifecycle and clarify its mutability model.

### B6 (P2) — `record.retire` permission vs the no-delete-on-records principle and disposition naming
`07` §3.2 names a permission `record.retire` ("move a Record to disposition") and §3.10 lists `record … retire`. But `06` §5.3 / `14` §5.5 model disposition as `disposition_state` advancing to `DISPOSED` via a `disposition_event`, and `07` §3.2's own note says records have "no edit and no delete." The verb "retire" appears nowhere in `06`'s disposition vocabulary; `08` §15.2 separately uses "retire" for *users*. Align the permission name with the disposition mechanism (e.g., `record.dispose`) to avoid an ambiguous catalog.

### B7 (P2) — Audit-program "Audit" state machine differs between `10` and `14`
`10` §5.1 audit states: `Scheduled → Planned → InProgress → FindingsDraft → Reported → Closing → Closed`. `14` §9 `audit.state` enum: `Scheduled, Planned, InProgress, FindingsDraft, Reported, Closing, Closed` — these match. **However** `11` §5.6 shows audit statuses as `done/in-prog/planned` (calendar strip) and the finding/CAPA gate is described slightly differently. Minor, but confirm the calendar-strip statuses map onto the canonical 7 states.

### B8 (P2) — "Permission role" naming drift: `document.author` vs `document.create`/`document.edit`
`08` §10.1 (seeded bundles) and `08` §1.2 use the capability name **`document.author`** (and `record.create`, `capa.own`, `audit_qms.conduct`, `audit_qms.*`). The authoritative permission catalog in `07` §3 has **no `document.author`** (it is `document.create` + `document.edit` + `document.submit`), **no `capa.own`** (it is `capa.create/...`), and **no `audit_qms.*`** namespace (it is `audit.*`). `08` invents capability names that don't exist in the catalog it claims to defer to. Rewrite `08`'s bundle tables to use `07`'s exact keys.

### B9 (P2) — Import permission names are inconsistent across three sections
`08` §1.2/§13 uses `import.initiate`; `09` §1.3/§15 uses `import.execute`, `import.review`, `import.commit`; `07` §2.1/§3.9 uses `import.administer`. Three different names for the import permission family. `14` §3 doesn't list import permissions explicitly. Converge (recommend `09`'s three-verb model: `import.execute/review/commit`) and add them to the `07` catalog and `14` seed.

### B10 (P2) — "Mara can grant permissions within QMS scope" contradicts the Admin-owns-permissions boundary
`07` §4.2 gives the QMS Owner bundle "`permission.grant` *within QMS scope* (delegated admin of QMS perms)." But `07` §3.9 places `permission.grant`/`permission.revoke` in **System administration (Avery's domain)** scoped to `SYSTEM`, and `08` repeatedly insists permission machinery is "squarely Avery's job and squarely outside the QMS" (`08` §10). Whether a non-admin QMS Owner can grant permissions at all is genuinely contradictory between `07` and `08`. Decide and state once whether `permission.grant` is scopable below SYSTEM.

### B11 (P3) — Two spellings/locations for the user-task inbox: "My Tasks" vs "My Actions"
`01` §6, `02` §5.2, `11` §2.1/§5.1 and `13` use **"My Tasks."** `10` §8 names it **"My Actions"** (and `14` §11 R8 says "My-Actions is a query/view"). Same surface, two names in the canonical nav. Pick one label (recommend "My Tasks," the older/more-used term) and use it everywhere.

### B12 (P3) — Lock TTL default disagrees (8h vs 24h)
`04` §5.2 and `05` §4.2 set the check-out lock TTL default to **8h**. `11` §5.4 wireframe says "auto-expires in **24h**." Reconcile the default.

### B13 (P3) — Search keyboard shortcut disagrees (`⌘K` vs `/`)
`11` §2.1/§2.4 uses **`⌘K / Ctrl-K`** for global search. `13` §2.1 says the global search bar shortcut is **`/`**. Pick one (or assign both, but state it).

### B14 (P3) — `01` UJ-3 says new docs are created "from a controlled template" but new-doc-from-blank is also allowed
`01` UJ-3 step 1 and `02` imply new documents come from controlled templates; `15` §4.1 `POST /documents` says "create a new document (from template/blank)" and `07` `document.create` says "from template/blank." Minor, but the "template-or-blank" choice and whether a Form/Template must pre-exist should be stated in `04`.

### B15 (P3) — Records "pin source version" is mandatory in `06` but `04`/`05` allow `EVIDENCE`/ad-hoc records with null source
`06` §3 says `source_version_id` is "null only for `EVIDENCE`/ad-hoc," which is fine — but `06` §1.3 invariant 2 states "**Every** Record persists `source_document_id` **and** `source_version_id`." The invariant as written is contradicted by the same section's own field table. Soften the invariant to "every Record that is produced under a controlled document pins its source version."

---

## C. RISKS & HARD PROBLEMS (the engineering reality behind the prose)

### C1 (P1) — Classification accuracy in the ingestion engine is overstated as a near-solved problem
`09` §6 presents a rules+heuristics weighted scorer with confidence bands and claims High-confidence bulk-accept drives M8 (<1 working day). Real-world risk: **filename/header/folder signals are exactly the noisy, inconsistent inputs the product exists to fix** (P1/P2). A messy share is precisely where doc-codes are absent, "FINAL" markers lie, and folder structure is meaningless (`09` IA2 even concedes structure is "never trusted"). The realistic auto-classify hit-rate on a genuinely chaotic share may be far below the implied ~70-80%, pushing far more items into Mara's "Needs Decision" queue and blowing the M8 target. The spec should (a) state a *measured* expected accuracy band and how it will be validated, (b) ensure the review UI scales to thousands of low-confidence items without becoming the multi-week scramble it replaces, and (c) consider that "Document vs Record" (kind) misclassification is the most damaging error (a Record wrongly made a versioned Document, or vice-versa) and may warrant always-confirm regardless of confidence.

### C2 (P1) — Drift detection on the on-disk mirror: the "vault wins, auto-overwrite" rule can mask, not surface, real signals; and read-only mounting is environment-fragile
`04` §10.4 and `05` §9.2 (D2/D3) re-hash the mirror and **automatically overwrite divergent files from the vault**, logging an alarm. Risks: (1) If the mirror is mounted read-only to *users* (`08` §7.1) but writable by the worker, an attacker/insider with host access can still write between scans; the window equals the scan interval. (2) Auto-correcting *before* anyone investigates can destroy the forensic artifact (the tampered bytes) — you log "tamper detected" but overwrite the evidence. (3) On some host/OS/permission setups, "read-only to users, writable to worker" is hard to guarantee (NFS, SMB, container UID mapping); a misconfig silently makes the whole drift-prevention claim hollow. (4) Detection only covers files *in* the mirror — a user who copies a mirror PDF elsewhere is undetectable except via the QR verify-token (`05` §6.4), which depends on someone choosing to scan it. Specify scan cadence vs. the accepted drift window, whether tampered bytes are quarantined before overwrite, and the exact mount/permission contract.

### C3 (P1) — Migrating messy real-world QMS folders: version-family reconstruction is a guess that can manufacture a false revision history
`09` §7 reconstructs version chains by stripping markers (`_v2`, `revB`, `FINAL`, dates) and ordering by marker+mtime, then offers "import as historical versions" with the newest as Effective. Hard problems: mtime is unreliable (copies reset it; shares re-stamp it); "FINAL2" vs "FINAL_revB" ordering is genuinely ambiguous; and a *wrong* reconstructed chain becomes **immutable, baseline-signed history** (`09` §10.2 `IMPORT_BASELINE`) that an auditor will later read as fact. Once committed to WORM there is no delete (`09` §11.4) — only obsolete. The blast radius of a bad import is permanent. Recommend: default to "import current only, archive the rest" (the safer `09` §7.3(b) option) and make reconstructed chains opt-in per family with an explicit Mara confirmation; capture the reconstruction as *provenance*, not as approved revision history.

### C4 (P1) — Full-text search & rendering performance/cost on large binaries and scanned PDFs
`03` §7 targets ≤1M docs/versions and search P95 ≤800ms; `09`/`06` OCR scanned PDFs with Tesseract. Hard problems: (1) OCR is CPU-heavy and slow; a large historical import of scanned records can saturate the single host for days (acknowledged in `09` §14 but the M8 "<1 day" target ignores OCR-heavy corpora). (2) Indexing extracted text of huge engineering drawings / 1000-page manuals into OpenSearch on an 8GB (S/M) host risks heap pressure; the S-profile *disables* OpenSearch entirely (`03` §7) — so on the smallest installs, full-text search is Postgres-FTS-only, which `13` §1.3 concedes is degraded. The spec should quantify expected index size per 1M docs, OCR throughput, and whether the M-profile's single OpenSearch node can actually hold the L-profile's 1M-doc corpus within 800ms. (3) Watermarking/stamping every download/print server-side (`04` §11.2) is a per-request render cost that can dominate under load and is not in the performance budget.

### C5 (P1) — Concurrent editing model has a real gap: external edits are uncontrolled by construction
The check-out/check-in + Redis lock model (`04` §5, `05` §4) guarantees single-writer *inside* the vault. But editing happens in the user's *native tool* on a downloaded working copy (N4/N5). Risks: (1) Two users can both hold the *file* even if only one holds the *lock* if the lock is broken/expired and the displaced user still has their local copy — on check-in the late one is rejected (good) but their work is lost (`05` §4.2 says abandoned-lock edits are "discarded"). There is no merge. (2) Lock TTL expiry (8/24h) mid-edit silently invalidates a long edit. (3) `document.break_lock` (Mara) can yank a lock from an actively-editing author; the displaced author's check-in then fails. For long, complex procedure edits this is a real UX/data-loss hazard. Specify: working-copy reconciliation on lock loss (offer the orphaned working draft as recoverable scratch, which `04` §5.2 hints at but `05` §4.2 contradicts by "discarded"), and a warning before break-lock.

### C6 (P1) — Backup/restore integrity: the DB↔blob "consistency point" via a brief quiesce is hand-waved
`03` §9 and `12` §8.2 align the PG dump and MinIO manifest via a "brief consistency lock / quiesce." Hard problems: (1) A logical `pg_dump` of a busy DB is not instantaneous; "brief" quiesce that actually blocks writes long enough to get a consistent DB+blob pair may be minutes on the L profile, conflicting with 99.5% availability. (2) WORM/object-lock on MinIO means a *restore* cannot overwrite existing locked objects — restoring into a populated bucket, or re-restoring, can fail or require a fresh bucket; the restore flow (`12` §8.2) doesn't address restoring *over* WORM objects. (3) `mc mirror` incremental of WORM blobs is safe for additions but the spec never addresses restoring to a *point in time* where some blobs existed and others didn't (PITR on PG to T, but MinIO mirror is "latest") — DB↔blob skew after PITR is possible. (4) The audit hash-chain verify-on-restore (`12` §8.2) is good, but if the restore is a PITR to mid-chain, the bundled checkpoint may be *ahead* of the restored DB, failing verification spuriously. Specify the WORM-aware restore procedure and PITR↔blob alignment.

### C6b (P2) — WORM object-lock retention vs. legitimate deletion (GDPR erasure of a record's PII, mis-imports)
`06` §5 and `12` §9 set MinIO object-lock ≥ retention period. But: (1) GDPR erasure of *user* PII is reconciled (`12` §9.4) by anonymizing the profile, not the blob — yet a *record blob itself* can contain PII (a signed training sheet with an employee's name/signature image). WORM blocks editing/removing it for years. The spec asserts records are excluded from erasure "where lawful," but doesn't address records whose *content* is PII and whose retention exceeds employment. (2) A mis-imported document committed to WORM (C3) can only be obsoleted, never removed, even if it contained the wrong company's confidential data. Acknowledge these as accepted constraints and document the legal posture, or provide a tightly-controlled, dual-control, audited "destroy under legal order" escape hatch.

### C7 (P2) — "Tamper-evident, not tamper-proof" honesty is good, but the signing key lives on the same host
`12` §4.3/§6.3 sign audit checkpoints with an app private key from a Docker secret on the same host that a privileged DBA/root controls. A root-level adversary who can rewrite history can also re-sign new checkpoints with the same key. The "copied to a write-once/external location" mitigation is **optional** (`12` §4.3 "may be copied"). Without a mandatory off-host/append-only anchor, the tamper-evidence guarantee collapses against the very adversary (`12` §10.1 "privileged operator/DBA") it is meant to expose. Recommend making at least one off-host or append-only checkpoint sink **required** for any install claiming tamper-evidence, or temper the claim.

### C8 (P2) — The audit row written "in the same transaction" as the action conflicts with at-least-once side effects and the hash chain under concurrency
`12` §4.4 / `10` §2.6 write the audit_event in the same txn as the state change, and the chain is per-org ordered by `bigint identity`. Hard problem: under concurrent writers, two transactions each computing `row_hash` from `prev_hash = previous row_hash` will **serialize on the chain tail** — every audited write must read the latest row and chain off it, forcing global serialization of all audited mutations per org (a throughput bottleneck and a deadlock/retry source). The spec asserts both "append-only hash chain" and "every mutating call audits in-txn" without addressing that the chain makes audited writes effectively single-threaded per org. Specify how chaining is serialized (e.g., a dedicated advisory lock / sequence + async chain-linking) and the performance implication.

### C9 (P2) — Evidence-pack scope-filtering "silently excludes" items can hide gaps from the auditor and the QM
`06` §7.4 says an over-broad pack "silently excludes items the user can't access (and notes the exclusion count)." Combined with the "gap report" (`06` §7.3) this is mostly good, but: if Mara generates a pack and items are excluded because *her own* scope is narrower than the audit scope, the auditor receives an incomplete pack that *looks* complete except for a count. For external audits this is a credibility risk. Specify that pack generation warns the *generator* prominently when exclusions occur and records which items were excluded and why (permission vs. genuine absence), distinct from the compliance-gap report.

### C10 (P2) — Single-host availability target (99.5%) vs. the number of stateful single-instance dependencies
`03` §5.1 runs PG (1 primary), MinIO (1), Keycloak (1), Redis (1), OpenSearch (1), Beat (exactly 1) on one host. Any one going down breaks core flows (Keycloak down = no login at all; Beat down = no scheduled cutover/retention/escalation). 99.5%/month ≈ 3.6h downtime budget; a single host + upgrade cycles (`03` §12 enforces backup-before-upgrade, implying downtime) will struggle to hold this. The graceful-degradation story (`03` §11) covers search/renderer but **not** Keycloak or Beat. Either lower the availability target for v1, or specify degradation/HA for the hard dependencies (esp. auth).

### C11 (P3) — Notification/escalation correctness depends on accurate org-hours, OOO, and manager graph that aren't modeled
`10` §4.2/§9.5 escalate to "owner's manager" and `10` §2.3 resolves assignees via `subject.author.manager` / `manager_of`. There is **no manager/reporting-line attribute** on `app_user` (`14` §3) and no org-hierarchy entity. "Business days" SLAs (`10` §6.2) need an org working-calendar/holidays, also unmodeled. Add a manager reference and a working-calendar, or restrict escalation targets to the QM/OrgRole.

### C12 (P3) — `is_singleton` enforcement (Quality Policy, Scope) vs. import and multi-site
`04` §7.2 and `14` §5.2 make Quality Policy / Scope singletons ("vault refuses a second"). But `09` import could legitimately encounter a draft + an old policy; and multi-site orgs (allowed by `08` §12 "sites/locations") sometimes maintain site-level scope statements. Confirm the singleton rule survives import dedup and multi-site, or relax to "one Effective at a time" rather than "one instance ever."

---

## D. OPEN QUESTIONS for the stakeholder (each with a recommended default)

### D1 — Is `effective_from` a date interpreted in the org's local timezone, or a UTC instant? (ties to A3)
**Recommended default:** Store `effective_from` as a `timestamptz`, but in the UI capture it as a *date* and interpret it as **local-midnight in the org timezone**, converting to UTC at save. Display effectivity in org tz everywhere; audit-store UTC. This matches user intent ("effective on July 1") and keeps the clock authoritative.

### D2 — Does FOLDER scope survive as a first-class concept, and if so where does the path live? (ties to A1)
**Recommended default:** **Keep FOLDER scope** (it is genuinely useful for "Priya authors /SOPs/Purchasing"), and add a nullable `folder_path` (materialized ltree) attribute on `document` managed via metadata, with subtree-prefix matching for scope evaluation. If the team wants to cut scope, drop FOLDER and fold its use-cases into PROCESS + DOC_CLASS scopes.

### D3 — On break-lock or lock expiry mid-edit, is the in-progress working copy preserved or discarded?
**Recommended default:** **Preserve** the working draft as recoverable scratch (resolve the `04` §5.2 vs `05` §4.2 contradiction in favor of preservation). On lock loss, the displaced editor can still check in *as a new draft* if no successor was released; if a successor exists, offer their work as a starting point for a fresh revision. Never silently discard authored content.

### D4 — Should "Document vs Record" (kind) classification always require human confirmation regardless of confidence?
**Recommended default:** **Yes.** Kind is the most consequential and least reversible classification (it determines mutability and lifecycle, and is WORM-committed). Auto-accept type/clause/process/PDCA at high confidence, but require an explicit Mara confirmation of *kind* for every item (cheap to confirm in bulk, catastrophic to get wrong).

### D5 — Default version-family handling on import: reconstruct history, or import-current-only? (ties to C3)
**Recommended default:** **Import current-only + archive the rest** by default; reconstruction of a revision chain is opt-in per family with explicit confirmation. Rationale: a wrong reconstructed chain becomes permanent WORM "fact"; safer to start clean and let true history live in the org's source archive, referenced via import provenance.

### D6 — Is an off-host / append-only audit-checkpoint anchor mandatory or optional? (ties to C7)
**Recommended default:** **Mandatory for any deployment that markets tamper-evidence / aims at Part 11 readiness**; optional (with a clear UI warning that tamper-evidence is degraded) for tiny pilots. Provide a simple file-based append-only sink (e.g., a separate WORM bucket or an external syslog/object store) configured in setup as a soft gate.

### D7 — Can a non-Admin (QMS Owner) hold `permission.grant`, or is permission administration strictly Avery's? (ties to B10)
**Recommended default:** **Allow scoped `permission.grant` for the QMS Owner within QMS-content permissions only** (so Mara can run her QMS day-to-day without Avery), but **never** for system permissions (`user.*`, `storage.*`, `backup.*`, `restore.*`). Update `08` to acknowledge this and keep the self-grant friction + audit (`08` §10.4) for any QMS→admin crossing.

### D8 — What is the canonical name for the task inbox and the search shortcut? (ties to B11, B13)
**Recommended default:** Task inbox = **"My Tasks"** everywhere (retire "My Actions"). Global search = **`⌘K` / `Ctrl-K`** as primary with `/` as a secondary focus shortcut. Update `10` and `13`.

### D9 — How is the audit hash-chain serialized without throttling all audited writes? (ties to C8)
**Recommended default:** Decouple the *write* from the *chain link*: write `audit_event` rows with `id` (sequence) and `before/after/reason` in the action's transaction, but compute `prev_hash`/`row_hash` via a single-threaded **chain-linker** (a Beat/worker job or a Postgres advisory-lock-guarded trigger) that runs continuously and lags by seconds. Tamper-evidence is preserved (gaps/edits still break the chain); throughput is not gated by chain-tail contention. State the (small, bounded) window during which a row is written-but-not-yet-chained.

### D10 — What availability target and HA posture for v1's hard dependencies (esp. Keycloak)? (ties to C10)
**Recommended default:** For v1 single-host, **state 99.0% (not 99.5%) including the auth dependency**, and document that Keycloak/Beat are single points of failure with a fast-restart runbook. Offer the documented K8s/HA path (`03` §7) as the only route to 99.5%+. Don't claim 99.5% on a stack with six single-instance stateful services.

### D11 — How are new joiners / role-changers brought current on acknowledgements? (ties to A9)
**Recommended default:** On a user's entry into any distribution target (role/process/folder), create acknowledgement tasks for the **current Effective version** of every doc in that target that requires acknowledgement. Surface as onboarding tasks in My Tasks; exclude already-acknowledged versions.

### D12 — Are customer complaints a first-class intake entity, or captured ad-hoc? (ties to A6)
**Recommended default:** Add a lightweight **Complaint** capture (a Record type, `record_type=COMPLAINT`) that can one-click spawn an NCR/CAPA with `source=complaint`, closing the dangling `source=complaint` reference. Low cost, high ISO value (customer feedback → 9.1.2 + 10.2 loop).

### D13 — Does the singleton rule for Quality Policy / Scope mean "one Effective" or "one instance ever"? (ties to C12)
**Recommended default:** **"Exactly one Effective at a time,"** not "one instance ever." This permits a draft successor while the current policy still governs (the normal revise flow) and survives import. Reword `04` §7.2 / `14` §5.2 accordingly.

### D14 — What is the watermark/preview policy for formats LibreOffice cannot render? (ties to A8, C4)
**Recommended default:** For non-renderable formats, store the source blob as the controlled artifact, mark "no preview available," and **gate download behind a click-through "uncontrolled when printed" notice** instead of a rendered watermark (since no rendition exists to stamp). Keep such documents fully versioned/controlled; flag them in the Document-Control dashboard as "no controlled rendition" so the QM is aware the page-borne provenance (`04` §11.3) is absent.

---

## RESOLUTION STATUS — Post-Reconciliation Audit (appended; original findings above are unchanged)

> **What this is.** A skeptical, line-by-line verification that the reconciliation pass actually edited sections `01`–`16` to satisfy the normative resolutions (R1–R37) in the **Decisions Register**. Each finding below is tagged **RESOLVED** (with the R-number and the file(s) that were actually changed and verified), **PARTIAL** (what still needs doing), or **DEFERRED** (intentionally out of v1). The original findings text in sections A–D above is **not modified** — this section annotates them.
>
> **Verification method.** I read the Decisions Register and every edited section file (`01`–`16`), confirmed the canonical tokens/enums/state-names/field-names appear character-for-character where required, and grep-scanned all section files for residual legacy spellings. The deep sections (`01`–`10`, `12`, `13`) were thoroughly back-propagated; the leakage scan was clean for `audit_qml`/`record.retire`/`import.initiate`/uppercase signature meanings across those.
>
> **Update — as of 2026-06-17:** all four PARTIAL residuals below have since been back-propagated and re-verified against the live docs. The UI doc (`11`) now carries the ten-step canonical wizard (`11` §5.8, R4) and the 8h lock TTL (`11` §5.4, R24); the `14` `task` row reads "My Tasks" (`14` §7, R23); the `15` §8.7 DCR is framed as a mutable-state workflow object (R22). Their rows are re-tagged **RESOLVED** and the headline counts updated accordingly. (The `11` §5.8 wireframe progress strip now reads "Step 4 of 10" with Step 0 rendered as a leading pre-step dot, so the earlier dot-count off-by-one is closed too.)

### Headline counts

- **RESOLVED: 41 / 41 findings** (A1–A14, B1–B15, C1–C12 incl. C6b, D1–D14). *(As of 2026-06-17: the four previously-PARTIAL items — B4, B5, B11, B12 — have all been back-propagated and re-verified; see the "Update" note above.)*
- **PARTIAL: 0** — the four former stragglers (**B4** R4 wizard, **B11** R23 "My Actions", **B12** R24 lock TTL, **B5** R22 DCR) are now done; their rows are re-tagged **RESOLVED** with the verifying lines.
- **DEFERRED:** 0 blocking. (R33 whole-vault export is implemented as a *data-model entity + stubbed endpoint + v1.x roadmap line*, exactly what the resolution requires — counted RESOLVED, not deferred.)
- **Residual P1/P2 issues still needing a fix:** **none** — all four back-propagation misses below are now resolved (see the "Residual" section at the very end, kept as a struck-through record of what was fixed).

### Section A — Gaps

| Finding | Status | Resolution & files verified |
|---|---|---|
| **A1** (controlled folder / scope-path entity) | **RESOLVED (R6)** | `14` §4.1 adds `documented_information.folder_path` (`ltree`, nullable); `07` §5.1/§5.3 evaluate `FOLDER` via subtree-prefix ltree match; `04` §6.1/§6.3 add the field + metadata-UI affordance; `11` metadata editor exposes the `folder_path` picker; `15` §3.2 exposes it in the document representation. |
| **A2** (Level-1/2/3 never modeled) | **RESOLVED (R7)** | `14` §4.2 adds `document_type.document_level` enum `L1_POLICY|L2_PROCEDURE|L3_WORK_INSTRUCTION|L4_FORM`; `07` §5.1 `DOC_CLASS` matches on `document_level`; `10` routing `document_class`→`document_level`; `15` §3.2 exposes `document_level`. |
| **A3** (effective-date timezone) | **RESOLVED (R8)** | `04` §4.5/§6.1, `05` §1.3 R-A4/§2.4, `08` §6 (org tz authoritative), `14` §4.3 all state: `timestamptz` UTC, captured as local-midnight in org tz→UTC at save, displayed in org tz, server UTC authoritative for cutover. |
| **A4** (review-only / `review_confirmed`) | **RESOLVED (R2)** | `review_confirmed` added to the canonical enum in `04` §4.2/§9.2, `05` §1.3, `10` §4.1, `12` §9, `14` §4.4; emitted only by a periodic review that concludes no change. |
| **A5** (outsourced-process control) | **RESOLVED (R17)** | `14` §7.1 adds `process.is_outsourced` + `outsourced_supplier_id`; `02` §5.3 renders the external/outsourced node linked to the supplier (8.4.1 / 4.4). |
| **A6** (customer-complaint intake) | **RESOLVED (R16)** | `record_type=COMPLAINT` with `customer/received_at/channel/description/severity` + one-click spawn `source=complaint`: `02` §2.1(8.2.1)/§6.1, `06` §2 catalog, `10` §6.3, `14` §6.3, `15` §4.2. Dangling `source=complaint` closed. |
| **A7** (risk scoring fields) | **RESOLVED (R18)** | `14` §7.2 adds `likelihood/severity/risk_rating/scoring_method` to `risk_opportunity`; `02` §6.1, `10` §6.4 (routing on `subject.risk_rating`), `13` §5.2 (high-risk dashboard) resolve against the real stored field. |
| **A8** (non-renderable source formats) | **RESOLVED (R26)** | `04` §11.4 stores source blob, marks "no preview available", click-through "uncontrolled when printed" notice, keeps fully versioned, flags "no controlled rendition"; `11` shows the flag; `13` §5.3 surfaces it on the Document-Control dashboard. |
| **A9** (new-joiner acknowledgements) | **RESOLVED (R15)** | `04` §8.3 creates ack tasks for the current Effective version on target entry, excludes already-acknowledged; `14` §13 `acknowledgement.created_reason=target_entry`; surfaced in My Tasks (`10`). |
| **A10** (calibration-failure impact loop) | **RESOLVED (R19)** | `06` §2 note + `10` §6.2 + `14` §10 (`result=fail` → impact-assessment task / candidate NCR with `source=calibration_fail`, 7.1.5.2). |
| **A11** (NCR disposition states) | **RESOLVED (R20)** | `14` §6.2 `ncr.disposition` enum `use_as_is|rework|scrap|return|concession|regrade` + `disposition_authorized_by`; `06` §5.4 documents them (8.7). |
| **A12** (scope-change → re-evaluate exclusions) | **RESOLVED (R31)** | `02` §2(Clause 8 note)/§6.1 (Scope Statement row): revising Scope to remove an exclusion re-surfaces hidden IA sections/entities and re-runs mandatory-coverage checks. |
| **A13** (email deliverability ownership) | **RESOLVED (R32)** | `08` §15.4/§15.6 own bounce/delivery-failure on the Health dashboard + system notification; `10` §7.2 confirms ownership — not deferred to a non-existent doc. |
| **A14** (whole-vault export) | **RESOLVED (R33)** | `06` §7.5 (mention, distinct from Evidence Pack/backup), `14` §11 `vault_export` entity, `15` §7 stubbed endpoint, `16` v1.x roadmap line. |

### Section B — Contradictions / Inconsistencies

| Finding | Status | Resolution & files verified |
|---|---|---|
| **B1** (two lifecycle state machines) | **RESOLVED (R1)** | `04` §3.1 is the canonical 7-state machine; `01` UJ-4 + glossary now annotate the 5-state form as the simplified user-facing view and reference the 7-state canon; `03` §6.2, `05`, `11`, `14` §4.3 use the seven tokens verbatim. |
| **B2** (`signature_event.meaning` enum) | **RESOLVED (R2)** | Single lowercase enum `review|approval|release|obsolete|verify|disposition|import_baseline|review_confirmed` (+ reserved `authored`/`responsibility`) in `04`/`05`/`06`/`09`/`10`/`12`/`14`/`15`. No uppercase forms remain (grep-confirmed). |
| **B3** (AuthZ algorithm differs 07 vs 12) | **RESOLVED (R3)** | `07` §6.3 is canonical (deny-always-wins; specificity breaks ALLOW-vs-ALLOW ties only); `12` §3.2 is rewritten to cite it verbatim and explicitly drops "most-specific-wins-first". |
| **B4** (wizard step count/order) | **RESOLVED (R4)** *(was PARTIAL; fixed 2026-06-17)* | `08` §3 IS the canonical 10-step flow (Step 0 Bootstrap … Step 10, blocking restore-test gate G-C before auth, org-profile before storage). `11` §5.8 now matches: its "Ten-step canonical flow (reconciled per Decisions Register R4)" lists **Step 0 Bootstrap & Welcome** (consumes the install secret before any account) → 1 Admin → 2 Organization Profile → 3 Vault & Mirror Storage → 4 Backup + Restore-Test → 5 Authentication → 6 Org Roles → 7 Users → 8 QMS Scope → 9 Import → 10 Review/Finalize, with org-profile before storage. The old 8-step/license-first wireframe is gone, and the progress strip now reads "Step 4 of 10" (Step 0 drawn as a leading pre-step dot). |
| **B5** (DCR Record vs workflow) | **RESOLVED (R22)** *(was PARTIAL; fixed 2026-06-17)* | `05` §5/§5.5, `10` §3.1, and `14` §7 (`dcr` entity + `dcr_stage_event`) model the DCR as a mutable-state **workflow object, NOT `kind=RECORD`**, lifecycle Open→…→Closed (+Cancelled/Rejected). `15` §8.7 now agrees: "A `dcr` is a **controlled workflow object with a mutable `state` column** plus an **append-only stage-event history** (`dcr_stage_event`) — it is **NOT a `kind=RECORD` immutable artifact**; its closed form is retained as a record-like snapshot (`14 §7`, reconciled per Decisions Register R22)." The pre-R22 "is a Record, immutable once closed" line is gone. |
| **B6** (`record.retire` vs disposition) | **RESOLVED (R5)** | Canonical `record.dispose` in `06` §5.3, `07` §3.2, `15` §4.1; `record.retire` removed everywhere (grep-confirmed). |
| **B7** (audit state machine 10 vs 14 vs 11) | **RESOLVED (R5/B7)** | `audit.state` enum `Scheduled→Planned→InProgress→FindingsDraft→Reported→Closing→Closed` aligned in `10` §5.1 and `14` §9.1; `11` calendar-strip statuses map onto these; `audit.*` namespace (not `audit_qms.*`). |
| **B8** (`document.author` vs catalog keys) | **RESOLVED (R5)** | `08` §10.1 bundles now use the doc 07 catalog keys exactly: `document.author`→`{document.create, document.edit, document.submit}`, `capa.own`→`capa.*`, `audit_qms.*`→`audit.*`. |
| **B9** (import permission names) | **RESOLVED (R5)** | `import.execute`/`import.review`/`import.commit` in `07` §3.9/§4.2, `08` §1.2/§13, `09` §1.3; legacy `import.initiate`/`import.administer` removed (grep-confirmed). |
| **B10** (Mara grants permissions vs Admin boundary) | **RESOLVED (R35)** | Two-tier model stated identically in `07` §4.3 and `08` §10.5: QMS Owner holds `permission.grant` for CONTENT domains in QMS scope; SYSTEM domains stay admin-only at SYSTEM scope. |
| **B11** ("My Tasks" vs "My Actions") | **RESOLVED (R23)** *(was PARTIAL; fixed 2026-06-17)* | "My Tasks" is canonical and used in `01` glossary, `10` §8 (retires "My Actions" explicitly), `11`, `13`. The `14` `task` row now reads "The atom of **My Tasks** (`10 §8`) (reconciled per Decisions Register R23)" — the stray legacy label is gone. (`15` retains "My-Actions" only as an API path; the prose labels read "My Tasks".) |
| **B12** (lock TTL 8h vs 24h) | **RESOLVED (R24)** *(was PARTIAL; fixed 2026-06-17)* | `04` §5.2 and `05` §4.2 both say **8h** (canonical). `11` §5.4 now agrees: the Check-out wireframe reads "Lock holder: Priya · auto-expires in **8h** (extendable)" — the stale 24h value is gone. |
| **B13** (search shortcut) | **RESOLVED (R23)** | Cmd-K / Ctrl-K primary, `/` secondary in `11` §2.1/§2.4 and `13` §2.1. |
| **B14** (new-doc from template vs blank) | **RESOLVED (R5)** | `07` §3.1 `document.create` = "from template/blank" (canonical); `15` §3.1 `POST /documents` matches; `01` UJ-3 / `04` consistent. |
| **B15** (record source-version invariant vs EVIDENCE null) | **RESOLVED (R21)** | `06` §1.3 inv-2 softened ("produced UNDER a controlled document pins source"); `06` §3 + `14` §6.1 make `source_version_id` nullable. |

### Section C — Risks & Hard Problems

| Finding | Status | Resolution & files verified |
|---|---|---|
| **C1** (classification accuracy overstated) | **RESOLVED (R10)** | `09` §2.3.1 states a measured ~70–85% high-confidence band, validated against a labelled holdout corpus, re-measured per import (precision/recall in the coverage report); §5/§6 review UI scales to thousands; `kind` excluded from auto-accept. |
| **C2** (mirror drift auto-overwrite / mount) | **RESOLVED (R11)** | `04` §10.4/§10.6 + `05` §9.1/§9.2.1: quarantine tampered bytes BEFORE overwrite, audit the anomaly, scan-cadence-vs-drift-window stated, RO-to-users/RW-only-worker mount contract incl. NFS/SMB/container-UID caveats; detection scoped to mirror, off-mirror copies handled by verify token. |
| **C3** (version-family false history) | **RESOLVED (R10)** | `09` §2.4.1/§7: default current-only baseline + archive older as provenance; revision-chain reconstruction opt-in per family with explicit confirmation, captured as provenance (not fabricated approved history). |
| **C4** (search/render performance & cost) | **RESOLVED (R34)** | `03` §7.1 (~1 GB OpenSearch index/1M docs, 2–5 OCR pages/s/core, per-request watermark/stamp a budgeted cost, S=Postgres-FTS-only degraded mode); `13` §3 mirrors the budget. |
| **C5** (concurrent editing / lock-loss data loss) | **RESOLVED (R9)** | `04` §5.2/§5.4 + `05` §4.2: working copy PRESERVED as recoverable scratch on lock expiry/break-lock (never silently discarded); check-in-as-new-draft if no successor, else offered as a fresh-revision starting point; break-lock requires confirm warning. doc 04 vs doc 05 contradiction resolved in favor of preservation. |
| **C6** (backup/restore over WORM + PITR↔blob) | **RESOLVED (R37)** | `03` §9.1 + `12` §6: WORM-aware restore to fresh/cleared/versioned target, PITR↔blob alignment via manifest blob-snapshot id, checkpoint-not-ahead-of-PITR check, bounded quiesce reconciled with R14. |
| **C6b** (WORM vs GDPR erasure of PII content) | **RESOLVED (R27)** | `06` §5.5 + `12` §8: PII-content records stay under object-lock; dual-control, fully-audited destroy-under-legal-order escape hatch; conflicting erasure logged refused-with-reason. |
| **C7** (off-host anchor optional) | **RESOLVED (R13)** | `03` §8.5 + `08` §8.3 (soft gate + warning) + `12` §5 + `14` §8.2 (`audit_checkpoint_sink`): off-host/append-only anchor MANDATORY for any tamper-evidence claim. |
| **C8** (in-txn audit row vs hash-chain serialization) | **RESOLVED (R12)** | `12` §4.2 + `14` §8.1: write decoupled from chain-link; single-threaded chain-linker; `prev_hash`/`row_hash`/`chained_at` nullable-until-linked; bounded written-but-not-yet-chained window stated; throughput not gated by chain-tail. |
| **C9** (evidence-pack silent exclusions) | **RESOLVED (R28)** | `06` §7.3 item 7 + §7.4: prominent generator warning + exclusion report (permission vs genuine absence), distinct from the gap report; `15` §8 endpoint behavior. |
| **C10** (single-host availability vs SPOFs) | **RESOLVED (R14)** | `03` §11/§11.1 + `12` §7: 99.0% single-host incl. Keycloak+Beat SPOFs with fast-restart runbook; 99.5%+ only on HA/K8s path. |
| **C11** (escalation needs manager graph + calendar) | **RESOLVED (R29)** | `14` §3.1 `app_user.manager_id` + §3.2 `working_calendar`; `10` §9 escalation/business-day SLA resolve against them, fall back to QM/OrgRole. |
| **C12** (`is_singleton` vs import & multi-site) | **RESOLVED (R25)** | `04` §7.2(rule 4), `09` §10, `14` §12: exactly one `Effective` instance at a time (NOT one ever); draft successor may coexist; survives import dedup + multi-site. |

### Section D — Open Questions (resolved with the recommended defaults)

| Finding | Status | Resolution & files verified |
|---|---|---|
| **D1** (effective_from local-date vs UTC) | **RESOLVED (R8)** | Same as A3 — `04`/`05`/`08`/`14`/`15`. |
| **D2** (FOLDER scope first-class + path location) | **RESOLVED (R6)** | Same as A1 — `04`/`07`/`14`/`11`/`15`. |
| **D3** (break-lock preserve vs discard) | **RESOLVED (R9)** | Same as C5 — preserve, `04`/`05`/`11`. |
| **D4** (always human-confirm `kind`) | **RESOLVED (R10)** | `09` §2.3.2/§3.1/§5.2: `kind` always human-confirmed regardless of confidence. |
| **D5** (reconstruct history vs current-only) | **RESOLVED (R10)** | Same as C3 — current-only default, reconstruction opt-in (`09`). |
| **D6** (off-host anchor mandatory) | **RESOLVED (R13)** | Same as C7 — mandatory + soft-gate warning (`03`/`08`/`12`/`14`). |
| **D7** (non-Admin QMS Owner holds permission.grant) | **RESOLVED (R35)** | Same as B10 — two-tier model in `07` §4.3 + `08` §10.5. |
| **D8** (task-inbox name + search shortcut) | **RESOLVED (R23)** *(was PARTIAL; fixed 2026-06-17)* | Search shortcut RESOLVED (Cmd-K/Ctrl-K primary + `/` secondary in `11` §2.1/§2.4 and `13` §2.1). Task-inbox name now RESOLVED for the same reason as B11 — the `14` §7 `task` row reads "My Tasks". |
| **D9** (hash-chain serialization without throttling) | **RESOLVED (R12)** | Same as C8 — decoupled chain-linker (`12`/`14`). |
| **D10** (availability target & HA posture) | **RESOLVED (R14)** | Same as C10 — 99.0% incl. hard deps (`03`/`12`). |
| **D11** (new joiners / role-changers acks) | **RESOLVED (R15)** | Same as A9 — on target entry (`04`/`14`/`10`). |
| **D12** (customer complaints first-class) | **RESOLVED (R16)** | Same as A6 — `record_type=COMPLAINT` + spawn (`02`/`06`/`10`/`14`/`15`). |
| **D13** (singleton "one Effective" vs "one ever") | **RESOLVED (R25)** | Same as C12 — one Effective at a time (`04`/`09`/`14`). |
| **D14** (watermark/preview for non-renderable) | **RESOLVED (R26)** | Same as A8 — source-blob + no-preview + click-through + "no controlled rendition" flag (`04`/`11`/`13`). |

### Auditor notes & caveats (honest residuals)

- **The meta-risk in §E is now closed across all section docs (including `11`).** The original §E warned that `14` was authoritative but the source sections were never edited. That is now untrue: `01`, `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `12`, `13`, `14`, `15`, `16` carry explicit "reconciled per Decisions Register Rn" annotations with canonical tokens reproduced verbatim. ~~However the UI/UX doc `11` was essentially NOT touched by the reconciliation pass~~ **Update (2026-06-17): doc `11` has since been back-propagated too** — §5.8 now carries the ten-step canonical wizard (Step 0 Bootstrap, blocking restore-test, org-before-storage; R4) and §5.4 the 8h lock TTL (R24). A build team reading `11` in isolation will now implement the reconciled wizard and lock TTL. The earlier "single biggest residual" no longer stands.
- **R33 is intentionally a stub and is correctly delivered.** Whole-vault export = a `vault_export` reference + a stubbed `POST /admin/export` (`vault.export`) endpoint in `15` §8.17 + the v1.7 roadmap line in `16` — exactly the scope the resolution asks for. Scored RESOLVED.
- **Leakage scan was clean for the worst tokens.** No `record.retire`, no uppercase `signature_event.meaning`, no `audit_qms.*` (outside explicit normalization notes), no `import.initiate`/`import.administer` (outside the "these replace…" note in `09`). The two stray legacy labels that previously survived have since been fixed (2026-06-17): the `14` §7 task row now reads "My Tasks" and `15` §8.7 now frames the DCR as a mutable-state workflow object (NOT a Record).

### Residual P1/P2 issues — ALL RESOLVED (as of 2026-06-17)

> The four back-propagation misses below have all been fixed and re-verified against the live docs. Kept as a struck-through record of what was done.

1. ~~**(P1) `11` §5.8 — First-Run wizard wireframe is the old 8-step flow.**~~ **RESOLVED (R4).** `11` §5.8 now carries the canonical ten-step flow with **Step 0 Bootstrap & Welcome**, the **blocking backup + restore-test gate** before authentication, and **org profile before storage**; the license-first ordering is gone. (Finding B4.)
2. ~~**(P2) `15` §8.7 — DCR mislabeled as "a Record, immutable once closed".**~~ **RESOLVED (R22).** `15` §8.7 now reads "a **controlled workflow object with a mutable `state` column** plus an append-only `dcr_stage_event` history — NOT a `kind=RECORD` immutable artifact"; only its closed form is retained as a record-like snapshot. (Finding B5.)
3. ~~**(P3) `11` §5.4 — lock TTL still "24h".**~~ **RESOLVED (R24).** `11` §5.4 now reads "auto-expires in **8h** (extendable)", matching `04` §5.2 / `05` §4.2. (Finding B12.)
4. ~~**(P3) `14` §7 — `task` row still labeled "The atom of My Actions".**~~ **RESOLVED (R23).** The `14` §7 `task` row now reads "The atom of **My Tasks**". (Findings B11 / D8.)

None of the four were integrity, security, authorization, audit-chain, or data-model-correctness defects — the load-bearing resolutions (R1, R2, R3, R5, R6, R7, R8, R9, R10, R11, R12, R13, R14, R18, R20, R21, R25, R27, R35, R37) were always fully and verbatim back-propagated; these were back-propagation misses concentrated in the UI doc (`11`) plus two stray sentences (`14`, `15`), now all closed.

---

## E. Cross-cutting observation (meta)

The spec's biggest *systemic* risk is **`14` §14 (the reconciliation table) is treated as authoritative, but the source sections it reconciles were never edited to match it.** A build team reading `01`, `07`, `08`, `10`, `12` in isolation will implement the *old* names/states/algorithms and only discover the conflicts when integrating against `14`. Strongest single recommendation: **make `14`'s reconciled names/enums/algorithm the single source of truth and back-propagate them into `01`–`13` (or add a one-line "superseded by 14 §14 Rn" banner at each drift point).** Items B1, B2, B3, B4, B8, B9, B11 are all instances of this same root cause.

---

## Summary of the most important findings

1. **Lifecycle, signature-meaning enum, and AuthZ-precedence rule each have two non-identical definitions across sections** (B1, B2, B3). `14` §14 reconciles some but the source sections (`01`, `07`, `08`, `10`, `12`) were not updated, and the reconciled enum is itself incomplete (missing `review_confirmed`, `authored`, `responsibility`). These are P1 build-blockers because the lifecycle state machine, the Part-11 signature hook, and the single PDP must each be exactly one thing.
2. **FOLDER permission scope and document "Level" classes are used pervasively but have no backing data model** (A1, A2, B8) — folder-scoped and level-scoped grants cannot be evaluated against fields that don't exist.
3. **The First-run wizard is specified twice with different step counts/order and a missing restore-test gate** (B4); `08` (10 steps, blocking restore-test) and `11` (8 steps, no restore-test) must converge.
4. **The three "hard problems" the product is built to solve are the three with the most optimistic prose:** import classification accuracy on genuinely messy shares (C1), version-family reconstruction manufacturing permanent false history into WORM (C3), and mirror drift-detection's auto-overwrite eroding forensic evidence and depending on fragile read-only mount guarantees (C2).
5. **Two integrity/availability assertions don't survive scrutiny:** the per-org hash-chain serializes all audited writes (C8), and the tamper-evidence signing key sits on the same host as the adversary it's meant to expose, with the off-host anchor only "optional" (C7). Both undercut the central trust promise unless addressed.
6. **Backup/restore over WORM and across PITR is under-specified** (C6) — restoring over object-locked blobs and aligning a PG point-in-time with a "latest" MinIO mirror are unsolved in the text.
7. **Several real ISO 9001 capabilities are missing:** new-joiner acknowledgements (A9), customer-complaint intake (A6, the dangling `source=complaint`), outsourced-process control (A5), risk scoring (A7), and calibration-failure impact (A10).

All recommended defaults are in §D; the single highest-leverage action is back-propagating `14` §14's reconciled canon into sections `01`–`13`.

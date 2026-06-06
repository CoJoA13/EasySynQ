# EasySynQ Decisions Register

This document is the **single authoritative source of truth** for the EasySynQ self-hosted ISO 9001:2015 QMS specification. It records the locked foundational decisions, the locked stakeholder decisions, and the normative resolutions (R1–R39) to every finding raised in the gap audit (`17-gaps-and-open-questions.md`); R38 (slice S-rec-4) is the first post-v1 *additive* decision (additive catalog extensibility + SoD-6), and R39 (slice family S-aud/S-capa) locks the Audits/Findings/CAPA model + workflow posture.

**Precedence:** Where this register conflicts with any text in sections `01`–`15`, **this register supersedes that text.** Section editors MUST back-propagate the changes listed under each resolution's *Back-propagation* note. The exact tokens, enum values, state names, and field names quoted here are **canonical and verbatim** — they must be reproduced character-for-character (case, snake_case, dot-namespacing, and all) wherever the underlying concept appears. Do not soften, rename, abbreviate, or omit any token.

This register also exists to end divergence: the same concept currently appears under multiple spellings across sections. From here forward there is exactly one spelling per concept, defined below.

---

## Part 1 — Locked Foundational Decisions (restated, unchanged)

These four decisions are fixed and are restated here unchanged for reference. They are not reopened by this register.

### D1 — Deployment model
EasySynQ is a **self-hosted web application**.

### D2 — Storage model
The system is built around a **managed, controlled vault** as the authoritative store. The **filesystem is a read-only mirror** of the vault, never an independent source of truth.

### D3 — Standards foundation
The product is founded on **ISO 9001:2015**. It is **extensibly designed toward 21 CFR Part 11 and multi-standard** support, but those are **not built now** (declared, not implemented, in v1).

### D4 — Technology stack
- **Frontend:** React / TypeScript + Mantine + Tailwind
- **Backend:** FastAPI / Python
- **Data & infra:** PostgreSQL + MinIO + OpenSearch + Redis
- **Async:** Celery
- **Identity:** Keycloak
- **Rendering:** Gotenberg
- **Edge / TLS:** Caddy
- **Orchestration:** Docker Compose

---

## Part 2 — New Stakeholder Decisions (just locked by the product owner)

These four decisions were locked by the product owner as part of this reconcile-and-harden pass. They are normative and bind every section.

### (a) Permission-grant boundary
The **Quality-Manager / QMS-Owner MAY hold `permission.grant`** scoped to **CONTENT permission domains** — namely `document.*`, `record.*`, `audit.*`, `capa.*`, `changeRequest.*`, `evidencepack.*` — **WITHIN QMS scope**.
**SYSTEM permissions** — namely `user.*`, `role.*`, `storage.*`, `backup.*`, `restore.*`, `config.*`, `import.*` — **remain admin-only at SYSTEM scope**. (See R35 for the consistency resolution.)

### (b) Import default
Import brings in the **current/latest version only as the controlled baseline** and **archives older copies as provenance**. **Revision-chain reconstruction is opt-in per document-family with explicit confirmation.** The **Document-vs-Record (`kind`) classification is ALWAYS human-confirmed regardless of confidence.** (See R10.)

### (c) Tamper-evidence
An **off-host / append-only audit-checkpoint anchor is MANDATORY for any install claiming tamper-evidence.** (See R13.)

### (d) Scope of this pass
Proceed with the **full reconcile-and-harden pass** — i.e., adopt R1–R37 below in full.

---

## Part 3 — Resolutions R1–R39

Each resolution states the decision, the exact canonical tokens/enums/states/field-names verbatim, and a Back-propagation note listing the section files that change.

### R1 — Document lifecycle (canonical 7-state machine)

**Decision:** The document lifecycle is a **seven-state machine**. The five-state form is *only* a simplified user-facing summary; the engine, data model, and all state diagrams use the seven-state machine.

**Canonical state tokens (engine/data-model, verbatim):**
`Draft`, `InReview`, `Approved`, `Effective`, `UnderRevision`, `Superseded`, `Obsolete`

**Display labels (verbatim):**
Draft / In Review / Approved / Effective / Under Revision / Superseded / Obsolete

**Simplified user-facing summary (allowed only as a summary, not in the engine):**
Draft → In Review → Approved → Effective → Obsolete

**Authority:** Doc 04 section 3.1 is the canonical definition. Doc 01 glossary and any 5-state diagrams MUST add a note stating they show the simplified view and MUST reference the 7-state canon.

**Back-propagation:** 01, 03, 04, 05, 11, 14.

---

### R2 — `signature_event.meaning` enum (v1)

**Decision:** The `signature_event.meaning` enum is fixed (v1, lowercase snake_case). All sections use these exact lowercase values — no `APPROVE`/`RELEASE` uppercase, no divergent sets.

**Canonical enum values (v1, emitted), verbatim:**
`review`, `approval`, `release`, `obsolete`, `verify`, `disposition`, `import_baseline`, `review_confirmed`

**Reserved for the future Part-11 phase (declared but NOT emitted in v1), verbatim:**
`authored`, `responsibility`

**Note:** `review_confirmed` is emitted by a **periodic review that concludes no change needed**.

**Back-propagation:** 04, 06, 09, 10, 12, 14, 15.

---

### R3 — Authorization precedence (canonical)

**Decision:** The authorization precedence algorithm is canonical as defined in doc 07, and **deny always wins**:

1. **Deny-by-default.**
2. Gather **all grants applicable** to the `(subject, action, resource)` within matching scope.
3. If **ANY explicit DENY** applies → result is **DENY**, regardless of scope specificity (**deny always wins**).
4. Else if **any ALLOW** applies → result is **ALLOW**.
5. **Scope specificity** (more specific scope wins) is used **ONLY to break ALLOW-vs-ALLOW ties**.
6. A **per-user override outranks a role-derived grant only WITHIN the same allow/deny class** (a more specific ALLOW never overrides a less specific DENY).

**Authority:** Doc 12 section 3.2 MUST be rewritten to cite this verbatim and MUST drop any "most-specific-wins-first" phrasing.

**Back-propagation:** 07 (confirm), 12 (rewrite to match).

---

### R4 — First-run wizard

**Decision:** The doc 08 **ten-step wizard is canonical**, including the **blocking backup + restore-test gate (G-C)** before authentication.

**Required alignment:** Doc 11 section 5.8 wireframe MUST be updated to the same step list/order — add the bootstrap step and the restore-test gate (`G-C`), and align ordering so that **org profile comes before storage** per doc 08.

**Back-propagation:** 08 (confirm), 11 (update wireframe).

---

### R5 — Permission catalog (normalization)

**Decision:** Doc 07 section 3 is the **canonical permission catalog**. All variant spellings normalize onto the doc 07 keys below.

**Document permission normalizations (verbatim, left → right canonical):**
- `document.view` → `document.read`
- `document.view_drafts` → `document.read_draft`
- `document.read_effective` → `document.read`
- `document.submit_for_review` → `document.submit`
- `document.make_obsolete` → `document.obsolete`
- `document.export_controlled` → `document.export`
- `document.checkin` stays `document.checkin` (or fold into `document.edit` per 07)

**Record disposition:** the permission is `record.dispose` (**NOT** `record.retire`).

**Change Request (DCR) permission family (verbatim):**
`changeRequest.create` / `changeRequest.assess` / `changeRequest.route` / `changeRequest.approve` / `changeRequest.implement` / `changeRequest.close`
Map doc 10 `dcr.raise` → `changeRequest.create`.

**CAPA permission family (verbatim):**
`capa.create` / `capa.update` / `capa.verify` / `capa.close`
Map `capa.raise` → `capa.create`. `capa.own` is a **role concept, not a permission**.

**Internal audit:** use the `audit.*` namespace (**NOT** `audit_qms.*`). Map `audit_qms.conduct` → `audit.conduct`.

**Import permission family (verbatim) — REPLACES `import.initiate` and `import.administer` everywhere:**
- `import.execute` — run the scan/classify
- `import.review` — review/correct classifications
- `import.commit` — commit to vault

Add all three to the doc 07 catalog and the doc 14 seed.

**Grant/revoke:** `permission.grant` / `permission.revoke` are **scopable to CONTENT domains within QMS scope** for the QMS Owner per stakeholder decision (a); **system-permission granting stays SYSTEM-scope admin-only**.

**Seeded role bundles (doc 08) MUST use doc 07 keys exactly:**
- `document.author` → `{document.create, document.edit, document.submit}`
- `record.create` stays `record.create`
- `capa.own` → `capa.*`
- `audit_qms.*` → `audit.*`

**Back-propagation:** 04, 05, 07, 08, 09, 10, 14, 15.

---

### R6 — Folder scope backing field

**Decision:** Add a **nullable `folder_path` column** (PostgreSQL **`ltree`**, materialized logical path) to the **`documented_information`** entity in doc 14. It is a **scope selector**, not physical storage. Scope evaluation uses **subtree-prefix (ltree ancestor) matching**. The path is set/edited via document metadata; specify a metadata UI affordance. **FOLDER survives as a first-class scope level.**

**Canonical tokens:** field `folder_path`; type `ltree`; entity `documented_information`; scope level `FOLDER`.

**Back-propagation:** 07 (reference the field), 14 (add column), 04 (metadata mgmt), 11 (metadata UI), 15 (expose in document representation).

---

### R7 — Document level

**Decision:** Add an explicit **`document_level`** attribute on the **`document_type`** catalog in doc 14.

**Canonical values (verbatim, extensible):**
`L1_POLICY`, `L2_PROCEDURE`, `L3_WORK_INSTRUCTION`, `L4_FORM`

The **`DOC_CLASS`** authorization scope in doc 07 is defined as matching on `document_level` (and optionally `kind` + `type`). Routing keys in doc 10 (`document_class`) resolve to `document_level`.

**Back-propagation:** 07, 10, 14.

---

### R8 — Effective-date timezone

**Decision:** `effective_from` is stored as **`timestamptz` in UTC**, but **captured in the UI as a DATE interpreted as local-midnight in the org timezone and converted to UTC at save**. Effectivity is **displayed in org tz**; the **server UTC clock remains authoritative for cutover**. This conversion rule is explicit and binding.

**Canonical tokens:** field `effective_from`; type `timestamptz`; storage = UTC; capture = local-midnight in org tz → UTC at save.

**Back-propagation:** 04, 05, 08 (org tz), 14.

---

### R9 — Lock loss / break-lock

**Decision:** On **lock expiry or admin break-lock**, the in-progress working copy is **PRESERVED as recoverable scratch** (never silently discarded). This resolves the doc 04 sec 5.2 vs doc 05 sec 4.2 contradiction **in favor of preservation**.

- The displaced editor may **check in as a new draft** if **no successor was released**.
- If a **successor exists**, their work is **offered as a starting point for a fresh revision**.
- **break-lock requires a confirm warning.**

**Back-propagation:** 04, 05, 11.

---

### R10 — Import version handling

**Decision:** The import default is **current/latest-only as the controlled baseline + archive older copies as provenance** (**NOT** approved revision history). **Revision-chain reconstruction is opt-in per family with explicit Mara confirmation**, captured as **provenance metadata**. The **Document-vs-Record `kind` classification is ALWAYS human-confirmed regardless of confidence.**

**Additional requirements:**
- State a **measured expected auto-classification accuracy band** and **how it is validated**.
- The **review UI MUST scale to thousands of low-confidence items** (bulk triage).

**Back-propagation:** 09 (primary), 14 (provenance fields), 11 (review UI note).

---

### R11 — Mirror drift detection

**Decision:** On detecting a divergent mirror file, **QUARANTINE the tampered bytes** (copy to a quarantine area) **BEFORE overwriting from the vault**, so forensic evidence is preserved; **log the anomaly to the audit trail**.

**Additional requirements:**
- Specify the **scan cadence vs the accepted drift window**.
- Specify the **exact mount/permission contract**: mirror is **read-only to users, writable only by the worker**; explicitly call out **NFS/SMB/container-UID caveats**.
- Detection covers **only files within the mirror**; copies taken outside are addressed only by the **controlled-rendition verify token**.

**Back-propagation:** 04, 05.

---

### R12 — Audit hash-chain (decoupled write/link)

**Decision:** **Decouple the write from the chain link.**
- Write **`audit_event`** rows (with **id sequence, before/after, reason**) in the action transaction.
- Compute **`prev_hash` / `row_hash`** via a **single-threaded chain-linker** (a Celery/Beat worker or a Postgres advisory-lock-guarded process) running continuously with a **small bounded lag**.
- Add **`chained_at`** (`timestamptz`, nullable until linked) and make **`prev_hash` / `row_hash` nullable-until-linked** on `audit_event` in doc 14.

**Properties:** Tamper-evidence is preserved (gaps/edits still break the chain); per-org write throughput is **not gated by chain-tail contention**. **State the bounded written-but-not-yet-chained window.**

**Canonical tokens:** entity `audit_event`; fields `prev_hash`, `row_hash`, `chained_at`.

**Back-propagation:** 10, 12, 14.

---

### R13 — Off-host audit anchor

**Decision:** An off-host audit anchor is **MANDATORY for any install claiming tamper-evidence / Part-11 readiness** (stakeholder decision c). At least one **off-host or append-only checkpoint sink** (e.g., a separate **WORM bucket**, external object store, or **append-only syslog**) is required and **configured during setup as a soft gate** with a **clear UI warning if absent**. Model an **`audit_checkpoint_sink`** config entity.

**Canonical token:** config entity `audit_checkpoint_sink`.

**Back-propagation:** 03 (architecture), 08 (setup step), 12 (requirement), 14 (config entity).

---

### R14 — Availability target

**Decision:** State **99.0% per month for the single-host profile**, **INCLUDING** the auth (**Keycloak**) and scheduler (**Beat**) dependencies. Document **Keycloak and Beat as single points of failure** with a **fast-restart runbook**. **99.5%+ is achievable only via the documented HA/K8s path.** Do **not** claim 99.5% on a six-single-instance-stateful-service single host.

**Back-propagation:** 03, 12.

---

### R15 — New-joiner acknowledgements

**Decision:** On a user entering any distribution target (**role / process / folder**), create **acknowledgement tasks for the CURRENT Effective version** of every doc in that target that **requires acknowledgement**; surface as **onboarding tasks in My Tasks**; **exclude already-acknowledged versions**.

**Back-propagation:** 04, 10, 14.

---

### R16 — Customer-complaint intake

**Decision:** Add a lightweight **Complaint** capture as **`record_type=COMPLAINT`** with fields **`customer`, `received_at`, `channel`, `description`, `severity`** that can **one-click spawn an NCR/CAPA** with **`source=complaint`**, closing the dangling `source=complaint` reference.

**Canonical tokens:** `record_type=COMPLAINT`; fields `customer`, `received_at`, `channel`, `description`, `severity`; spawn `source=complaint`.

**Back-propagation:** 02 (8.2.1 flow), 06 (record type), 10 (spawn-to-CAPA), 14 (entity/type), 15 (endpoint).

---

### R17 — Outsourced-process control

**Decision:** Add **`is_outsourced`** (boolean) and **`outsourced_supplier_id`** (nullable FK to **supplier**) on the **`process`** entity in doc 14; represent an **outsourced/external process node** in the process map (doc 02) and link it to the supplier that performs it (ISO 9001 8.4.1 + 4.4).

**Canonical tokens:** fields `is_outsourced`, `outsourced_supplier_id`; entity `process`.

**Back-propagation:** 02, 14.

---

### R18 — Risk scoring

**Decision:** Add **`likelihood`**, **`severity`**, **`risk_rating`** (derived/stored), and **`scoring_method`** to the **`risk_opportunity`** entity in doc 14. Doc 10 workflow routing on **`subject.risk_rating`** and doc 13 high-risk dashboards now resolve against real fields.

**Canonical tokens:** fields `likelihood`, `severity`, `risk_rating`, `scoring_method`; entity `risk_opportunity`; routing key `subject.risk_rating`.

**Back-propagation:** 02, 10, 13, 14.

---

### R19 — Calibration-failure impact

**Decision:** When a calibration/measuring-resource record has **`result=fail`**, trigger an **impact-assessment task / candidate NCR** over the records and releases that depended on that instrument (ISO 9001 7.1.5.2).

**Canonical token:** `result=fail`.

**Back-propagation:** 06, 10, 14 (link).

---

### R20 — NCR disposition

**Decision:** Add a **`disposition`** enum to the **`ncr`** entity in doc 14, plus **`disposition_authorized_by`** (ISO 9001 8.7).

**Canonical `disposition` enum values (verbatim):**
`use_as_is`, `rework`, `scrap`, `return`, `concession`, `regrade`

**Canonical tokens:** entity `ncr`; fields `disposition`, `disposition_authorized_by`.

**Back-propagation:** 02, 06, 14.

---

### R21 — Record source-version invariant

**Decision:** Soften doc 06 invariant 2 to: **every Record produced UNDER a controlled document pins `source_version_id`; ad-hoc EVIDENCE records may have null source.** `source_version_id` is **nullable** in doc 14.

**Canonical token:** field `source_version_id` (nullable).

**Back-propagation:** 06, 14.

---

### R22 — DCR model

**Decision:** The **Document Change Request** is a **controlled WORKFLOW object** with a **mutable state column** and an **append-only history of stage events**. It is **NOT a `kind=RECORD` immutable artifact** (its closed form is retained as a record-like snapshot).

**Canonical DCR lifecycle (verbatim):**
`Open` → `Assessed` → `Routed` → `InApproval` → `Approved` → `Implemented` → `Closed`
with terminal states `Cancelled` / `Rejected`.

Doc 10 short form (Raised / Triage / Accepted) **maps onto these**.

**Back-propagation:** 05, 10, 14.

---

### R23 — Nav labels

**Decision:** The task inbox is **My Tasks** everywhere (**retire "My Actions"**). The global search shortcut is **Cmd-K / Ctrl-K** primary, with **`/`** as a secondary focus shortcut.

**Canonical tokens:** label `My Tasks`; shortcut `Cmd-K` / `Ctrl-K` (primary), `/` (secondary).

**Back-propagation:** 01, 07, 10, 11, 13.

---

### R24 — Lock TTL

**Decision:** The check-out lock default TTL is **8h** (canonical). Doc 11 wireframe MUST say **8h** (not 24h).

**Back-propagation:** 04, 05, 11.

---

### R25 — Singleton rule

**Decision:** The **Quality Policy** and **Scope Statement** enforce **exactly one EFFECTIVE instance at a time** (**NOT** one instance ever). A **draft successor may coexist** while the current governs; this survives import dedup and multi-site. Reword doc 04 sec 7.2 and doc 14 sec 5.2.

**Canonical token:** invariant = exactly one `Effective` instance at a time (per Quality Policy / Scope Statement).

**Back-propagation:** 04, 09, 14.

---

### R26 — Non-renderable formats

**Decision:** For formats **LibreOffice/Gotenberg cannot render**:
- Store the **source blob as the controlled artifact**.
- Mark **"no preview available"**.
- Gate download behind a **click-through uncontrolled-when-printed notice** (no rendition to watermark).
- Keep the doc **fully versioned/controlled**.
- Flag it as **"no controlled rendition"** on the **Document-Control dashboard**.

**Back-propagation:** 04, 11, 13.

---

### R27 — GDPR vs WORM

**Decision:** Document the legal posture: records whose **CONTENT is PII** and whose **retention exceeds employment** **remain under object-lock**; provide a **tightly-controlled, dual-control, fully-audited destroy-under-legal-order escape hatch** for WORM blobs (mis-imports, erasure orders).

**Back-propagation:** 06, 12.

---

### R28 — Evidence-pack exclusions

**Decision:** When pack generation **excludes items the generator cannot access**, **warn the GENERATOR prominently** and **record which items were excluded and why** (**permission vs genuine absence**), **distinct from the compliance-gap report**.

**Back-propagation:** 06.

---

### R29 — Escalation dependencies

**Decision:** Add a **nullable `manager_id`** (reporting-line) FK on **`app_user`** and a **`working_calendar`** entity (org holidays/working days) in doc 14. **Notification escalations and business-day SLAs resolve against these** (or, where unset, **fall back to the QM/OrgRole**).

**Canonical tokens:** field `manager_id` on entity `app_user`; entity `working_calendar`.

**Back-propagation:** 10, 14.

---

### R30 — Mandatory-doc coverage

**Decision:** The consolidated **star (mandatory) documented-information list** in doc 02 sec 2.1 is **authoritative for the Compliance Checklist seed**. Add the missing **8.5.6 (production/service change control)** row to the clause-8 walkthrough table.

**Back-propagation:** 02, 13.

---

### R31 — Scope-change side effect

**Decision:** When the **Scope Statement is revised to remove an exclusion**, **re-surface the previously-hidden IA sections/entities** and **re-run mandatory-coverage checks**.

**Back-propagation:** 02, 08.

---

### R32 — Email deliverability

**Decision:** Assign **ownership of email bounce/delivery-failure handling**: surface failures on the **Health dashboard (doc 08 sec 15.6)** and as a **system notification**; do **not** leave it deferred to a non-existent doc.

**Back-propagation:** 08, 10.

---

### R33 — Whole-vault export

**Decision:** Add a **portable, whole-QMS export capability** (documents + records + audit in **open formats**) for **tenant migration/decommission**, distinct from scoped **Evidence Packs** and from **backup**; schedule it in the roadmap (**v1.x**) and **stub an export endpoint**.

**Back-propagation:** 06 (mention), 15 (endpoint), 16 (roadmap).

---

### R34 — Search/render performance budget

**Decision:**
- Quantify **expected index size per 1M docs** and **OCR throughput**.
- Note that **per-request watermark/stamp rendering is a real cost** that belongs in the **performance budget**.
- Explicitly state that the **S sizing profile runs Postgres-FTS-only (OpenSearch disabled)** as a **documented degraded mode**.
- **OpenSearch is absent system-wide in MVP/v1, not merely S-profile-off.** Every feature with an OpenSearch path ships its **OpenSearch-disabled realization first** and reserves the OpenSearch impl as a *documented, not-built* drop-in behind a seam — no container, no compose entry, not probed in `/readyz`. So far: **search** = `PostgresFtsIndexer` behind the `Indexer` seam (the `OpenSearchIndexer` is the reserved drop-in); **ingestion near-dup** (doc 09 §7.1) = the **in-process MinHash** `InProcessMinHashDetector` behind the `DedupDetector` seam (the `OpenSearchDedupDetector` is the reserved drop-in, S-ing-3). Standing up the OpenSearch container is a *future* register-level decision, made in the slice that actually consumes it — a family-level "full-fidelity" posture does **not** by itself authorize adding the heavy service before anything reads from it.

**Back-propagation:** 03, 09, 13.

---

### R35 — Permission-grant boundary (consistency)

**Decision (stakeholder decision a):** The **QMS Owner may hold `permission.grant` scoped to CONTENT permission domains within QMS scope**; **SYSTEM permissions remain admin-only**. Resolve the **doc 07 sec 4.2 vs doc 08 sec 10 contradiction in favor of this two-tier model** and **state it once, consistently, in both**. Keep the **self-grant friction + audit** for any **QMS→admin crossing**.

**Canonical tokens:** content domains `document.*`, `record.*`, `audit.*`, `capa.*`, `changeRequest.*`, `evidencepack.*` (QMS scope); system domains `user.*`, `role.*`, `storage.*`, `backup.*`, `restore.*`, `config.*`, `import.*` (SYSTEM scope, admin-only); grant permission `permission.grant`.

**Back-propagation:** 07, 08.

---

### R36 — Metric numbering

**Decision:** Doc 01 sec 5 **metric numbering is canonical**. Correct doc 05 sec 11.3 cross-references:
- zero-uncontrolled-effective-versions = **M2**
- audit-trail-completeness = **M7**

**Canonical tokens:** `M2`, `M7`.

**Back-propagation:** 05.

---

### R37 — Backup/restore over WORM + PITR

**Decision:**
- Specify a **WORM-aware restore procedure**: restoring over object-locked blobs requires a **fresh/cleared bucket or versioned restore target**.
- Specify **PITR ↔ blob alignment**: a Postgres point-in-time restore MUST be **paired with the matching blob set**, not merely the latest mirror.
- **Verify the audit hash-chain checkpoint is not ahead of a mid-chain PITR target.**
- **Bound the consistency-quiesce window** and **reconcile it with the R14 availability target**.

**Back-propagation:** 03, 12.

---

### R38 — Additive permission-catalog extensibility + the SoD-6 creator≠disposer constraint (slice S-rec-4)

**Context.** The doc-07 §3 permission catalog was declared "closed for v1" (R5). Shipping
`/retention-policies` management (doc 15 §8.16, deferred to v1) needs permission keys the closed 96-key
catalog does not contain. Records disposition also lacked any creator≠disposer segregation (doc 07 §7
listed only SoD-1…SoD-5).

**Decision:**
- **The catalog is ADDITIVELY extensible post-v1.** "Closed for v1" (R5) is REFINED, not contradicted:
  existing keys are never renamed or removed (the normalization in R5 stands), but new keys MAY be added
  with a register entry. The **first** such additive extension is **`retention.read` + `retention.manage`**
  (CONTENT-domain — `is_system_domain=false`, non-sig-hook, non-SoD-sensitive, `finest_scope=SYSTEM`
  because retention policies are org-level). Seeded to **QMS Owner** (`retention.read` + `retention.manage`)
  and **Internal Auditor** (`retention.read` — the checklist-read precedent). Being CONTENT-domain, the
  R35 two-tier guard already lets a QMS Owner's content-tier `permission.grant` grant them.
- **SoD-6 (creator≠disposer)** is a new SoD constraint in the **overridable** SoD-2/4/5 small-org class
  (NOT the hard SoD-1/3 class): a record's own capturer (`record.captured_by`) may not execute its
  disposition to DISPOSED/DESTROY (refused **409 `sod_self_disposition`**, audited
  **`DISPOSITION_REFUSED_SOD`**), unless the org sets the **`allow_self_disposition`** flag
  (`system_config`, default OFF = enforced; flipped via PATCH `/admin/config`, the SYSTEM-only
  `config.update`). It is enforced in the service layer (like the R27 dual-control), NOT in the PDP, so a
  SYSTEM-scope `record.dispose` override does not bypass it — only the flag relaxes it. It gates only the
  DISPOSED edge (never DUE_FOR_REVIEW / ACTIVE re-anchor), is exempt for the Beat sweep (a system actor),
  and is subsumed by the stronger dual-control (requester≠approver) on the R27 legal-order hatch.
- **Retention-policy lifecycle:** full CRUD + **soft-archive** (a hard DELETE is structurally impossible —
  3 RESTRICT FKs from record / document_type / disposition_event). PATCH is **extend-forward only while a
  policy has non-disposed pinned records** (a duration reduction, a weaker `disposition_action`, or
  `review_required` true→false is refused 422 `retention_reduction_blocked`) because the sweep
  live-dereferences the pinned policy; shortening retention for FUTURE captures is done by **archiving the
  policy + creating a shorter one** (doc 06 §5.2's one-way ratchet, honored without snapshotting the
  duration onto each record). The seeded **System Default is protected** (cannot be archived/renamed).

**Back-propagation:** 06 (§5.1/§5.3), 07 (§3 catalog + §7 SoD-6), 14 (§10), 15 (§8.16), 16.

---

### R39 — Audits / Findings / CAPA family: model + workflow + SoD posture (slice family S-aud / S-capa)

**Context.** The v1 Audits/Findings/CAPA family (doc 02 Cl 9.2 / 10.2, doc 10 §5-6, UJ-5/UJ-6) was started.
Several modelling + governance choices were locked by the product owner and an adversarial design pass.

**Decision:**
- **Workflow posture = "+ Declarative routing".** The family builds the audit/finding/CAPA **records +
  state machines + closure gates + the atomic NC→CAPA auto-link** AND the doc-10 **declarative routing
  engine** on the existing `workflow_*` tables (multi-stage + severity-conditional routing, quorum,
  candidate-pool resolution, due-date SLAs, real My-Tasks). **Deferred** to a later Workflow & Notifications
  family: SMTP/in-app notification *delivery*, digests, and the `manager_id`/`working_calendar`
  auto-*escalation* Beat. v1 candidate-pool resolution reuses the existing permission-role membership seam;
  `org_role_assignment`-based resolution stays deferred (owner-assignment track).
- **SoD-4 (CAPA verifier ≠ action implementer) = severity-aware.** Critical/Major CAPAs HARD-enforce
  (service-layer 409); Minor respects a per-org `allow_capa_self_verify` flag (`system_config`, default OFF),
  mirroring the SoD-6 `allow_self_disposition` mechanic (service layer, NOT a PDP `sod_constraint` row). The
  flag + grant-backfill for the orphaned `capa.update` / `ncr.create` / `ncr.record_correction` keys land
  with the CAPA slice (S-capa-1).
- **Rejected-CAPA → audit close = block-until-corrected.** An NC-sourced CAPA in `close_state=Rejected` does
  NOT satisfy the audit-close gate. The gate keys off **live NC findings** (`finding_type=NC` AND
  `superseded_by_correction IS NULL`), each requiring a linked CAPA at `close_state=Closed`. A legitimately
  rejected NC must be corrected via a `correction_of` finding retyping it (NC → Observation/OFI), which
  supersedes the original and removes it from the live-NC set. No audit ever closes over an uncorrected NC.
  *(S-aud-2 owner forks:* the finding correction is a **general any-direction retype** — a retype TO an NC
  auto-creates its mandatory CAPA on the successor and re-enters the gate, not only NC→OBS/OFI; and the
  finding-create/correct window is **open-until-Closed** — findings may be logged/corrected in any
  non-terminal audit state, rejected only once the audit is Closed.*)
- **`audit_program` is an own-table scheduling container, NOT a `documented_information` subtype** — a
  deliberate divergence from doc 14's "a maintained document" phrasing. A programme is a period + coverage +
  a set of planned audits; a version-less `kind=DOCUMENT` would leave an Effective document with no
  `document_version` (silently dropped by the mirror join, but mis-listed by the document library + its
  detail/download paths). `audit_plan` is likewise an own-table. The **retained evidence** —
  `audit` / `audit_finding` / `capa` — stays a **`kind=RECORD` shared-PK subtype** per doc 14 (`audit.id` →
  `record.id`), with a **mutable lifecycle column** (`audit.state` / `capa.close_state`) — the
  `record.disposition_state` precedent (record immutability governs captured content + sealed stage-blocks,
  not the lifecycle column).
- **No new permission keys for this family.** `audit.*` / `finding.*` / `ncr.*` / `capa.*` already exist in
  the closed doc-07 catalog (0004) and are granted to roles — `audit_object_type` reuses the reserved
  `record` value for the record subtypes and the reserved `audit` value for programme/plan container events
  (zero ADD VALUE). The only catalog work is the S-capa-1 grant-*backfill* of three already-defined-but-
  ungranted keys (no new keys; not a catalog extension).

**Enum canon (S-capa-1 normalization).** The CAPA-family enums are all-lowercase, extending the R2
(`signature_event.meaning`) / R16 (`source=complaint`) lowercase precedent: `capa_source` =
`audit`,`process`,`complaint`,`review_output` (doc 14 §9's `AUDIT` was a spec typo, corrected;
`review_output` is a RESERVED forward seam for the deferred Management-Review family, never written in
v1); `ncr_source` = `audit`,`process`,`complaint`,`internal`. The `nc_severity`
(`Critical`/`Major`/`Minor`) vocabulary is shared across `capa` / `ncr` / `complaint` (and
`audit_finding` in S-aud-2). `complaint` is implemented as a **`kind=RECORD` shared-PK subtype**
(`complaint.id` IS `record.id`) — a justified divergence from doc 14 §6's literal `id PK + record_id
FK` satellite phrasing, for consistency with the `audit`/`capa` record-subtype family (the same kind
of divergence this register made for `audit_program`). `capa_stage`'s doc-14 `attachments` member is
realized as `evidence_for_link(target_type=CAPA_STAGE)` edges (Mode C), not a column.

**S-capa-2 action-plan approval (the severity-routed engine wiring).** The Action-Plan approval is a
seeded declarative `workflow_definition` (`capa_action_plan_approval`, subject `CAPA`, migration `0038`,
seed-only) the propose step instantiates. Routing (doc 10 §6.3): a ROUTER entry on the CAPA `severity`
context — **Critical** → `crit_qm` (QMS-Owner, ANY) → `crit_topmgmt` (**Top Management**, ANY) as
SEQUENTIAL stages (the cross-role "QM *and* top-management" conjunction; a single merged-pool `N_OF_M`
over a [QM,TopMgmt] union would *false-PASS* because two QMs could satisfy it with no top-management
sign-off); **Major / Minor** → `qm_approval` (QMS-Owner, ANY). A uniform ≤5-business-day SLA (doc 10
§6.2; informational in v1, no escalation). **"Top Management" is a NEW additive reserved role** (R38
catalog-growth posture applied to roles; resolved by `Role.name` via the candidate-pool seam — org-role
resolution stays deferred), holding only `capa.read`; a single-operator install must assign QMS-Owner /
Top-Management members or the approval fails closed (`NEEDS_ATTENTION`), the records-family SoD posture.
**Approval model:** the proposed plan rides the mutable `workflow_instance.context` (a draft);
`capa.close_state` flips RootCause→ActionPlan **only at approval-complete**, so `close_state==ActionPlan`
⟺ the plan was approved. ONE `signature_event(meaning=approval, signed_object_type=capa_stage)` is written
per approved plan (signer = the completing approver; per-approver decisions are the `task_outcome` trail;
per-approver crypto-signatures are a Part-11 refinement); `capa_stage.signed_event_id` is set **at INSERT**
via a pre-generated stage UUID (the signature `signed_object_id` = that id; the stage `signed_event_id` =
the flushed signature id) — two mutually-referencing INSERTs, never an UPDATE on the append-only table.
**Authz:** the approval decision is gated by **candidate-pool membership** (no catalog key gates "approve a
CAPA action plan" — the role-resolved pool IS the authority, the self-scoped-task doctrine doc 07) +
a decision-time **live-role re-check** + a **cross-STAGE distinct-approver** guard (a single user holding
both QMS-Owner and Top-Management cannot clear both Critical tiers alone). SoD-4 (verifier ≠ implementer) +
`allow_capa_self_verify` remain **S-capa-3**. The per-stage endpoints (`/capas/{id}/root-cause` gate
`capa.record_rca`; `/capas/{id}/action-plan` gate `capa.plan_action`) follow the shipped `/containment`
precedent, **superseding** doc 15 §8's unified `POST /capas/{id}/stages` (a single `capa.update` gate).

**S-capa-3 closure (Implement / Verify / Close — the M4 gate, severity-aware SoD-4, the effectiveness
loop).** Zero-migration (head stays `0038`): `CapaCloseState`, `signature_event.meaning=verify`,
`cycle_marker`, `evidence_for_link(CAPA_STAGE)`, and `system_config.allow_capa_self_verify` all pre-exist.
Three per-stage endpoints (the `/containment` precedent): `POST /capas/{id}/implement` (gate
`capa.capture_effectiveness`, ActionPlan→Implement, unsigned) · `/verify` (gate `capa.verify`,
Implement→Verify; the REAL `signature_event(meaning=verify, signed_object_type=capa_stage)`, written the
S-capa-2 way — pre-generated stage UUID + flush + INSERT, never an UPDATE on the append-only table; the
`effective`/`not_effective` decision is sealed into the Verify block) · `/close` (gate `capa.close`).
**Owner decisions (this session):** (1) **`/close` adjudicates the M4 gate** — `/verify` records the
decision + signature + SoD-4; `/close` runs the gate. (2) **Re-approval required on the loop** — a
`not_effective` verification must re-propose + re-approve a revised plan. Because `propose_action_plan`
is only legal from a pre-ActionPlan state, the effectiveness loop **routes the FSM through RootCause**:
the doc 10 §6.1 edge changes from `Verify→ActionPlan` to **`Verify→RootCause`** (then
`RootCause→(re-propose+re-approve)→ActionPlan`), a faithful expansion of §6.4's "routes back to Action
Plan" under the re-approval rule — `close_state==ActionPlan ⟺ an approved plan exists` still holds.
**The M4 gate** (`domain/capa/closure.py`, pure; server-derived under the `capa` FOR UPDATE): `effective`
∧ root_cause ∧ ≥1 implemented-action-with-evidence ∧ effectiveness-evidence → **Closed**; `not_effective`
→ **loop** (`Verify→RootCause`, `cycle_marker++`); `effective` but a missing evidence clause → **409
`capa_close_incomplete`** (NOT the loop — a forgotten link must not discard a recorded effective
verification). "Evidence" is a real `evidence_for_link(CAPA_STAGE)` row on the stage; the
implemented/effectiveness checks are **current-cycle-scoped** while `root_cause` is **cycle-agnostic**
(the loop carries the established RCA forward; v1 has no re-RCA path). **SoD-4** (`domain/capa/sod.py`,
pure; doc 10 §6.3): the verifier must not be in the implementer set — Critical/Major HARD, Minor honours
`allow_capa_self_verify` (default OFF) — checked **unconditionally before any permission short-circuit**
(the SoD-6 `allow_self_disposition` mechanic, service-layer, NOT a PDP `sod_constraint`). The
**implementer set** is the union over the whole stage trail of every **Implement** stage's `created_by`
plus every ActionPlan block's `action_items[].owner` that parses as a UUID — it **excludes the ActionPlan
stage `created_by`** because in S-capa-2 that is the plan APPROVER (approving ≠ implementing; counting it
would bar an approving QM from verifying and make a single-QM install unable to close a Major+ CAPA).
**Freeze:** `records.unlink_evidence` 409s `evidence_frozen` for a CAPA_STAGE target whose stage is
`Verify` or whose CAPA is `Closed` (the verification + the closed record are immutable). A latent S-capa-2
replay bug the loop exposes is fixed: `_enrich_completed_replay` now scopes to the replayed instance via
`content_block.workflow_instance_id` (was `signed[-1]`, which after a loop returns a later cycle's
signature). Lifecycle events reuse `CAPA_TRANSITIONED` (no new event type). No new permission keys / enum
/ Celery task.

**S-aud-capa-pack (Evidence-Pack FINDING/CAPA scope + the sealed dossier — the family close-out).**
Migration `0039` is a pure `ALTER TYPE pack_scope_kind ADD VALUE 'FINDING'/'CAPA'` (additive, downgrade
no-op; no new permission keys — pack creation/download ride the existing `report.evidence_pack.generate`
/ `report.export`). A FINDING/CAPA pack **resolves only the records linked AS EVIDENCE** to the
finding(s) / the CAPA's stages (`evidence_for_link(target_type=finding|capa_stage)`) — the finding/CAPA
SUBJECT is **never a pack_item record** (a record subtype carries no `evidence_blob` → no ZIP bytes; a
phantom INCLUDED member would be unverifiable). The subjects instead get a synthesized, content-hash-
**sealed dossier** (doc 06 §7.1's "prove this NC was closed effectively"): per scope subject a
`findings/<id>.json` / `capas/<id>.json` carrying the finding's fields + correction chain + linked CAPA,
or the CAPA's full append-only stage trail (RootCause→ActionPlan→Verify, grouped by `cycle_marker`) with
each stage's **e-signature metadata** + per-stage evidence + the origin finding (inline). **PII boundary:**
every signer/creator is projected to `{user_id, display_name}` ONLY — never `email`/`keycloak_subject`
(the ZIP is externally shareable). **Seal:** `pack_content_hash` gains a `dossier_digest` param → when
present the seal is **v2** (`easysynq.evidencepack.v2` preamble + the digest folded in), so the version
field self-describes; CLAUSE/PROCESS stay byte-identical **v1**. `dossier_digest` hashes the SORTED
per-file sha256s (those in `manifest["dossier"]["files"]`) so it is reconstructable from the ZIP alone
(self-verifying per §7.4). **gap analysis is N/A** for FINDING/CAPA (`gap_summary.applicable=False`; the
cover/manifest/portfolio render N/A, never a misleading 0-of-0); the PDF portfolio's verify-scheme line
matches the v2 seal. Pure dossier serializers in `domain/packs/dossier.py`; the build orchestration in
`services/packs/dossier.py`. No new Celery task / event type / enum (beyond the two scope values).

**Implemented in slices:** S-aud-1 (`audit_program` + `audit_plan` + `audit` + FSM, migration `0034`);
S-wf-engine (the declarative engine, migration `0035`); **S-capa-1** (the CAPA core + intake —
`capa` + append-only `capa_stage` + `ncr` + `complaint` + complaint→CAPA spawn + Raised/Containment +
the grant-backfill of `capa.update`/`ncr.create`/`ncr.record_correction` + the `allow_capa_self_verify`
flag, migration `0036`); S-aud-2 (findings + NC→CAPA auto-link + the block-until-corrected close gate,
migration `0037`); **S-capa-2** (RootCause + ActionPlan stages + the severity-routed engine approval +
the real `signature_event` write for `capa_stage.signed_event_id` + the Top-Management role, migration
`0038`); **S-capa-3** (Implement/Verify/Close + severity-aware SoD-4 + the M4 closure gate + the
`Verify→RootCause` effectiveness loop + the `evidence_frozen` unlink guard, **zero-migration**, head stays
`0038` — the production path that drives a CAPA to Closed and satisfies the S-aud-2 audit-close gate);
**S-aud-capa-pack** (Evidence-Pack FINDING/CAPA scope + the content-hash-sealed dossier, migration
`0039` = `ALTER TYPE pack_scope_kind ADD VALUE`). **The Audits/Findings/CAPA family is now COMPLETE.**

**Back-propagation:** 02 (Cl 9.2/10.2), 06 (§7), 07 (§7 SoD-4), 10 (§5-6), 14 (§6/§9/§14), 15, 16.

---

### R40 — Revision & change depth (DCR) family: model + the InApproval reject-loop target (slice family S-dcr)

**Context.** The v1 "Revision & change depth" family (doc 05) was started. doc 05 §5.5 and doc 15 §8.7 — both
authoritative section docs — **disagree** on one DCR FSM edge: where the InApproval "changes-requested"
rejection loops back to. doc 05 §5.5's state diagram shows `InApproval → Routed`; doc 15 §8.7's shows
`InApproval → Open`. The Register is otherwise silent, so the owner resolved it.

**Decision:**
- **The DCR is an OWN table with a mutable `state` column + an append-only `dcr_stage_event` trail — NOT a
  `kind=RECORD` subtype** (R22, the `worm_destroy_request` mutable-state precedent). The "closed form retained
  as a record-like snapshot" (R22) is the frozen `dcr` row + its immutable stage trail — **no separate snapshot
  table**. A DCR id is not a record id, so its audit events key on a fresh `audit_object_type='dcr'` (the `ncr`
  own-table precedent), NOT `record`.
- **The InApproval changes-requested rejection loops to `Open`** (re-assess + re-route), per doc 15 §8.7 —
  **superseding doc 05 §5.5's `Routed`** (owner decision). Rationale: a substantively changed draft should
  re-derive its impact assessment + approver routing, so returning to the start of the flow is the safer QMS
  posture. The pure `domain/dcr/fsm.py` table encodes this. (Also reconciled: `Cancelled` is reachable only
  from the pre-approval states `{Open, Assessed, Routed}` per doc 15's "while not implemented" — there is **no**
  `Approved → Cancelled` edge; an InApproval DCR exits via `Rejected` or the changes-requested loop, not Cancel.)
- **Permission keys = the seeded `changeRequest.*` family** (R5 normalizes doc 15's `dcr.*`). No new keys: the
  S-dcr-1 slice **backfills** the two orphaned keys it surfaces (`changeRequest.assess` for PATCH-while-Open,
  `changeRequest.close` for cancel) to Process Owner + QMS Owner, PROCESS-scoped via the `:assignment_process`
  placeholder (the S-capa-1 / R39 backfill recipe; rides SYSTEM overrides until owner-assignment binds).
- **Scope-fork breadth (owner):** redline/diff is **full** (metadata + on-demand text via the S-ing-2 Tika
  sidecar + visual page-image, S-dcr-3); where-used adds a **`document↔document` link table** (S-dcr-2).
  Scheduled re-review (D5) + drift detection (D1–D4) stay in the **separate v1.x drift family** (roadmap §5 / D-6).

**S-dcr-3 addendum.** Redline/diff (doc 05 §8) is **full** (owner: metadata + text + visual), delivered in two
PRs: **S-dcr-3a** = the two §8.1 core dimensions — metadata diff (field-by-field over the frozen
`metadata_snapshot`; version columns + signatures live in the provenance header, NOT diffed) + text redline
(on-demand Tika extraction behind a `TextExtractor` seam → `difflib` line-LCS; fail-closed → `text_diff:
unavailable`) — at `GET /documents/{id}/versions/{vid}/diff?from={vid2}`, **gated on `document.read_draft`** (the
diff exposes non-released version content, so `document.read` alone would leak Draft text to an Employee/Guest;
the diff-critic catch). `document.diff` (doc 05 §11.2's non-authoritative "representative" list) is NOT seeded —
the diff rides the existing read key (R5 closed catalog), so 3a is **zero-migration**. **S-dcr-3b** = the visual
page-image diff via **pypdfium2** (Apache/BSD, prebuilt wheels — NOT PyMuPDF/fitz, so it passes the
`test_no_pymupdf_or_fitz_in_lockfile` AGPL guard; no system dep / air-gap impact) + Pillow, with on-demand
Gotenberg render for non-Effective versions (whose rendition is NULL). The §8.1 "visual is a complement" framing +
those constraints justify the 3a/3b split (text+metadata ship first; the dependency-heavy visual follows).
**S-dcr-3b shipped (mig 0042)** as **worker-async** (owner chose full coverage over in-request-PDF-only, since the
**API can't render** — `LoggingRenderSink` no-op; rendering is worker-only): a `visual_diff` cache table
(`UNIQUE(from,to)` idempotency latch + cache key; `VisualDiffStatus` Pending/Ready/Failed/Unavailable; GRANT UPDATE —
regenerable cache, not append-only) + `easysynq.visual_diff` (FOR-UPDATE early-return idempotent; `acks_late` + re-POST
self-heal, no Beat reaper) + pure `domain/diff/visual.py` (pypdfium2 rasterize + Pillow per-page `ImageChops` overlay).
The task obtains each version's PDF (cache-hit on the mirror `rendition_blob_sha256`; else render via the worker's
`GotenbergRenderSink` **TRANSIENTLY — never persists `rendition_blob_sha256`**, else a Draft's diff-rendition
[copy_status=state, no verify QR] would poison the mirror's controlled-copy cache when it goes Effective). Contract =
**POST-compute (202) + pure-GET-poll (404-before-request) + GET page/{n}?layer= PNG stream** (the design-critic-mandated
shape; the packs/imports async precedent), gated `document.read_draft`. Watermark band-noise (band differs by rev → a
changed footer region) accepted + documented for v1 (raw-render = v1.x). NO new permission key / event type. **The doc 05
§8 full diff (metadata + text + visual) is COMPLETE.**

**S-dcr-2 addendum (owner).** doc 05 §7.3 mandates that obsoletion is **blocked** unless replacement /
`force_retire`+justification. The two authoritative docs do not say WHERE the block is enforced; the owner
chose to **defer enforcement to S-dcr-5** (the RETIRE-DCR implement call site) rather than wire it into the
shipped S4 `document.obsolete` endpoint now. So **S-dcr-2 ships the pure `obsoletion_blocked` predicate +
SURFACES it** (the `GET /documents/{id}/where-used` `obsoletion_safety` advisory + a RETIRE-DCR's
`impact_assessment` `clause_coverage` dimension) but does NOT block; the 409-blocking gate (with the
`force_retire` escape hatch) lands in S-dcr-5. The shipped `document.obsolete` endpoint is untouched.
The `document_link` table (doc 14 §5.6) is editable metadata (`SELECT,INSERT,DELETE`, not append-only);
`impact_assessment` (doc 14 §7) UPSERTs one row per dimension at assess (auto_populated re-computed, the
requester_annotation preserved). No new permission keys (where-used=`document.read`, link CRUD=
`document.manage_metadata`, assess=`changeRequest.assess`, impact=`changeRequest.read`/`.assess`).

**Slices.** **S-dcr-1** (DCR core + intake: `dcr` own-table mutable-state FSM + append-only `dcr_stage_event`
[`REVOKE UPDATE,DELETE`] + `DCR-{YYYY}-{SEQ}` 4-digit identifier + `domain/dcr/fsm.py` + `DCR_RAISED`/
`DCR_UPDATED`/`DCR_TRANSITIONED` events + `audit_object_type=dcr`; endpoints POST/GET `/dcrs`, GET/PATCH
`/dcrs/{id}`, POST `/dcrs/{id}/cancel`; migration `0040`). **S-dcr-2** (where-used/impact + assess:
`document_link` doc↔doc graph + CRUD + `GET /documents/{id}/where-used` [the §7.2 categories + the §7.3
`obsoletion_safety` advisory] + `impact_assessment` + `POST /dcrs/{id}/assess` [Open→Assessed, auto-populates
the 7 §5.3 dimensions] + `GET/PUT /dcrs/{id}/impact`; migration `0041`; obsoletion enforcement deferred to
S-dcr-5 per the addendum). Next: S-dcr-3 (diff), S-dcr-4 (routing + approval, subject_type=DCR via the engine),
S-dcr-5 (implement/close + effectivity + the CAPA→DCR loop, the deferred cross-FK
`document_version.dcr_id` ↔ `dcr.resulting_version_id`).

**Back-propagation:** 05 (§5.5 reject-loop → Open), 14 (§7), 15 (§8.7), 16. Supersedes B5 (DCR dual-lifecycle).

---

## Part 4 — Gap-audit finding → resolution map

This table maps **every** gap-audit finding id from `17-gaps-and-open-questions.md` — Section A (Gaps: A1–A14), Section B (Contradictions/Inconsistencies: B1–B15), Section C (Risks & Hard Problems: C1–C12, including C6b), and Section D (Open Questions: D1–D14) — to the R-number(s) that resolve it. Several findings share a resolution (the audit raised the same concern as a gap, a contradiction, and an open question); those rows point to the same R-number.

### Section A — Gaps

| Finding id | Resolved by |
|------------|-------------|
| A1 (controlled folder / scope-path entity) | R6 |
| A2 (document Level-1/2/3 never modeled) | R7 |
| A3 (effective-date timezone) | R8 |
| A4 (review-only / `review_confirmed` meaning) | R2 |
| A5 (outsourced-process control) | R17 |
| A6 (customer-complaint intake) | R16 |
| A7 (risk-register scoring fields) | R18 |
| A8 (non-renderable source formats fallback) | R26 |
| A9 (new-joiner acknowledgements) | R15 |
| A10 (calibration-failure impact loop) | R19 |
| A11 (NCR disposition states) | R20 |
| A12 (QMS scope-change → re-evaluate exclusions) | R31 |
| A13 (email deliverability / bounce ownership) | R32 |
| A14 (whole-vault / tenant-offboarding export) | R33 |

### Section B — Contradictions / Inconsistencies

| Finding id | Resolved by |
|------------|-------------|
| B1 (two document lifecycle state machines) | R1 |
| B2 (`signature_event.meaning` enum inconsistent/incomplete) | R2 |
| B3 (AuthZ resolution algorithm differs 07 vs 12) | R3 |
| B4 (first-run wizard step count/order 08 vs 11) | R4 |
| B5 (DCR is both Record and workflow / two lifecycles) | R22 |
| B6 (`record.retire` vs disposition naming) | R5 |
| B7 (audit-program state machine 10 vs 14 vs 11 calendar strip) | R5 *(audit `audit.*` namespace + state alignment; see also R1 lifecycle pattern)* |
| B8 (`document.author` vs catalog keys) | R5 |
| B9 (import permission names inconsistent) | R5 |
| B10 (Mara grants permissions vs Admin boundary) | R35 |
| B11 ("My Tasks" vs "My Actions") | R23 |
| B12 (lock TTL 8h vs 24h) | R24 |
| B13 (search shortcut Cmd-K vs `/`) | R23 |
| B14 (new-doc from template vs blank) | R5 *(catalog `document.create` "from template/blank" is canonical)* |
| B15 (record source-version invariant vs EVIDENCE null) | R21 |

### Section C — Risks & Hard Problems

| Finding id | Resolved by |
|------------|-------------|
| C1 (ingestion classification accuracy overstated) | R10 |
| C2 (mirror drift auto-overwrite / read-only mount fragility) | R11 |
| C3 (version-family reconstruction manufacturing false history) | R10 |
| C4 (search/render performance & cost on large binaries) | R34 |
| C5 (concurrent editing / lock-loss data loss) | R9 |
| C6 (backup/restore over WORM + PITR↔blob alignment) | R37 |
| C6b (WORM object-lock vs GDPR erasure of PII content) | R27 |
| C7 (tamper-evidence signing key on-host / off-host anchor optional) | R13 |
| C8 (in-txn audit row vs hash-chain serialization) | R12 |
| C9 (evidence-pack silent scope exclusions) | R28 |
| C10 (single-host availability target vs SPOFs) | R14 |
| C11 (escalation needs manager graph + working calendar) | R29 |
| C12 (`is_singleton` vs import & multi-site) | R25 |

### Section D — Open Questions (each with the resolving R-number)

| Finding id | Resolved by |
|------------|-------------|
| D1 (`effective_from` local-date vs UTC) | R8 |
| D2 (FOLDER scope first-class + where path lives) | R6 |
| D3 (break-lock / lock-expiry: preserve vs discard) | R9 |
| D4 (always human-confirm `kind` classification) | R10 |
| D5 (import: reconstruct history vs current-only) | R10 |
| D6 (off-host audit-checkpoint anchor mandatory) | R13 |
| D7 (non-Admin QMS Owner holds `permission.grant`) | R35 |
| D8 (canonical task-inbox name + search shortcut) | R23 |
| D9 (hash-chain serialization without throttling writes) | R12 |
| D10 (availability target & HA posture for hard deps) | R14 |
| D11 (new joiners / role-changers acknowledgements) | R15 |
| D12 (customer complaints first-class intake) | R16 |
| D13 (singleton = "one Effective" vs "one instance ever") | R25 |
| D14 (watermark/preview policy for non-renderable formats) | R26 |

> **Coverage note.** Every finding id in the gap audit (A1–A14, B1–B15, C1–C12 + C6b, D1–D14) is bound above to a normative resolution R1–R37. Where the audit raised the same underlying concern across multiple sections (e.g., the timezone issue as A3 and D1; the import-history concern as C1, C3, D4, D5; the off-host anchor as C7 and D6), those finding rows resolve to the same R-number. Conversely, the four stakeholder decisions in Part 2 are codified normatively in R10 (import default), R13 (off-host anchor), R35 (permission-grant boundary), and decision (d) is the adoption of this full R1–R37 pass.

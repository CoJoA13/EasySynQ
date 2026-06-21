# EasySynQ Decisions Register

This document is the **single authoritative source of truth** for the EasySynQ self-hosted ISO 9001:2015 QMS specification. It records the locked foundational decisions, the locked stakeholder decisions, and the normative resolutions (R1–R51) to every finding raised in the gap audit (`17-gaps-and-open-questions.md`); R38 (slice S-rec-4) is the first post-v1 *additive* decision (additive catalog extensibility + SoD-6), R39 (slice family S-aud/S-capa) locks the Audits/Findings/CAPA model + workflow posture, R40 (slice family S-dcr) locks the Revision & change-depth (DCR) family model + the InApproval reject-loop target, and R41 (slice S-drift-3) adds the `drift.read` SYSTEM-domain permission key; R42 (slice S-ack-1) adds the `document.distribute` CONTENT-domain key, and R43 locks the Acknowledgements-family model.

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

## Part 3 — Resolutions R1–R51

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

**S-dcr-4 addendum (owner — routing + approval, mig 0043).** The DCR approval rides the declarative engine
(subject_type=DCR, the S-capa-2 pattern). Owner decisions: (1) **Routing** = a seeded `dcr_approval`
`workflow_definition` with a ROUTER on `change_significance` — **MAJOR → Process Owner → QMS Owner (SEQUENTIAL, 2
distinct approvers); MINOR → QMS Owner (single editorial)** — reusing the existing seeded roles (no new role),
candidate-pool authz (no permission key; `changeRequest.approve` stays ungranted, the CAPA precedent), the cross-stage
distinct-approver guard. (2) **Signature model = PER-APPROVER** (doc 05 §5.4 "each approval writes a signature_event",
the S5 document precedent — NOT the CAPA one-signature model): each approve writes a `signature_event(meaning=approval,
signed_object_type='dcr', signed_object_id=<DCR id>)`, so a MAJOR DCR carries TWO; the `dcr_stage_event(InApproval→
Approved).signed_event_id` links the sealing (final) signature. (3) `POST /dcrs/{id}/route` does **Assessed→Routed→
InApproval atomically** (the approval authorizes the *change* — the resulting version + the cross-FK are S-dcr-5; so
there is no concrete draft to "submit", and §5.5's draft-submission semantics defer to S-dcr-5; the false "CAPA-precedent"
rationale from the design draft was struck per the design-critic). (4) A DCR **reject (→Rejected) / changes_requested
(→Open, the R40 loop) is DECISIVE** — one approver ends the approval (force-terminate the instance + skip sibling PENDING
tasks), since the engine's ANY quorum does not fail on a single negative when other candidates are live; a re-route after
a fix opens a FRESH instance. Migration `0043` = one `signed_object_type ADD VALUE 'dcr'` + the seed + the
`changeRequest.route` grant backfill (`workflow_subject_type.DCR` already existed from 0008). NO new permission key / event type.

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

**S-dcr-5 addendum (owner — implement/close + the obsoletion gate + the CAPA→DCR loop, mig 0044).** The
FINAL DCR slice; **the DCR family is now COMPLETE.** Owner decisions: (1) **Implement model = DCR-as-orchestrator**
— `POST /dcrs/{id}/implement` (gate `changeRequest.implement`, already seeded in 0004) DRIVES the vault action
for the change_type (reconciling doc 05/15's "DCR drives the release" over doc 10 §3's "spawn-a-revision"): REVISE
releases the target's Approved revision, CREATE releases the out-of-band-authored `resulting_version_id`, RETIRE
obsoletes the target. The flip is **atomic by construction** — REVISE/CREATE set the version's `effective_from`
(`proposed_effective_from` or now) + the cross-FK link + flip → Implemented in ONE commit, and the EXISTING
`release_due` Beat sweep performs the SERIALIZABLE single-Effective cutover (so a DCR-driven release is
system-attributed, the scheduled-release norm; no new reaper); RETIRE folds the flip into `lifecycle.obsolete`'s
own commit. (2) **No DCR side-door past document control** — the implement endpoint ALSO enforces the underlying
`document.release` (REVISE/CREATE, with the full SoD-2 overlay — author≠releaser + the sole-approver-release gate +
sig_hook) / `document.obsolete` (RETIRE) IN ADDITION to `changeRequest.implement`. SoD-2 is keyed on
`document.release` in the PDP, so a hand-rolled service check would silently skip the approver-side leg; the
endpoint `enforce("document.release", scope)` over the promoted version is the only faithful mechanism (shared with
the direct release endpoint via the extracted `enrich_release_sod_scope`). So the author of a revision cannot
self-implement it (403 `sod_violation`). (3) **The §7.3 obsoletion gate moves to the SHARED `lifecycle.obsolete()`
— BOTH the direct `POST /documents/{id}/obsolete` AND the DCR RETIRE-implement enforce it** (a 409
`obsoletion_blocked` unless `force_retire` + `override_justification`, recorded on the signature intent + audit) —
**superseding the S-dcr-2 addendum's "defer to the RETIRE call site / leave document.obsolete untouched"**: the
gate must have no bypass (doc 05 §7.3 "blocks silent obsoletion" is unconditional). Scoped to the T11
document-level branch only (a T12 Superseded-version archive removes no coverage). The §7.3 input reads +
`evaluate_obsoletion` live ONCE in `services/vault/obsoletion.py` (the where-used advisory consumes the same
function, so gate + advisory can't diverge; no vault→dcr import cycle — dcr→vault is the allowed direction).
(4) **`Implemented → Closed`** (gate `changeRequest.close`) requires the change to have actually taken effect
(§5.5 — the resulting version Effective / the target Obsolete; 409 `dcr_effectivity_pending` while a scheduled
cutover is outstanding). (5) **CAPA→DCR loop = a dedicated `POST /capas/{id}/raise-dcr`** (gate
`changeRequest.create`; doc 02 Cl 10.2 / doc 05 §5.1, the §10→§7.5 loop) via `raise_dcr(_commit=False,
source_link_type=capa)`. **1:N** — a CAPA may spawn child DCRs (doc 05 §5.3), so NO one-DCR-per-CAPA latch; an
`Idempotency-Key` (the new `dcr.spawn_idempotency_key` partial-UNIQUE) makes a retry return the same DCR (201
new / 200 replay). A terminal (Closed/Rejected) CAPA cannot spawn (409 `capa_terminal`). (6) **The deferred
cross-FK** `document_version.dcr_id` ↔ `dcr.resulting_version_id` lands (mig 0044, `use_alter` 2-table cycle, the
`capa.origin_finding_id`↔`audit_finding` precedent). Mig 0044 = the cross-FK pair + `spawn_idempotency_key` +
partial-UNIQUE + the `changeRequest.implement` grant-backfill (Process Owner + QMS Owner). NO new permission key
(R5), NO new event type (reuse `DCR_TRANSITIONED`), NO new enum value (`Implemented`/`Closed` exist since 0040).

**Slices.** **S-dcr-1** (DCR core + intake: `dcr` own-table mutable-state FSM + append-only `dcr_stage_event`
[`REVOKE UPDATE,DELETE`] + `DCR-{YYYY}-{SEQ}` 4-digit identifier + `domain/dcr/fsm.py` + `DCR_RAISED`/
`DCR_UPDATED`/`DCR_TRANSITIONED` events + `audit_object_type=dcr`; endpoints POST/GET `/dcrs`, GET/PATCH
`/dcrs/{id}`, POST `/dcrs/{id}/cancel`; migration `0040`). **S-dcr-2** (where-used/impact + assess:
`document_link` doc↔doc graph + CRUD + `GET /documents/{id}/where-used` [the §7.2 categories + the §7.3
`obsoletion_safety` advisory] + `impact_assessment` + `POST /dcrs/{id}/assess` [Open→Assessed, auto-populates
the 7 §5.3 dimensions] + `GET/PUT /dcrs/{id}/impact`; migration `0041`; obsoletion enforcement deferred to
S-dcr-5 per the addendum). **S-dcr-3** (diff: metadata + text redline [3a, zero-migration] + visual page-image
[3b, mig `0042`]). **S-dcr-4** (routing + approval, subject_type=DCR via the engine; mig `0043`). **S-dcr-5**
(implement/close + the shared-path obsoletion gate + the CAPA→DCR loop + the deferred cross-FK
`document_version.dcr_id` ↔ `dcr.resulting_version_id`; mig `0044`) — **CLOSES the DCR family.**

**Back-propagation:** 05 (§5.5 reject-loop → Open; §7.3 gate on the shared `document.obsolete`), 14 (§7 cross-FK
realized), 15 (§8.7 implement/close are explicit gated endpoints — superseding the "engine auto-drives" note),
16. Supersedes B5 (DCR dual-lifecycle).

---

### R41 — `drift.read` (S-drift-3): the second R38-additive catalog key

**Decision (owner, 2026-06-10).** The admin drift-status surface (`GET /admin/drift/status`,
`GET /admin/drift/superseded-copies`) is gated on a NEW SYSTEM-domain key **`drift.read`**
(`is_system_domain=true`, `sod_sensitive=false`, `sig_hook=false`, `finest_scope=SYSTEM`), seeded
in migration 0047 and granted to **System Administrator**. Riding `storage.read` was rejected:
that key is storage *config*, the D4 copies report isn't storage at all, and riding would silently
widen every storage-config reader's view. Per R38: additive only — no rename/removal; the catalog
count moves 98 → 99. The trailing S-web-8 UI gates on the same key. Related S-drift-3 owner forks
(spec §0): ONE `BLOB_INTEGRITY_FAILED` event type (classification in the payload); D1 cadence =
one daily rolling task (rotation = the periodic full set; `--full` CLI on demand); D4 is a live
read (no persisted scan).

---

### R42 — `document.distribute` (S-ack-1): the third R38-additive catalog key

**Decision (owner, 2026-06-10).** Distribution management (`POST /documents/{id}/distribution`,
`DELETE /documents/{id}/distribution/{entry_id}`) and the named per-user acknowledgement matrix
(`GET /documents/{id}/acknowledgements`) are gated on a NEW CONTENT-domain key
**`document.distribute`** (`is_system_domain=false`, `sod_sensitive=false`, `sig_hook=false`,
`finest_scope=ARTIFACT`), seeded in migration 0048 and granted to **QMS Owner**. Riding
`document.manage_metadata` was rejected for exactly the failure mode R41's reasoning names: an
ill-fitting ride silently widens every existing holder's reach — every metadata editor would gain
audience/issuance control, and deciding **who must read what** is a QM governance act, not a
metadata edit. Per R38: additive only — no rename/removal; the catalog count moves 99 → 100. This
resolves doc 15 §8.5's pre-existing dangling `document.distribute` reference (the key now exists;
the §8.5 row splits per the R43 back-propagation). The trailing S-ack-2 UI gates on the same key
(the distribution editor + the named matrix; Remind, when the notifications family delivers it,
rides it too).

---

### R43 — Acknowledgements family: MAJOR-only re-ack, the carry-forward boundary + the engine-task model (slice S-ack-1)

**Context.** Doc 04 §8 + R15 define the distribution / read-and-understood obligation surface, but
doc 04 §8.2's blanket "Re-release (new rev) creates NEW ack tasks" conflicts with doc 05
§2.2/§2.4/§5.3's MAJOR/MINOR significance posture and with the shipped, test-pinned DCR impact
contract (`reacknowledge_required = is_major`, `services/dcr/where_used.py`). The owner locked the
trigger model and the family's data + mechanism shape (design spec
`docs/superpowers/specs/2026-06-10-s-ack-acknowledgements-design.md` §0); this entry records the
as-built form.

**Decision:**
- **Re-acknowledgement is MAJOR-only with carry-forward satisfaction — superseding doc 04 §8.2's
  blanket re-trigger.** A user's obligation on a document is **satisfied** iff they hold an
  `acknowledgement` row on a version with `version_seq >= last_major_seq`, where `last_major_seq`
  = the newest `change_significance = MAJOR` seq ≤ the current Effective version's seq, **falling
  back to the LOWEST seq when the chain holds no MAJOR version** (a chain may legally start MINOR
  — the design premise "every chain starts MAJOR" was false in the built system;
  `domain/ack/rules.py`). A MAJOR release re-arms the whole audience; a MINOR release mints
  nothing and coverage carries forward. Ack rows stay strictly **version-pinned evidence** — only
  the satisfaction computation walks MINOR chains. This honors the engine the DCR contract
  promised, and completes R15: release and target-entry trigger families flow through ONE mint. This REFINES R15's "exclude already-acknowledged versions": "already acknowledged" now means satisfied under the carry-forward boundary — a target-entrant holding any acknowledgement at or above `last_major_seq` receives no task, even if they never acknowledged the current Effective version itself.
- **What an ack IS: its own append-only evidence row — never a `signature_event`** (R2 untouched;
  `document.acknowledge` stays `sig_hook=false`; doc 07 §6.3's non-sig-hook pipeline writes the
  audit event only). The `acknowledgement` table as built deliberately diverges from doc 14 §5.6:
  **+ `org_id`** (the §1.1 convention), **+ `document_id`** (coverage queries), **+
  `created_reason` enum(`release`,`target_entry`)** (doc 17's promised discriminator); **NO FK to
  `distribution_entry`** (entries are deletable config; the evidence must survive them);
  `client_ip` is **Text, not INET** (the value is attacker-controllable `X-Forwarded-For` input —
  an INET column would fail the evidence write on a malformed header); append-only via **DB
  `REVOKE UPDATE, DELETE`** (the `capa_stage`/`dcr_stage_event` house style — harder than doc 14
  §1.2's "App" enforcement). `UNIQUE(user_id, document_version_id)` is the idempotency backstop.
  `distribution_entry` is editable issuance config: `UNIQUE(document_id, target_type, target_id)`,
  grants `SELECT, INSERT, DELETE` only (change = delete + re-add); `distribution_target_type`
  carries all four doc-14 members (`user`,`org_role`,`process`,`folder`) but the API **422s
  `target_kind_deferred`** for `process`/`folder` until owner-assignment binding lands (an honest
  refusal, never a silently-empty audience).
- **Mechanism = workflow-engine tasks; ONE idempotent sweep is the universal mint**
  (`services/ack/sweep.py`, under `LOCK_ACK_SWEEP`): additive `DOC_ACK` task_type + subject_type;
  per-user instances off the seeded single-stage `doc_acknowledgement` definition (mode PARALLEL,
  quorum ANY, NO signature block). **Cancel-before-mint in ONE pass**: cancel = instance → the
  `CANCELLED` sentinel + PENDING tasks → `SKIPPED` (the S-dcr-4 inline force-terminate, with a
  fresh `populate_existing` locked load — the S-drift-1 identity-map trap); every cancel is
  audited (`STAGE_FAILED`, object_type=document, scope_ref=identifier, payload-discriminated
  `{"event": "ack_obligation_cancelled", "why": lapsed|ineligible}`). Triggers are threaded
  explicitly (`ack_sweep.delay(document_id, trigger)`): `release`/`release_due` →
  `created_reason=release`; everything else (distribution writes, the daily Beat catch-up) →
  `target_entry` (doc 17's discriminator, honestly stamped). Three post-commit lifecycle enqueues
  (release / per-doc `release_due` / obsolete) ride the `AckEnqueueSink` seam
  (Celery/Logging/Capturing trio); the daily Beat sweep (`easysynq.ack.sweep`) is the self-heal.
  **The sweep fail-closes on a missing `doc_acknowledgement` definition** — a logged no-op
  INCLUDING the cancel pass (an empty eligible-set would otherwise classify every obligation as
  lapsed → an org-wide mass-cancel on broken config).
- **The in-force predicate is `current_effective_version_id IS NOT NULL` — NOT
  `current_state == Effective`**: an UnderRevision/InReview/Approved document still governs (R1/T7
  — the prior Effective keeps governing), and keying on doc-state would mass-cancel obligations
  the moment a revision opens. Both the sweep's eligibility and the decide leg's lapse check use
  the pointer.
- **The decide leg** (the fourth `POST /tasks/{id}/decision` dispatch): membership
  **404-collapse** → outcome whitelist **`{acknowledge}`** (422 anything else) → engine
  `decide(_commit=False)` **with replay-parity** — an Idempotency-Key replay re-derives ids and
  returns 200 **bypassing the mutable key check** (a replay is not an act: the decision already
  committed, and membership — a 1-member pool — proves the caller IS its decider, so a
  since-lapsed grant must not 403 a legitimate retry) → fresh-path **`document.acknowledge`
  enforced at the document's scope** (the key's FIRST consumer; a missing key is a calm 403; the
  ResourceContext carries the document's `process_ids` so the seeded PROCESS-scoped Employee
  grant is PDP-reachable) + the obligation re-checks → **409 `ack_obligation_lapsed`** | **409
  `ack_superseded`** (pinned seq < the boundary; a 403/409 raise rolls the engine's uncommitted
  rows back, the task stays PENDING) → the `acknowledgement` INSERT + `DOCUMENT_ACKNOWLEDGED`
  (object_type=document, scope_ref=identifier) in ONE transaction. **No `signature_event`.**
  Bulk-ack (doc 10 §8.2) = the client loops this endpoint.
- **Coverage truth = distribution × acknowledgements** — never the tasks (the to-do surface only).
  The live audience = `user` targets ∪ `org_role` members (via `RoleAssignment.role_id`, ACTIVE
  non-guest only); `ACK_DUE_DAYS` (env, default 14) sets informational-only due dates (no
  escalation in v1). Snapshot fold: `acknowledgement_required` + the serialized entry list are
  frozen into `document_version.metadata_snapshot` at check-in (doc 04 §6.1), deliberately
  EXCLUDED from the redline's SNAPSHOT_FIELDS in v1 (S-ack-2's call).
- **Deferred (named, not faked):** Remind + reminder history (the notifications family); the doc
  13 §6.3 report (v1.x); `process`/`folder` target resolution (owner-assignment track); the
  org-wide PDCA rollup endpoint (the dashboard slice); a compliance-checklist ack leg
  (deliberately NOT added — doc 13 §3.1's leg list omits acks); bulk re-acknowledge (v1.2); the
  every-release re-ack org config flag (v1.x); the delegation carve-out — **DOC_ACK is never
  delegable** (a personal awareness attestation; recorded here for the delegation family to
  inherit); ack retention/GDPR posture (`client_ip` is PII-adjacent and the table holds no
  retention class — the next R27 pass); the seeded Employee role's PROCESS-scoped
  `document.acknowledge` grant: the decide leg's ResourceContext now populates `process_ids` from
  the document's process-links (the PDP reach is wired — Codex P1); what stays deferred is only
  the owner-assignment *binding* default (the seeded `:assignment_process` placeholder
  resolution) — until it lands, v1 rides SYSTEM overrides (the standing pattern), bound by the
  owner-assignment track together with the deferred `process`/`folder` targets.

**Implemented in slice S-ack-1 (migration `0048`):** `distribution_entry` + `acknowledgement` +
`documented_information.acknowledgement_required` + the additive `DOC_ACK` /
`DOCUMENT_ACKNOWLEDGED` / `DISTRIBUTION_UPDATED` enum values + the R42 key seed + the
`doc_acknowledgement` workflow seed. The contract gained the DOC_ACK enums, the `acknowledge`
outcome, 3 paths (4 operations) + 5 schemas (and closed a pre-existing `DecisionResult` additionalProperties gap).

**Back-propagation:** 04 (§8.2 reconciliation note; §12 key parenthetical), 08 (§10.1 spelling),
10 (§8.4 MAJOR-only note), 13 (§6.3 status note), 14 (§5.6 as-built note; §7 enum members), 15
(§8.5 split; §8.8 non-sig-hook carve-out), 16 (v1 row).

---

### R44 — Quality Objectives family (clause 6.2) — slice S-obj-1

**Decision (owner, 2026-06-11).** A Quality Objective is a maintained Document (kind=DOCUMENT
shared-PK subtype of `documented_information`, document_type `OBJ`) per R3 — the `form_template`
precedent — so the commitment (title, target, direction, due date) is versioned and approved
through the existing vault lifecycle, while the operational `current_value` is a mutable rollup
rolled OUTSIDE the version by append-only `KPI_READING` evidence records (`target_at_capture`
frozen at capture, never rewritten). Each measurement is an ad-hoc `KPI_READING` record
(`capture_record(..., _commit=False)` — no `source_document_id`, the `capture_complaint`
precedent; pinning a non-FRM source triggers the R21 422, and a Draft objective has no version to
pin) with a `kpi_measurement` projection; recording one rolls `current_value` up under
`FOR UPDATE` + `populate_existing` (the S-drift-1 stale-identity-map trap).

On/off-target is **direction-aware + amber-banded**, computed at read from the pure
`domain/objectives/rules.py` (`rag_status` → `green`/`amber`/`red`/`unmeasured`), **never
stored** (N9 — against a rule; N6 — no SPC/forecast). `direction` (`HIGHER_IS_BETTER` /
`LOWER_IS_BETTER`, the `objective_direction` enum) + `at_risk_threshold` (nullable) +
`baseline_value` are added versus the original doc-14 spec; `owner_user_id` is the BASE
`documented_information.owner_user_id` (not duplicated on the satellite).

The Quality Policy is the R25 singleton (already-seeded `POL` document_type);
`objective.policy_id` records the consistency link (a validation hint, validated by the service
against the current Effective POL — doc 02 §502). Each objective auto-maps to clause `6.2` at
create (a `clause_mapping` insert; standard `_objective_scope` PROCESS resolver + SYSTEM
fallback).

The family **rides the already-seeded `objective.*` / `kpi.*` keys** (PROCESS finest-scope;
seeded + granted to QMS Owner in `0004_seed_authz.py`) — **no new permission key, catalog stays
100, no R38 change**. Audit: lifecycle events reuse existing `DOCUMENT_*` types; new acts emit
additive `OBJECTIVE_MEASUREMENT_RECORDED` / `OBJECTIVE_PLAN_ADDED` / `OBJECTIVE_PLAN_REMOVED`
event types with `object_type='document'` + `scope_ref=<identifier>` (R39 reuse — no new
`audit_object_type`). No new `SignatureMeaning` (R2 closed). **Migration `0049`.**

**Implemented in slice S-obj-1 (migration `0049`):** `quality_objective` subtype + `objective_plan`
action rows + append-only `kpi_measurement` projection (REVOKE UPDATE,DELETE) + the
`objective_direction` enum + the three additive `OBJECTIVE_*` event types + the `OBJ` document_type
seed. `/objectives` router (create/list/get/measurements/plans CRUD/scorecard). `_objective_scope`
PROCESS-level authz resolver. 24 unit + integration tests.

**Back-propagation:** 14 (§6 as-built quality_objective/objective_plan/kpi_measurement note), 16
(PDCA dashboard now buildable — objectives + acks both landed).

---

### R45 — Management Review family (clause 9.3) — slice family S-mr

**Decision (owner, 2026-06-12).** A Management Review is a maintained **Document** (kind=DOCUMENT
shared-PK subtype of `documented_information`, document_type `MR`) — the S-obj-1/R44 Quality-Objective
precedent. **This is a deliberate, register-sanctioned deviation from doc-14 §9's RECORD
classification:** only a released *document* earns the `current_effective_version_id` the compliance
checklist counts (`services/reports/checklist.py:84`), so the DOCUMENT path is the only one that flips
the **9.3 ★** node COVERED with zero checklist code. The convened review's **minutes** (the
auto-compiled 9.3.2 inputs *as-of* + the 9.3.3 decisions/outputs) **freeze into the version snapshot at
submit** — the S-obj-3 recipe verbatim: `rfc8785.dumps` the JSON-safe dict (hashed **bare, no
preamble**) → `finalize_worm` an `application/json` source blob (lands `no_controlled_rendition`, R26)
→ the SAME dict into `metadata_snapshot.mgmt_review_minutes` via a one-kwarg `_snapshot` fold. The
document then rides the unchanged submit→approve→release machinery; **release** files the minutes as
the 9.3.3 retained record and flips 9.3 COVERED. The operational lifecycle (the doc-10 §7
Scheduled→…→Closed cycle) maps onto the authoring FSM + a mutable `close_state` (`ActionsTracked` →
`Closed`) on the satellite, outside the version.

**Sign-off** rides the standard `document.approve` / `document.release` (signed `meaning=approval` /
`release` — **no new `SignatureMeaning`**, R2; SoD-2 submitter≠approver≠releaser binds because the
submitter authored the frozen version); `attendees` jsonb is the informational roster only (a dedicated
Top-Management approval routing is deferred). **Outputs** are append-only `review_output` rows; an
`ACTION` output (owner + due) spawns one `MR_ACTION` task at release on a `MGMT_REVIEW`
workflow_instance (the S5 direct-insert; **a per-action `stage_key` "action:&lt;output_id&gt;"** so the
engine's distinct-approver guard never spans two actions of one owner). The new `MGMT_REVIEW`
`/tasks/{id}/decision` dispatch leg (`decide_mr_task`, the `decide_periodic_review` mirror;
404-collapse non-membership) flips the task DONE. The **review-close gate mirrors `_audit_close_gate`**
(pure `output_blocks_close`, fail-closed: an ACTION whose task is None/not-DONE blocks; a DECISION never
blocks; the loader OUTERJOINs the spawned task) — 409 `review_close_blocked` until every action is DONE.

**Input compilation** (`compile-inputs`, Draft-only) runs each of the six live org-wide reads
(objectives scorecard, audits, CAPAs/NCRs/complaints, KPI readings, compliance-checklist+overdue, drift)
gated on the review **owner's** grants (the deterministic-pack F3 choice — `gather_grants`+`authorize`
direct, the non-auditing PDP path), fail-closed per source → a `available=false` gap row, **never a
403**; the four sourceless 9.3.2 inputs + risk + improvement are honest gap rows. **Cadence** is a coded
default (`system_config.mgmt_review_cadence_months`=12; org-config later) driving a daily Beat sweep
(`sweep_mgmt_reviews`, mirroring `sweep_reviews` — advisory-lock single-flight, org-scoped
`open_review_exists` idempotency, NULL-owner + mis-seed honest degrades) that mints the next Scheduled
Draft MR + an `MR_INPUT` task.

The family **rides the already-seeded `mgmtReview.read`/`create`/`record_outputs` keys** (SYSTEM
finest-scope; seeded + granted to QMS Owner in `0004_seed_authz.py`) — **no new permission key, catalog
stays 100, no R38 change**; `record_outputs` is `sig_hook=False` (recording outputs mints no signature —
R43) with its `sod_sensitive=True` flag documentary-only (no engine path). Audit: lifecycle reuses
`DOCUMENT_*`; new acts emit additive `MGMT_REVIEW_INPUTS_COMPILED` / `_OUTPUT_RECORDED` /
`_ACTION_SPAWNED` / `_CLOSED` with `object_type='document'` + `scope_ref=<identifier>` (R39 reuse — no
new `audit_object_type`). Imported legacy minutes remain kind=RECORD type `MGMT_REVIEW` (evidence; do
not flip the ★) and coexist. **Migration `0050`.**

**Implemented in slice S-mr-1 (migration `0050`):** `management_review` subtype + append-only-by-posture
`review_input` / `review_output` child tables + the `review_input_type` (12) / `review_output_type` /
`management_review_close_state` enums + the four `MGMT_REVIEW_*` event types + the `MR` document_type
seed + the `management_review` workflow_definition seed + the `system_config` cadence/owner columns.
`/management-reviews` router (create/list/detail/compile-inputs/outputs CRUD/submit-review/approval/
release/close/meta-PATCH). The 9.3-★-COVERED-on-release headline proven in integration. diff-critic
MAJOR fixed (the per-action stage_key). **Deferred (named, not faked):** the four sourceless inputs +
risk/opportunity (e) + `improvement_initiative` (f) as gap rows; the CAPA `review_output` un-reserve +
the DCR `mgmt_review` link → slice-2 (the `spawned_capa_id`/`spawned_initiative_id` columns ship
reserved-null); the rendered Management-Review-Pack PDF → v1.1; a dedicated Top-Management approval
routing; MR commitment **revision** (first-release-only in v1, the S-obj-3 posture); the trailing
**S-mr-2** UI + the Home "next review in N days" widget.

**Back-propagation:** 02 (9.3 as-built — DOCUMENT subtype + the RECORD-deviation note), 06 (the
MGMT_REVIEW record type is now the imported-minutes path; authored review = an `MR` document), 07 §3.7
(`mgmtReview.*` now reach a resource), 10 §7 (the as-built lifecycle = authoring FSM + a `close_state`
tail; the input compiler; the close gate), 13 §5.2 (the MR dashboard backed by real data — **chart
vocabulary restated to calm tables/RAG**; the pack is v1.1), 14 §9 (as-built tables + the DOCUMENT-head
deviation + the reserved `spawned_*` seams), 15 (the `/management-reviews*` endpoints), 16 (the ★ spine
feature-complete).

---

### R46 — Improvement Initiatives family (clause 10.3) — slice family S-improvement

**Decision (owner, 2026-06-15).** An Improvement Initiative is an **own-table mutable-state workflow
object** (the DCR / R22 doctrine) — NOT a `kind=RECORD` immutable artifact and NOT a
`documented_information` subtype. This is a **deliberate, register-sanctioned deviation from doc 02's
RECORD (`M/R='R'`) classification:** clause 10.3 is **non-★** (the frozen clause seed carries
`is_mandatory_star=False`; the compliance checklist only scans `is_mandatory_star=True` clauses), so
there is **no ★ node to flip** — the very R44/R45 rationale that justified routing OBJ/MR through the
`kind=DOCUMENT` path is **absent** here. An initiative's essence is a *progressing activity owned over
time* (a moving `stage`), not point-in-time immutable captured content, so the own table is the
right-fit shape; copying the DOCUMENT path by blind analogy would be a category error. The mutable
`stage` is the headline; the append-only `improvement_initiative_stage_event` trail (REVOKE
UPDATE,DELETE — the `dcr_stage_event`/`capa_stage` precedent; no `updated_at`) is the immutable
history. Lifecycle (R46 §F2) is the **simple stage-completion close, unsigned**: `Open → InProgress →
Completed → Closed` (+ `Cancelled` from the pre-completion states only); the `Closed` transition MAY
freeze a free-text realized-benefit note into the sealed `stage_event.payload` (the lightweight 10.3
continual-improvement evidence). **No signed gate, no effectiveness loop, no new `SignatureMeaning`**
(R2 closed); the `stage_event.signed_event_id` Part-11 hook ships day-one but stays NULL/unsigned in
v1.x (D3). An initiative is purely operational PostgreSQL state — never a Released version, never the
mirror, no blob / disposition / WORM-destroy path (D2).

**Two additive (R38) CONTENT-domain permission keys**, seeded in migration `0052`:
`improvement.read` and `improvement.manage` (both `is_system_domain=false`, `sod_sensitive=false`,
`sig_hook=false`, `finest_scope=PROCESS`). Granted to **QMS Owner** (read + manage, org/QMS scope) and
**Process Owner** (read + manage, PROCESS-scoped via the `:assignment_process` placeholder — rides
SYSTEM overrides until owner-assignment binding lands); **Internal Auditor** gets read only (the
checklist-read precedent — the auditor raises OFIs and reads the improvement pipeline but does not
drive initiatives). **Riding `capa.*` was rejected** (the R41/R42 anti-pattern): it conflates
corrective action (10.2) with improvement opportunity (10.3) and silently widens every CAPA holder's
reach. Per R38: additive only — no rename/removal; the catalog count moves **100 → 102**.

**Spawn cardinality (F5):** **1:N one-way `source_link_id`** on the initiative (the polymorphic
origin id — a `finding.id` for `source=OFI`, a `review_output.id` for `source=review`, NULL for
`manual`); a per-(org, source_link_id) `spawn_idempotency_key` partial-UNIQUE makes a retry return the
same initiative. `review_output.spawned_initiative_id` is **left reserved-null** (not dropped) — this
closes the S-mr-3 deferral (the seam now has a table to point at) without un-reserving the latch.
Audit: the initiative's acts emit additive `INITIATIVE_RAISED` / `INITIATIVE_UPDATED` /
`INITIATIVE_TRANSITIONED` on a fresh `audit_object_type='improvement_initiative'` (an own-table id is
not a record id — the `ncr`/`dcr` precedent; `scope_ref=<identifier>`); the slice-2 MR-side spawn adds
`MGMT_REVIEW_INITIATIVE_SPAWNED` on `object_type='document'`. **Migration `0052`.**

**Implemented in slice S-improvement-1 (migration `0052`):** `improvement_initiative` own table
(mutable `stage`) + the append-only `improvement_initiative_stage_event` trail + the
`improvement_stage` / `improvement_source` enums + the four `INITIATIVE_*`/`MGMT_REVIEW_INITIATIVE_*`
event types + `audit_object_type='improvement_initiative'` + the two `improvement.*` keys & role
grants. `domain/improvement/fsm.py` (the pure edge map) + `services/improvement` (create / transition
/ update / list / get / stage-events; `_improvement_scope`). `/improvement-initiatives` router (the 6
lifecycle endpoints). All spawn-seam columns ship now so **slice 2 (the OFI-finding + MR-output spawn)
was zero-migration**.

**Implemented in slice S-improvement-2 (zero-migration; PR #182):** the two spawn endpoints —
`POST /findings/{finding_id}/raise-initiative` (OBSERVATION/OFI → `source=OFI`; 422
`finding_not_improvable` on an NC, 409 `finding_superseded` on a corrected finding; inherits the
audit auditee process) + `POST /management-reviews/{review_id}/outputs/{output_id}/raise-initiative`
(ACTION/IMPROVEMENT → `source=review`; 422 `output_not_improvable` on a DECISION, 409
`review_not_tracking`; emits `MGMT_REVIEW_INITIATIVE_SPAWNED`). Both are 1:N + optional
`Idempotency-Key` (201 new / 200 replay; the replay re-authorizes the **stored** initiative scope) and
mint no `signature_event` (R43); `review_output.spawned_initiative_id` stays reserved-null (un-reserving
the reciprocal latch is a future `0053` owner call).

**Deferred (named, not faked):** the
register/drawer/PDCA-ACT tile UI (S-improvement-3); the optional unsigned **Verified** benefit-review
stage and/or an engine-routed management-authorization approval (S-improvement-4, opt-in); discrete
`improvement_initiative_action` milestone rows; objective-miss / 9.1.3 auto-seed.

**Back-propagation:** 02 (10.3 as-built — own-table workflow object + the RECORD-deviation note), 07
§3 (`improvement.*` now reach a resource), 14 §9 (the last named-but-unbuilt entity is now built —
as-built `improvement_initiative`/`_stage_event` tables + the reserved `source_link` seam), 15 (the
`/improvement-initiatives*` lifecycle + the `raise-initiative` spawn endpoints, §8.12b), 16 (clause 10
fully addressed — 10.2 CAPA + 10.3 initiatives).

---

### R47 — `clauseMap.read` for the Process Owner role (S-records-C): an R38-additive role grant

**Decision (owner, 2026-06-19).** The seeded **Process Owner** role gains a **SYSTEM-scoped
`clauseMap.read`** grant (migration `0057`) so a bound Process-Owner can read the org-wide ISO clause
map (`GET /clauses`) — the create-in-process wizard's clause step. This is the **most conservative**
R38-additive change yet: it adds **no permission key** (`clauseMap.read` already exists — seeded in
`0004`, held by QMS Owner / Internal Auditor), only a **new role grant** on an existing key, so the
catalog count **stays 102**. The migration inserts the grant for **every** org's Process Owner role
(by role name, idempotent `on_conflict_do_nothing`), reaching a renamed install (e.g. `AHT`), not just
the `DEFAULT` org `0004` seeds.

**The grant alone is insufficient — a companion resolver fix is part of this decision.** A bound
Process-Owner's `role_assignment` carries a PROCESS `bound_scope` (the owner-assignment mint), and
`services/authz/repository._grant_from_role` resolved **every** grant of that assignment through the
bound_scope (`bound_scope or scope_template`), which would clamp the new SYSTEM `clauseMap.read` down
to PROCESS — unsatisfiable against the SYSTEM clause-map resource. The fix: a **SYSTEM-level
`scope_template` is never clamped by a `bound_scope`** (a SYSTEM-finest grant carries no
`:assignment_process` placeholder to concretize); parameterized (PROCESS/FOLDER/…) templates still
defer to the bound_scope exactly as before. Verified blast radius = precisely this case — every
pre-existing bound assignment uses `bound_scope={"level":"SYSTEM"}` (unaffected), and the only
PROCESS-bound role today (the owner-assignment Process Owner) had no SYSTEM grant before this slice.

**Back-propagation:** none required (07 already lists `clauseMap.read`; this records the new
Process-Owner holder + the resolver semantics). Closes the named "records process-scope read" arc's
final functional gap (the wizard's clause step for a bound Process-Owner; the picker landed in
S-process-scope-2).

---

### R48 — `PROCESS`-grant descendant inclusion is v1.x-deferred; v1 is own-id-only (reconcile)

**Decision (owner, 2026-06-19).** The authorization spec (07 §5.1 table, §5.3, the §9.1 Diego persona
row) and 15 §9.2 described a `PROCESS` grant as **optionally including descendants** via an
`include_subprocesses` flag (designed **default `true`**). The **v1 PDP does not implement this**:
`domain/authz/pdp.py` `_matches_scope` matches a PROCESS grant purely on
`bool(scoped & resource.process_ids)` — an **own-id intersection** that never walks
`process.parent_id`. Every PROCESS-scoped surface built to date is consistent with own-id-only:
owner-assignment (slice S-owner-assignment-1) mints an explicit `process_ids` set; `_document_scope`, the
records resolver (`services/records/repository.record_process_ids*`), search, and the
`GET /processes`/`/map` row-filter all match on own-id / own-link sets. This register entry
**reconciles the docs to the code**: in **v1 a `PROCESS` grant covers only its own `process_id`**, and
**descendant (subprocess) inclusion is v1.x-deferred**. (No code change — `pdp.py` is already the
authority; the integration test `test_process_owner_list_hides_unreadable_parent_id` already encodes
and asserts own-id-only behavior, where a bound child-owner does not reach a resource via the parent.)

**Why deferred, not removed.** Descendant inclusion is **not a single-surface fix** — it is a
cross-cutting authz-model change that must land identically everywhere a PROCESS scope is matched
(documents, records, processes, CAPA, search) or the scope model becomes inconsistent and exploitable.
A spec-of-the-fork (S-include-subprocesses) confirmed the trap concretely: the naive implementation
(expand a PROCESS grant's `process_ids` to the `parent_id` subtree at the `gather_grants`
grant-resolution chokepoint) would **silently defeat every per-target write re-auth guard**
(`_enforce_target_process_record`, `_enforce_target_process`, the per-process `capa.create` gate),
because each re-enforces against a single literal target process id that the expanded grant would now
intersect — re-opening exactly the non-converging records/CAPA **write-escalation** surface the
records-process-scope arc (S-records-W) deliberately trimmed to broad-only. A faithful `default true`
flip would also **silently widen every existing bound Process-Owner grant** at once (no grant carries
the flag today). The multi-standard / hierarchy-following intent is therefore **retained in the spec**
(marked v1.x), not deleted.

**The better long-term design, if v1.x ever implements it.** Mirror how `FOLDER` already achieves
descendant inclusion with **zero per-check walk** (R6): the ltree `folder_path` carries the hierarchy
as a string, so the PDP's subtree-prefix test needs no DB traversal. The process analogue is a
**materialized ltree ancestry column** on `process` (org-rooted node-id path, maintained on
insert/reparent), letting the PROCESS branch mirror the FOLDER branch — but that is its own slice (a
migration + backfill + reparent-rewrite + a resource-side path on every `ResourceContext` builder) and
its own register decision, **not** an on-the-fly grant-side expansion.

**Scope of the reconcile (docs only; no migration, no new key, no behavior change).** 07 §5.1 PROCESS
selector cell + §5.3 Processes bullet + the §9.1 Diego "(+subprocs)" bound-scope cell; 15 §9.2 "Scope
inheritance" bullet. The data-model entries describing `process.parent_id` as a self-FK (14 / the ERD
`parent/subprocess` edge) are **factual schema** (a process *can* nest) and are **unchanged** — the
nesting exists; only its **authz-grant inheritance** is deferred.

**Back-propagation:** none beyond the four doc edits above. Raised by Codex CX-3 on S-process-scope-2,
named as a non-goal in the records-process-scope spec
(`docs/superpowers/specs/2026-06-19-records-process-scope-authz-design.md` §7) and the
S-process-scope-2 slice-history entry — both stay as historical record; this entry makes it a binding
decision.

---

### R49 — Risk & Opportunity register family (clause 6.1) — slice family S-risk

**Decision (owner, 2026-06-19).** The clause 6.1 Risks & Opportunities register is the **first register
family** in EasySynQ and is modeled as a **maintained controlled Document** (per the registers-as-Documents
doctrine — see *citation note* below): one `documented_information` with `kind=DOCUMENT`, a **new
`document_type` code `RSK`**, `is_singleton` (one non-Obsolete head per org, enforced by a partial-unique
guard beyond the Effective-only `uq_doc_info_singleton_effective`), holding many **`risk_opportunity`
satellite rows** (`id` PK + `register_doc_id` FK + `row_version` — a register-**row** satellite, **not** a
shared-PK subtype like `quality_objective`, because a register has many rows per head). The rows **are the
version's controlled content**, edited **through FSM revisions** (`start_revision` → edit the satellite
while Draft/UnderRevision → publish/release supersedes), **read-only while Effective**; live reads resolve
against the **governing Effective version** (the `form_template`/objectives working-copy precedent). The
register-document follows the **lightweight approval profile** (`04 §4.4`). This sets the pattern the future
Context (4.1) / Interested Parties (4.2) registers reuse; this slice ships **risk only**.

**Why a non-★ clause is Document-backed.** Clause 6.1 is **non-★** (`is_mandatory_star=False`,
`iso9001_clauses.py`) — yet it is Document-backed, unlike `improvement_initiative` (clause 10.3, also
non-★, own-table per R46). The discriminator is **register vs progressing-activity**, not ★-vs-non-★: ★
*forces* the Document shape for a mandatory-DI clause (6.2/9.3 flip a checklist node); a **register** — a
maintained controlled list — is bound to the Document lifecycle by `04 A3` + the doc-14 §0 finding-R3
("Both, layered") **regardless of ★**. An *activity* that progresses through stages (improvement, DCR) is
own-table; a *maintained register* is Document-backed.

**⚠ R-citation note (do not propagate the mislabel).** `14 §6/§1.2/§328` and the S-obj-1 spec cite "R3"
for the registers-as-Documents doctrine, but the **published R3** (this register) is *Authorization
precedence (deny-wins)*. The registers-as-Documents doctrine is the **doc-14 §0 gap-audit finding-R3**
("Both, layered", `14:582`) + `04 A3` — **not** a numbered resolution. R49 restates the doctrine in its own
text and cites `14 §0/§6` + `04 A3`.

**Scoring (R18-reconciled).** `scoring_method` enum (v1 sole value `5x5_matrix`, a forward-compatible
append-only enum); `likelihood`,`severity` ∈ 1..5; `risk_rating = likelihood × severity` ∈ 1..25 **stored
numeric**, derived by a **pure rule** (`domain/risk/rules.py`) and **re-derived on every write** (never
client-supplied); a 4-band RAG (Critical/High/Medium/Low + Not-yet-measured) over the numeric reusing the
objectives `green`/`amber`/`red`/`unmeasured` vocabulary + the ✓◔✕○ glyph canon. **Derive-and-freeze (the
S-obj-freeze analogue):** the band is graded against the **governing version's frozen criteria** (pinned in
`document_version.metadata_snapshot.risk_register.criteria` at publish — load-bearing for the live read,
the `resolve_commitment(governing)` precedent), **not** live code, so a band-threshold change cannot
retroactively re-grade history or the live register; a **golden test** pins each `scoring_method`'s code
criteria (forcing the mint-a-new-value path); `scoring_method` is **write-once** (a re-score recomputes
`risk_rating` + emits a `RISK_RESCORED` audit). No per-row `*_at_capture` column (the version snapshot pins
the basis) and **no backfill** (greenfield — NOT-NULL from day one).

**Permissions — ride the seeded `register.*` (no new key).** The risk **rows** gate on the already-seeded
`register.read` / `register.manage` (`0004:97-98`, `finest_scope=PROCESS`) — **catalog stays 102, no R38
catalog change**; the register **Document** lifecycle rides `document.*`. One **R38-additive *grant*** of
`register.manage` to the **Process Owner** role (migration `0058`) lets bound owners maintain risks in their
own process. **⚠ The grant MUST use the `_PROCESS_SCOPE` template** (`{"level":"PROCESS","selector":
{"process_id":":assignment_process"}}`), **NOT** `_SYSTEM_SCOPE`: a SYSTEM scope_template is *exempt* from
`bound_scope` clamping (`services/authz/repository.py:53`) and would match every process — the
`clauseMap.read`/S-records-C grant was SYSTEM only because *its* resource is org-level, the opposite case.
ADMIN gets no `register.*` (AZ-INV-6). The 3→2 reconcile of `15 §8.10b`'s aspirational `risk.read`/`risk.create`/`risk.update`:
`risk.read → register.read`, `risk.create` + `risk.update` → `register.manage` (a deliberate coarsening,
losing the create-vs-update split).

**Process-scope (R48 own-id-only).** Risk rows carry `process_id`; `GET /risks` is a filter-not-403 row-filter
(per-row `authorize` over `process_ids={row.process_id}` only — **no `artifact_id`** to avoid a shared-head
leak; `source_ip` threaded; SYSTEM matches all). Writes re-enforce the **target** process (`POST` over
`body.process_id`; `PATCH`-reassign over the new target — the `_enforce_target_process` discipline). Own-id
only; **no `parent_id` walk** (R48). The head carries **zero `ProcessLink`s** (else a PROCESS `document.*`
grant would match the org head).

**CAPA & events.** Risk→CAPA one-click via `risk_opportunity.linked_capa_id`, idempotent under a **`FOR
UPDATE` lock on the risk row** held across check-then-spawn (the R16 complaint precedent — the latch is the
lock, not a UNIQUE); an additive **`CapaSource.risk`** enum value (`ALTER TYPE … ADD VALUE`). **No new**
`SignatureMeaning` (R2), `audit_object_type`, or sig-hook; additive `RISK_*` event types only.

**Migration `0058`** (creates `risk_opportunity`, seeds `RSK` `document_type` + the single-head guard, adds
the `register.manage`→Process-Owner grant). **Deferred (named, not faked):** doc-10 `subject.risk_rating`
workflow routing (the routing subject is the document but `risk_rating` is on the row — no resolver);
`include_subprocesses` descendant scoping (R48); a register-steward role/UI for the org-head lifecycle (v1
rides a SYSTEM override); the Context 4.1 / Interested Parties 4.2 registers (pattern set, not built);
finer `register.*` create-vs-update SoD.

**Validation.** Spec-first (`docs/superpowers/specs/2026-06-19-s-risk-register-design.md`) with a 5-lens
adversarial refute panel that found and drove the fix of a real WORM-safety flaw (a freely-editable working
satellite — withdrawn for the strict controlled-document model) and a real re-grade flaw (live-code band
grading — fixed to governing-snapshot resolution) **before any migration**.

**Back-propagation** (staged with the slices): `02` (6.1 register), `04` (`§4.5`→`§4.4` lightweight-profile
pointer fix; `RSK` in the hierarchy), `07` (`register.*` now reach a resource), `10` (routing deferred),
`13` (high-risk dashboard), `14` (as-built `risk_opportunity` + the stale `kpi_measurement` 0055-columns
row fix), `15` (`/risks` endpoints; `§8.10b` gate mapping `risk.*`→`register.*`), `16`, `18`.

---

### R50 — Context register family (clause 4.1) — slice family S-context

**Decision (owner, 2026-06-20).** The clause 4.1 "Context of the organization" register is the **second
register family** (after R49's Risk & Opportunity register), modeled — per the registers-as-Documents
doctrine (doc-14 §0 finding-R3 "Both, layered" + `04 A3`, NOT a numbered resolution; the R49 *citation
note* applies verbatim) — as a **maintained controlled Document**: one `documented_information` with
`kind=DOCUMENT`, a **new `document_type` code `CTX`**, `is_singleton` (one non-Obsolete head per org),
holding many **`context_issue` satellite rows** (`id` PK + `register_doc_id` FK + `row_version` — a
register-**row** satellite, the R49 shape, **not** a shared-PK subtype). The rows **are the version's
controlled content**, FSM-revision-edited then frozen into an immutable version at publish; the lifecycle
(start-revision / publish / freeze / release) ships **in the same slice** as the core (core+lifecycle
together — the owner fork; the risk family split it 1 → 1b).

**Content model (enriched — the owner fork).** The contracted `14 §6` minimum is `classification`
enum(`internal`,`external`) + `description`; the v1 as-built **enriches** it with `category`
enum(`strength`,`weakness`,`opportunity`,`threat`) — the optional **SWOT** axis (NULLABLE) — `status`
enum(`active`,`closed`) (a new issue is always `active`; retire by closing, never delete), and
`last_reviewed_at`. `classification` is the ISO clause-4.1 spine (the standard mandates external/internal
issues). The enum value tuples are **golden-pinned** (`tests/unit/test_context_register_content.py`) and
**append-only** — a PESTLE extension mints NEW values, never re-letters (so a frozen published row is never
silently re-interpreted; the R49/S-obj-freeze discipline applied to a categorical axis). **No
computed/graded axis** (SWOT + status are user inputs, not a derived band), so — unlike risk — there is
**no `criteria` block, no `resolve_criteria`, no derive-and-freeze**: the frozen content is purely the rows.

**Org-level (NOT process-scoped) — the owner fork.** Clause 4.1 context is strategic and org-wide (the
standard's own examples: legal/market/cultural environment; org values/knowledge), so `context_issue`
carries `org_id` but **no `process_id`** (deliberately unlike `risk_opportunity`, matching the `14 §6`
contract). Authz rides the **already-seeded `register.read` / `register.manage`** keys at the **SYSTEM**
scope — **catalog stays 102, NO new key, NO new role grant**: the QMS Owner holds `register.*` @ SYSTEM
(the steward), and the `0058` Process-Owner `register.manage` @ PROCESS grant matches no (process-less)
context row. `GET /context` is filter-not-403 (a no-grant caller → 200 + empty); `GET /context/{id}`
enforces `register.read` @ SYSTEM; `POST`/`PATCH` + the steward acts enforce `register.manage` @ SYSTEM;
release is `document.release` + SoD-2 (a SYSTEM override in v1). ADMIN gets no `register.*` (AZ-INV-6).

**Reservation (generalized).** R49's `reject_rsk_register_mutation` head guard is generalized to
`reject_managed_register_mutation`, keyed by a `_MANAGED_REGISTERS = {RSK, CTX}` `document_type`-code map
(per-code message; the RSK behavior + error code `risk_register_managed_via_risks` byte-identical) — the
CTX head is reserved from EVERY generic document mutation (metadata/distribution/links + link-target,
clause-mapping, obsoletion at the `lifecycle.obsolete` CHOKEPOINT, DCR `_resolve_target`, and import) with
error code `context_register_managed_via_context`, exactly as the RSK head is (D-3b, now covering CTX). The
CTX head carries ZERO ProcessLinks (else a PROCESS `document.*` grant would match the org head).

**Interested Parties (clause 4.2) is a SEPARATE register** (its own `interested_party` head + table + slice
`S-interested-parties-1`), per the `14 §6` two-table contract — **not** typed rows in one head.

**Migration `0060`** (creates `context_issue` + the 3 enums + the `CTX` `document_type` + the additive
`CONTEXT_ISSUE_UPDATED` event type; NO role grant, NO new permission key).

**Deferred (named, not faked):** the read consumers (`S-context-2` — `GET /context/summary` + the
Management-Review 9.3.2(b) context-change input; the `governing_register` helper is the seam); the FE
(`S-context-fe`); the Interested-Parties 4.2 register (`S-interested-parties-1`; the contract omits
`org_id` — an editorial gap to add when built); the no-edit re-publish dedup integration path (covered at
the unit level via `register_needs_freeze`); the carried-over server-computed `can_release`/`can_manage`
capability on `GET /risks/register` (+ `/context/register`).

**Validation.** Spec-first (`docs/superpowers/specs/2026-06-20-s-context-register-design.md`); the
architecture is settled (R49 set the register-as-Document pattern), so 4 owner decisions were surfaced via
AskUserQuestion before any code (content model, keys, 4.2-separate, scope).

**Back-propagation:** `02` (4.1 ContextRegister as-built), `07` (`register.*` now reach the org-level
context resource), `14` (as-built `context_issue` — note the enriched `category`/`status`/
`last_reviewed_at` additions beyond the minimal contract), `15` (`/context` endpoints).

---

### R51 — Interested Parties register family (clause 4.2) — slice family S-interested-parties

**Decision (owner, 2026-06-21).** The clause 4.2 "Understanding the needs and expectations of interested
parties" register is the **third register family** (after R49 Risk & Opportunity and R50 Context) and the
second half of clause 4, modeled — per the registers-as-Documents doctrine (doc-14 §0 finding-R3 "Both,
layered" + `04 A3`; the R49/R50 *citation note* applies verbatim) — as a **maintained controlled Document**:
one `documented_information` with `kind=DOCUMENT`, a **new `document_type` code `IPR`**, `is_singleton` (one
non-Obsolete head per org), holding many **`interested_party` satellite rows** (`id` PK + `register_doc_id`
FK + `row_version` — a register-**row** satellite, the R49/R50 shape, **not** a shared-PK subtype). The rows
**are the version's controlled content**, FSM-revision-edited then frozen into an immutable version at
publish; the lifecycle (start-revision / publish / freeze / release) ships **in the same slice** as the core
(core+lifecycle together — the R50 fork; `S-interested-parties-1`). A near-byte-identical clone of the
Context register (R50).

**Content model (enriched + influence — the owner fork).** The contracted `14 §6` minimum is `party_name` +
`needs_expectations`; the v1 as-built **enriches** it with `party_type`
enum(`customer`,`regulator`,`supplier`,`employee`,`owner`,`community`,`partner`) — the ISO clause-4.2 spine
(NOT NULL; the relevant interested-party category) — `influence` enum(`low`,`medium`,`high`) — the optional
relevance axis (NULLABLE) — `status` enum(`active`,`closed`) (a new party is always `active`; retire by
closing, never delete), and `last_reviewed_at`. The enum value tuples are **golden-pinned**
(`tests/unit/test_interested_party_register_content.py`) and **append-only**. `influence` is a **categorical**
axis, NOT a graded/computed one, so — like Context and unlike risk — there is **no `criteria` block, no
`resolve_criteria`, no derive-and-freeze**: the frozen content is purely the rows.

**Org-level (NOT process-scoped) — the owner fork.** Clause 4.2 interested parties are strategic and org-wide
(customers, regulators, suppliers, employees, owners, the community), so `interested_party` carries **`org_id`**
— the §1.1 org_id-everywhere convention, **correcting the `14 §6` editorial gap** that omitted it (the only
register satellite that did; R50 named it) — but **no `process_id`** (deliberately unlike `risk_opportunity`,
matching the Context posture). Authz rides the **already-seeded `register.read` / `register.manage` /
`document.release`** keys at the **SYSTEM** scope — **catalog stays 102, NO new key, NO new role grant**. `GET
/interested-parties` is filter-not-403 (a no-grant caller → 200 + empty); `GET /interested-parties/{id}`
enforces `register.read` @ SYSTEM; `POST`/`PATCH` + the steward acts enforce `register.manage` @ SYSTEM;
release is `document.release` + SoD-2 over the multi-axis `_register_release_scope` (a SYSTEM override in v1).
Server-computed `can_release`/`can_manage` on `GET /interested-parties/register` via the shared
register-agnostic `register_capabilities` (the S-context-fe pattern). ADMIN gets no `register.*` (AZ-INV-6).

**Reservation (generalized to the quad).** `IPR` is added to `_MANAGED_REGISTERS = {RSK, CTX, IPR}` — the IPR
head is reserved from the **create / mutate / DCR-implement / import** quad: the generic mutation chokepoint
(metadata/distribution/links + link-target, clause-mapping, obsoletion at `lifecycle.obsolete`, DCR
`_resolve_target`), the generic `POST /documents` create guard (`reject_managed_register_creation`), the
CREATE-DCR `_resolve_implement_version` guard (`is_managed_register_doc`) — all keyed off the dict — **plus**
the separate inline ingestion-import guard (`commit.py`, the one site not keyed off the dict), error code
`interested_parties_register_managed_via_interested_parties`. The IPR head carries ZERO ProcessLinks.

**Migration `0061`** (creates `interested_party` + the 3 enums + the `IPR` `document_type` + the additive
`INTERESTED_PARTY_UPDATED` event type; NO role grant, NO new permission key).

**Deferred (named, not faked):** the read consumers (`S-interested-parties-2` — `GET
/interested-parties/summary` + `governing_register` + `summarize_register` [+ the `by_influence` bucket] +
the **Management-Review 9.3.2(b)** context-change input, finally sourced from BOTH the 4.1 Context AND 4.2
Interested-Parties governing summaries [un-gapping `CONTEXT_CHANGES`, the R50 / S-context-2 deferral]); the
FE (`S-interested-parties-fe`, an own `/interested-parties` SPA cloning `features/context/`).

**Validation.** Spec-first (`docs/superpowers/specs/2026-06-21-s-interested-parties-design.md`); the
architecture is settled (R49/R50), so 4 owner decisions were surfaced via AskUserQuestion before any code
(content model, `org_id`, scope-split, FE). migration-reviewer + diff-critic + a 3-lens adversarial Workflow
all CLEAN/0-confirmed; Codex not run (owner squashed on green CI + 3 clean in-house reviews).

**Back-propagation:** `02` (4.2 InterestedParty register as-built), `07` (`register.*` now reach the
org-level interested-parties resource), `14 §6` (**`org_id` correction** + the as-built enriched columns),
`15` (`/interested-parties` endpoints — §8.10d).

Bumps the resolutions range **R1–R50 → R1–R51**.

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

# EasySynQ Decisions Register

This document is the **single authoritative source of truth** for the EasySynQ self-hosted ISO 9001:2015 QMS specification. It records the locked foundational decisions, the locked stakeholder decisions, and the normative resolutions (R1–R38) to every finding raised in the gap audit (`17-gaps-and-open-questions.md`); R38 (slice S-rec-4) is the first post-v1 *additive* decision (additive catalog extensibility + SoD-6).

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

## Part 3 — Resolutions R1–R38

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

**Back-propagation:** 03, 13.

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

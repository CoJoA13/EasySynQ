# Records process-scope authz — model + slice plan (spec)

> The deferred "records process-scope read" follow-up (named in S-process-scope-1 / S-process-scope-2).
> This spec pins the **authz model** for scoping records by a bound Process-Owner's PROCESS grant, the
> writes that mint those bindings, and a **converging slice plan** — *before* any code, because this is
> the surface that did **not** converge under Codex when it was attempted as one read-enrichment in
> S-process-scope-1 (built → reverted). Adversarially reviewed (§11) before owner ratification.

## 0. Owner decisions (RESOLVED — ratified 2026-06-19 via AskUserQuestion)

- **D-1 / D-4 — Read AND write-enable (both ship; W NOT deferred).** **Ratified: do Slice R + Slice W.**
  The read is the user goal and is escalation-free standalone (§3, adversarially upheld §11); the owner
  elected to *also* enable Process-Owner record **authoring** (Slice W), which adds the write-gate
  enrichment + the target-process re-auth (PROCESS **and** CAPA_STAGE via `Capa.process_id`) + the
  capture per-process guard + the deeper R3-2 fix (CAPA closure counts only *authorized* links).
  *Sequencing (implementer's call, for convergence): ship **R first** as its own PR — it's clean and
  proven escalation-free — then **W** as a separate PR, since W is the high-risk write surface that
  opens+closes the `capa_stage` class; both land this arc. W is fully specced in §6.*
- **D-2 — Correction-chain scope (R3-1).** **Ratified: (a) walk `correction_of`** in the read loader
  (bounded, §5/§11.4) so a source-less corrected record stays visible to the process that owned the
  original. Read-side only.
- **D-3 — `GET /clauses` for the create-in-process wizard.** **Ratified: (a) add `clauseMap.read` at
  SYSTEM to the seeded Process-Owner bundle** (additive **R38** register-level catalog change — a new
  `role_grant` for the *Process Owner* role + a decisions-register entry + the catalog-count test bump).
  Ships as the independent sibling **Slice C**. *The clause map is whole-catalog reference data a
  Process-Owner legitimately reads to map a document.*

## 1. Why / what

A bound Process-Owner (S-owner-assignment-1: a PROCESS-scoped `role_assignment` carrying a `process_ids`
set) holds `record.read` / `record.create` at **PROCESS** finest-scope (the seeded *Process Owner*
bundle, `0004_seed_authz.py:205,322`). But every records authz surface resolves a scope with **no
`process_ids`**, so their grant mis-denies them. The goal: a Process-Owner sees (and, optionally,
authors) the records bound to their owned processes — end-to-end, **without opening an over-grant**.

**The S-process-scope-1 revert lesson (the governing constraint).** The records read enrichment was
built then reverted because it enriched the **shared** `_record_scope` resolver — which the records
**read** *and* **write** gates both use — so it simultaneously let a Process-Owner *write* (correction,
evidence-link), and those writes do **not** re-authorize the process they target. Every Codex finding
that killed it was a **write-path** finding (CX-1 evidence-link target, CX-4/R2-3 correction source,
R2-2 evidence-link DELETE). The thesis: *a READ surface is only safe to scope by process bindings if
every WRITE that can mint those bindings re-authorizes the target process.* This spec's central result
(§3) is that **decoupling the read from the write** sidesteps the entire class for the read.

## 2. The model

**A record's process binding** — `services/packs/repository.py:232 record_process_ids(session, record)`
(already exists; today its *only* caller is the evidence-pack classifier, `packs/service.py:163`):

- **Leg A** — the record's own `EvidenceForLink` rows with `target_type=PROCESS` (`target_id`s).
- **Leg B** — the **source document's** `ProcessLink`s (`WHERE documented_information_id =
  record.source_document_id`), guarded by `source_document_id IS NOT NULL`.
- A record holds **no `ProcessLink`s of its own** (`capture_record` passes `process_ids=frozenset()`),
  so Leg B (the source doc) is the primary real source; Leg A depends on evidence-for-PROCESS links.

**The read gates (today — all process-blind):**

- `GET /records/{id}` + every sub-resource (`/evidence/{sha}/download`, `/rendition`,
  `/evidence-links`, `/disposition`, `/worm-destroy-requests`) ride `_read = require("record.read",
  _record_scope)`. `_record_scope` (`records.py:220-232`) builds `ResourceContext(artifact_id,
  folder_path)` — **no `process_ids`/framework/kind**.
- `GET /records` **list** is a row-filter (`records.py:374-385`): `gather_grants("record.read")` then
  per-row `authorize` over `ResourceContext(artifact_id, folder_path)` — same omission.

**The write gates (today):**

| Write | Endpoint | Key · gate | Re-auths the target process? |
|---|---|---|---|
| Capture (new record) | `POST /records` | `record.create`, **in-handler** enforce over `_capture_scope` | derives the **source doc's** process_ids (process-aware); does not mint a *new* target |
| Correction | `POST /records/{id}/correction` | `record.create` via `_create_scoped`→`_record_scope` | **NO** — and `capture_correction` *forces* a source-backed original's OWN source (`service.py:586-589`), so the inherited source's process is never re-checked |
| Evidence-link **add** | `POST /records/{id}/evidence-links` | `record.create` via `_create_scoped`→`_record_scope` | **NO** — `link_evidence` does existence/org/framework checks only; zero authorize on `target_id` (PROCESS *and* CAPA_STAGE org-checked only) |
| Evidence-link **remove** | `DELETE …/evidence-links/{id}` | `record.create` via `_create_scoped`→`_record_scope` | **NO** — only a CAPA_STAGE freeze guard |
| Disposition / hold / destroy | `…/disposition`,`/legal-hold`,`/worm-destroy-requests` | `record.dispose` via `_dispose`→`_record_scope` | **NO** (and SoD dual-control) — but mints **no** process binding |

**The crux:** `_record_scope` is **shared** by `_read` (read), `_create_scoped` (correction +
evidence-link), and `_dispose`. Enriching it with `process_ids` enables a Process-Owner to **read AND
write** in one stroke — exactly the S-process-scope-1 coupling that opened the unguarded writes.

## 3. Central finding — the read decouples from the write and is escalation-free on its own

**Use a SEPARATE read-only resolver.** Enrich a new `_record_read_scope` (= `_record_scope` +
`record_process_ids` + `framework_id` + `kind=RECORD`) for `_read` and the `GET /records` list
row-filter **only** — leaving `_create_scoped` and `_dispose` on the unenriched `_record_scope`. Then:

- A Process-Owner can **read** records whose binding intersects their processes, but **cannot reach any
  binding-minting write** (the write gates stay process-blind → they 403 at the base gate, exactly as
  today). So they **mint nothing**.
- Every binding a Process-Owner reads by is minted only by a **broad** holder or a **re-authorized**
  act:
  - **Leg B** (source-doc `ProcessLink`) is created by a **document** process-link write, which *is*
    re-authorized (`documents._enforce_target_process`, S-owner-assignment-1). A record inherits Leg B
    by being captured under that doc; capture (`_capture_scope`) enforces `record.create` over the
    doc's `process_ids`, so a narrow holder can only capture under a doc that includes **≥1 process
    they own** (PDP PROCESS = non-empty intersection) — and that doc's *other*-process links were
    themselves re-authorized. The record-under-doc visibility to those other-process owners is the
    *intended* "this record is evidence under a doc that spans those processes."
  - **Leg A** (evidence-for-PROCESS link) is minted by `POST evidence-links`, reachable **only** by
    SYSTEM/ARTIFACT/FOLDER `record.create` holders today (write gate process-blind). A broad holder
    declaring a record "evidence for process P" → readable to P's owners is the *intended* semantic,
    exercised by a holder who already had broad authority. **No narrow holder gains anything.**

**Therefore the read-only enrichment is escalation-free without any write re-auth.** All four
write-path findings that killed S-process-scope-1 become **moot** (the writes are unreachable to narrow
holders). R3-1 (below) is a *visibility/usability* gap, not a security one. R3-2 (`capa_stage`) is
**not widened** by read-only (it doesn't change who can link).

**B1 (the write-path target re-auth) is only required for write-ENABLEMENT** (Slice W) — i.e. only if
we let a Process-Owner *author* records. That, and only that, opens the unguarded `capa_stage` target.

## 4. Slice plan

- **Slice R — records read (decoupled read-only resolver).** The user goal; escalation-free (§3); no
  migration / no new key (the grant exists; `finest_scope` is documentary — the S-process-scope-2
  lesson); contract = description text. Includes the R3-1 correction-chain handling (D-2). **Ship this
  first.** *(Optionally pair with the D-3 `GET /clauses` bundle add as a tiny sibling so the wizard's
  clause step also works for a Process-Owner — separable.)*
- **Slice W — Process-Owner record authoring (write-enable) — IN SCOPE, sequenced after R (D-1/D-4).** Enrich a separate
  `_record_write_scope` (process-aware) for `_create_scoped`, AND add a records
  `_enforce_target_process_record` re-auth (mirror `documents._enforce_target_process`: re-enforce
  `record.create` over `dataclasses.replace(full_scope, process_ids={target})`, preserving
  artifact/folder/framework so an ARTIFACT/FOLDER holder isn't over-blocked — AZ-INV-8/R2-1) on **both**
  evidence-link PROCESS targets (Leg A) **and** CAPA_STAGE targets (via `Capa.process_id`), the
  correction effective-(forced)-source processes, and a per-process capture guard. **Plus** the deeper
  R3-2: CAPA closure must count only *authorized* `capa_stage` evidence links.
- **Slice C (sibling, D-3) — `GET /clauses` for a Process-Owner.** Add `clauseMap.read` SYSTEM to the
  Process-Owner bundle (R38 register entry) **or** reroute the wizard clause step. Tiny; independent.

**Dependency:** R is standalone. W depends on nothing code-wise but should *follow* R (read before
authoring). C is independent.

## 5. Slice R design (records read)

- **New `_record_read_scope(request, session)` resolver** (read-only): resolve `record_id` from the
  path → load the base → `ResourceContext(artifact_id=str(base.id), kind="RECORD",
  folder_path=base.folder_path, framework_id=str(base.framework_id), process_ids=<record_process_ids>)`.
  Point **`_read`** at it. **Do not touch** `_create_scoped` / `_dispose` (they stay on `_record_scope`).
- **`GET /records` list row-filter**: batch-load `record_process_ids` per row (a batched
  `record_process_ids_for(records) -> dict[id, frozenset[str]]`, two grouped queries — Leg A
  `EvidenceForLink` IN(ids) + Leg B `ProcessLink` joined on the rows' `source_document_id`s — to avoid
  N+1), and include `process_ids`+`framework_id` in each per-row `ResourceContext`.
- **One source of truth:** the batched loader must return the **same** union as
  `packs::record_process_ids`; refactor `packs/service.py:163` onto the shared loader and assert the
  records gate + the pack classifier yield the **same** visible set for the same caller.
- **R3-1 correction-chain (D-2 = walk `correction_of`):** in the loader, if a record's own
  `record_process_ids` is empty AND it has a `correction_of`, fold in the predecessor's binding. Keeps a
  source-less corrected record visible to the process that owned the original. (Read-only; no write
  change.) **Cap the walk at N hops** (defense-in-depth — the chain is provably acyclic / at-most-once-
  successor, `service.py:577-580`, so termination is guaranteed regardless) and **never cross an org**
  (already guaranteed by `_load_record`'s org-404, `service.py:154`). Fold the predecessor's binding only
  when the successor's own union is empty — never widen a successor that already has its own scope (§11.4).
- **No migration, no new key, no request/response shape change** (the record serializer is unchanged;
  this changes *who* may read — filter-not-403 for the list, 403→200 for the detail of an owned record).

## 6. Slice W design (write-enable — sequenced after R)

- **`_record_write_scope`** (new, process-aware) for `_create_scoped` (correction + evidence-link
  add/remove) → a Process-Owner can reach those writes for a record bound to their process. `_dispose`
  stays process-blind (disposition mints no binding; SoD dual-control unchanged).
- **`_record_scope_by_id` + `_enforce_target_process_record`** (mirror `documents.py:1098-1116`): build
  the record's full non-process scope, `dataclasses.replace(…, process_ids={target})`, `enforce(…,
  "record.create", …)` via the **PEP** (so `source_ip` threads + a deny audits). Order per documents:
  validate existence/org → **re-auth target** → dup-check → mutate.
  - **evidence-link add**, `target_type=PROCESS` → re-auth over `{target_id}` (Leg A).
  - **evidence-link add**, `target_type=CAPA_STAGE` → re-auth over the CAPA's `process_id` (closes the
    Leg-into-CAPA escalation enabling opens; skip when `Capa.process_id IS NULL` — decide fail-open vs
    deny in W).
  - **evidence-link remove**, `target_type∈{PROCESS,CAPA_STAGE}` → re-auth the existing link's target
    (R2-2), preserving the CAPA freeze guard ordering.
  - **correction** → re-auth over each process of the **effective (forced) source** doc
    (`original.source_document_id` ∨ body's), not the caller's body source (R2-3).
  - **capture** → per-process re-enforce so a Process-Owner can only capture under a doc whose process
    set they fully own (tighten `_capture_scope`'s intersection-match to a per-process loop), else a P1
    owner could mint a P1+P2-bound record under a shared doc.
- **R3-2 deeper fix:** `capa/repository.py::stages_with_evidence` (→ `adjudicate_capa_closure`) must
  count only links whose creator was authorized for the stage's process — else a closure-evidence
  requirement is satisfiable with zero CAPA authority. (Compliance-gate; its own blast radius — packs
  dossier + audit-close consume the same links.)
- **Deltas:** no migration; **possibly** a new key only if the owner wants a dedicated
  `record.link_evidence` (D-1's earlier sub-decision said re-enforce the existing `record.create` →
  no new key). Codex edge classes per §11.

## 7. Non-goals

- The `audit_finding`/CAPA closure compliance redesign beyond the minimal R3-2 link-authority count
  (Slice W) — full CAPA-evidence governance is its own track.
- Multi-org (D1: single-org; the process intersection is within one org).
- `include_subprocesses` descendant inclusion (docs/07 §5.3, "default true") — **unimplemented anywhere
  in the v1 PDP** (own-id intersection only; no resolver walks `parent_id`); a faithful own-id port is
  correct. Descendant inclusion is a cross-cutting authz-model change across documents/records/processes
  together — a separate, named follow-up (raised by Codex CX-3 on S-process-scope-2).
- `ip_allow` predicate evaluation is v1-deferred (threaded for fidelity where a write re-auth runs via
  the PEP; the read row-filter matches the records/search precedent).

## 8. Testing & verification

- **Slice R (api integration; delta-/run-scoped per the shared-DB rule):** a bound Process-Owner of P1
  **reads** a record whose source-doc (Leg B) or evidence-for-PROCESS (Leg A) link intersects P1
  (detail 200, list shows it) and **cannot** read a record bound only to an unowned P2 (403 detail /
  absent in list); a SYSTEM holder reads all **byte-identical**; the records gate and the pack
  classifier return the **same** visible set for the same caller (one-source-of-truth); a source-less
  ad-hoc record with **no** binding is correctly invisible (genuine absence); the R3-1 case — a
  source-less correction of a P1 record **stays** visible to the P1 owner via `correction_of`.
  **Crucially:** a PROCESS-only fixture (no SYSTEM override) — constructed via the
  `test_processes._assign_role_bound` direct-PROCESS-grant precedent — or the SYSTEM mask hides the gap.
- **Slice W (if elected):** escalation-403 (link/correct into an unowned process or a P2 CAPA stage) ·
  owned-target-200 · SYSTEM byte-identical · ARTIFACT/FOLDER holder **not** over-blocked (the AZ-INV-8
  proof) · evidence-link DELETE target · capture-under-shared-doc guard · CAPA closure ignores an
  unauthorized link.
- Windows box: `-m integration` is **CI-authoritative** (the unit-baseline note); local gate = ruff +
  mypy-strict (`src` only) + targeted unit files.

## 9. Open questions / risks (for the adversarial pass)

- Is the §3 "read-only is escalation-free" proof airtight? Specifically: (a) can any **narrow** holder
  mint a Leg-A or Leg-B binding to a process they don't own, reachable today? (b) does capture's
  intersection-match (a P1 owner capturing under a P1+P2 doc) constitute a real escalation, or is the
  resulting P2-visibility the intended doc-spans-processes semantic? (c) are there **other** record
  write/derive paths (ingestion-imported records? a record's `correction_of` predecessor's links?) that
  mint a binding a narrow holder controls?
- R3-1: does walking `correction_of` ever **widen** visibility incorrectly (inherit a predecessor's
  binding the successor shouldn't have)? Bound the hop; never cross an org.
- One-source-of-truth drift: the batched loader vs `record_process_ids` (org_id handling, Leg-A/Leg-B
  parity) must be mutation-tested identical.

## 10. Deltas summary

| Slice | Migration | New key | Contract | Tests |
|---|---|---|---|---|
| **R** (read) | none (head `0056`) | none (rides `record.read`; `finest_scope` documentary) | description text only | +~5 api integration |
| **C** (`GET /clauses`) | none | **maybe** `clauseMap.read`→bundle (R38 register entry, owner call) | none | +1–2 api |
| **W** (write-enable, deferred) | none | none (re-enforce `record.create`) | gate-note only | +~7 api integration |

## 11. Amendments — adversarial fold (2026-06-19, 4-lens refutation panel)

**Verdict: read-only-safe — the §3 central claim is UPHELD.** A 4-lens adversarial panel (narrow-minter
· capture-intersection · other-minters sweep · decoupling/R3-1), each reading the source, returned
`claim-holds` with **zero** `isRealEscalation=true` findings. Cross-verified against source:
`_record_scope` carries no `process_ids` (`records.py:220-232`) and `_create_scoped`/`_dispose` ride it
(`records.py:269,272`), so every binding-minting write **403s a PROCESS-only holder at the base gate**
(empty-intersection deny, `pdp.py:81-83`); the sole `EvidenceForLink` constructor is `_create_scoped`-
gated (`service.py:680`); `capture_record` mints nothing of its own (`process_ids=frozenset()`,
`service.py:480-481`); `documents._enforce_target_process` re-auths over `{target}` only
(`documents.py:1114-1116`); `record_process_ids` Leg-B is guarded by `source_document_id IS NOT NULL`
(`packs/repository.py:244`). **No narrow holder can mint a Leg-A or Leg-B binding to an unowned process.**

**§9(c) sweep — CLOSED-NEGATIVE (the minter enumeration is COMPLETE).** A full-tree grep confirms:
**one** `EvidenceForLink` constructor (`service.py:680`); `Record.source_document_id` is set **only** via
`capture_record`/`capture_correction` (audits/capa/objectives/`packs.build`/`ingestion.commit` all pass
`None` — ingestion explicitly `source_document_id=None` at `commit.py:453`); **three** `ProcessLink`
mint-sites, all either re-authorized (`documents._enforce_target_process`) or SYSTEM-gated
(`import.commit` — and a Process-Owner bundle holds no `import.*`). **No `cli/` or `tasks/` minter; no
correction-predecessor / KPI / audit / CAPA path mints a narrow-controllable PROCESS binding.**

**Accepted residuals (named, not faked) — bounded caveats, none an escalation:**

1. **Capture doc-spans-processes visibility.** A bound P1-owner can capture a record under a source doc
   D linked to P1+P2 (`_capture_scope` intersection-match passes, `records.py:323-324`); under Slice R
   its Leg-B binding makes it readable to P2's owners. **Not an escalation:** the captured record carries
   no binding the capturer controls (mints `frozenset()`); every P2 link on D was re-authorized at link
   time over `{P2}`; the P2-visibility is the *intended* doc-spans-processes semantic, read by a P2 owner
   who already saw everything under that shared doc. The per-process capture tightening lives in **Slice
   W** (§6 capture bullet). *Resolves §9b.*
2. **Bounded source-doc naming.** `_capture_scope` only org-checks the *named* source doc
   (`records.py:245-247`), but the next line enforces `record.create` over **that doc's** actual scope
   (`records.py:323-324`), so a narrow holder succeeds only when the named doc's process set intersects a
   grant they hold — the same gate as the legitimate path. No unbounded source-set is reachable.
3. **Sibling record-read surfaces stay SYSTEM-gated (stricter — the safe direction).** Slice R
   deliberately does **not** enrich complaints (`_complaint_read=require("record.read")` SYSTEM-default,
   `capa.py:294`), audit findings (`_finding_read=require("finding.read")` SYSTEM-default — a *different*
   key, `audits.py:249-251`), or evidence-pack record bytes (SYSTEM). A record reachable by both
   `/records/{id}` (process-scoped post-R) and a sibling (SYSTEM-gated) yields an inconsistent-but-
   **stricter** answer (a PROCESS holder lacking SYSTEM is *denied* at the sibling). Documented as a
   consistency caveat overlapping the already-named "pre-existing packs `record.read` exposure" deferral —
   **not** a missed surface. *Resolves §9c sibling-surface question.*
4. **R3-1 `correction_of` walk is bounded and cannot widen incorrectly.** It folds the predecessor's
   binding **only** when the successor's own `record_process_ids` is empty (no surprising union); the
   chain is strictly backward/acyclic (`correction_of` set once at capture, never UPDATEd; at-most-once
   successor via the 409-already-superseded guard, `service.py:577-580`) so a multi-hop walk terminates;
   it never crosses an org (`_load_record` 404s on mismatch, `service.py:154`). **Code a hop cap as
   defense-in-depth** (not a correctness requirement). *Resolves §9 R3-1 question.*

**Monotonicity/AZ-INV-8 confirmed:** enriching the read resolver with `process_ids`+`framework_id`+
`kind="RECORD"` is byte-identical for every existing SYSTEM/ARTIFACT/FOLDER caller (field-segregated PDP
matcher, `pdp.py:76-101`); across the 8 seeded roles `record.read` is granted only at SYSTEM/PROCESS/
ARTIFACT — never FRAMEWORK/FOLDER — so `framework_id`/`kind` newly match nothing and `process_ids` only
flips a previously-mis-denying PROCESS grant deny→allow. Strictly additive; cannot flip any existing
allow→deny.

**§9 open questions — all resolved above** (read-only proof airtight; capture intended-semantic; sweep
complete; R3-1 bounded). **Recommendation unchanged: ship Slice R; defer Slice W; Slice C independent.**

## 12. Docs in-PR

The spec ships in its own `docs(...)` PR (the #210/#212 precedent). Each slice (R, C, W) updates
`docs/slice-history.md` + the `s-owner-assignment-resume-point` memory + this spec's §11 on merge.

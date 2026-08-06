# Design — audit remediation execution order after PR #443

> **Status:** proposed for owner review; documentation only
> **Date:** 2026-08-06 · **Baseline:** `main` at `1e35a21` (PR #443) · **Migration head:** `0085`
> **Authority:** the finding ledger, slice contracts, and historical evidence remain in
> [`2026-08-04-audit-remediation-v2.md`](../plans/2026-08-04-audit-remediation-v2.md).
> This document chooses an execution path through that programme; it does not rewrite the audit. On
> merge, §2 supersedes only v2 §5's open-status entry and §5.1 rows for D-C, D-F, D-B3, and D-B4.
> Sections 4 and 4.2 supersede only v2 §4's Stage-2 integrity/recovery ordering and v2 §6 rule 1's
> migration-reservation table; v2's later-stage ordering, hard dependencies, release gates, finding
> ledger, and slice contracts remain authoritative.

---

## 1. Why this document exists

PR #443 merged the reconciled audit, its evidence trail, and a bounded set of decision-independent
closures. It deliberately did not claim production recovery or upgrade eligibility. The remaining
integrity work is interdependent: starting at the Critical recovery-generation finding would skip the
identity, version-binding, backup-leg, and restore-target premises that make a recovery proof meaningful.

This document gives each premise a reviewable PR boundary, keeps the single Alembic tree ordered, and
identifies the owner decisions that must be settled before implementation. It is the short handoff for
continuing the programme from another workstation without turning the merged audit PR into a rolling
branch.

### 1.1 Scope

- Ratify the next Stage-2 implementation order.
- Select the recommended paths for D-C, D-F, D-B3, and D-B4.
- Define which existing issues fold into later slices.
- Name the first implementation slice and its falsifying proof.
- Preserve one slice per PR, except for the explicitly atomic WORM/container pair.

### 1.2 Non-goals

- No production, test, contract, migration, Compose, or CI changes.
- No claim that C-01, C-01b, M-01, or the full upgrade-safety slice is closed.
- No detailed implementation plan for any slice. Each slice still receives its own approved design and
  task-level TDD plan before code.
- No reprioritisation of the later authentication, accessibility, or Stage-6 depth programme.

---

## 2. Owner decisions proposed for ratification

Merging this document ratifies the selections below. Until then they remain proposals and must not be
silently treated as implementation authority.

| Decision | Proposed selection | Why |
|---|---|---|
| **D-C — upload digest enforcement** | **Stream and hash the staged object on the server before promotion.** | The final authority verifies the bytes it is about to WORM-lock. This avoids making correctness depend on a browser-supplied checksum header or a MinIO-version assumption. The extra staging read is accepted; later object-store checksums may be additive defence in depth. |
| **D-F — container hardening scope** | **Perform the full capability and credential inventory, then separate principals and run non-root.** | Merely splitting environment files does not separate authority. API/worker credentials must not carry DB-owner, MinIO-root/governance-bypass, Keycloak-admin, backup-key, or checkpoint-signing authority. |
| **D-B3 — failed restore targets** | **Persist restore-job and target inventory, including terminal disposition.** | Object-locked targets cannot be assumed deletable after failure. Persisted inventory makes abandoned targets discoverable and reconciliable without relying on an operator-maintained side list. |
| **D-B4 — legacy backup envelope** | **Allow a bounded decrypt-only fallback across the current and previous configured candidates, then require resealing under the exact current key ID.** | This preserves a controlled recovery path without ever treating the static `BACKUP_ENCRYPTION_KEY:sha256-v1` label as current. Unknown IDs fail with a specific configured-key error. |

The migration lock/statement timeout value and abort policy remain a separate owner-visible choice inside
the full `S-upgrade-safety` design. It does not block the first implementation slice.

---

## 3. Approaches considered

### A. Start with `S-recovery-generation`

Rejected. A self-contained generation cannot trust content addressing until staged bytes are verified,
cannot restore an exact snapshot object without `object_version_id`, and cannot be declared complete while
backup legs and cutover targets remain partial.

### B. Finish `S-upgrade-safety` first

Valuable, but not the critical-path opener. Operation identity, single-flight execution, cancellation,
reconciliation, and timeouts should close, but even a mechanically correct upgrade cannot advertise its
pre-backup artifact as a safety net until self-contained recovery passes.

### C. Start with `S-upload-identity`, then satisfy the recovery prerequisites

**Selected.** It is a bounded High-severity integrity fix with no migration, a deterministic RED proof,
and no dependency on the later recovery schema. It closes a defect on its own while establishing the
content-addressed invariant every recovery generation needs.

---

## 4. Proposed binding implementation sequence

Every numbered row is a separate PR unless the row explicitly says otherwise. A later row may be designed
while an earlier row is under review, but production edits that share backup, storage, Compose, migration,
or CI surfaces stay serialized.

| Order | Slice / PR boundary | Prerequisite and result | Migration / issue ownership |
|---:|---|---|---|
| 1 | **`S-upload-identity`** | Requires D-C ratification. Verify the staged bytes before any documents/records/ingestion promotion; reject a false identity before WORM copy or owner-row commit. Establishes trustworthy content addressing. | No migration. |
| 2 | **`S-worm-retention` + `S-container-identity` — one atomic PR** | Requires D-F ratification. Apply monotone per-object retention and simultaneously remove governance-bypass/root authority from ordinary application principals. Adds exact object-version binding used by recovery. | `0086`; the pair must not ship separately. |
| 3 | **`S-db-grants`** | The application role loses blanket mutation authority on the five load-bearing tables; authority-bound destructive paths remain explicit. | `0087`, privilege-only. |
| 4 | **`S-backup-legs`** | Requires D-B4 ratification. Require a real key and mandatory realm/config/checkpoint legs, bind checkpoint capture to the exported DB snapshot, and validate one typed backup verdict across every consumer. | No migration. Fold **#420** (plaintext restore-test drill) into the G-C/durable-path design rather than patching it independently. |
| 5 | **`S-restore-target`** | Requires D-B3 ratification. Restore into fresh role-preserving documents/records/renditions targets, preserve object keys, persist target disposition, deny source-vault reads in the cutover proof, and close cancellation/records-WORM cleanup gaps. | `0088`; add the durable restore-job/target inventory. The slice design owns its exact schema. |
| 6 | **`S-backup-destination`** | Prove worker ownership and approved persistent backing, reject overlay paths, and retain a worker-written probe across restart and recreation. | No migration currently planned. |
| 7 | **`S-recovery-generation`** | Consumes orders 1, 2, 4, 5, and 6. Build the shared CAS plus sealed self-contained generation and restore with source-vault access denied. | `0089`; closes the C-01 recovery-generation boundary only when the full boot/read proof passes. |
| 8 | **Complete `S-upgrade-safety`** | Add operation identity, single-flight, orphan reconciliation, cancellation semantics, shared typed backup validation, audit-underclaim signalling, and bounded migration waits. Production eligibility is evaluated only after order 7 passes. | No migration currently assigned. |

`S-db-grants` is not a data dependency of recovery generation, but it stays at order 3 because Alembic is
a single tree. Selecting D-B3 makes durable inventory—not an audit-detail convention—part of the
`S-restore-target` contract. The current tree has no durable restore-job/target model, so this sequence
reserves its migration before the recovery-generation ledger rather than leaving the conflict to the
implementer.

### 4.1 Dependency view

```text
D-C  ──> S-upload-identity ────────────────────────────────┐
D-F  ──> S-worm-retention + S-container-identity (0086) ──┤
         S-db-grants (0087; serialized migration)          │
D-B4 ──> S-backup-legs ────────────────────────────────────┤
D-B3 ──> S-restore-target (0088) ──────────────────────────┤
         S-backup-destination ─────────────────────────────┤
                                                           v
                                             S-recovery-generation (0089)
                                                           |
                                                           v
                                      production upgrade eligibility review
```

### 4.2 Revised migration reservations

This is the single-tree order after ratifying persisted target inventory. It supersedes the reservation
numbers in v2 §6 rule 1; slices still rebase their revision when an earlier migration lands first.

| Revision | Owning slice | Reservation |
|---|---|---|
| `0086` | `S-worm-retention` | definite |
| `0087` | `S-db-grants` | definite |
| `0088` | `S-restore-target` | definite — durable restore-job/target inventory |
| `0089` | `S-recovery-generation` | definite — backup-generation ledger and additive event type |
| `0090` | `S-deploy-rollback` | definite — release event types |
| `0091` | `S-mfa-enrollment` | definite — MFA reset event type |
| `0092` | `S-restore-job-recovery` | conditional — reuse the `0088` ledger; migrate only if its UI lifecycle needs additive fields |
| `0093` / `0094` | `S-abuse-liveness` / `S-audit-tail-policy` | conditional, retaining v2's reuse-first guidance |

---

## 5. First implementation slice — `S-upload-identity`

The slice boundary is the transition from mutable staging storage into a WORM vault bucket. A caller's
declared SHA-256 is untrusted input until EasySynQ streams the staged object through its own digest check.

The required falsifier is fixed before implementation:

1. Declare `sha256(good)` and upload different **same-sized** bytes under that staging key.
2. Attempt each promotion path: controlled document, Record evidence, and ingestion commit.
3. Require refusal before target-bucket copy, blob/owner-row commit, or success audit.
4. Require an honest rejection audit and deterministic staging cleanup/reconciliation behavior.
5. Mutation-check the proof by bypassing the comparison and observing RED.

The per-slice design must settle the stable problem code, streaming helper interface, retry behavior, and
cleanup ownership before its implementation plan is approved. It must not materialize an unbounded object
in API memory or re-hash only after bytes have already been WORM-locked.

---

## 6. Existing issue and PR coordination

- **#420** belongs to `S-backup-legs`; its old site-specific document references are historical and must
  not be reintroduced.
- **#424** belongs to later `S-deploy-rollback`: the wrapper must use the deployed overlay set and
  `--no-deps` rather than silently recreating services under base Compose configuration.
- **#425** is a separate backup-scheduling/Celery contract slice. It may be designed alongside the
  recovery work but should not share an implementation PR with mandatory backup legs.
- **#430–#436** remain a separate identity-provisioning follow-up lane. Group the API error-mapping cluster
  (#431–#433) and serialize the shared web surfaces (#430, #434–#436).
- Dependabot PRs remain independent maintenance work. Do not merge a lockfile or application-image update
  into an active recovery/container slice without rebasing and rerunning that slice's full gates.

At design time there is no active human feature PR competing with this programme.

---

## 7. Per-slice delivery contract

Every implementation PR in this sequence must include:

1. an owner-approved slice design and task-level implementation plan;
2. a falsifying proof observed RED against the then-current `main`, or an explicitly labelled
   mutation-verified coverage delta where production behavior is already correct;
3. focused tests followed by the complete affected API/web/contract/migration gates;
4. all twelve GitHub checks green on the pushed head;
5. documentation truth updated in the same slice, with `bash scripts/check-no-site-data.sh` and
   `git diff --check`; and
6. one adversarial diff review before merge, with residual risk named rather than smoothed over.

No slice may claim production recovery or upgrade eligibility merely because its own tests pass. That
claim becomes reviewable only after the source-denied, self-contained generation restore and recovered
stack boot/read proof succeeds.

---

## 8. Owner review gate and next action

Review this draft for two things:

1. approve or revise the four selections in §2; and
2. approve or revise the PR sequence in §4.

Once approved, merge this documentation PR. The next branch creates the dedicated
`S-upload-identity` design; only after that written spec is reviewed does its task-level implementation
plan and test-first code begin.

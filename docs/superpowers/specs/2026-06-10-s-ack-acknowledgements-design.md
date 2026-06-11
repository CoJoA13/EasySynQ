# S-ack — Acknowledgements family (Sam's "read & understood" flow) — Design

**Date:** 2026-06-10 · **Family:** S-ack (S-ack-1 backend engine → S-ack-2 trailing web UI) ·
**Status:** owner-approved design · **Unblocks:** the acks half of the PDCA dashboard (the other
half, quality objectives, stays parked).

> Doc grounding: doc 04 §8 (distribution & acknowledgement), doc 05 §2.2/§2.4/§5.3 (MAJOR/MINOR
> re-ack), doc 07 §3.1/§6.3 (`document.acknowledge`, non-sig-hook pipeline), doc 10 §8 (DOC_ACK,
> My Tasks, bulk-ack), doc 13 §5.8/§6.3 (dashboards/report), doc 14 §5.2/§5.6 (`distribution_entry`,
> `acknowledgement`), doc 15 §8.5/§8.8, R2/R15/R23/R38/R41, the owner mockup (doc-page ring/tile,
> PDCA Do quadrant), and the shipped `reacknowledge_required = is_major` contract
> (`services/dcr/where_used.py:117-123`, test-pinned).

## §0 Owner decisions (this session, 2026-06-10)

1. **Re-ack trigger = MAJOR-only** (doc 05's posture ×3 + the shipped where-used contract),
   **superseding doc 04 §8.2's blanket every-re-release re-trigger** — register entry required (R43).
   Corollary adopted: **carry-forward satisfaction** — coverage counts a prior-version ack as
   satisfying across MINOR-only chains (else a MINOR release creates a coverage gap no task closes).
   No org config flag in v1 (doc 05 §2.2's "by default" stays a cheap v1.x additive).
2. **Mechanism = workflow-engine tasks** (the S-drift-1 periodic-review template): additive
   `DOC_ACK` task_type + subject_type, per-user instances, outcome whitelist `{acknowledge}`
   (already quorum-positive), **no signature**. Rejected: a bespoke obligation table (would need a
   second My-Tasks source, violating R15's surface requirement or complicating the self-scoped
   `/tasks`).
3. **Mint `document.distribute`** (register entry **R42**, the R38/R41 additive recipe; catalog
   99 → 100). Rejected: riding `document.manage_metadata` (silently widens every metadata-manager
   into audience/issuance control — the failure mode R41's reasoning names). Aligns doc 15 §8.5's
   pre-existing `document.distribute` reference.
4. **Target kinds: enum 4, accept 2** — `distribution_entry.target_type` carries all four doc-14
   members (`user`, `org_role`, `process`, `folder`); the API **422s `process`/`folder`** at create
   until owner-assignment binding lands (honest refusal, never a silently-empty audience). The
   resolver keeps a seam for the two deferred kinds.
5. **Slice shape: backend-first** — S-ack-1 = the engine (API-smokeable loop: distribute → release
   → tasks minted → acknowledge → coverage moves); S-ack-2 = the trailing web slice (the drift-family
   rhythm). Design sections 1 and 2 below were each owner-approved in-session.

## §1 What the canon already pins (settled, restated — not re-decided)

- **Assignment model** (doc 04 §8.1 + doc 14 §5.6 + R15): a per-document `distribution_entry` list,
  dynamically resolved; a per-document `acknowledgement_required` master flag; obligations exist for
  audience members of ack-required Effective docs. Two trigger families: release of an ack-required
  doc, and **target entry** (R15, `created_reason=target_entry` per doc 17's resolution table).
  Already-satisfied users are excluded (dedup per user × version, carry-forward per §0.1).
- **What an ack IS** (doc 14 §5.6 + doc 07 §3.1/§6.3 + R2): its **own immutable append-only row** —
  NOT a `record` subtype (no `record_type` member exists) and NOT a `signature_event`
  (`document.acknowledge` is seeded sig_hook=false; doc 07 §6.3 step 8: a non-sig-hook ALLOW writes
  an audit_event only; R2's v1 enum has no acknowledge member, not even reserved). **No
  signature-meaning enum change.** The doc-pinned evidence tuple: user, version, timestamp, IP.
- **The act's copy** (doc 04 §8.2): "I have read & understood", clicked after opening/reading the doc.
- **Surfaces** (doc 11 §2.1/§4.3/§5.3, doc 13 §5.1/§5.4/§5.8/§6.3, mockup): My Tasks rows, doc-page
  Acks tab + Acknowledged tile + coverage ring + Remind, TopBar ack bell, QM distribution report,
  PDCA **Do** quadrant ("Acknowledgements complete 92%" — mockup-confirmed).
- **Bulk-ack is sanctioned** (doc 10 §8.2) precisely because acks are not one-at-a-time signature
  decisions.
- **Auth holders** (doc 00 §6): Mara (QM) manages distribution (P), Diego secondary; Sam acknowledges
  (P). Read-only/guest principals are structurally stripped of `document.acknowledge` (doc 07 §5.4)
  and can never be audience members in effect.

## §2 Data model — migration `0048`

Both new tables are org-scoped (`org_id uuid NOT NULL`, the §1.1 convention — doc 14 §5.6's ack row
omitted it; the build follows the convention). ORM modules registered in `db/models/__init__.py`
(+ `__all__`); constraints name-matched migration↔ORM; FK names kept under PG's 63-char limit.

### `distribution_entry` (issuance config — editable, NOT evidence)

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `org_id` | uuid FK NOT NULL | |
| `document_id` | uuid FK → documented_information NOT NULL | |
| `target_type` | enum `distribution_target_type` (`user`,`org_role`,`process`,`folder`) | all 4 members created; API accepts `user`/`org_role` only (§0.4) |
| `target_id` | uuid NOT NULL | user id / role id (process/folder ids reserved) |
| `ack_required` | bool NOT NULL DEFAULT true | per-entry requirement |
| `created_by` | uuid FK NOT NULL | |
| `created_at` | timestamptz NOT NULL | |

`UNIQUE(document_id, target_type, target_id)`. Grants: SELECT/INSERT/DELETE (the `document_link`
editable-metadata precedent; no UPDATE — change = delete + re-add, keeping rows simple).
Acknowledgements carry **no FK to entries** (the doc-14 ER edge is dropped in favor of its own
attribute table): entries are deletable config; the evidence must survive them.

### `acknowledgement` (the Clause 7.3 evidence — append-only)

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `org_id` | uuid FK NOT NULL | |
| `document_id` | uuid FK NOT NULL | denormalized for coverage queries |
| `document_version_id` | uuid FK NOT NULL | **pinned** (doc 04 §8.2) |
| `user_id` | uuid FK → app_user NOT NULL | |
| `acknowledged_at` | timestamptz NOT NULL | |
| `client_ip` | text NULL | from the request (doc 04 §8.2 tuple) |
| `created_reason` | enum `ack_created_reason` (`release`,`target_entry`) | doc 17's promised discriminator, copied from the task's instance context |

`UNIQUE(user_id, document_version_id)` (the idempotency backstop). **`REVOKE UPDATE, DELETE`** — the
`capa_stage`/`dcr_stage_event` DB-grant house style, deliberately harder than doc 14 §1.2's "App"
enforcement column. Index: `(document_id, user_id)` for satisfaction lookups (the global index plan
has no ack entry — S-ack owns it).

### `documented_information.acknowledgement_required`

`bool NOT NULL DEFAULT false` master switch. **Obligation predicate = doc flag AND entry
`ack_required`.** Both the flag and the serialized entry list are folded into
`document_version.metadata_snapshot` at version freeze — additive keys in the shared `_snapshot`
(uniform for all docs, so the S-rec-3 never-branch rule is respected; old snapshots simply lack the
keys). The S-dcr-3a metadata-diff exclusion (`domain/diff/metadata.py:9`) is revisited in S-ack-2,
not here.

### Additive enum values + seeds (same migration)

- `task_type ADD VALUE 'DOC_ACK'`; `workflow_subject_type ADD VALUE 'DOC_ACK'`;
  `event_type ADD VALUE 'DOCUMENT_ACKNOWLEDGED'`, `'DISTRIBUTION_UPDATED'` — the additive ALTER TYPE
  pattern (no-op downgrade), ORM members added, migration tuples sourced from the ORM `*_VALUES`.
  (`FINDING_ACK` stays reserved for the audits family; doc 10's canonical doc-ack name is `DOC_ACK`.)
- **R42 seed**: `("document.distribute", False, False, "ARTIFACT")` CONTENT-domain
  (`is_system_domain=false`), the 0004 `on_conflict_do_nothing` shape; granted to **QMS Owner**.
  Downgrade deletes role_grant before permission (RESTRICT FK). `test_authz` catalog assertion
  99 → 100.
- **Seeded `doc_acknowledgement` workflow definition** (the 0045 recipe verbatim: resilient
  `short_code='DEFAULT'` + single-org fallback; never skip-if-absent): single stage, mode PARALLEL,
  assignees from context (`context_users: "user_id"`), `task_type: DOC_ACK`,
  `action_expected: "acknowledge"`, quorum ANY, **no signature block**, `default_sla` None (due_at is
  set by the sweep).

## §3 The satisfaction rule (R43's core)

A user's obligation on a document is **satisfied** iff they hold an `acknowledgement` row on a
version of that document with `version_seq ≥ last_major_seq`, where `last_major_seq` = the highest
`version_seq` among the document's versions with `change_significance = MAJOR` that are ≤ the
current Effective version's seq. `document_version.change_significance` is NOT NULL on every version
and every chain starts MAJOR (authored baselines and import baselines are MAJOR), so `last_major_seq`
always exists.

Consequences, stated plainly:

- A **MAJOR** release re-arms the whole audience (their prior acks fall below `last_major_seq`).
- A **MINOR** release re-arms no one; coverage carries forward; **no tasks are minted**.
- Ack rows stay strictly **version-pinned evidence** — only the satisfaction computation walks
  MINOR chains. Acknowledging Rev C still does not "edit" anything when Rev D lands; D either
  re-arms (MAJOR) or is covered (MINOR).
- The shipped DCR impact contract (`reacknowledge_required = is_major`) is now honored by the engine
  it promised ("the read-acknowledge engine is a later family" — this is that family).

## §4 Obligation lifecycle — ONE idempotent sweep is the universal mint

`services/vault/ack_sweep.py` (name indicative), runnable org-wide (daily Beat:
`easysynq.ack.sweep`) or **doc-scoped** (enqueued post-commit by every path that flips a version to
Effective — direct release and the scheduled `release_due` cutover, per doc 04 §3.4's
post-commit-async rule — and by distribution writes, §6). Under a session advisory lock
(`LOCK_ACK_SWEEP`), fresh-session-per-unit (the S-ing-5 sessionmaker pattern) for the org-wide walk.

Per ack-required Effective document:

1. **Resolve the audience** (live): union of `user` targets + members of `org_role` targets
   (`users_with_roles`, the engine's existing resolver seam), as a deduplicated user set. Entries
   with `ack_required=false` contribute nothing. The resolver keeps a seam for `process`/`folder`
   (deferred, §0.4).
2. **Cancel FIRST** open DOC_ACK tasks whose obligation lapsed: user left the audience, entry/flag
   removed, the pinned version no longer satisfies (`pinned version_seq < last_major_seq` — a newer
   MAJOR supersedes it), or the doc left Effective (Obsoleted / superseded without successor).
   Cancel = **instance termination + skip PENDING tasks** (the S-dcr-4 force-terminate precedent) —
   never a task-state flip (`decide()` accepts only PENDING; the S-drift-1 lesson). Cancel runs
   before mint so a stale task never blocks the fresh one under the mint guard below.
3. **Mint**: for each audience member NOT satisfied (§3) and with no open DOC_ACK task for this
   document → create one engine instance + task pinned to the **current Effective version**
   (context: `user_id`, `document_id`, `document_version_id`, `created_reason` = `release` when
   minted by the release-scoped run for a fresh version, else `target_entry`), `due_at = now +
   ACK_DUE_DAYS` (env, default 14 — informational RAG only; **no escalation in v1**, the
   notifications family owns delivery/escalation).

This single path covers **all** trigger families — release, R15 target entry, flag flips, entry
adds/removes, imported docs later gaining distribution — so the "retroactive trigger coverage" gap
dissolves: the daily sweep is the self-heal; the scoped enqueues are the snappiness. Idempotency: the
mint key is (open task for user × document); re-runs no-op. Per-user **instances** (not one
instance with N tasks) because the engine cannot add tasks to a materialized stage — late joiners get
their own instance, uniformly.

## §5 The act — the DOC_ACK decide leg

A new `DOC_ACK` branch in the `POST /tasks/{id}/decision` dispatch (`api/workflow.py`), the
PERIODIC_REVIEW sibling (`services/vault/review.py` is the worked template):

- **Outcome whitelist `{acknowledge}`** — 422 anything else (the per-subject whitelist pattern).
- **404-collapse non-membership** (never a 403 that leaks another user's task). Membership compares
  `/me`.id (`app_user.id`), never the Keycloak subject.
- **Enforce `document.acknowledge` at the document's scope** — its first consumer (doc 10 §8.3:
  "enforced by permission scope, not UI hiding"). Key failure → calm **403** (the task is honestly
  yours; the capability is missing). Sam's seeded PROCESS-scope grant rides SYSTEM overrides until
  owner-assignment binds (standing pattern).
- **Live re-check under FOR UPDATE with `populate_existing=True`** (the authz scope-resolver has
  already identity-mapped the doc — the S-drift-1 trap): the doc flag is still on, the user is still
  in the resolved audience, and the pinned version still stands (`pinned version_seq ≥
  last_major_seq`; if a MAJOR superseded it and the sweep hasn't caught up → **409
  `ack_superseded`** — a fresh task replaces it).
- **One transaction**: `engine.decide(_commit=False)` + INSERT `acknowledgement` (client_ip from the
  request; `created_reason` from instance context) + `DOCUMENT_ACKNOWLEDGED` audit event
  (object_type=document, **scope_ref=identifier** so it surfaces on `GET
  /documents/{id}/audit-events` — the import precedent). Replay rides the existing Idempotency-Key
  path (capture ids before any rollback); `UNIQUE(user_id, document_version_id)` is the backstop.
- **No signature_event** (§1). Bulk-ack (doc 10 §8.2's sanctioned bulk action) = the client loops
  this endpoint; no bulk server endpoint in v1.

## §6 API surface (all contracted in `openapi.yaml` in-PR)

| Verb + path | Gate | Behavior |
|---|---|---|
| `GET /documents/{id}/distribution` | `document.read` | Entries + the doc flag + a slim coverage rollup for the current Effective version: `{required, acknowledged, pending, overdue}` (counts only — feeds tile/ring; Sam-safe). |
| `POST /documents/{id}/distribution` | `document.distribute` | Add entries and/or PATCH the doc-level flag (one body). 422 on `process`/`folder` targets (§0.4). Writes `DISTRIBUTION_UPDATED` audit; enqueues the doc-scoped sweep post-commit. |
| `DELETE /documents/{id}/distribution/{entry_id}` | `document.distribute` | Remove an entry; audit + scoped sweep enqueue (cancels lapsed tasks). |
| `GET /documents/{id}/acknowledgements` | `document.distribute` | The **named** per-user status matrix for the current Effective version (who's outstanding/overdue, who acked what version when) — the chase/avatar-stack data. Doc 13: "Mara sees the full matrix"; Sam's own status rides his tasks + the counts rollup. |
| `POST /tasks/{id}/decision` (`acknowledge`) | membership + `document.acknowledge` | §5. |

Coverage definitions (shared by the rollup + matrix + the future dashboard): `required` = live
audience size; `acknowledged` = members satisfied per §3; `pending` = required − acknowledged;
`overdue` = open DOC_ACK tasks past `due_at` (a subset of pending). A doc with the flag on and zero
entries is an honest 0/0. Acks by users no longer in the audience remain evidence but leave the
denominator. Reports/KPIs compute from PostgreSQL only (doc 13 §1.2); tasks are the to-do surface,
never the coverage truth — **coverage = distribution × acknowledgements**.

## §7 Audit & events

- `DOCUMENT_ACKNOWLEDGED` — the act (§5).
- `DISTRIBUTION_UPDATED` — entry add/remove + flag flips (before/after in payload).
- Mint/cancel are engine-instance lifecycle (already audited by the engine); overdue is computed at
  read — **no overdue event, no state flip** (the S-drift-1 escalation lesson).

## §8 Testing (TDD per task; the standing shard rules)

- **Unit**: the §3 satisfaction rule (MAJOR/MINOR chains, import-baseline starts, exact-boundary
  seqs); sweep set-algebra (mint/cancel/dedup across overlapping targets, flag/entry flips,
  left-audience cancel, MAJOR re-arm); 422 target kinds; audience resolution (user + org_role union,
  dedup).
- **Integration** (Linux-CI-only on this box): the full loop (distribute → release → tasks minted →
  acknowledge → coverage moves); R15 target-entry catch-up (role grant → sweep → task); MINOR
  release = no re-mint + coverage carry-forward; MAJOR release = re-arm + stale-task cancel; decide
  authz matrix (404 non-member / 403 no-key / 422 outcome / 409 superseded); a two-session race
  (decide vs sweep-cancel; the engine FOR UPDATE + the §5 409); replay/Idempotency-Key. Assertions
  **delta-based / run-scoped, preconditions self-provided** (shard composition shifts under you);
  `app_under_test` even for service-level tests.
- **Gates**: `/check-migrations` (round-trip 0048), `/check-api` static (ruff/format/mypy locally;
  unit/integration in CI), `/check-contracts`. `diff-critic` on the branch diff pre-PR; live smoke
  via the worker-exec heredoc pattern + Chrome MCP where UI-adjacent (overrides on the LIVE demo
  row — the S-web-8 JIT-row trap).

## §9 S-ack-2 — trailing web slice (sketch; gets its own spec)

- The `/tasks` **DOC_ACK leg**: fourth subject_type branch (the S-web-8 PERIODIC_REVIEW template) —
  best-effort doc context with calm-403 that never blocks the card; decision card titled with the
  doc-pinned copy **"I have read & understood"**; `retry:false` on expected denies; production-defaults
  QueryClient pin.
- Doc page: **Acks tab** (coverage ring + counts from the distribution GET; the named matrix +
  outstanding list only when `document.distribute`) + the **Acknowledged tile** (restoring the
  S-web-4 honest omission); the distribution editor for Mara (per-key gated affordances).
- **TopBar ack bell**: enable the Indicator with the open-DOC_ACK count; route to a filtered
  `/tasks`. Mind the duplicate-`aria-label` trap; keep "Acknowledgements" distinct from "Tasks".
- **Bulk-ack**: multi-select in the inbox looping the decision POST (doc 10's sanctioned bulk).
- MSW fixtures pinned to the **real S-ack-1 serializers** (the #1 false-PASS rule).

## §10 Deferred / residuals (named, not faked)

- **Remind** (+ "Last reminded" provenance, reminder history) → the notifications family — a Remind
  that delivers nothing would be faked; the §6.3 report's reminder-history column depends on it.
- **§6.3 Distribution & Acknowledgement report** (provenance header, exports) → v1.x per doc 13 §2.1.
- **`process`/`folder` target resolution** → owner-assignment track (the API 422s until then).
- **Org-wide PDCA rollup endpoint** → the dashboard slice computes it from these tables.
- **Compliance-checklist ack leg** → deliberately NOT added (doc 13 §3.1's leg list omits acks; the
  spec'd home is §5.8/§6.3).
- **Bulk re-acknowledge (admin)** → v1.2 (roadmap §5).
- **Org config flag for every-release re-ack** (doc 05 §2.2 "by default") → cheap v1.x additive
  (`system_config`, the `allow_self_disposition` mechanic).
- **Delegation/OOO carve-out**: when task delegation lands, DOC_ACK is excluded — a personal
  awareness attestation is not delegable. Recorded here so the notifications/delegation family
  inherits it.
- **Ack retention/GDPR posture**: doc 06 assigns the `acknowledgement` table no retention class and
  `client_ip` is PII-adjacent — raise at the next GDPR/R27 register pass.
- **Doc 06 "awareness ack" field** on COMPETENCE records (`awareness_ack?`) is a separate,
  record-side representation — untouched by this family.

## §11 Register entries + back-propagation (write with the S-ack-1 PR)

- **R42 — `document.distribute`**: third R38-additive key (catalog 99 → 100), CONTENT-domain,
  ARTIFACT-finest, non-sig-hook, non-SoD; seeded to QMS Owner; gates distribution management, the
  named ack matrix, and (later) Remind. Riding `document.manage_metadata` rejected (the R41
  ill-fitting-ride reasoning). Resolves doc 15 §8.5's dangling reference.
- **R43 — Acknowledgements family model**: MAJOR-only re-ack + the §3 carry-forward satisfaction
  rule (supersedes doc 04 §8.2's blanket re-trigger); ack = own append-only table + audit event,
  never a signature_event (no R2 change); engine-task mechanism with additive `DOC_ACK`
  task_type/subject_type; enum-4-accept-2 target kinds; the §10 deferrals.
- **Back-propagation**: 04 (§8.2 re-trigger note), 05 (confirm), 08 (§10.1 `acknowledge.read` →
  `document.acknowledge`, the missed R5 normalization), 10 (§8.1 DOC_ACK confirmed; §9.2 no new
  event key), 13 (§6.3 source names), 14 (§3.1 + §5.6 shapes as built: org_id, created_reason,
  document_id denorm, no entry-FK; task_type/subject_type members), 15 (§8.5 split into the three
  distribution endpoints + the decide leg; §8.8 "every decision writes a signature_event" gains the
  non-sig-hook carve-out per doc 07 §6.3).

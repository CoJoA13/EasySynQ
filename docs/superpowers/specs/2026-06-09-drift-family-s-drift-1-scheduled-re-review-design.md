# S-drift-1 — Scheduled re-review (D5) (slice design)

> **Status:** approved (owner, 2026-06-09). **Track:** v1.x drift family (backend-first).
> **Family:** the doc 05 §9.1 detection mechanisms D1–D5 (roadmap §4 "Drift detection" row), decomposed
> per the owner fork (§0) into **S-drift-1 (D5, this spec) → S-drift-2 (D2+D3 mirror tamper/staleness
> scan) → S-drift-3 (D1 blob re-hash + D4 superseded-copies report + thin admin drift-status surface)**,
> with **one trailing web slice (S-web-8)** for the overdue UI signals once the family's backend is in.
> **Depends on:** the workflow engine (S-wf-engine, S-dcr-4 dispatch precedent), the `/tasks` inbox
> (S-web-5/7b), the DCR family (S-dcr-1..5, reason class `periodic_review`). **Migration:** `0045`
> (head today: `0044`). **Unblocks:** the deferred-since-S-web-1 UI chain — `next_review_due`, the
> "Days to review" tile, the compliance checklist's overdue leg (S-web-6 §3.1), currency/overdue
> reports, metric **M3 (< 5 % overdue)**.

## 0. Owner forks (resolved 2026-06-09, via AskUserQuestion)

1. **Family decomposition = 3 slices** (not 4, not 2): D4 has shrunk to a report query (the S7c
   `/verify` endpoint already returns the full CURRENT/SUPERSEDED/UNKNOWN tri-state and
   `EXPORTED`/`PRINTED` audit rows already exist), and D1 is a single sweep worker — each too thin for
   its own PR. D2+D3 (one scanner with a classification branch) stays its own focused thesis slice.
2. **D5 first** — it carries the only review-semantics migration and unblocks the longest deferred UI
   chain; the mirror scan is independent and follows as slice 2.
3. **UI = thin fields now, web slice after** — S-drift-1 ships `next_review_due`/`review_state` in the
   existing serializers (the established thin read-enrichment pattern); a single trailing **S-web-8**
   delivers all overdue UI signals after S-drift-3.
4. **Backfill = opt-in, none** — existing docs (incl. the live dev vault) keep `review_period = NULL`
   → exempt from the sweep until an owner sets it via metadata PATCH. No upgrade task flood.
5. **`review_state` = derived at read** — computed from `next_review_due` vs today (+ lead window) in
   serializers; never stored. (Deliberate, noted divergence from doc 14's literal
   `(next_review_due, review_state)` index line — a partial index on `next_review_due` alone serves
   the sweep; doc 05 §9.3 specifies the *field*, not its storage.)
6. **Default `review_period` = constant 24 months** applied server-side at authored create (doc 04's
   "e.g. 12/24/36 months" middle value). An org-configurable default is additive later
   (`system_config`), no migration needed then.

## 1. Why / what

**D5 (doc 05 §9.1):** *"Documents past their review-by date (latent drift: reality moved, doc didn't).
Each Document has a `review_interval` and `next_review_due`; Beat sweep flags overdue, surfaces on the
PDCA dashboards, and notifies owner. (Supports metric M3 < 5 % overdue.)"* The full behavioral spec is
**doc 04 §9** (review scheduling, outcomes, the `review_confirmed` signature) + doc 04 §6.1 (the
metadata fields). Today **zero schema exists** — but every hook is pre-paved:

- `WorkflowSubjectType.PERIODIC_REVIEW` and `TaskType.PERIODIC_REVIEW` already exist in the enums.
- `signature_event.meaning = review_confirmed` is already in the canonical R2 enum ("emitted by a
  periodic review that concludes no change needed") — no signature-enum migration.
- The DCR reason class `periodic_review` already exists (doc 05 §5.2) — the "change needed" outcome
  has a landing place.
- `PATCH /documents/{id}` under `document.manage_metadata` already exists — `review_period` rides it
  (doc 05 §11.2 normalizes `document.set_review_interval` → `document.manage_metadata`).

**This slice ships:** migration 0045 (3 columns + index + 2 audit event-type values + the seeded
`periodic_review` workflow definition) · the recompute rules on create/release/review/PATCH · the
daily Beat review-sweep (lead-window task creation + overdue escalation) · the `PERIODIC_REVIEW`
dispatch on `POST /tasks/{id}/decision` · the read surface (document serializers + the compliance
checklist's deferred overdue leg) · contract updates in-PR.

**No new permission key** (R38 untouched): the review decision rides task membership; setting the
period rides `document.manage_metadata`; reads ride `document.read` / the existing checklist key; the
sweep is a system op.

## 2. Schema — migration `0045`

On `documented_information`:

| Column | Type | Null | Meaning |
|---|---|---|---|
| `review_period_months` | `INTEGER` | yes | Per-doc currency interval in months. NULL = not scheduled (legacy/opt-out). **Amended from doc 14's literal `INTERVAL`:** psycopg3 cannot load a PG interval containing month components into `timedelta` — an `INTERVAL '24 months'` column would crash every ORM read. Months-as-int is the honest storage; date math via a pure `add_months()` (day-clamped). |
| `next_review_due` | `DATE` | yes | **Stored** (not derivable: review-confirm resets it from the *review* date). |
| `last_reviewed_at` | `TIMESTAMPTZ` | yes | Set by review-confirm; makes the recompute rule deterministic. |

- **Index:** partial `ix_documented_information_next_review_due ON (next_review_due) WHERE
  next_review_due IS NOT NULL` — serves the sweep (doc 14's index intent, owner fork §0.5).
  ⚠ `migrations/env.py._include_object` excludes expression/partial indexes from autogenerate —
  mirror the partial index in the ORM `__table_args__` anyway and confirm `alembic check` is clean
  (the 0020 lesson).
- **`event_type` enum:** `ALTER TYPE … ADD VALUE` (additive, no-op downgrade — the 0011 pattern,
  tuples sourced from the ORM `*_VALUES`): `REVIEW_CONFIRMED`, `REVIEW_OVERDUE`.
- **Seed:** a `periodic_review` workflow definition (single stage, the 0043 `dcr_approval` seed
  precedent): one stage `review` (mode `PARALLEL` + quorum ANY — the 0043 house shape; the engine
  materializes a one-person SEQUENTIAL identically), task type `PERIODIC_REVIEW`, assignee resolved
  at instantiate to the document owner via a new additive `context_users` assignee-spec key.
  Downgrade deletes the seed guarded `NOT EXISTS(<child instance>)` (the 0023 lesson).
  ⚠ The org lookup must NOT copy 0043's `WHERE short_code='DEFAULT'` `scalar_one()` — an
  operational install renames the short_code at setup G-E (this live box: `AHT`), so 0045 falls
  back to the D1 single-org row (never skip-if-absent: a missing seed = the sweep 500s daily).
- **API representation:** **`review_period_months` (int, 1–120, or null)** in all payloads — the
  column and the API field are the same unit, no conversion layer.
- **`metadata_snapshot`:** `review_period` is Snapshot-✔ (doc 04 §6.1) — add it to the shared
  `_snapshot(doc)` builder so new versions freeze it (the doc 05 §8.2 metadata-diff worked example
  literally shows "Review interval 24 → 12 months" — this falls out of the existing metadata diff for
  free). Old snapshots simply lack the key; readers tolerate-missing. Do NOT branch the shared
  snapshot (engineering-patterns).

## 3. Derivation & write rules

**The recompute rule (single source of truth, one domain function):**

```
anchor          = the LATER of (last_reviewed_at, effective_from)   # whichever exist
next_review_due = date_in_org_tz(anchor) + review_period_months     # add_months, day-clamped
                  (NULL if review_period_months is NULL or no anchor exists)
```

(Amended from the earlier `last_reviewed_at or effective_from`: max-of-the-two makes ONE rule
correct at every trigger — a re-release after a confirm anchors on the newer `effective_from`, a
confirm after a release anchors on the newer review date.)

| Trigger | Behavior |
|---|---|
| **Authored create** (`create_document`) | `review_period` defaults to **24 months** (constant `REVIEW_PERIOD_DEFAULT_MONTHS = 24`). `next_review_due` stays NULL (no `effective_from` yet). |
| **Release / cutover** (T6 immediate release AND the `release_due_versions` scheduled cutover) | recompute: `next_review_due = effective_from + review_period` ("recomputed on release", doc 04 §6.1). |
| **Review-confirm** (decision outcome `complete`, §5) | `last_reviewed_at = now()`; recompute from the review date ("recomputed on review"). |
| **`PATCH /documents/{id}`** (`document.manage_metadata`) | accepts `review_period_months` (int or null); recompute on change. Setting it on a legacy doc is the **opt-in** path (§0.4). Clearing to null exempts the doc from the sweep (next_review_due → NULL). |
| **T2 submit gate** | **Amended from a 422 to an auto-default:** at `Draft → InReview`, a NULL `review_period_months` is set to the 24-month default (not rejected). Rationale: a legacy doc revised between S-drift-1 and S-web-8 would otherwise be STRANDED at submit (the SPA has no field to set it yet; API-PATCH-only escape). Auto-default = the create-default applied late — every doc that passes T2 has a period (the doc 04 §6.1 intent) and nothing ever blocks. |
| **S-ing-5 import baseline** | leaves `review_period` **NULL** — imports are exactly the "pre-existing documents" class the owner opted out of backfilling (§0.4); a bulk import of legacy docs with past as-of dates must not instantly flood overdue tasks. Owners opt in per doc (or per family later) via PATCH. |

**`review_state` (derived projection, never stored):**

| Value | Condition (org-tz today) |
|---|---|
| `null` | `next_review_due IS NULL` (not scheduled) |
| `current` | `today < next_review_due − lead` |
| `due_soon` | `next_review_due − lead ≤ today < next_review_due` |
| `overdue` | `today ≥ next_review_due` |

Lead window = constant `REVIEW_LEAD_DAYS = 30` (doc 04 §9.1's "e.g. 30 days"; org-config later,
additive). The projection lives in ONE domain function used by every serializer + the checklist.

## 4. The Beat review-sweep

`easysynq.documents.review_sweep`, **daily** (86400 s) Beat entry — the retention-sweep module shape
(fresh disposed async engine, `asyncio.run`, registered in `tasks/__init__.py` + the `app.tasks`
membership unit test), **single-flighted via a session advisory lock** (`LOCK_REVIEW_SWEEP`,
skip-if-held — the mirror-sync posture; no schema constraint stops two open instances per subject,
and acks-late re-delivery makes concurrent runs real).

Per run, over docs where `kind = DOCUMENT` AND `current_state = Effective` AND
`next_review_due IS NOT NULL`:

1. **Lead-window task creation** — for docs with `next_review_due ≤ today + 30d`: if **no open
   `PERIODIC_REVIEW` workflow instance exists for this `subject_id`** (the idempotency check — the
   `WHERE NOT EXISTS` pattern), instantiate the seeded `periodic_review` definition:
   `subject_type = PERIODIC_REVIEW`, `subject_id = documented_information.id`, single task with
   `type = PERIODIC_REVIEW`, `assignee_user_id = doc.owner` (+ candidate_pool `[owner]`),
   `due_at = next_review_due`. The task lands in the existing `/tasks` inbox.
2. **Overdue escalation** — for open (`PENDING`) review tasks whose `due_at < now`: write ONE
   `REVIEW_OVERDUE` audit event (`object_type = document`, **`scope_ref = identifier`** so
   `GET /documents/{id}/audit-events` surfaces it — the S-ing-5 precedent). ⚠ Do **NOT** flip the
   task to `ESCALATED`: `engine.decide()` accepts only `PENDING` tasks (engine.py:390), so an
   ESCALATED task would be **undecidable** — and widening the shared engine guard is off-limits (the
   keep-the-welded-path rule). Idempotence anchor: stamp the workflow-instance id into the event's
   `after` and skip if a `REVIEW_OVERDUE` for this `object_id` with that `after.instance_id` exists
   — once per CYCLE (a new cycle's instance re-arms it), and clock-skew-proof (an
   `occurred_at`-vs-`started_at` comparison would race the PG clock). The overdue *signal* to humans
   is the derived `review_state = overdue` + the task's own `due_at` — both already serializable;
   email stays best-effort/out of scope.
3. **Re-nag semantics (deliberate):** a `changes_requested` decision (§5) closes the instance without
   resetting the clock — if the owner then checks the doc out, `current_state` leaves `Effective` and
   the sweep skips it (the state filter); if they do nothing, the next sweep opens a fresh task. A doc
   that is overdue and untouched **stays nagged** — that is the honest ISO posture, not a bug.
4. **Idempotent + acks-late-safe:** re-running the sweep creates no duplicate instances/tasks (the
   open-instance check) and writes no duplicate `REVIEW_OVERDUE` (the audit-exists check). The doc 04
   "overdue ≥ grace → surface as potential Finding" row is **optional policy — deferred** (non-goal §7).

## 5. The review decision — `POST /tasks/{id}/decision`, `PERIODIC_REVIEW` dispatch

The existing decision endpoint gains a `PERIODIC_REVIEW` subject handler (the S-dcr-4 dispatch
precedent; the DOCUMENT and DCR and CAPA paths stay byte-identical). Membership follows the FULL
sibling posture (`_assert_dcr_approver`/`_assert_capa_approver`): non-membership **404-collapses**
(never a 403 that leaks another user's task), and authority is re-checked LIVE — the caller must be
the document's CURRENT `owner_user_id`, not merely in the pool frozen at sweep time (the
`context_users` analogue of the siblings' live role re-check).

| Outcome | Doc 04 §9.2 row | Effect (one transaction) |
|---|---|---|
| `complete` | **"No change needed"** | `signature_event(meaning = review_confirmed, signed_object_type = document_version, signed_object_id = current_effective_version_id, content_digest = that version's source-blob sha256)` · audit `REVIEW_CONFIRMED` (`object_type = document`, `scope_ref = identifier`) · `last_reviewed_at = now()` · recompute `next_review_due` (§3) · task `DONE` + outcome row · instance → terminal. |
| `changes_requested` | **"Minor/major revision needed"** | task `DONE` + outcome row · instance → terminal · **no clock reset, no new audit type** (the engine's task-decided audit suffices) — the continuation is the owner raising a DCR (reason class `periodic_review`) / checking out; the sweep re-nags while the doc stays Effective and due (§4.3). **No auto-DCR in v1** (non-goal §7). |
| *(obsolete it)* | **"Obsolete it"** | NOT a task outcome — rides the existing obsolete endpoint (T11, with the S-dcr-5 §7.3 gate). The trailing web slice offers the link from the task UI. |

Other outcome kinds (`approve`, `reject`, `verify`, `acknowledge`) → 422 for this subject type.

## 6. Read surface (the thin enrichment for S-web-8)

- **Document detail + library listing serializers** gain `review_period_months`, `next_review_due`,
  `last_reviewed_at`, `review_state` (§3 projection). ⚠ Pin the eventual MSW fixtures to THESE
  serializer shapes (the #1 web-track false-PASS).
- **Compliance checklist** (`GET /reports/compliance-checklist`): each ★ row gains
  `overdue_review: bool` (true iff ANY mapped Effective doc has `review_state = overdue`) and the
  rollup gains an `overdue_review` count — closing the S10 service's explicitly-deferred doc 13
  "overdue review?" leg. Row status (COVERED/PARTIAL/GAP) is unchanged — overdue is an orthogonal
  flag, not a fourth status.
- **The task serializer** already carries `subject_type`/`subject_id` (S-web-7b) — a
  `PERIODIC_REVIEW` task is routable by the SPA without new fields.
- **`packages/contracts/openapi.yaml`** updated in-PR (document schemas, PATCH body, checklist
  schema, the decision endpoint's subject-outcome note).

## 7. Non-goals (this slice)

- **No UI beyond serializer fields** (S-web-8 trails the family — owner fork §0.3).
- **No auto-DCR** on `changes_requested` (the owner raises it; deep-link prefill is S-web-8's call).
- **No grace→Finding policy** (doc 04 §9.1 marks it optional).
- **No org-config** for the default period or lead window (constants; `system_config` later is additive).
- **No email/notification engine work** (the in-app task + the derived `overdue` state + the
  `REVIEW_OVERDUE` audit are the only signals; task state is never flipped — §4.2).
- **No backfill** of existing docs, **no import-path default** (§0.4, §3).
- **No currency/overdue *report* endpoint** (doc 13's saved-search/report leg stays in the v1.x
  reporting family; the checklist flag + serializer fields are this slice's reporting surface).

## 8. Testing & verification

- **Unit:** the recompute rule (max-anchor, org-tz non-identity, null anchors, `add_months`
  day-clamping) + the `review_state` projection boundaries; sweep task registered in `app.tasks`.
  (The T2 leg is the auto-default — integration-tested; periods are plain ints, no conversion.)
- **Integration (Linux-CI-only on this box):** migration round-trip via `/check-migrations` locally;
  sweep creates exactly one instance/task per due doc and is idempotent on re-run; escalation writes
  `REVIEW_OVERDUE` once; `complete` writes the `review_confirmed` signature bound to the Effective version's digest +
  resets the clock; `changes_requested` leaves the clock + re-nag behavior; checklist overdue leg.
  ⚠ **Run-scoped/delta assertions only** — the `-m integration` suite shares one session DB
  (engineering-patterns); a sweep test MUST scope to this run's docs (other files leave Effective docs
  behind; a global "created N tasks" assertion is a false-PASS/flake).
- **Local gates (this box):** api static checks (ruff/format/mypy-strict) + `/check-migrations` +
  `/check-contracts`; both api test suites run in Linux CI.
- **Pre-PR:** diff-critic on the branch diff; **pre-merge live smoke** (rebuild api+worker+beat
  images first — compose changes aren't live until `up -d --build`): set a short `review_period` on a
  doc via PATCH (demo needs `document.*` SYSTEM overrides on the LIVE login's app_user row, org AHT),
  force one sweep run, see the task in `/tasks`, decide `complete`, verify the signature row + the
  reset `next_review_due` + the checklist flag.

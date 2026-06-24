# S-notify-5a — Awareness events: `doc.released`, read-scope-filtered (design)

> Notification family (doc 10 §9, R53/R54), slice 5a. The first of the three slice-5 subsystems
> (5a awareness events → 5b Health panel + Config tab → 5c SSE), owner-confirmed split + sequencing.
> **BE-only. Migration 0066. No new permission key (catalog stays 102).**

- **Date:** 2026-06-23
- **Depends on:** S-notify-1 (the outbox spine, dispatch), S-notify-3a (the class-aware enqueue: `classes.py`, `preferences`/`quiet`/`schedule`, digests), S-notify-4 (the Beat-sweep + claim-and-stamp idempotency pattern).
- **Migration head:** `0065` → **`0066`**.
- **Validated:** 2026-06-23 — a code-anchor + adversarial-refute workflow (8 agents) confirmed the design against the live codebase and surfaced 3 fixes (the re-release dedup defect, the SERIALIZABLE savepoint swallow, the fan-out-time read-filter wording); all folded in below (owner-ratified the dedup-grain + send-time decisions, §13).

---

## 1. Goal & context

The notification family reaches users for **task** events (`task.assigned`/`due_soon`/`overdue`/`escalated`).
doc 10 §9.2's event catalog also lists **awareness** events — QMS lifecycle facts that are not a unit of
work assigned to anyone, the headline being **`doc.released`**: *"New Effective version (subscribers +
process readers)."* These never reach users today — the awareness event keys are already classed in
`classes.py` (`doc.released` → `AWARENESS` → daily digest) but **no code emits them and no template is
seeded**.

The **defining constraint** (doc 10 §9.2 scoping note, R3 deny-wins): an awareness notification *"only
reaches users who can read that document/process."* `doc.released` is a **broadcast filtered by
`document.read`** — not to all org members, not to task-holders, but to exactly the set of active users
the PDP would let read the document **at release/fan-out time** (the filter is computed once when the
fan-out worker runs, not re-verified at each later digest send — an R32-bounded residual, §4 + §15).
There is **no reverse "who-can-read-X" index** in the codebase today; building one correctly is the
substance of this slice.

This slice ships **`doc.released` only** (owner-confirmed MVP). The infra it builds — an awareness
outbox, a read-scope audience resolver, a fan-out worker, the subject-based enqueue path — is the rails
every later awareness event key rides (a hook call + a seeded template each). Those are named residuals.

## 2. Scope

**In scope (BE-only):**
- A new `awareness_event` **outbox** table (migration 0066) + a new nullable `notification.subject_version_id` column + an awareness **dedup** partial-unique index on `notification` (version-discriminated — §5).
- One **emission hook** at the vault release chokepoint (`_cutover`) writing one `awareness_event` row, best-effort, atomic with the release (SERIALIZABLE-aware — §6).
- A **read-scope audience resolver** (`services/authz/audience.py::resolve_document_readers`) — the per-user `authorize(document.read)` loop.
- A **subject-based enqueue path** (`dispatch.enqueue_awareness_one`) reusing the 3a class/digest/quiet-hours/email machinery, with the version-discriminated awareness dedup target.
- A **fan-out Beat worker** (`tasks/notifications.py::awareness_fan_out` @120 s) that claims pending `awareness_event` rows, resolves the read-scoped audience, and creates per-recipient notification (+ digest/email) rows, idempotently.
- The seeded **`doc.released` template** (in-app + email) + its variable whitelist.
- `openapi.yaml`: **no change** (no new endpoint; reads are the existing self-scoped `GET /notifications`).

**Out of scope (named residuals, §15):** every other awareness event key (`doc.approved`, `doc.obsoleted`,
`dcr.raised/accepted`, `audit.scheduled/report_issued`, `finding.assigned`, `capa.stage_changed`,
`mr.scheduled`); a subscription/opt-in model; an audience cache; a send-time `document.read` re-check
(the v1 filter is fan-out-time, an accepted R32-bounded residual); imported-baseline awareness; any FE
(the bell already renders any notification, slice-2); SSE (5c); the Health panel (5b).

## 3. Architecture — the awareness outbox + fan-out worker

The read-scope audience for one release is ≈ *N active users × 2 grant queries* (the per-user PDP loop,
§4). That is fine in a **worker** but far too heavy to run inside the **SERIALIZABLE `_cutover`** hot path
(it would lengthen the release's serialization window and, under the family's best-effort posture, a
notification bug must never block a release — R53). So fan-out is **decoupled** via a transactional
outbox, exactly the family's R53 philosophy applied to a 1→N event:

```
[ vault _cutover  (SERIALIZABLE, T6 Approved→Effective) ]
   … append RELEASED audit (in-txn) …
   record_awareness_event(...)         ← ONE cheap INSERT, best-effort begin_nested() SAVEPOINT
   session.commit()                    ← INV-1 + SERIALIZABLE adjudicate the race here
        │  (race-loser's whole txn — incl. the savepoint row — rolls back: no phantom event)
        ▼
   awareness_event row  (fanned_out_at = NULL, subject_version_id = the Effective version)
        │
[ Beat: awareness_fan_out @120s ]   (worker process)
   claim pending event  (FOR UPDATE SKIP LOCKED, fresh session per event)
   render doc.released template ONCE
   audience = resolve_document_readers(org_id, doc_id)   ← the per-user authorize() loop (§4)
   for reader in audience (minus the actor):
       recipient = _recipient_for_user(uid)   ← uid→Recipient, double-filters inactive/cross-org
       enqueue_awareness_one(...)      ← in-app row always; email row per class/mode/quiet-hours (3a)
   stamp fanned_out_at ; commit        ← all in ONE per-event txn → idempotent
        │
[ existing outbox_drain @120s ]  sends the IMMEDIATE email rows
[ existing digest sweep   @1h  ]  bundles the DAILY ones (awareness default = daily)
[ slice-2 bell ]                  shows the in-app rows
```

The emit side is trivially cheap and atomic with the release; the expensive fan-out is off the hot path,
idempotent, and re-uses all the already-shipped delivery machinery downstream.

## 4. The read-scope audience resolver (the crux)

**New module `services/authz/audience.py`** (the audience computation is an authz *inversion*, reusable
beyond notifications — kept next to the PDP it depends on, not buried in `services/notifications/`):

```python
async def resolve_document_readers(
    session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID, *, now: datetime.datetime,
) -> list[uuid.UUID]:
    """All ACTIVE users in org_id who can read doc_id, per the real PDP (deny-wins, ABAC-complete),
    AT FAN-OUT TIME (not re-verified at later send — §15 residual)."""
```

Implementation: load the active users (`AppUser.org_id == org_id`, `status NOT IN {LOCKED, DISABLED,
RETIRED}` — mirror `recipients._INACTIVE`); build the document's `ResourceContext` **once** via a new
**`services/authz/resource.py::build_document_resource_context(session, doc_id)`** (`artifact_id`,
`folder_path`, `document_level`, `lifecycle_state`, `process_ids` via `vault_repo.process_ids_for_doc`).
This is extracted from `api/documents._document_scope_by_id`, which becomes a thin wrapper delegating to
it (so the api gate and the audience resolver share one builder; **no `api/` → `services/` import** —
authority flows the right way). The extracted builder **must preserve the doc-missing degraded fallback**
verbatim (`_document_scope_by_id` returns `ResourceContext(artifact_id=str(doc_id))` when the doc is
None — moot for awareness since the doc always exists post-release, but the api gate's tests pin it).
Then per user `gather_grants(session, uid, org_id, "document.read")` + `authorize(grants,
"document.read", resource, RequestContext(now=now))`, collecting `decision.allow`.

**Why this and not a SQL join (deny-wins is not joinable):** DENY lives in a separate `permission_override`
table and beats any role ALLOW; ABAC predicates (`valid_from/until`, `ip_allow`, `read_only`,
`lifecycle_state`) are evaluated at decision-time against the request clock; SoD is an audit-history
check. A grant-join produces false positives (ignores DENY) and false negatives (ignores time-windowed
predicates). The per-user PDP loop is the **only** ABAC-correct answer and reuses the exact path every
request takes. (`document.read` is `sig_hook=False, sod_sensitive=False` in the 0004 seed, so the
step-up gate and the SoD step are both inert here — `RequestContext`'s `step_up_satisfied`/
`actor_user_id` defaults never bite.)

**The resource context is requester-invariant** — `gather_grants` keys only on `(user_id, org_id, key)`
and **all** per-user variance (the Process-Owner `bound_scope` clamp included) lives in the returned
*grants*, never the resource. So building `ResourceContext` once and varying only the (here-inert)
`RequestContext` per user is correct.

**Cost:** ≈ N×2 queries + the one scope load. For a self-hosted single-org QMS (D1, typically 10–100
active users) that is ≈ 200 queries in a background worker per release — acceptable. Scale caching
(Redis-memoized per-user grants, or a **sound** candidate pre-filter that drops only users with *zero*
`document.read` grants — never a scope-matching pre-filter, which would wrongly drop SYSTEM-scope
readers) is a named residual (§15), not v1.

**`source_ip = None` (worker has no request IP):** a grant carrying an `ip_allow` predicate will **not**
match (`pdp.py` `source_ip is None` ⇒ no match), so such a reader is **excluded**. This is fail-safe
(under-includes, never over-includes) and consistent with the already-named codebase-wide
capability-probe `source_ip` gap; `ip_allow` is v1-deferred and unseeded. Documented limitation, not a
bug. (Over-inclusion was adversarially probed and refuted: `authorize` is deny-wins + deny-by-default,
every predicate can only *narrow* a grant, and a stale `now` can only drop or briefly-retain a
time-windowed grant — no path flips a DENY to an ALLOW.)

**uid → Recipient:** the resolver returns `list[uuid.UUID]`, but `enqueue_awareness_one` needs a
`Recipient`. The worker builds one per uid via the existing **`escalation._recipient_for_user(session,
uid, *, org_id=event.org_id)`** precedent, which re-checks active + same-org — a second safety net on top
of the resolver's active-user filter.

**Self-suppression:** the fan-out drops `awareness_event.actor_user_id` from the audience — the releaser
is not notified of their own release. On the system-release (`release_due` Beat) path `actor` is `None`,
so `actor_user_id` is stored NULL and the subtract is a no-op — every reader is notified (correct; a
system go-live has no self to suppress).

**Admins:** the System Administrator holds **no `document.*`** (deny-by-default); the loop therefore
**excludes** them from `doc.released` awareness automatically — correct, no special-casing. A user who
genuinely holds `document.read` at SYSTEM scope (e.g. a QMS Owner) matches every document — correct.

**Send-time posture (R32-bounded residual, owner-ratified 2026-06-23):** the audience is resolved at
**fan-out time**; the downstream delivery machinery (`digest`/`drain`) re-checks org-flag / preference /
active / has-email at send but **does not** re-run `authorize(document.read)`. So a reader revoked in the
≤2 min between fan-out and the digest send still receives the item. This matches the existing
task-notification posture and is bounded: the payload is **summary + deep link only** (R32 / §9.3 —
`subject.identifier/title/version.label`), and the deep link 403s the revoked reader at click, so **no
controlled content** leaks. Recorded as an accepted v1 residual (§15); a send-time `document.read`
re-check was considered and deferred (owner's call).

**Live `lifecycle_state` (fail-safe):** `build_document_resource_context` reads the doc's *current*
`current_state` at fan-out, which a rapid re-release/start-revision within the ≤2 min window could move.
`lifecycle_state` predicates can only *narrow* a grant, so this can only **under-include**, never over —
zero impact under the v1 seed set (no `lifecycle_state` predicate is seeded). Documented; snapshotting the
released state into `context` is a named residual (§15) only if such a predicate is ever used.

## 5. Data model & migration 0066

**New table `awareness_event` (the outbox):**

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `org_id` | uuid FK → organization | not null |
| `event_key` | text | not null (e.g. `doc.released`) — TEXT, no enum (the family convention) |
| `subject_type` | text | not null (e.g. `DOCUMENT`) |
| `subject_id` | uuid | not null — the subject head id (plain uuid, polymorphic, no FK) |
| `subject_version_id` | uuid, nullable | **the Effective `document_version.id` at release — the dedup discriminator** so each new version re-notifies (the re-release fix, §6/§7). Plain uuid (no FK — operational outbox, mirrors `subject_id`); always set for `doc.released`, nullable for future non-version awareness keys |
| `actor_user_id` | uuid FK → app_user, nullable | the releaser (for self-suppression); null for system-triggered releases (`release_due`) |
| `context` | jsonb, not null default `{}` | emit-time facts the worker needs in the template (e.g. `{"version.label": "2.0"}`) |
| `occurred_at` | timestamptz | not null — the release instant |
| `fanned_out_at` | timestamptz, nullable | the idempotency stamp; the claim predicate |
| `created_at` | timestamptz | not null default now() |

- Index `ix_awareness_event_pending` on `(occurred_at)` **WHERE `fanned_out_at IS NULL`** — backs the claim scan (migration-managed partial index → add to `env.py::_MIGRATION_MANAGED_INDEXES`, absent from the ORM).
- **GRANT posture (the family trap):** `awareness_event` is a TABLE, so 0010's `ALTER DEFAULT PRIVILEGES … GRANT … DELETE` auto-grants the app role all DML. The app role needs **INSERT** (emit, in the api/worker), **SELECT** + **UPDATE** (claim + stamp `fanned_out_at`, in the worker), but **not DELETE** → **`REVOKE DELETE`** (the 0063 ledger precedent). The migration-reviewer must confirm against the live `information_schema.role_table_grants`.

**New column `notification.subject_version_id` (uuid, nullable):** so the per-recipient dedup index can
discriminate by version (the `notification` table already carries `subject_type`/`subject_id` from
slice-1). `enqueue_awareness_one` sets it from `awareness_event.subject_version_id`; the welded task path
leaves it NULL (untouched). Additive, nullable — no backfill.

**New dedup index on `notification` (version-discriminated — the re-release fix):** the existing dedup
index is partial `WHERE task_id IS NOT NULL`, so awareness rows (`task_id IS NULL`) are uncovered. Add
**`uq_notification_dedup_awareness`** = unique on `(recipient_user_id, event_key, subject_type,
subject_id, subject_version_id)` **WHERE `task_id IS NULL`**. **The `subject_version_id` term is
load-bearing:** a re-revision routes the *same* `doc_id` back through `release()`/`_cutover` (the doc row
is mutated in place, `current_effective_version_id` repointed), so a *version-less* key would collide with
the surviving release-#1 notification row (notification rows are never deleted) and **silently notify no
one** on every re-release — worst for the singleton register heads (RSK/CTX/IPR) and OBJ/MR, which
republish through one stable head id every cycle. With `subject_version_id` in the key, each distinct
Effective version re-notifies; the prior-version row remains the per-`(recipient,version)` dedup backstop
that survives a fan-out redelivery. Migration-managed (created in 0066, added to
`env.py::_MIGRATION_MANAGED_INDEXES`, **absent from the ORM** — the S-notify-3a/0064 lesson: a partial
index declared in the ORM round-trips wrong). `enqueue_awareness_one`'s `ON CONFLICT DO NOTHING` targets
it with `index_elements=[recipient_user_id, event_key, subject_type, subject_id, subject_version_id]` +
the matching `index_where=sa.text("task_id IS NULL")`.

**Seed the `doc.released` template** (en, v1, `is_effective=true`) via the 0065 raw
`INSERT … ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING` form (re-upgrade-safe, targets
the partial-unique template index — **not** a plain `bulk_insert`). **Downgrade:** drop the indexes/table/
column; the template delete must use a **`NOT EXISTS (SELECT 1 FROM notification WHERE template_id = …)`
guard** (the `notification.template_id` RESTRICT FK aborts a delete on a populated DB once a `doc.released`
notification exists — the 0023/0065 CI-blind trap). No new enum value; no `EventType`/`audit_object_type`
ALTER (the outbox is operational, not WORM — the `RELEASED` audit row already exists and is untouched).

## 6. The emission hook (`_cutover`)

In `services/vault/lifecycle.py::_cutover`, after the in-txn `RELEASED` audit append (the
RELEASED/SUPERSEDED audit block ≈ lines 551-571) and **before** `session.commit()` (line 573), call a new
best-effort helper:

```python
await record_awareness_event(
    session, org_id=doc.org_id, event_key="doc.released",
    subject_type="DOCUMENT", subject_id=doc.id,
    subject_version_id=version.id,                    # the dedup discriminator (re-release fix, §5)
    actor_user_id=(actor.id if actor is not None else None),
    occurred_at=now, context={"version.label": version.revision_label},
)
```

(`version` is the freshly-promoted Effective `DocumentVersion` in scope at the hook; the display string is
`version.revision_label` — there is **no** `version.label` column; the template variable *key* stays
`"version.label"` but its *value* is sourced from `revision_label`.)

**`services/notifications/awareness.py::record_awareness_event`** wraps the single INSERT in
`async with session.begin_nested():` + a best-effort `try/except` that logs and swallows — following the
*shape* of `enqueue_task_notifications` (dispatch.py) **but with a SERIALIZABLE adaptation that is NOT
optional:** every existing `enqueue_task_notifications` caller runs in a Read-Committed txn, whereas
`_cutover` is **SERIALIZABLE**. Under SERIALIZABLE a `40001`/`40P01` raised by *any* statement poisons the
whole transaction, and a blanket swallow would then hit a doomed `session.commit()` *and* mask a genuine
serialization conflict — turning `_cutover`'s clean 409 into an opaque 500. So `record_awareness_event`
must **re-raise** any `DBAPIError` whose SQLSTATE ∈ `{40001, 40P01, 23505}` (let `_cutover`'s outer
`_is_race_loss` path handle it as the clean 409 it already produces) and **swallow only non-serialization
exceptions**. Rationale: a non-serialization savepoint failure rolls back only the awareness row — the
release still commits (R53: awareness must never block a transition). On a SERIALIZABLE **race loss**, the
loser's *entire* txn rolls back, discarding the savepoint row too → exactly one `awareness_event` per
successful release (INV-1 `uq_document_version_one_effective` guarantees one winner — the same reasoning
that keeps the `RELEASED` audit phantom-free). No import cycle: `lifecycle.py → notifications.awareness`
(which imports only `db.models`); notifications never imports vault on the emit side.

**Which releases fire:** `_cutover` is the single chokepoint for every **authoring-FSM** release — ordinary
controlled documents, OBJ, MR, and the singleton register heads (RSK/CTX/IPR) all route through the shared
`release()`/`release_due`. **The MVP fires `doc.released` for ALL of them** (owner-confirmed 2026-06-23:
*fire for all* — read-scope keeps each relevant; no chokepoint special-casing). With the version-
discriminated dedup (§5) this is now genuinely correct for the registers (a republish re-notifies rather
than going silently dead). **Imported baselines are out of scope:** the ingestion import-baseline path
(`ingestion/commit.py`) creates versions directly at `version_state=Effective` *without* `_cutover`, so an
imported baseline emits no `doc.released` — a deliberate exclusion (an import is not a "release" event in
the awareness sense; named in §15). If register-republish noise ever proves real in practice, suppress at
the **worker** via `subject_type` (never special-case the `_cutover` chokepoint).

## 7. The subject-based enqueue path

**`dispatch.enqueue_awareness_one(session, *, org_id, subject, subject_version_id, recipient, event_key,
context_vars, now, org_enabled, org_pierce) -> EnqueueOutcome`** — a sibling of `_enqueue_one` for a
**subject** (not a task):
- Builds the variable bag from `subject` (`subject.identifier/title/kind`, `deep_link`) + `context_vars`
  (e.g. `version.label`) + `recipient.first_name` + `prefs_link()` — **drops** the `task.*` vars.
- Resolves class/mode/digest exactly as `_enqueue_one` (awareness → daily by default → `digest_due_at`;
  immediate honours quiet hours unless a critical pierce — inert for awareness).
- Inserts the `notification` row with `task_id=NULL`, `subject_type=subject.kind`,
  `subject_id=<doc id>`, **`subject_version_id=subject_version_id`**, `ON CONFLICT DO NOTHING` on
  **`uq_notification_dedup_awareness`** (`index_elements=[recipient_user_id, event_key, subject_type,
  subject_id, subject_version_id]`, `index_where="task_id IS NULL"`). Email row per `wants_email and
  IMMEDIATE` (the awareness default is DAILY → typically no immediate email; the digest sweep handles it).

**Keep the welded task path byte-identical (engineering-patterns "new module, prove parity"):** rather
than parametrise `_enqueue_one(task: Task | None)` (which the slice-1–4 suites pin), **extract the pure
class/mode/email-eligibility resolution** (the `pref → effective_preferences → class_of → mode →
_email_eligible → wants_email/is_daily → digest_due_at` block, already pure over `(pref, event_key,
org_enabled, recipient.email, now)` and touching no `Task` attr) into a tiny shared helper that *both*
`_enqueue_one` and `enqueue_awareness_one` call — the existing `test_notification_*` suites are the
regression backstop that `_enqueue_one`'s behaviour is unchanged (run the **full `/check-api`**, not a
per-file unit run, to catch any drift in the pinned task path). Duplication is the documented fallback if
extraction perturbs the old path.

## 8. The fan-out Beat worker

**`tasks/notifications.py::awareness_fan_out`** (Beat @120 s; register in `tasks/app.py` + a
task-registration unit test — the family rule, else `.delay`/Beat publishes to a name no worker handles).
Core in **`services/notifications/fanout.py`**, mirroring the claim+stamp *shape* of
`escalation.sweep_task_timers` / `digest.sweep_digests` — with **PK-pinned `FOR UPDATE SKIP LOCKED` and
NO per-event advisory lock** (the `outbox_drain` precedent; the digest/escalation advisory locks are
belt-and-suspenders on top of already-PK-pinned claims, **not** load-bearing — do **not** add one to
"match" the siblings):

- `fan_out_awareness(sessionmaker, now)`: read pending event ids (`fanned_out_at IS NULL`, oldest first,
  **bounded `LIMIT`** so a release burst can't fan out unboundedly in one sweep), then **fresh session per
  event** (the `MissingGreenlet` guard).
- `process_one_awareness_event(session, event_id, now)`: `SELECT … WHERE id = :id AND fanned_out_at IS
  NULL FOR UPDATE SKIP LOCKED` (+ `populate_existing` — the S-drift-1 stale-attr trap); if already
  claimed/stamped → no-op. Load org config (`org_enabled`/`org_pierce`). `resolve_subject(...)` once.
  `render(...)` once — **template miss → do NOT stamp** (log, retry after restore — the 3a/4 rule).
  `audience = resolve_document_readers(...) − {actor}`. Per reader: build `recipient =
  _recipient_for_user(session, uid, org_id=event.org_id)` (double-filters inactive/cross-org), then
  `enqueue_awareness_one(..., subject_version_id=event.subject_version_id, ...)`. Stamp
  `fanned_out_at = now`. **One commit per event** → atomic claim+fanout+stamp, idempotent under
  `task_acks_late` redelivery and concurrent sweeps (`SKIP LOCKED` keeps two sweeps off the same row; the
  per-recipient dedup index is the belt-and-suspenders backstop).
- **No reaper needed** (unlike ingestion): a worker death mid-event rolls the whole txn back → the row's
  `fanned_out_at` stays NULL → re-claimed next sweep. There is no lock-free human-paced rest state here
  (the path is fully machine-driven, terminal-on-stamp), so there is nothing for a reaper to misread.

## 9. Templates & event vocabulary

- `event_key = "doc.released"` (already in `classes._EVENT_CLASS` → `AWARENESS`). No new constant *required*
  but add `EVENT_DOC_RELEASED = "doc.released"` to `constants.py` for symmetry + a `VARIABLE_WHITELIST`
  entry: `{recipient.first_name, subject.identifier, subject.title, subject.kind, version.label,
  deep_link, prefs_link}` (the `version.label` *key* carries the `version.revision_label` value, §6).
- Template (en, v1): in-app form (compact — *"{{subject.identifier}} {{version.label}} is now Effective"*)
  + email form (subject + body, summary + deep link only, **no controlled content** — R32/§9.3). The
  renderer is the existing logic-free HTML-escaped whitelisted `render` (an un-whitelisted variable is
  never substituted — a render test pins this so a later template edit can't widen the payload).
- Deep link: `subjects.resolve_subject("DOCUMENT", doc_id)` already routes `DOCUMENT → /documents/{id}`
  (with OBJ→`/objectives/{id}`, MR→`/management-reviews/{id}` subtype routing, orphan-satellite fallback
  to `/documents/{id}`) — **no `subjects.py` change needed**.

## 10. Permissions

**No new permission key (R38; catalog stays 102).** The audience resolver *consumes* `document.read`; it
introduces no new gate. The notification reads remain the slice-1 self-scoped `GET /notifications`
(authenticated-self). No role/grant seed.

## 11. Testing

**Unit** (`tests/unit`): `resolve_document_readers` with hand-built grants — ALLOW/role, **DENY-override
beats a SYSTEM-scope ALLOW (the deny-wins regression backstop)**, SYSTEM-scope reads all, PROCESS-scope
intersection, FOLDER-prefix, time-windowed predicate excluded, inactive user excluded, actor
self-suppression, **`actor=None` system release → NULL-subtract no-op (every reader notified)**,
`ip_allow`+no-IP excluded; `enqueue_awareness_one` class/mode/digest/email-eligibility (incl. the extracted
shared helper parity, and `subject_version_id` threaded into the dedup target); **template render — an
un-whitelisted variable is NOT substituted**. **Integration** (`tests/integration`, testcontainers): the
full `_cutover → awareness_event → awareness_fan_out → notification rows` path with seeded grants proving
the read-scope filter end-to-end (a reader gets it, a non-reader does not, the actor does not); **the
re-release regression — same doc, a SECOND distinct Effective version → every prior recipient gets a NEW
notification (not deduped)** (the §5 critical fix); idempotency (second sweep = 0 new rows; dedup holds);
concurrent fan-out (two sweeps, exactly-once via SKIP LOCKED); no-template → not stamped; org-email-off →
in-app row still created, no email row, and the DAILY default sets `digest_due_at`; **a serialization
failure injected on the awareness INSERT under a concurrent release → the release still yields a clean 409
(not a 500) and no phantom `awareness_event` row** (the §6 SERIALIZABLE fix). **Delta-based / run-scoped
assertions** (never assume a clean *or* dirty shared DB — the S-ing-4/S-drift-2 rule); FK-ordered cleanup
of any org/user the test creates (the S-notify-4 `test_restore` lesson — a leaked second `Organization`
aborts `test_restore`'s `scalar_one()`). The existing `test_notification_*` suites must stay green (the
welded-path parity backstop).

## 12. Migration-trap checklist (the family CI-blind class)

- [ ] `REVOKE DELETE` on `awareness_event` (0010 ALTER-DEFAULT-PRIVILEGES auto-grant); INSERT/SELECT/UPDATE retained — verify on live `role_table_grants`.
- [ ] `notification.subject_version_id` added (nullable, additive — no backfill); the welded task path leaves it NULL.
- [ ] `uq_notification_dedup_awareness` (incl. `subject_version_id`) + `ix_awareness_event_pending` in `env.py::_MIGRATION_MANAGED_INDEXES`, **absent from the ORM** (else `alembic check` phantom-DROP / wrong round-trip).
- [ ] The new `AwarenessEvent` model module imported in `db/models/__init__.py` (+ `__all__`) — else `alembic check` phantom-DROP, migrations CI red (the SlaPolicy 0065 precedent).
- [ ] Template seed via the 0065 raw `ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING` form (re-upgrade-safe, partial-index target); downgrade template delete guarded by `NOT EXISTS` (RESTRICT FK on populated DB).
- [ ] No bare-token CHECK/constraint name doubling (the 0064 trap) — 5a adds no CHECK, but if any is added, pass the bare token in both create + drop.
- [ ] `alembic` up↔down↔`alembic check` round-trip clean on a throwaway PG16 (`/check-migrations`) **and** a populated-DB downgrade (the CI-blind fresh-DB blind spot).

## 13. Owner decisions captured

- **AskUserQuestion (2026-06-23, brainstorming):** slice-5 split + sequencing = **5a→5b→5c as proposed**;
  5a emission architecture = **awareness-outbox + fan-out worker** (migration 0066); 5a event scope =
  **`doc.released` only (MVP)**.
- **AskUserQuestion (2026-06-23, post-validation):**
  - §14.1 republish scope = **fire for all** (ordinary docs + OBJ + MR + register heads).
  - The re-release dedup fix = **add `subject_version_id`** to `awareness_event` + the dedup index
    (option a — each distinct Effective version re-notifies; the prior-version row is the backstop).
  - Send-time read-scope = **accept the fan-out-time filter as an R32-bounded v1 residual** (no
    send-time `document.read` re-check; §4 + §15).
- **Validation provenance:** an 8-agent code-anchor + adversarial-refute workflow (2026-06-23) confirmed
  the design's code anchors and surfaced the 3 fixes folded into §4/§5/§6; the emit atomicity, fan-out
  idempotency, no-reaper claim, migration-trap checklist, over-inclusion safety, resource-context
  extraction, payload leak-safety, and event-storm risk were all adversarially probed and held sound.

## 14. Spec-review questions — resolved

1. **Register/OBJ/MR republishes:** **RESOLVED — fire for all** (owner, §13). The version-discriminated
   dedup (§5) makes this genuinely correct for the singleton register heads (a republish re-notifies, not
   silently dies). Worker-side `subject_type` suppression is the lever if churn ever proves noisy.
2. **Fan-out cadence:** `awareness_fan_out` @120 s (matches `outbox_drain`). Awareness is daily-digest by
   default, so in-app latency of ≤2 min is ample. **Confirmed: 120 s.**
3. **Version capture:** carry the effective version into `awareness_event` at emit — both
   `subject_version_id` (the dedup discriminator) and `context["version.label"] = version.revision_label`
   (the template var), the governing facts at release (vs re-deriving in the worker). **Confirmed: capture
   at emit.**

## 15. Named residuals (not faked; out of scope for 5a)

- **A send-time `document.read` re-check** — the v1 audience filter is fan-out-time; a reader revoked in
  the ≤2 min before the digest send still receives the R32-bounded summary + a deep link that 403s at
  click (owner-accepted, §4/§13). A focused re-verify at the email send + in-app read is the v1.x option.
- **Snapshotting the released `lifecycle_state`** into `awareness_event.context` — only needed if a
  `lifecycle_state` ABAC predicate is ever seeded (today the live-state read is fail-safe under-include;
  §4).
- **The remaining awareness event keys** (`doc.approved`, `doc.obsoleted`, `dcr.raised/accepted`,
  `audit.scheduled/report_issued`, `finding.assigned`, `capa.stage_changed`, `mr.scheduled`) — each a
  hook call + a seeded template on these rails; several have **non-read-scope** audiences (role/stakeholder)
  that need their own resolver shape → their own slice(s). (A future non-version awareness key whose
  `subject_version_id` is NULL would not dedup under the version-discriminated index — revisit the dedup
  grain per key.)
- **Imported-baseline awareness** — the ingestion import path bypasses `_cutover`, so a baseline emits no
  `doc.released` (a deliberate v1 exclusion; §6).
- **Audience scale caching** (Redis-memoized per-user grants or the sound zero-grant candidate pre-filter)
  — only if profiling shows the per-user loop is too slow for large orgs.
- **A subscription/opt-in model** — explicitly rejected for v1 (orthogonal to authz; a subscriber who
  loses `document.read` would still be notified, violating §9.2).
- **5b** (Health delivery-failure panel + admin Config tab) and **5c** (SSE) — the next slice-5 subsystems.
- **Opportunistic fold-in candidate (BE):** the slice-4 timer-sweep **claim-threshold filter** (the
  `remind_2_sent_at IS NULL` tautology) is unrelated to awareness and does **not** fit 5a — left for a
  focused escalation-sweep follow-up.

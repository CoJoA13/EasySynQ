# S-notify-3a — Notification preferences, digests & quiet hours (engine)

> **Date:** 2026-06-22 · **Family:** Notifications (doc 10 §9, R53) · **Slice:** 3a (BE-only)
> **Migration:** 0064 (head 0063 → 0064) · **New permission key:** none (R38, catalog stays 102)
> **Spec status:** ratified with the owner (4 forks + 2 confirms, 2026-06-22).

## 1. Context & scope

Slice 1 (S-notify-1, #255) shipped the transactional-outbox spine: durable in-app `notification`
rows + a `notification_email` ledger drained by the `outbox_drain` Beat (120 s). Today only two
events fire — `task.assigned` (emails **immediately** when org-email is ON + the user opts in) and
`system.email_delivery_failed` (in-app, admins). `notification_preference` is just
`{user_id, email_enabled, updated_at}`; `_email_eligible` **excludes** `subject_type == "DOC_ACK"`
(the deferred D-6 switch). Slice 2 (S-notify-fe) added the bell + center + a *master email toggle*
at `/settings/notifications`. S-deeplink-login fixed the logged-out deep-link round-trip.

**This slice (3a) is the digest ENGINE — BE-only.** It introduces the per-user, per-event-class
digest preference model, a daily-digest Beat, quiet hours with an org-gated escalation pierce,
switches DOC_ACK email on, and routes objective/MR document deep-links to their dedicated SPA
surfaces. It runs off **seeded code defaults** — there is no UI to configure the matrix yet.

**Deferred (not this slice):**
- **3b (FE-only):** the per-class matrix UI on `/settings/notifications` (consumes the GET/PUT
  shipped here). Shipping the engine first — off defaults — avoids a UI that promises modes the
  engine can't honor.
- **Slice 4:** escalation timers (`SlaPolicy`/`working_calendar`/`timer_sweep`, `manager_id`) — the
  events that populate the `critical` class (`*.overdue`) and exercise the pierce end-to-end.
- **Slice 5:** awareness events (`doc.released`/…, read-scope filtered) + the Health-dashboard
  delivery-failure panel + SSE.
- **`hourly`/`weekly` cadences:** out of v1 (owner: Immediate/Daily/Off only). Added later via
  `ALTER TYPE … ADD VALUE` if a user ever asks.

### Ratified decisions (owner, 2026-06-22)
1. **Decomposition:** split BE-first — **this slice = the engine**; the matrix UI is 3b.
2. **Granularity:** per **event-class** (a code map, ~4 classes), not per-event-type.
3. **Quiet hours:** **org-gated escalation pierce** built now (predicate unit-tested; exercised
   end-to-end once slice-4 `*.overdue` events fire).
4. **Cadences:** **Immediate / Daily / Off** only in v1.
5. **action_required default = `daily`** (confirmed behavior change — see §6).
6. Record as **R54** (see §15).

### Binding constraints (carried)
- **D1** self-hosted/single-org · **D2** vault→mirror: digest emails carry **summaries + deep links
  only, never controlled content** (the `notification` rows already hold only summaries) · **D4**
  fixed stack (use stdlib `zoneinfo`; `aiosmtplib`/Celery already present — no new deps) ·
  **deny-wins** · notification reads stay **authenticated-self** (no new key, R38).
- **No WORM touch.** No append-only ledger is modified destructively.
- **Migration trap (R53 family):** 0010's `ALTER DEFAULT PRIVILEGES … GRANT … DELETE` auto-grants
  the app role all DML **on new TABLES**. **3a adds NO new table** (only columns + 2 enums on
  existing tables) → the default-privilege grant does not fire and no `REVOKE` is needed. Confirm
  during migration review that no table is created.

## 2. Event-class taxonomy (pure code map)

A code map in `services/notifications/classes.py` — `event_key → NotificationClass` + a per-class
default cadence. No DB taxonomy table (owner's "code map" choice; additive — a new event is one
line). The class set is fixed in code for v1.

| Class | Events (current ✚ forward-mapped for slices 4–5) | Default email cadence | Pierce |
|---|---|---|---|
| `action_required` | **`task.assigned`** (incl. `DOC_ACK` subjects), `task.due_soon`, `doc.review_requested`, `doc.changes_requested`, `review.due`, `finding.assigned`, `mr.input_requested`, `mr.scheduled`, `dcr.raised`, `dcr.accepted` | **daily** | no |
| `awareness` | `doc.approved`, `doc.released`, `capa.stage_changed`, `audit.scheduled`, `audit.report_issued`, `guest.access_expiring` | **daily** | no |
| `critical` | `task.overdue`, `capa.overdue`, `integrity.alarm` | **immediate** | **yes** (org-gated) |
| `admin_ops` | `system.backup_failed`, **`system.email_delivery_failed`** | immediate | n/a (in-app only today) |

- Resolution is by **`event_key`** (DOC_ACK is `event_key=task.assigned, subject_type=DOC_ACK` →
  `action_required`, same as any task — no special class). An unknown `event_key` falls back to
  `action_required` (fail toward delivering, not dropping) and is logged.
- `admin_ops` events are in-app-only today (`system.email_delivery_failed` has an empty email
  template). The class exists for the matrix; it governs no email until slice 5.
- The **pierce set** is exactly the `critical` class (`*.overdue`, `integrity.alarm`) — this is
  distinct from doc §9.2's "Escalates" column (which marks slice-4 *timer* eligibility, a different
  concept).

## 3. Schema — migration 0064 (additive; no new tables)

Two enums, sourced from the ORM `*_VALUES` (the 0010 precedent):
- `notification_digest_mode` ∈ `{immediate, daily, off}`
- `notification_email_kind` ∈ `{single, digest}`

**`notification_preference`** (+ columns; absence of the row still ⇒ all defaults):
| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `digest_mode_action_required` | `notification_digest_mode` | yes | — | **NULL ⇒ code default** (`daily`) |
| `digest_mode_awareness` | `notification_digest_mode` | yes | — | NULL ⇒ `daily` |
| `digest_mode_critical` | `notification_digest_mode` | yes | — | NULL ⇒ `immediate` |
| `digest_mode_admin_ops` | `notification_digest_mode` | yes | — | NULL ⇒ `immediate` |
| `digest_hour` | `smallint` | no | `8` | CHECK `0 <= digest_hour <= 23` |
| `timezone` | `text` | no | `'UTC'` | IANA; validated app-side via `zoneinfo` |
| `quiet_start` | `time` | yes | — | both set ⇒ window active (wrap-around ok) |
| `quiet_end` | `time` | yes | — | |

NULL-as-default keeps the matrix additive (a new class = a new nullable column, never a backfill)
and means a user who never touches preferences has every class at its code default.

**`system_config`** (+ 1 column, rides `/admin/config`):
| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `notifications_escalation_pierce_quiet_hours` | `boolean` | no | `true` | critical events pierce quiet hours |

**`notification`** (+ digest markers):
| Column | Type | Null | Notes |
|---|---|---|---|
| `digest_due_at` | `timestamptz` | yes | set at enqueue for **daily**-mode rows = "awaits a digest" |
| `digested_at` | `timestamptz` | yes | stamped when bundled into a sent digest |

**`notification_email`** (a digest email has no single source notification):
| Change | Notes |
|---|---|
| `email_kind notification_email_kind NOT NULL DEFAULT 'single'` | discriminates single vs digest |
| `recipient_user_id uuid NULL` (FK `app_user`, RESTRICT) | so the drain re-check can resolve a digest's user (a digest has `notification_id IS NULL`) |
| `item_count int NULL` | bundle size, for the digest body/audit |
| `notification_id` → **NULLABLE** | NULL for digest rows |
| UNIQUE(`notification_id`) → **partial** UNIQUE `WHERE notification_id IS NOT NULL` | many digest rows (NULL) allowed; single rows still 1:1 |

`alembic check` must be clean: every column + the enum types + the partial-unique + the named FK is
mirrored in the ORM (`db/models/notification.py`, `db/models/system_config.py`). The migration drops
the **existing** unique on `notification_email.notification_id` (confirm its actual name in the ORM /
`\d notification_email` first) and creates the partial-unique in its place; mirror it as an
`Index(..., unique=True, postgresql_where=...)` and exclude any partial/expression index from
autogenerate per `env.py._include_object` if it round-trips noisily.
Round-trip up↔down↔check on a throwaway PG16; the downgrade drops columns then the enum types
(guard nothing — no child FK to these columns).

## 4. Preference resolution + defaults

`services/notifications/preferences.py`:
- `resolve_mode(pref | None, klass) -> NotificationDigestMode` — the pref column value, else the
  class code default. A missing `notification_preference` row ⇒ all code defaults.
- `effective_preferences(pref | None) -> EffectivePrefs` — the fully-resolved view (every class's
  mode, `digest_hour`, `timezone`, `quiet_start/end`, `email_enabled`) for the GET response and the
  engine. Code defaults live **only here** — no per-row seed/backfill (mirrors slice-1's
  "absent row ⇒ `email_enabled` true").
- **Master switch:** `email_enabled == false` ⇒ **no email at all** (every class effectively `off`);
  the per-class modes apply only when `email_enabled` is true.

## 5. Digest engine

### 5.1 Enqueue (`dispatch.py`) becomes class-aware
For each recipient of an eligible event, resolve `klass = class_of(event_key)` then
`mode = resolve_mode(pref, klass)` (only when org-email ON + user opt-in + address present — the
existing `_email_eligible`, **minus** the DOC_ACK exclusion, see §7):
- **immediate** → create a `notification_email` (`email_kind=single`) now, **subject to the
  quiet-hours hold** (§6). `notification.digest_due_at = NULL`.
- **daily** → **no email row now**; set `notification.digest_due_at = next_digest_at(pref, now)` —
  the next occurrence of the user's `digest_hour` in their `timezone` strictly after `now`.
- **off** → in-app only; no email row; `digest_due_at = NULL`.

The in-app `notification` row is **always** created immediately (the bell is the live record) — the
mode governs **email** only.

`next_digest_at(pref, now)` is pure (`services/notifications/schedule.py`): compute today's
`digest_hour` in `tz`; if already past, roll to tomorrow; return as UTC. Unit-tested across tz +
DST boundaries (zoneinfo handles DST).

### 5.2 The digest sweep Beat (`notification_digest_sweep`)
A new Beat task (registered in `tasks/notifications.py` + the `beat_schedule` in `tasks/app.py` +
already-imported in `tasks/__init__.py`), **hourly** (`schedule=3600`). It is a **bundler** — it
creates digest `notification_email` rows (PENDING); the **existing `outbox_drain` sends them**,
reusing all retry/backoff/failure-ownership machinery.

Per run (one fresh `AsyncSession` **per user** — the MissingGreenlet guard):
1. Find distinct `recipient_user_id`s with pending digest rows: `digest_due_at <= now AND
   digested_at IS NULL` (org-scoped).
2. For each such user, in **one idempotent txn**:
   - `SELECT … WHERE recipient_user_id = :u AND digest_due_at <= now AND digested_at IS NULL
     ORDER BY created_at FOR UPDATE SKIP LOCKED` (claim this user's pending rows; the lock serializes
     against a concurrent sweep — a sibling skips this user).
   - **Re-check email eligibility at send-prep** (org-email ON, user opt-in, address present); if not
     eligible, **stamp `digested_at` and create NO email** (the rows are consumed; no email leaks
     after an opt-out — mirrors the drain's SUPPRESSED re-check).
   - Render ONE digest email from the seeded `digest.daily` template (the item list built in code:
     **group by `event_key`/class** for readable sectioning and list each row's `title` + `deep_link`;
     when a group has many identical-event rows the header carries the count ("3 documents released")
     — the doc §9.4 bundling rule, mostly forward-looking since awareness events arrive in slice 5).
     Summaries + deep links only — **never controlled content**.
   - Insert a `notification_email` (`email_kind=digest`, `notification_id=NULL`,
     `recipient_user_id=:u`, `item_count=N`, `status=PENDING`).
   - `UPDATE … SET digested_at = now` on the claimed rows. Commit (atomic: email row + stamps).
3. **Idempotency:** a re-delivered task (`task_acks_late`) finds the rows already stamped → claims
   nothing → no-op. A crash before commit leaves zero side-effects (one txn).

### 5.3 Drain reuse for digest rows (`drain.py`)
- The claim query is unchanged (status/next_attempt_at).
- `_still_eligible` resolves the recipient via `notification_email.recipient_user_id` when
  `notification_id IS NULL` (digest), else via the notification (single) as today.
- `_emit_failure` uses `email.id` in the `system.email_delivery_failed` context when
  `notification_id IS NULL` (the failure template's `notification_id` var tolerates the email id /
  a generic descriptor). Owned-failure posture (R32) unchanged.

## 6. Quiet hours + org-gated pierce

Quiet hours gate the **immediate** email path only — digests are already user-timed at `digest_hour`
and send regardless (if a user sets `digest_hour` inside their quiet window, that's their explicit
choice; we don't block it).

At enqueue of an **immediate** email (§5.1), in `services/notifications/quiet.py`:
- `in_quiet_window(pref, now) -> bool` — pure; interprets `now` in the user's `tz`; both
  `quiet_start`/`quiet_end` set ⇒ active; handles wrap-around (`start <= end` ⇒ `start <= t < end`;
  else `t >= start OR t < end`). Unit-tested incl. wrap-around + the exact boundaries.
- `should_pierce(klass, org_flag) -> bool` — pure; `klass == critical AND org_flag`.
- If `in_quiet_window` **and not** `should_pierce` → set the new email row's
  `next_attempt_at = window_end(pref, now)` (the next `quiet_end` in the user's tz, as UTC) so the
  **existing drain naturally defers** it; else send-eligible immediately.

**Testability now:** an `action_required = immediate` email held by quiet hours is fully end-to-end
testable today (no slice-4 event needed). The `should_pierce` predicate is unit-tested in isolation;
it is only exercised end-to-end once `critical` events (`*.overdue`) fire in slice 4.

## 7. DOC_ACK email on

Drop the `subject_type != "DOC_ACK"` term in `_email_eligible`. DOC_ACK is `event_key=task.assigned`
→ `action_required` → `daily` by default → **naturally bundled into the daily digest** (the "no
onboarding flood" intent — no special-casing). A user who sets `action_required = immediate` would
get immediate DOC_ACK emails (held by their quiet hours like any immediate). Update the obsolete
slice-1 comment.

## 8. Subtype deep-link routing (`subjects.py`)

For `subject_type == "DOCUMENT"` only (the other document-ish subject types — DOC_ACK /
PERIODIC_REVIEW / LEADERSHIP_AUTHORIZATION — stay on `/documents/{id}`, they are
lifecycle-on-the-document views), resolve the document's `document_type.code`:
- `"OBJ"` → `/objectives/{id}` · `"MR"` → `/management-reviews/{id}` · else → `/documents/{id}`.

Both routes resolve directly because `quality_objective.id` and `management_review.id` **are** the
`documented_information.id` (shared-PK subtypes — verified) and the SPA detail routes
(`/objectives/:id`, `/management-reviews/:id`) key on that same id.

`deep_link_for(subject_type, subject_id, *, document_type_code: str | None = None)` gains the
optional code param (default None ⇒ today's `/documents/{id}` — every existing caller stays safe).
`resolve_subject` already loads the `DocumentedInformation` row for DOCUMENT subjects; it additionally
loads `document_type.code` (one `session.get(DocumentType, row.document_type_id)` or a join) and
passes it. Detection mirrors the `document_type.code` precedent (`objectives/service.py`,
`mgmt_review/service.py`).

## 9. API + contract

`GET/PUT /me/notification-preferences` (authenticated-self; **no new key**):

```jsonc
// GET response / PUT body (effective values; NULL columns resolved to code defaults)
{
  "email_enabled": true,                 // master kill-switch
  "digest_modes": {                      // one mode per class
    "action_required": "daily",
    "awareness": "daily",
    "critical": "immediate",
    "admin_ops": "immediate"
  },
  "digest_hour": 8,                       // 0..23, the user's local digest hour
  "timezone": "UTC",                      // IANA
  "quiet_start": "22:00",                 // "HH:MM" | null
  "quiet_end": "06:00"                    // "HH:MM" | null
}
```

- **PUT** is a partial update (only provided fields/classes change). Validation → **422** on:
  unknown class key, mode ∉ enum, `digest_hour ∉ [0,23]`, `timezone ∉ zoneinfo`, malformed time,
  or exactly one of `quiet_start`/`quiet_end` set (require both-or-neither). Upsert via
  `on_conflict_do_update` (the existing pattern).
- **GET** returns the fully-resolved effective view (so 3b's UI shows real defaults even before the
  user has a row). Back-compat: `email_enabled` stays; old clients reading only that field are
  unaffected.
- Update `packages/contracts/openapi.yaml` (the `NotificationPreferences` schema + a
  `NotificationDigestMode` enum) — redocly lint (contracts CI). Document the new
  `notifications_escalation_pierce_quiet_hours` field on the `/admin/config` schema.

## 10. Templates (`digest.daily`)

Seed one new DB-backed, versioned template (migration 0064 bulk-insert, locale `en`,
`is_effective=true`), the §9.3 pattern:
- `in_app_title`/`in_app_body`: empty (digests are email-only; in-app already showed each event —
  the `system.email_delivery_failed` empty-field precedent).
- `email_subject`: e.g. `"[EasySynQ] Your daily summary — {{item_count}} item(s)"`.
- `email_body`: greeting + `{{items}}` (a **code-pre-rendered**, escaped, newline-joined list — the
  logic-free `render.py` engine substitutes `{{ var }}`/`{{ var | date }}` only, so the loop lives
  in the sweep, not the template) + `{{prefs_link}}`.
- Add `digest.daily` to the `VARIABLE_WHITELIST` (`constants.py`):
  `{recipient.first_name, item_count, items, prefs_link}`.

## 11. Testing strategy

**Unit** (no DB): class map + unknown-event fallback; `resolve_mode`/`effective_preferences`
incl. master-switch override; `in_quiet_window` (wrap-around + both boundaries + tz/DST);
`should_pierce`; `next_digest_at`/`window_end` across tz + DST; the digest item-collapse render;
PUT validation (each 422 path).

**Integration** (testcontainers; run-scoped/delta-based — never assume a clean *or* dirty shared
DB, self-provide preconditions):
- enqueue with `action_required=daily` (default) ⇒ in-app row created, **no** `notification_email`,
  `digest_due_at` set.
- enqueue with `mode=immediate` outside quiet hours ⇒ a `single` email PENDING.
- enqueue `immediate` **inside** quiet hours, non-critical ⇒ email `next_attempt_at == window_end`
  (deferred); with `critical` + org pierce ON ⇒ send-eligible now.
- the sweep bundles a user's pending rows into ONE `digest` email + stamps `digested_at`; the drain
  then sends it (Mailpit/FakeMailSender); a **second sweep run is a no-op** (idempotent).
- sweep re-check: user opt-out between enqueue and sweep ⇒ rows stamped, **no** email row.
- **DOC_ACK** now produces an email (bundled into the digest by default).
- subtype routing: an OBJ doc ⇒ `/objectives/{id}`; an MR doc ⇒ `/management-reviews/{id}`; a plain
  doc ⇒ `/documents/{id}`.
- `email_enabled=false` master switch ⇒ no email row by any path.

## 12. Verification & rollout

- `/check-api` (ruff + format + mypy-strict + unit) · `/check-migrations` (up↔down↔check on PG16) ·
  `/check-contracts` (redocly) · `/check-web` (the contract type only — no FE code this slice) — all
  green before the PR.
- **migration-reviewer** on 0064 + the ORM (phantom-DROP, partial-unique mirroring, enum additivity,
  populated-downgrade); **diff-critic** on the branch; a small **adversarial Workflow** (lenses:
  digest idempotency/double-send · quiet-hours correctness/pierce · D2 no-controlled-content +
  authenticated-self); **Codex** (migration- + dispatch-adjacent).
- **Live-smoke** (Chrome MCP; owner does the Keycloak login): rebuild api + worker + beat; enable
  org email + SMTP→Mailpit; set a test user's `digest_hour` to the current hour + tz; create a
  daily-mode action item; trigger `notification_digest_sweep` (Celery call/CLI); observe ONE bundled
  email in Mailpit + the `digested_at` stamps; verify an `immediate`+quiet-hours hold defers.
- Branch `feat/s-notify-3a` → PR → green CI (all five) → owner squash-merge → `/finish-slice` + the
  docs follow-up.

## 13. Risks & traps

- **Behavior change (§6):** `task.assigned` email moves immediate → daily-bundled by default. This is
  the doc-§9.4 intent (immediate in-app + daily email) and **owner-confirmed**; called out in the
  slice-history entry so it is not a silent regression.
- **Drain failure path for digests:** `notification_id IS NULL` must not 500 `_emit_failure` — covered
  by §5.3 + a test.
- **Idempotency:** the sweep's claim+stamp+email is one txn; `task_acks_late` re-delivery is a no-op.
- **`alembic check`:** the partial-unique swap + the 4 nullable enum columns must be mirrored exactly
  (the recurring phantom-DROP trap).
- **No new table** ⇒ the `ALTER DEFAULT PRIVILEGES` REVOKE trap does not apply this slice — verify in
  migration review (if a join table is ever added, REVOKE DELETE — append-only link).
- **tz validation:** reject non-zoneinfo strings at PUT (422) so the sweep/quiet math never throws on
  a bad tz.

## 14. Out of scope (named residuals)
- The matrix UI (**3b**) · `hourly`/`weekly` cadences · the `critical`-class events + end-to-end
  pierce (**slice 4**) · awareness-event emission + the Health panel + SSE (**slice 5**) · admin
  template editing · a hard `notification → digest_email` audit FK (soft link via `digested_at` for
  v1; the email body carries the bundled summaries).

## 15. Decisions register — R54 (proposed)

> **R54 — Notification digest & quiet-hours preference model.** Email cadence is set **per user, per
> event-class** (`immediate | daily | off` in v1; `hourly`/`weekly` reserved). Events map to **four
> code-defined classes** — `action_required`, `awareness`, `critical`, `admin_ops` — with default
> cadences `daily`/`daily`/`immediate`/`immediate` (defaults live in code; `notification_preference`
> columns are NULL-as-default, no backfill). **In-app delivery is always immediate**; the mode governs
> **email** only. **Quiet hours** (per-user `quiet_start`/`quiet_end` in the user's `timezone`) hold
> immediate email to the next window; **critical-class** events (`*.overdue`, `integrity.alarm`) pierce
> when the org flag `notifications_escalation_pierce_quiet_hours` (default ON) is set. The **daily
> digest** is a Beat-driven bundler that, per user at their local `digest_hour`, collapses pending
> rows into ONE summary email (summaries + deep links only — never controlled content, R53/D2) sent via
> the existing outbox drain. **DOC_ACK email is enabled** (bundled in the digest by default). No new
> permission key (authenticated-self, R38). Back-prop: extends R53. Range bump R1–R53 → R1–R54.

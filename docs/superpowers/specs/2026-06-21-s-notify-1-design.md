# S-notify-1 — Notification spine + email delivery (doc 10 §9) — slice-1 design (spec)

> The **first slice of the Notification family** — the biggest remaining functional gap in EasySynQ.
> The workflow engine + My Tasks UI exist and create `Task` rows, but `grep` confirms **no**
> `services/notifications/`, **no** SMTP config, **no** notification models, **no** email dependency —
> approval / CAPA / MR / ack tasks land in the inbox but **never reach a user by email**. doc 10 §9 fully
> specs the target (dual in-app + email channels, a 16-event catalog, versioned templates, per-user digest
> matrix, quiet hours, escalation timers, owned bounces). That is ~5 slices of work (§2). This slice ships
> the **BE notification spine + email delivery** and nothing user-facing in the SPA. SPEC-FIRST per
> CLAUDE.md; the scope forks were the owner's calls (§0). The design was **adversarially validated by a
> 5-lens refute panel (§12)** that confirmed the load-bearing premises (atomic SAVEPOINT enqueue, all 5
> task sites commit-after, no lock-ordering deadlock, no D2/WORM touch, no R38 break) and drove the fixes
> in §3–§11 — **before any migration**.

## 0 · Owner decisions (RESOLVED — ratified 2026-06-21 via AskUserQuestion ×6 + a design approval)

- **D-1 — Slice boundary: the BE notification spine + email, BE-only.** **Ratified.** Slice 1 = config +
  models + dispatch service + an outbox-drain SMTP worker + delivery-failure ownership + `task.assigned`
  event hooks + a per-user opt-out + `GET /notifications`. **No SPA bell, no preferences matrix, no
  digests, no escalation timers** — those are later slices (§2). *Rejected:* a thinnest email-only pipe (no
  durable in-app backbone) and a bigger slice that bundles the SPA notification center.
- **D-2 — Events: `task.assigned` at every task-creation site.** **Ratified.** One event class — a new
  `Task` for you → an in-app row + (optionally) an email with a deep link, to the assignee (else each
  candidate-pool member). *Refined by the refute panel (L5-2):* "every task-creation site" is the
  **engine `_materialize_stage` (covering 6 subject types incl. `LEADERSHIP_AUTHORIZATION` and
  `PERIODIC_REVIEW`) + 3 direct-add sites (MR)** — broader than the illustrative 5-item list (§5). Awareness
  events (`doc.released`, …) are deferred — they need a per-recipient read-scope filter (§6); `task.assigned`
  recipients are **intrinsically scoped** (they are the eligible assignees).
- **D-3 — Per-user master email toggle, default on.** **Ratified.** A `notification_preference` row per
  user with one `email_enabled` boolean (absence ⇒ enabled). In-app is always on (the awareness backbone);
  email can be globally silenced per user. An **orthogonal master switch that survives the slice-3
  per-event-class matrix** — not throwaway. *Rejected:* org-enable-only with all per-user prefs deferred.
- **D-4 — DB-backed versioned templates now.** **Ratified.** A `notification_template` table (versioned,
  global, seeded; in-app + email forms) is the render source of truth from slice 1; the template id+version
  that produced each message is **snapshotted onto the notification row** for fidelity. *Scoped:* the
  versioning + render machinery + the seeded `en` set ship now; an **admin template-editing API/UI is
  deferred** (v1 templates are seed-managed). *Rejected:* in-code template functions.
- **D-5 — Design approved.** **Ratified.** §1–§11 were presented and approved as-is, including the
  **SAVEPOINT enqueue / async-drain at-least-once contract (§4)**, `aiosmtplib`, email **opt-in per org
  (default OFF)**, **no new permission key**, and an in-app-only `system.email_delivery_failed` to admins.
- **D-6 — DOC_ACK email deferred to slice 3 (refute-panel L5-4).** **Ratified.** A new joiner entering a
  distribution folder with N ack-required docs would otherwise get N onboarding emails in one drain (no
  digest until slice 3). Slice 1 emits the **in-app** notification for `DOC_ACK` (it mirrors My Tasks — no
  storm) but **suppresses its email** via a cheap `subject_type=DOC_ACK` gate at the email-enqueue decision
  (§4/§5). Approval emails (DOCUMENT/DCR/CAPA/MR/IMPROVEMENT_INITIATIVE/LEADERSHIP_AUTHORIZATION/
  PERIODIC_REVIEW) stay live; DOC_ACK email switches on with the slice-3 digest that bundles it.
- **D-7 — R53 binding + reconcile doc 10 (refute-panel L1-1).** **Ratified.** The notification delivery
  architecture is recorded as **binding R53** (§13), and **doc 10 W2/§156's "enqueue side-effects after
  commit" wording is reconciled** to the in-txn transactional-outbox (atomic-on-success SAVEPOINT enqueue +
  post-commit async drain) as part of this slice's docs back-prop. *Rationale:* the in-txn enqueue closes
  the commit→enqueue crash gap (a committed task with a dropped notification) and is strictly more robust;
  it still never blocks a transition (enqueue failure rolls back only the savepoint, §4).

## 1 · What the canon already pins (settled — restated, not re-decided)

- **doc 10 §9 is the target spec.** Notifications = *awareness*; My Tasks = *work* (the two are deliberately
  distinct). Channels: in-app (durable rows, SPA polls) + email (Celery→SMTP relay, STARTTLS,
  **at-least-once via an outbox**, bounces **owned**). Email **never carries controlled content** — only a
  summary + a deep link back into EasySynQ (keeps PII/IP out of mail archives; reinforces the D1 boundary).
- **⚠ Side-effect timing — reconciled (D-7, supersedes doc 10 W2/§156).** doc 10 W2 ("notifications are
  *consequences* of the committed transition") and §156 ("side effects … enqueued to Celery **after**
  commit via an outbox") describe a **post-commit** dispatch. Slice 1 **supersedes** that wording with an
  **in-txn transactional outbox**: the durable rows are enqueued *inside* the domain txn under a SAVEPOINT
  (atomic-on-success, §4), and the async **drain** (the post-commit Celery step) does the actual SMTP send.
  This closes the post-commit dispatch's crash gap (a committed task whose notify enqueue is lost) and is
  still non-blocking (enqueue failure rolls back only the savepoint). doc 10 W2/§156 get the reconciled text
  in the docs back-prop; R53 (§13) is the binding anchor.
- **R32 — delivery-failure ownership.** Email bounce / delivery failure is **owned by the system, not
  deferred**: every outbound message is tracked through the outbox; on a permanent failure the system emits
  a **`system.email_delivery_failed`** notification and surfaces it on the Health dashboard (doc 08 §15.6).
  Because email is best-effort and never gating, a delivery failure **never blocks a workflow transition**.
  *Slice 1 honors the emit (in-app, to admins); the Health-dashboard panel is slice 5.*
- **Audit posture — reconciled (refute-panel L1-2).** doc 10 §672 lists "notification-policy **override**"
  as audit-trail-worthy. That privileged act = the **org `notifications_email_enabled` flag**, which already
  writes a `CONFIG_UPDATED` audit via `/admin/config` (`api/config.py:122`), plus the deferred slice-3 admin
  preferences matrix. The **per-user self opt-out** (D-3) is **deliberately unaudited** — an ordinary,
  non-privileged self-service preference, not a policy override (and there is no fitting `AuditObjectType`;
  an additive enum is out of slice-1 scope). Notification *delivery* is operational (the
  `notification_email` ledger is its record), not a QMS controlled change → **no `audit_event`**.
- **R29 / R15 (referenced, not implemented here).** Escalation resolves against `app_user.manager_id` with a
  QM/OrgRole fallback, evaluated against `working_calendar` (R29) — **slice 4**. Distribution-target entry
  mints `DOC_ACK` tasks (R15) — those tasks already exist; slice 1 only *notifies* (in-app) on their creation.
- **The seams already exist** (verified against live code, 2026-06-21):
  - `app_user.email` (`db/models/app_user.py:54`, **nullable**, sourced from the Keycloak `email` claim at
    first login, **not re-synced**) and **`manager_id`** are already modeled. The notifier has a recipient
    email per `app_user.id` and must tolerate a NULL email. `Task` has **no ORM relationships** (recipient
    resolution is an explicit `select(AppUser)` — no lazy-load `MissingGreenlet`).
  - **Mailpit is already in the dev compose** (`infra/compose/compose.yml:220`, `axllent/mailpit`, profile
    `dev`) — the dev/test SMTP catcher needs **no new infra**.
  - **No email code/deps exist** — `pyproject.toml` has no SMTP lib; `config.py` no SMTP fields; `db/models/`
    no notification model; `services/` no `notifications/`.
  - **Task-creation topology** (the dispatch hook points — refute-panel L5-1/L5-2): **one engine site**,
    `services/workflow/engine.py:_materialize_stage` (engine.py:196), reached by the multi-stage flows for
    **6 subject types** — DOCUMENT/DCR/CAPA/IMPROVEMENT_INITIATIVE/LEADERSHIP_AUTHORIZATION/PERIODIC_REVIEW
    **and DOC_ACK** (`ack/sweep.py:212`) — plus **3 direct `session.add(Task)` sites**: the single-stage
    `instantiate_approval` (`services/workflow/service.py:84`, MGMT_REVIEW/DOCUMENT lightweight), the MR
    cadence sweep (`services/mgmt_review/cadence.py:273`, MR_INPUT), the MR-action spawn
    (`services/mgmt_review/spawn.py:93`, MR_ACTION). **All hand back an open txn and commit *after* the add**
    (verified L5-1) — so a SAVEPOINT enqueue placed at the add is atomic with the `Task`. `Task` is
    **mutable** (no `notified` flag) with `assignee_user_id` + `candidate_pool` (JSONB).
  - **Config precedent:** SMTP *creds* → app `Settings` (env, `pydantic-settings`); per-org *policy* →
    `SystemConfig` (mirrors `mgmt_review_cadence_months` / `_owner_user_id`, admin-gated via `/admin/config`).
    **Beat/idempotent-task precedent:** `tasks/ack.py` (fresh engine + sessionmaker per run, `asyncio.run`),
    registered in `tasks/__init__.py` + scheduled in `tasks/app.py`.

## 2 · The decomposition (the Notification family roadmap)

| Slice | Scope | Status |
|---|---|---|
| **1 — this spec** | **BE notification spine + email.** Config + models + dispatch + outbox-drain worker + R32 failure ownership + `task.assigned` hooks + per-user opt-out + `GET /notifications`. | **now** |
| 2 | **In-app FE** — the bell + notification center (read/unread, deep links) in the SPA, consuming `GET /notifications`. | deferred |
| 3 | **Preferences + digests** — the per-user per-event-class matrix (immediate/hourly/daily/weekly/off) + the daily-digest Beat task + quiet hours. **DOC_ACK email (D-6) switches on here**, bundled in the digest. | deferred |
| 4 | **Escalation timers** — `SlaPolicy` + `working_calendar` + a durable `timer_sweep` + reminder/overdue/escalation, using `manager_id` (R29). | deferred |
| 5 | **Awareness events + Health surface** — `doc.released`/`doc.approved`/`dcr.*`/`review.due`/… (opt-in, **per-recipient read-scope filtered**) + the Health-dashboard delivery-failure panel. | deferred |

Slice 1 delivers the headline value (work reaches people by email, with a durable in-app record and owned
failures) and is the foundation every later slice extends.

## 3 · Data model (migration `0063`; head `0062 → 0063`)

Four new tables + one `SystemConfig` column + new `Settings` fields. **None touch a WORM invariant** —
these are operational/mutable like `Task` (no `audit_event` hash chain, no `signature_event`, no `blob`
bytes; refute-panel L1-4 cleared). The `Task` model is **NOT** changed (no `notified_at`); the dedup record
lives on `notification`. All FKs use `ondelete='RESTRICT'` (the house default for non-transient parents;
`Task`/`notification` are never deleted in v1 — L4-3 cleared).

### 3.1 `notification` — the durable in-app awareness record
- `id` uuid PK · `org_id` uuid FK→organization · `recipient_user_id` uuid FK→app_user
- `event_key` **text** (e.g. `task.assigned`, `system.email_delivery_failed`) — **TEXT not a PG enum**, so
  later slices add events with **no `ALTER TYPE` migration**; canonical values live in a code registry.
- `subject_type` **text** (`DOCUMENT`/`DCR`/`CAPA`/`MGMT_REVIEW`/`IMPROVEMENT_INITIATIVE`/
  `LEADERSHIP_AUTHORIZATION`/`PERIODIC_REVIEW`/`DOC_ACK`/`SYSTEM`) · `subject_id` uuid null ·
  `task_id` uuid null FK→task (`RESTRICT`)
- `title` text · `body` text — the **rendered in-app form, snapshotted** at enqueue · `deep_link` text
- `template_id` uuid null FK→notification_template (`RESTRICT`) · `template_version` int null — fidelity ·
  `context` jsonb — the typed variable bag (debug / future re-render)
- `created_at` timestamptz default now() · `read_at` timestamptz null (**null = unread**; the only mutable field)
- **Dedup — a PARTIAL UNIQUE INDEX** (not a constraint; PG has no WHERE-clause UNIQUE constraint, L4-2):
  `uq_notification_dedup_task` `UNIQUE (recipient_user_id, task_id, event_key) WHERE task_id IS NOT NULL`,
  created via `op.create_index(..., unique=True, postgresql_where=...)` + its name added to
  `env.py._MIGRATION_MANAGED_INDEXES` (the IS-NOT-NULL-predicate index round-trips wrong if declared in the
  ORM — the 0024 lesson, L4-1). Enqueue uses `ON CONFLICT DO NOTHING … RETURNING id` so a re-materialized
  stage / retried txn never double-notifies, and the dependent `notification_email` insert happens **only
  when a row was actually inserted** (conflict → skip the email, L2-3). *(A `task_id`-less system event is
  de-duped by the caller, not the index — NULL task_ids are distinct in PG.)*
- **Index:** `(recipient_user_id, read_at, created_at DESC)` for the My-Notifications read.

### 3.2 `notification_email` — the email delivery ledger (0..1 per notification)
- `id` uuid PK · `org_id` uuid FK · `notification_id` uuid FK→notification (`RESTRICT`), **UNIQUE** → at most
  one email per notification
- `recipient_email` text **NOT NULL** — denormalized snapshot of `app_user.email` at enqueue (idempotency +
  audit; a later email change does not retro-target a queued send)
- `subject` text · `body` text — the **rendered email form, snapshotted**
- `status` **PG enum `notification_email_status`** (`PENDING`/`SENT`/`FAILED`/`SUPPRESSED`) — a **closed,
  fresh `CREATE TYPE`** (no additivity concern) · `attempts` int default 0 · `next_attempt_at` timestamptz
  null (backoff **+ the send-lease**, §4) · `last_error` text null · `sent_at` / `failed_at` timestamptz
  null · `created_at`
- Inserted **only** when the §4 gates pass (org-enabled + user opt-in + email present + not `DOC_ACK`, D-6).
- **Index:** `(status, next_attempt_at)` for the drain query.

### 3.3 `notification_template` — versioned, global, seeded
- `id` uuid PK · `event_key` text · `locale` text default `'en'` · `version` int
- `is_effective` bool · `in_app_title` / `in_app_body` / `email_subject` / `email_body` text · `created_at`
- **`uq_notification_template_one_effective`** — partial unique INDEX, one `is_effective` per
  `(event_key, locale)`. Boolean predicate (`WHERE is_effective`) → **declare in the ORM**
  via `Index(..., postgresql_where=sa.text("is_effective"))` (the `workflow_definition.py:54` precedent
  round-trips correctly; no `env.py` exclusion needed — L4-1). **Global (no `org_id`)** — D1 single-org + no
  editing API in slice 1; org-override is a later additive option.
- App role gets **SELECT only** (seeded via migration; editing deferred).

### 3.4 `notification_preference` — the per-user master toggle (D-3)
- `user_id` uuid PK FK→app_user · `email_enabled` bool **default true** · `updated_at` timestamptz
- **Absence ⇒ enabled** — no per-user backfill. The `PUT` upserts. Deliberately unaudited (§1, L1-2).

### 3.5 `system_config.notifications_email_enabled boolean NOT NULL DEFAULT false`
- The per-org **opt-in gate** (admin-flipped via `/admin/config`). Mirror the exact
  `server_default=sa.false(), default=False, nullable=False` triple from the 5 sibling toggles
  (`system_config.py:64`) so `alembic check` stays clean. A **column-add, not a row insert** → no org lookup,
  no fresh-DB blind spot (L4-5 confirmed).

### 3.6 App `Settings` (env-driven, `config.py`)
- `smtp_host: str = ""` · `smtp_port: int = 587` · `smtp_username: str = ""` ·
  `smtp_password: str = ""` (`# noqa: S105` dev default) · `smtp_use_tls: bool = True` (STARTTLS) ·
  `smtp_from_address: str = "noreply@easysynq.local"` · `smtp_from_name: str = "EasySynQ"`
- `app_base_url: str = "http://localhost"` — browser-facing base for deep links (`{app_base_url}{spa_route}`).

## 4 · The at-least-once contract (the crux — SAVEPOINT enqueue + async drain)

The transactional-outbox split that reconciles **atomic enqueue** with **best-effort, never-blocks-a-transition**
(supersedes doc 10 W2/§156 per D-7; the in-txn-SAVEPOINT idiom is proven at `services/ingestion/commit.py:564`
and `services/owner_assignment.py`):

- **Enqueue is synchronous, inside the caller's `Task`-creation txn, wrapped in a SAVEPOINT**
  (`async with session.begin_nested():`). On success the `notification` (+ optional `notification_email`)
  rows commit **atomically with the `Task`** — no notification for a rolled-back task, exactly-once intent.
- **On any enqueue failure** (missing template, render bug, recipient-lookup error) the **savepoint rolls
  back**, we **log a warning, and continue** — the **parent task txn is untouched**, so a notification bug
  can **never block a workflow transition** (the doc 10 §9 hard requirement), and a caught statement-level
  error never poisons the parent txn (L1-3: connection-level drops would fail the parent anyway and the
  enqueue path adds no new connection-level risk — `Task` has no lazy-load). *Prefer catching the
  render/lookup/`IntegrityError` surface explicitly over a blanket `except Exception`.*
- **Enqueue gates** (skip the `notification_email` row; **always** write the in-app `notification`):
  (a) org `notifications_email_enabled` is off, (b) the user's `notification_preference.email_enabled` is
  false, (c) `app_user.email` is NULL, or (d) **`subject_type == 'DOC_ACK'`** (D-6 — in-app only in slice 1).
  The `notification_email` row is inserted only if the `notification` `ON CONFLICT … RETURNING id` actually
  returned a row (L2-3 — conflict ⇒ skip the email).
- **The notify enqueue touches a DISJOINT lock set** from the engine's `FOR UPDATE` on
  `workflow_instance`/`task` (it reads `notification_template`/`system_config`/`app_user` with no lock, and
  INSERTs only the new tables) — no lock-ordering deadlock (L5-5 cleared).
- **Send is the async `outbox_drain` Beat task** (every ~120 s; no per-task `.delay` kick in slice 1 —
  ≤2-min latency is acceptable for awareness; a kick is a named latency option). Per claimed row, **count
  before send (the lease — L2-2 fix):**
  1. `SELECT … WHERE status='PENDING' AND (next_attempt_at IS NULL OR next_attempt_at <= now()) ORDER BY
     created_at LIMIT N FOR UPDATE SKIP LOCKED` (a true work-queue claim — concurrent drains never
     double-send).
  2. If `attempts >= MAX` (e.g. 5) → set `FAILED` + `failed_at`, **emit `system.email_delivery_failed`**
     (in-app, to admins), commit, skip the send.
  3. Else **increment `attempts` + set `next_attempt_at = now() + backoff(attempts)` (a visibility-timeout
     lease) + commit** (releases the row lock; a concurrent drain in the send window sees `next_attempt_at`
     not-due and skips). **This counts every attempt before the SMTP call**, so a send-succeeds-then-crash
     window cannot cause an unbounded/unowned resend — it is bounded to `MAX` and ultimately trips `FAILED`.
  4. Send the **already-snapshotted** body via `SmtpMailSender`. On success → `SENT` + `sent_at` + commit. On
     a transient exception → leave the row (the lease already set; retried after backoff).
  Idempotent under `task_acks_late` re-delivery (SKIP LOCKED + the lease + the status guard). Fresh
  engine+sessionmaker per run (the `tasks/ack.py` precedent). Returns `{sent, failed, suppressed, retried}`.
  *(Acknowledged at-least-once: an always-succeeds-but-always-crashes-before-`SENT` row is sent up to `MAX`
  times then false-`FAILED` — bounded and owned, the correct trade for an un-undoable external side effect.)*

## 5 · Events, recipients, templates, render

- **Hook topology (refute-panel L5-2 — corrects the "5 sites" framing):** one **engine hook** inside
  `_materialize_stage` taking **`(session, instance, task)`** and **resolving the subject polymorphically**
  from `(instance.subject_type, instance.subject_id)` (no loaded subject object exists there) — it fires for
  **all 6 engine subject types** (DOCUMENT/DCR/CAPA/IMPROVEMENT_INITIATIVE/LEADERSHIP_AUTHORIZATION/
  PERIODIC_REVIEW + DOC_ACK), once per fresh `task_id` inside the per-candidate loop — plus **3 direct-add
  hooks** (`instantiate_approval`, `cadence.py`→MR_INPUT, `spawn.py`→MR_ACTION). **No change to task-creation
  semantics**; the welded single-stage `instantiate_approval` path stays byte-identical but for the added
  call (the S5 `test_approval` regression backstop stays green — L5-6).
- **`deep_link_for(subject_type, subject_id)` + the `task.assigned` variable resolver must cover all 7
  subject types** (the 6 engine + MGMT_REVIEW) with a **`/tasks` fallback** for any unmapped kind — so
  `LEADERSHIP_AUTHORIZATION` / `PERIODIC_REVIEW` never render a broken link or a missing-variable placeholder.
- **`due_at` fidelity (L5-3):** the ack sweep and the periodic-review sweep materialize the `Task` with
  `due_at=NULL` inside `_materialize_stage` and **patch the real `due_at` post-flush**. The notify enqueue
  must read `due_at` **after** that patch (or be passed the resolved value), not the just-added row — else
  the onboarding/review messages render `Due: (none)`. (CAPA/DCR/MR set `due_at` at materialization and are
  unaffected.)
- **Recipient resolution** (`recipients.py`): `assignee_user_id` if set, else **each `candidate_pool`
  member**; load the `app_user` rows (email + display_name). The pool already de-dups a user
  (`engine.py:165`, `repository.py:74`), and the dedup index (§3.1) makes the assignee-vs-pool and
  re-materialization cases safe (L2-1).
- **Render** (`render.py`): fetch the **effective** `notification_template` for `(event_key, 'en')`;
  substitute a **per-event whitelisted, HTML/text-escaped** variable set; support a tiny filter set
  (`| date`). **Logic-free — no `eval`, no Jinja** (matches the repo's ast-whitelist / ReDoS posture). A
  missing variable renders a safe placeholder + logs; a missing template triggers the §4 savepoint fallback.
- **Seeded `en` templates** (D-4):
  - `task.assigned` (in-app compact + email subject/body). Variable set: `recipient.first_name`,
    `subject.identifier`, `subject.title`, `subject.kind`, `task.action_expected`, `task.due_at`,
    `deep_link`, `prefs_link`. Bodies carry **summary + deep link only**, never controlled content. *(Email
    `subject.title` is the same triage label My Tasks already shows that exact recipient with no
    `document.read` gate — not a D1 leak; L3-3 cleared.)*
  - `system.email_delivery_failed` (**in-app only**). **⚠ Operational-only variable set (L3-1):**
    `recipient_email`, the failing `notification_id`, `last_error`, `attempts`, `created_at` — **EXCLUDES**
    `subject.title`/`subject.identifier`/the original body, because admins hold **no `document.read`**
    (AZ-INV-6) and must not learn a controlled doc's metadata from a delivery-failure notice. A unit test
    asserts the rendered body contains no subject title/identifier.
- **Deep link:** `{app_base_url}` + the SPA route (`/documents/{id}`, `/capa?capa={id}`, `/tasks`, …);
  `prefs_link` → the future `/settings/notifications`.

## 6 · Authz & scoping

- **No new permission key** (R38 honored; catalog stays 102 — L3-4 cleared). `GET /notifications`, the
  mark-read endpoints, and `GET/PUT /me/notification-preferences` are **authenticated-self** (the
  `GET /tasks` posture — no `document.*`). The org flag rides the existing **admin-gated `/admin/config`**.
- **⚠ Self-scope is a WHERE predicate, not a post-fetch 404 (L3-2):** every notification read/update filters
  by `recipient_user_id = caller.id` in the SQL (`GET /notifications` returns only the caller's rows;
  `POST /{id}/read` and `read-all` `UPDATE … WHERE id = :id AND recipient_user_id = :caller`, returning 404
  when no row matches **both**). A bare `WHERE id = :id` would let a caller mark another user's notification
  read — forbidden. Tested (user B cannot mark user A's notification read).
- **Scoping is intrinsic for slice-1 events (L3-3 cleared).** `task.assigned` recipients are the task's
  assignee / candidate-pool — **by construction authorized to act on the task** — so **no per-recipient
  ABAC** is required, and the emailed `subject.title` is the same label My Tasks already surfaces to them.
  `system.email_delivery_failed` recipients are admins (System-Administrator role), and its body is
  **operational-only** (§5, L3-1) so it leaks no controlled metadata. *(This is precisely why awareness
  events like `doc.released`, which need a per-recipient `document.read`/process-scope filter, are deferred
  to slice 5 — slice 1 carries no scoping-leak surface.)*

## 7 · Config, secrets, SMTP, Mailpit

- SMTP **creds** in app `Settings` (env; prod operator overrides) — shared across the install (D1 single-org).
  Per-org **policy** (`notifications_email_enabled`) in `SystemConfig`. **Default OFF** — email delivery is
  opt-in: an admin configures SMTP env + flips the flag (mirrors the cautious
  `leadership_release_requires_top_management_authorization` default-OFF posture; fail-safe — no surprise mail).
- **`aiosmtplib`** new dep (async STARTTLS client; fits the async worker). A Python-lib add, **not** a D4
  stack substitution. The api never sends (only the worker instantiates `SmtpMailSender`); the api holds the
  no-op / test `MailSender` — the `LoggingRenderSink` vs `GotenbergRenderSink` split.
- **Dev/test:** the already-present **Mailpit** dev container catches SMTP; the live-smoke verifies a real
  send → Mailpit inbox. Integration tests inject a **fake `MailSender`** (no container dependency).

## 8 · Worker & Beat

- `tasks/notifications.py` → `@task(name="easysynq.notifications.outbox_drain")` (fresh engine + sessionmaker,
  `asyncio.run`, the `ack.py` shape). Scheduled in `tasks/app.py` `beat_schedule` at `120.0` s. Registered in
  `tasks/__init__.py` (+ a unit test asserting it is in `app.tasks` — the recurring "register or it hangs" trap).

## 9 · API surface + contract (`openapi.yaml` in-PR)

- `GET /notifications?unread_only&limit&cursor` → the caller's notifications, newest-first, `read_at`-bearing
  (`WHERE recipient_user_id = caller.id`).
- `POST /notifications/{id}/read` and `POST /notifications/read-all` — mark read; `UPDATE … WHERE id AND
  recipient_user_id = caller.id`; 404 when no row matches both (L3-2).
- `GET /me/notification-preferences` → `{email_enabled}`; `PUT /me/notification-preferences` upserts it.
- `notifications_email_enabled` added to the admin `/admin/config` read/write schema (the `bool | None = None`
  additive field pattern).
- All additive; documented in `packages/contracts/openapi.yaml` in-PR (redocly-lint).

## 10 · Migration & invariants

- `0063` creates the 4 tables + the `notification_email_status` enum (`CREATE TYPE`, fresh) + the
  `system_config` column + the seeded `en` templates + the app-role grants
  (INSERT/UPDATE/SELECT on `notification`, `notification_email`, `notification_preference`; **SELECT** on
  `notification_template`). Downgrade drops `notification_email` → `notification` → `notification_template` /
  `notification_preference`, then `DROP TYPE notification_email_status`, then the `system_config` column —
  FK-safe order, all `IF EXISTS` (the 0052 precedent, L4-4).
- **alembic-check clean:** new models imported in `db/models/__init__.py` (+ `__all__` — the 0027
  phantom-DROP trap, L4-6); every migration-built constraint/FK name-matched in the ORM; the **two partial
  indexes** handled per §3 (`uq_notification_dedup_task` via `op.create_index` + `env.py`
  `_MIGRATION_MANAGED_INDEXES`; `uq_notification_template_one_effective` declared in the ORM with
  `postgresql_where`).
- **No WORM / blob / hash-chain touch** — slice 1 adds only operational tables; no `REVOKE UPDATE,DELETE`,
  no `delete_blob_and_links` path, no `audit_event` write (delivery is operational, L1-2/L1-4).
- Round-trip `up↔down↔check` on a throwaway PG16 (`/check-migrations`).

## 11 · Testing & verification

- **Unit:** render (substitution / escaping / missing-var placeholder / `|date`); the
  `system.email_delivery_failed` body contains **no** subject title/identifier (L3-1); dispatch gates
  (org-off / user-off / NULL-email / `DOC_ACK` → in-app row but **no** email row — L5-4/D-6); dedup
  `ON CONFLICT … RETURNING` (conflict ⇒ email skipped, L2-3); recipient resolution (assignee vs pool); the
  **SAVEPOINT non-poisoning** property (force a render error → the parent task still commits + a warning
  logged); the **count-before-send lease** bounds resends and a post-send-crash mode trips `FAILED` + the
  R32 emit (L2-2); `deep_link_for` + the variable resolver cover all 7 subject types incl. a `/tasks`
  fallback (L5-2); the `outbox_drain` task is in `app.tasks`.
- **Integration** (testcontainers; **delta / run-scoped** assertions per the rules — never assume a clean or
  dirty shared DB): a real task creation → exactly the expected `notification` (+ `notification_email`) rows;
  a DOC_ACK task → an in-app row but **no** email row (D-6); `due_at` is rendered (not NULL) for the
  ack/periodic sweep paths (L5-3); drain with a **fake `MailSender`** → status transitions; a **2-session
  `FOR UPDATE SKIP LOCKED` race** asserting no double-send (the workflow-engine lock-test precedent); the
  single-stage `test_approval` suite stays green (welded-path parity, L5-6); user B cannot mark user A's
  notification read (L3-2).
- **Live-smoke:** `just up s` + Mailpit; configure SMTP + flip the org flag; submit a document for review →
  the approver receives the email in Mailpit with a working deep link; a bad SMTP host → bounded retries →
  `FAILED` + `system.email_delivery_failed` in-app to admin (operational-only body).
- **Pre-PR gate:** `/check-api` + `/check-migrations` + `/check-contracts`; then **diff-critic** +
  **migration-reviewer** + a small **3-lens adversarial Workflow** + **Codex** (delivery + authz-adjacent).

## 12 · Adversarial refute panel (RUN 2026-06-21, pre-migration — 5 lenses)

> Verdict: **SOUND-WITH-FIXES — no critical findings, no architectural rework.** The panel cleared the
> load-bearing premises: the SAVEPOINT enqueue is atomic and non-poisoning for the realistic (statement-level)
> failures it introduces (L1-3), the 4 tables touch no WORM/hash-chain/blob invariant and raise no D2
> divergence (L1-4), all 5 task sites commit-after so the atomic-enqueue contract holds (L5-1), the dedup is
> correct for both engine task shapes (L2-1), the SKIP-LOCKED drain has no double-send/starvation race
> (L2-4), the no-key authenticated-self posture is consistent with R38 + deny-wins (L3-4), the migration
> enum/FK/column/registration mechanics match house precedents (L4-3/4/5/6), and emailing `subject.title` to
> task recipients is not a D1 leak (L3-3). Confirmed findings + dispositions (all folded above):

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| L2-2 | major | `attempts` counted only on the transient branch → post-send-crash = unbounded unowned resend | **Fixed** §4: count-before-send lease; every attempt bounded; trips `FAILED` + R32 emit |
| L3-1 | major | `system.email_delivery_failed` to admins (no `document.read`) could leak subject title/identifier | **Fixed** §5/§6: operational-only template var set + a no-leak unit test |
| L5-2 | major | engine hook is one `_materialize_stage` over **6** subject types, no loaded subject; "5 sites" + 3-arg signature wrong | **Fixed** §1/§5: `(session, instance, task)` + polymorphic subject resolution + all-7 `deep_link_for`/`/tasks` fallback |
| L5-3 | major | ack/periodic sweeps patch `due_at` post-flush → in-place hook snapshots `Due: (none)` | **Fixed** §5: read `due_at` after the post-flush patch |
| L5-4 | major | DOC_ACK onboarding email flood (no digest until slice 3) | **Owner D-6:** defer DOC_ACK email to slice 3 (in-app only; `subject_type` gate) |
| L1-1 | major | in-txn SAVEPOINT enqueue silently supersedes doc 10 W2/§156 "enqueue after commit" | **Owner D-7:** R53 binding + reconcile doc 10; rationale recorded §1 |
| L2-3 | minor | email-row insert path when notification `ON CONFLICT` no-ops unspecified | **Fixed** §3.1/§4: `RETURNING id`, insert email only if a row was inserted |
| L3-2 | minor | self-scope must be a WHERE predicate, not post-fetch 404 | **Fixed** §6/§9: `WHERE recipient_user_id = caller.id` + test |
| L4-1 | minor | two partial indexes' env.py-exclusion not named; approach not pinned | **Fixed** §3/§10: named both + per-index approach (exclude dedup; ORM-declare template) |
| L4-2 | minor | dedup described as a "UNIQUE constraint" (PG has no WHERE-clause UNIQUE constraint) | **Fixed** §3.1: partial unique **INDEX** wording + `create_index` idiom |
| L1-2 | minor | per-user opt-out unaudited vs doc 10 §672 "notification-policy override" | **Fixed** §1: §672 maps to the audited org flag; self-toggle deliberately unaudited |
| L2-5 | minor | "5 sites" framing imprecise | **Subsumed** by L5-2 |
| L1-3, L1-4, L2-1, L2-4, L3-3, L3-4, L4-3, L4-4, L4-5, L4-6, L5-1, L5-5, L5-6 | non-issue | considered + cleared | optional one-liner notes folded where useful |

## 13 · Decisions-register entry (R53 — BINDING, ratified D-7)

- **R53 — Notification delivery architecture.** The notification subsystem is a **dual-channel** (durable
  in-app `notification` rows + a `notification_email` outbox ledger) **transactional outbox**: enqueue is
  **atomic-on-success inside the domain txn** (a SAVEPOINT — **this supersedes doc 10 W2/§156's
  "enqueue side-effects after commit" wording**; it closes the commit→enqueue crash gap and still never
  blocks a transition), and send is an async **at-least-once drain** (`FOR UPDATE SKIP LOCKED`, count-before-
  send lease, retry/backoff). **Email is opt-in per org (default OFF)**, **carries summary + deep link only —
  never controlled content**, and a permanent failure is **owned** (`system.email_delivery_failed`,
  operational-only body, + the Health dashboard — R32). Notifications **respect recipient permission scope**
  (intrinsic for `task.assigned`; an explicit read-scope filter for awareness events). Templates are
  **DB-backed + versioned** (`notification_template`), the producing version snapshotted per message.
  Notifications add **no new permission key** (reads are authenticated-self by a `recipient_user_id =
  caller.id` WHERE predicate; R38 unbroken). *Back-prop:* doc 10 W2/§156 reconciled; range bump R1–R52 →
  R1–R53.

## 14 · Out of scope (named residuals → later slices)

- The **SPA notification bell + center** (slice 2); the **per-event-class preference matrix + digests +
  quiet hours** AND **DOC_ACK email** (D-6) (slice 3); **escalation timers / `SlaPolicy` /
  `working_calendar`** (slice 4); **awareness events** (`doc.released`/`doc.approved`/`dcr.*`/`review.due`/
  `capa.*`/`mr.*`/`guest.*`) with per-recipient read-scope filtering + the **Health-dashboard
  delivery-failure panel** (slice 5).
- **Admin template-editing API/UI** (templates are seed-managed in v1); **i18n** beyond `en`; a per-task
  `.delay` send-kick (the ≤2-min Beat latency stands); `notification` table **partitioning** (a scale option).

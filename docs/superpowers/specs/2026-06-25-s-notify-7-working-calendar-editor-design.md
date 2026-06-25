# S-notify-7 — Working-calendar admin editor (design)

> **Status:** spec (validated — 6-lens adversarial pass folded in, 2026-06-25). **Slice:** S-notify-7
> (Notification family tail; R29 close-out). **Migration:** NONE (head stays `0067`). **New permission
> key:** NONE (reuse `config.update`; catalog stays 102). **Surfaces:** BE (2 endpoints + 1 shared pure
> module + openapi) + FE (one Config-tab section).

## 1. Goal & motivation

Close the R29-named residual: *"The admin editor (a `config.update`-gated GET/PUT + Config-tab UI) is
also deferred."* S-notify-6 wired the per-org `working_calendar` into the `timer_sweep` so reminder /
escalation offsets are computed as **business days** (skip weekends + holidays). But the calendar is
**frozen at the Mon–Fri / `[]` / `<org-tz>` seed** — there is no in-app way to set the org's working
days, add a holiday, rename it, or fix its timezone. This slice adds the editor, so business-day SLAs
become actually configurable by a System Administrator (`config.update`).

It lives in the **existing** admin Config tab (`/admin/config`, shipped S-notify-5b), next to the two
notification toggles and the read-only delivery-health panel.

## 2. What already exists (reuse, do not reinvent)

- **`working_calendar` table** (`db/models/working_calendar.py`, migration 0067): `id, org_id, name,
  working_days` (jsonb ISO-weekday mask `1=Mon..7=Sun`), `holidays` (jsonb `YYYY-MM-DD` list),
  `timezone` (IANA TEXT, default `UTC`), `is_default` (bool; ≤1 default/org via the migration-managed
  partial-unique index `uq_working_calendar_one_default ON (org_id) WHERE is_default`),
  `created_at`/`updated_at` (server_default `now()`, **no `onupdate`** → the service sets `updated_at`
  explicitly on UPDATE). **App-role grants: INSERT / SELECT / UPDATE; DELETE is REVOKEd** — the editor
  needs INSERT (no-row edge) + UPDATE only, so **no grant change, no migration.**
- **`resolve_working_calendar(session, org_id)`** + **`_parse_working_days(value)`**
  (`services/notifications/escalation.py`): the **fail-safe** resolver the sweep trusts. `_parse_working_days`
  is the strict ISO-weekday validator (non-empty list of real ints 1..7; rejects `bool`/`float`/JSON-string;
  **dedups + accepts duplicates** — `[1,1,2,7] → frozenset{1,2,7}`, NOT a rejection). The resolver also
  validates `timezone` via `zoneinfo.ZoneInfo` (unknown → UTC + warn) and parses each holiday via
  `datetime.date.fromisoformat(str(h))` (per-entry **skip-bad, kept-good** — a bad holiday is dropped, the
  rest survive; it does **not** fall back).
- **`PATCH/GET /admin/config` + `GET /admin/notifications/health`** (`api/config.py`): the admin route
  precedent — `require("config.update")` (SYSTEM, admin-only; the R35 two-tier guard blocks a content-tier
  QMS Owner from holding it), org from `caller.org_id`, `CONFIG_UPDATED` audit on `object_type=config`,
  `object_id=org_id`, written only **`if after:`** (a real diff), `request_id=_rid()`.
- **FE:** `admin/ConfigAdmin.tsx` (working-state + dirty-diff + Save form; parent gates no-access on the
  GET `/admin/config` **403 `forbidden` flag**, NOT `usePermissions` — the cold-`/admin`-cache flash
  trap), `admin/NotificationHealthPanel.tsx` (a self-contained child with its own loading/error states,
  relying on the parent's 403 gate), `admin/AdminShell.tsx` (the Config tab), `admin/hooks.ts`
  (`useOrgConfig`/`useNotificationHealth` — `retry:false`, `refetchOnWindowFocus:false`), and
  `features/notifications/timezones.ts` + `NotificationSettingsPage.tsx` (the curated+searchable tz Select
  idiom + the `onDropdownOpen` reset + the native `type=date`/`type=time` no-new-dependency precedent).
- **MSW base handlers** live in `apps/web/src/test/msw/handlers.ts`; `test/setup.ts` runs
  `onUnhandledRequest: "error"` → **every endpoint a mounted component fetches needs a base handler** or
  the sibling suites go red (the health-panel precedent at `handlers.ts` ~`:3564`).

## 3. Confirmed forks (AskUserQuestion, 2026-06-25)

1. **Timezone → EDITABLE in this editor** (revised after the spec-validation found the original "slaved"
   premise false). The validation showed there is **no operational UI to change the org timezone**:
   `set_org_profile` is reachable only via `PATCH /setup/org-profile`, whose sole FE caller is the
   `SetupWizard`, which `App.tsx` redirects away from the moment the org is OPERATIONAL; the Config tab
   has no org-profile field. So a "slaved + read-only note pointing to the org profile" would point at a
   dead surface, and an org could never fix a wrong business-day timezone in-app. Decision: the editor
   exposes an **editable** timezone Select. The PUT accepts + validates `timezone` (the validation already
   needs `is_valid_timezone` for parity). This editor becomes the **operational** way to set the
   business-day timezone. **Accepted consequence:** the calendar's business-day tz may diverge from
   `organization.timezone` (the R8-authoritative tz for *effective dates*) — that is acceptable; the
   calendar tz governs only working-day/weekend/holiday boundaries for SLAs. `set_org_profile`'s
   calendar-tz sync still runs at setup time; since it is unreachable operationally it can never clobber
   an operator's later edit.
2. **Holiday UX → native `<input type=date>` + Add, allow past.** A list deduped + **sorted ascending**,
   each row with a remove button. Any valid `YYYY-MM-DD` is allowed (a holiday calendar naturally spans
   the whole year incl. dates already passed). No new dependency (matches the `type=time` precedent).
3. **GET no-row → synthesized Mon–Fri default.** Return `{name:"Default", working_days:[1,2,3,4,5],
   holidays:[], timezone: <org tz>, exists:false}` so the editor opens on a sensible starting point; the
   **PUT upserts** (INSERT an `is_default=true` row on first Save). (`<org tz>` is the post-first-Save tz,
   not what the sweep uses pre-Save — see §4.2.)

## 4. Backend design

### 4.1 Shared pure validator module — `services/notifications/calendar_spec.py`

The **crux constraint** is *validation parity*: the PUT must reject **exactly** what
`resolve_working_calendar` would silently degrade to the Mon–Fri / UTC / kept-good fallback, so a saved
calendar never quietly stops working. To prevent drift, extract the strict parsers into one pure, DB-free
module (the `timer.py` precedent) that **both** consumers import:

```python
# pure, stdlib-only, no DB
def parse_working_days(value: object) -> frozenset[int] | None:
    """Strict ISO-weekday set. None ⇒ broken. MOVED VERBATIM from escalation._parse_working_days
    (byte-identical semantics): non-empty list of real ints 1..7 (no bool/float/JSON-string);
    DEDUPS + ACCEPTS duplicates ([1,1,2,7] → frozenset{1,2,7})."""

def parse_holiday(value: object) -> datetime.date | None:
    """A single holiday entry → a date, or None. Preserves the resolver's str()-coercion:
    `datetime.date.fromisoformat(str(value))` — so an int entry like 20260101 still parses
    (keeps the resolver byte-identical). None on any ValueError/TypeError."""

def is_valid_timezone(value: str) -> bool:
    """zoneinfo.ZoneInfo(value) succeeds (the resolver's tz check)."""
```

- **`escalation.py`** imports `parse_working_days` (its private `_parse_working_days` is **deleted**, not
  aliased) + `parse_holiday` (replacing the inline `date.fromisoformat(str(h))`) + reuses
  `is_valid_timezone` shape. **Resolver behaviour stays byte-identical** — `parse_holiday` keeps the
  `str(value)` coercion so an int holiday entry still parses exactly as before.
- **`tests/unit/test_working_calendar_resolve.py`** currently imports the private
  `escalation._parse_working_days` (~11–15 assertions). **Repoint that import** to
  `easysynq_api.services.notifications.calendar_spec.parse_working_days` and rename the call-sites (named
  in §9). (No back-compat alias — the canonical name is the new module's.)
- The **editor service** (below) wraps the SAME parsers **fail-loud**: any `None`/invalid → a 422
  `ProblemException`, never a silent coerce.

> **Parity is asymmetric by field, and that is intentional.** A broken `working_days` makes the resolver
> **fall back to Mon–Fri**; a broken `timezone` makes it **fall back to UTC**; a broken `holiday` is
> **dropped, kept-good** (not a fallback). The editor is **strictly stronger**: it 422s on a broken
> `working_days`/`timezone` (= what the resolver degrades) AND 422s on a broken holiday (vs the resolver's
> silent drop) — because the editor is the *authoring* surface and a typo'd holiday must be surfaced, not
> silently lost. The guarantee is therefore "**editor rejects ⊇ what the resolver would silently
> degrade/drop**", not bidirectional equality.

### 4.2 Service — `services/notifications/calendar_admin.py`

```python
async def get_working_calendar(session, org_id) -> dict:
    """The org's is_default working_calendar as a view dict (.limit(1) defensive read), or the
    synthesized Mon–Fri default (timezone = organization.timezone, exists=False) when none.
    Stored holidays are SANITIZED through parse_holiday (drop unparseable, kept-good — mirrors the
    resolver) so a malformed legacy entry can never wedge a later Save."""

async def update_working_calendar(session, *, actor, name, working_days, holidays, timezone) -> dict:
    """Validate (fail-loud → 422) → ATOMIC upsert of the is_default row → audit CONFIG_UPDATED (on a
    real diff) → return the saved view."""
```

- **Validation (fail-loud, 422 on any), all via the shared strict parsers so it is the SAME code path
  the resolver trusts:**
  - `name` non-empty after strip + ≤255.
  - `working_days` → `parse_working_days` non-None (non-empty, each int 1..7, no bool/float/string;
    **duplicates deduped + accepted, NOT rejected** — parity with the resolver). Stored **sorted unique**
    int list. **Bound:** reject a raw request array longer than **31** elements (DoS guard) → 422.
  - `holidays` — each entry → `parse_holiday` non-None (**reject the whole request on the first bad
    entry**, with the offending value in the 422 title). Deduped, **sorted ascending**, canonicalized to
    `date.isoformat()` `YYYY-MM-DD` strings. **Bound:** reject a list longer than **1000** entries → 422.
  - `timezone` → `is_valid_timezone` else 422 (the resolver degrades an unknown tz → UTC; the editor
    rejects it — parity).
- **Atomic upsert (NO check-then-insert race).** Use `pg_insert(WorkingCalendar)` with
  `.on_conflict_do_update(index_elements=["org_id"], index_where=text("is_default"),
  set_={name, working_days, holidays, timezone, updated_at: func.now()})` — inferring the
  `uq_working_calendar_one_default` partial-unique index. A concurrent first-Save on a no-row org
  therefore resolves to a clean UPDATE (no `IntegrityError`/500). Supply `id` (uuid4), `is_default=True`,
  and `timezone` in the INSERT `values`. **DELETE is never used** (clearing all holidays = empty list).
  > The earlier draft's claim that the bare INSERT was "safe against the unique index (no existing
  > default to collide with)" was **wrong** — the index is exactly what makes a concurrent second INSERT
  > fail. `ON CONFLICT` is the fix (the `setup/service.py` `_load_config(lock=True)` lock is the
  > alternative precedent).
- **Audit (mirror the PATCH precedent):** compute `before` (the prior calendar view, or `{}` for the
  no-row INSERT) and `after` (the saved `{name, working_days, holidays, timezone}`). Write **one**
  `CONFIG_UPDATED` `AuditEvent` (`object_type=config`, `object_id=org_id`, `actor_id=actor.id`,
  `actor_type=user`, `request_id=_rid()`, **real `now()`**) **only when `before != after`** (no no-op
  WORM rows). The `after` carries a `{"working_calendar": {...}}` discriminator. **Accepted v1
  consequence:** reusing `CONFIG_UPDATED` means the discriminator is **human-readable only**, not
  API-filterable (`api/audit.py` filters by `event_type`/`object_type`).
- **No-row vs sweep tz (documented edge).** The synthesized no-row GET previews `timezone = org tz` (so
  the form pre-fills the right value and the first Save creates the correct row). Until that first Save,
  `resolve_working_calendar`'s no-row branch returns `DEFAULT_CALENDAR` (UTC) — so on a genuinely
  un-seeded org the sweep evaluates in UTC while the editor previews the org tz. This is unreachable in
  v1 prod (migration 0067 seeds a default row for every existing org); the first Save makes them
  consistent. Stated here so a no-row GET test asserts the *preview* contract, not sweep behaviour.

### 4.3 Endpoints — `api/config.py` (alongside the health endpoint)

| Method | Path | Gate | Body | Returns |
|---|---|---|---|---|
| GET | `/admin/notifications/working-calendar` | `config.update` | — | `WorkingCalendar` view |
| PUT | `/admin/notifications/working-calendar` | `config.update` | `WorkingCalendarUpdate` | `WorkingCalendar` view |

- **PUT is a full replace** of the editable content (the whole calendar is the unit of edit; a partial
  merge of a holiday list is ill-defined). The FE sends the complete working state; "dirty" only gates
  the Save button. **Last-write-wins is the accepted concurrency model** (admin-only single-org config,
  mirroring `PATCH /admin/config` — no `If-Match`/optimistic token).
- **Request body `WorkingCalendarUpdate`** (Pydantic): `{ name: str, working_days: list[Any],
  holidays: list[Any], timezone: str }`. ⚠ **`list[Any]`, NOT `list[int]`/`list[str]`** — empirically
  pinned on this project's pydantic 2.13.4 (lax mode), `working_days: list[int]` **silently coerces**
  `[true]→[1]`, `["1"]→[1]`, `[1.0]→[1]` (HTTP 200) and 422s `[1.5]` **at Pydantic**, so the strict
  bool/float/string guards in `parse_working_days` would be **dead code** and the parity guarantee would
  be false. With `list[Any]` every value reaches the strict service parser → one uniform service 422 that
  mirrors the resolver's degrade. (A non-array — `"67"`/`null` — still 422s at Pydantic with `list_type`;
  the resolver also degrades those, so parity holds.)
- **Response view `WorkingCalendar`:** `{ name: str, working_days: list[int], holidays: list[str],
  timezone: str, exists: bool }`. `exists=false` only on the synthesized no-row GET.

### 4.4 OpenAPI

Add the two paths under `/admin/notifications/working-calendar` + the `WorkingCalendar` /
`WorkingCalendarUpdate` schemas, mirroring the `/admin/notifications/health` block (tags `[admin]`,
`403 ProblemResponse`). The contract documents `working_days` as `array<integer>` and `holidays` as
`array<string>` (the authoring intent); the strict parity validation is server-side (the `list[Any]`
body is an implementation detail of the parity fix). Redocly-lint only.

## 5. Frontend design

A self-contained **`admin/WorkingCalendarEditor.tsx`** imported into `ConfigAdmin.tsx`, placed as a
visually-delimited titled section between the Notifications toggles and the `NotificationHealthPanel`. It
mirrors `NotificationHealthPanel`'s contract: its own loading/error states, **relying on the parent
ConfigAdmin's GET-`/admin/config`-403 gate** for no-access (the parent returns `NoAccessState` before the
children mount, so the editor never renders for a non-admin; an in-flight 403 falls to `ErrorState`). It
has its **own Save** (PUT) — an independent save section by design (the toggles keep their PATCH Save);
the two are visually separated so the separate Save is obvious.

- **Hooks** (`admin/hooks.ts`): `useWorkingCalendar()` (GET, `retry:false`, `refetchOnWindowFocus:false`,
  `refetchOnReconnect:false` — the unsaved-edit-clobber guard from #273) + `useUpdateWorkingCalendar()`
  (PUT; `onSuccess` invalidates `["working-calendar"]`).
- **Working state + value-equality dirty-diff + single Save:**
  - **Week mask:** a Mantine **`Checkbox.Group`** labelled **"Working days"** (a real group/`fieldset`,
    not 7 loose checkboxes — grouping context for AT) with 7 boxes Mon..Sun (ISO 1..7), colour-safe (the
    check glyph + visible label — never colour alone). `Checkbox.Group` yields `string[]` → **`Number()`
    → sorted-unique ints** before the PUT body + the dirty diff. **≥1 working day enforced** (Save
    disabled + an inline error when none selected).
  - **Holidays:** a native `TextInput type="date"` (aria-label **"Holiday date"**) + an **Add** button
    (aria-label **"Add holiday"**). **Add is a no-op / disabled when the input is empty or invalid**
    (`<input type=date>`.value is `""` then) — never append `""`. Appends to a deduped,
    **sorted-ascending** list; a date already present is a no-op. Each entry renders as a removable
    `Badge`/row whose remove button has a **DISTINCT** accessible name `aria-label={"Remove holiday " +
    date}` (the duplicate-label `getByRole` single-match trap + screen-reader context). Past dates allowed.
  - **Name:** a `TextInput` (required; trims; non-empty to Save).
  - **Timezone:** an **editable** Select reusing the `timezones.ts` curated+searchable idiom
    (`onDropdownOpen` reset; `comboboxProps={{ keepMounted:false }}`).
  - **`dirty`** is computed by **VALUE-equality over CANONICAL forms**, not reference `!==`: `dirty` iff
    `name` changed OR `timezone` changed OR the **sorted-unique** `working_days` differ OR the **sorted**
    `holidays` differ. (Reference equality would read permanently-dirty after the post-save reseed — the
    S-notify-3b post-save-reset class.) A regression test asserts Save returns to **disabled** + shows
    "Saved." after a successful round-trip.
- **Save** is disabled unless `dirty` AND **valid** (name non-empty, ≥1 weekday, and — defensively — no
  un-parseable entry in the holiday list). It PUTs the full `{name, working_days, holidays, timezone}`.
  Success → "Saved." (the ConfigAdmin idiom).

## 6. Testing

- **Shared-module unit test** (`tests/unit/test_calendar_spec.py`): `parse_working_days` /
  `parse_holiday` / `is_valid_timezone` table-driven, incl. `parse_holiday(20260101)` (int entry) →
  `date(2026,1,1)` pinning the `str()`-coercion (the byte-identical backstop). Repoint the existing
  `tests/unit/test_working_calendar_resolve.py` import to `calendar_spec.parse_working_days` (the old
  resolver suite still passes — behaviour unchanged).
- **API integration** (`tests/integration/test_*`, the config-endpoint idiom):
  - **GET split (don't conflate seeded with synthesized):** (a) an org with **no** default row → the
    synthesized Mon–Fri default `exists=false`, `timezone == org tz`; (b) **PUT-then-GET** (or a direct
    INSERT) → the persisted `working_days`/`holidays`/`timezone` round-trip with `exists=true` (the
    production existing-row read path). *(Test orgs are created after 0067, so they have no seeded
    calendar — (a) is the default state; (b) must write first.)*
  - **PUT update:** name/working_days/holidays/timezone change → stored sorted+deduped; a `CONFIG_UPDATED`
    audit is written (real `now()` / a seeded `2026-06/07/08` partition — never a pinned far-future
    `occurred_at`, the partition trap); a **no-op** Save (before==after) writes **no** audit.
  - **INSERT branch (provision a calendar-less org):** create a fresh `organization` row (direct insert
    via the test session, satisfying `short_code` uniqueness + FKs) with **no** default calendar → PUT →
    assert a new row (`is_default=true`, `timezone == that org's tz`/the body tz). A **2-session
    `asyncio.gather`** concurrent no-row PUT race → exactly one row, **no 500** (the `ON CONFLICT`
    proof). *(Note: the INSERT branch is NOT covered by live-smoke — the dev DB org already has the
    0067 seed.)*
  - **Validation-parity matrix (split by field, service-originated 422):** with `list[Any]`, each of
    `[]`, `[8]`, `[0]`, `[true]`, `["1"]`, `[1.0]`, `[1.5]`, a non-array → **422 from the service**;
    a companion unit asserts the SAME value makes `resolve_working_calendar` **fall back to Mon–Fri**.
    **Duplicate `[1,1]` → 200**, stored `working_days==[1]` (dedup+accept) + a companion assert the
    resolver does **NOT** degrade on `[1,1]` (returns `{1}`). Broken holiday (`"2026-13-01"`, `"nope"`,
    `""`) → **422 from the service**; a companion assert the resolver **DROPS** that entry (kept-good),
    the good holidays survive — **not** a fallback. Unknown `timezone` → **422**; companion assert the
    resolver degrades it → UTC.
  - **Bounds:** `working_days` raw length > 31 → 422; `holidays` length > 1000 → 422.
  - **403** for a caller without `config.update`.
- **FE component tests** (`WorkingCalendarEditor.test.tsx`; **import `expect`/`it` from `vitest`** — the
  jest-dom × vitest trap; MSW pinned to the real serializer via `satisfies WorkingCalendar`):
  - **MSW base handlers** for GET **and** PUT `/api/v1/admin/notifications/working-calendar` added to
    `src/test/msw/handlers.ts` (`satisfies WorkingCalendar`) so the existing `ConfigAdmin.test.tsx`
    success-path suites (which now mount the editor) don't fire an unhandled request under
    `onUnhandledRequest:"error"`.
  - Renders the seeded mask + holidays + tz; toggling a weekday / adding + removing a holiday / changing
    the tz makes Save **dirty**; Save PUTs the full body; **Save returns to disabled + "Saved." after the
    round-trip** (stateful MSW — the value-equality dirty regression).
  - **≥1-weekday gating (mutation-verify, not tautology):** start from a valid seeded calendar →
    deselect all weekdays via the UI (dirty=true, valid=false) → assert Save **disabled** + inline error
    → re-select one → assert Save **enabled**. Same shape for clearing the name.
  - **Holiday input:** set the date via `fireEvent.change(input, {target:{value:'2026-12-25'}})` then
    click Add → the `YYYY-MM-DD` chip appears + Save dirty (jsdom `userEvent.type` is unreliable on
    `type=date`). Clicking Add on a **blank** input is a no-op + does not enable Save. The per-row remove
    buttons have distinct `Remove holiday {date}` names.
  - A **jest-axe** smoke.
- **Gates:** `/check-api`, `/check-web`, `/check-contracts`. (No `/check-migrations` — no migration.)
- **Live-smoke:** grant `config.update` SYSTEM override for `demo`; open `/admin/config`; set the week
  mask + add a holiday + change the tz + Save; verify the DB row; then run a `timer_sweep` against a task
  whose business-day reminder/escalation threshold straddles the new holiday and confirm the threshold
  **SHIFTS** (the end-to-end proof that a saved holiday changes SLA behaviour). *(Covers the UPDATE path;
  the INSERT path is integration-test-only — the dev org already has the seed.)*

## 7. Non-negotiable constraints

- **R38 additive-only:** NO new permission key (reuse `config.update`). Catalog stays **102** — do NOT
  touch the `==102` assertions.
- **Validation parity (now literally true):** the `list[Any]` body routes every value to the shared
  strict parsers; the PUT 422s exactly what `resolve_working_calendar` degrades (working_days→Mon–Fri,
  tz→UTC) and additionally rejects a holiday the resolver would silently drop. Editor ⊇ resolver.
- **No migration** (table + grants exist; audit reuses `CONFIG_UPDATED`). Confirm `alembic` head stays
  `0067`.
- **N9 / R53 / R32 unchanged:** the editor only edits config; it never fires, decides, reassigns, or
  delivers anything.

## 8. Named residuals (carried forward, honest)

- The un-numbered **`due_at`-snap-at-materialize** reconcile (overdue can still fire on a non-working day).
- The **claim-threshold-filter tautology** (`remind_2_sent_at IS NULL` always true while `remind_2` unused).
- A distinct **`remind_2`** (second reminder), **`escalate_2`** / reassign, **`capa.overdue`**.
- **Multiple named calendars per org** (this editor governs the single `is_default` row only).
- **Holiday recurrence / bulk import** (e.g. an annual holiday or a country-holiday import); a
  weekday-name / locale-aware holiday label (the list is bare `YYYY-MM-DD`).
- **No-row org sweep-tz preview gap** (synthesized GET previews org tz; pre-first-Save sweep uses UTC) —
  unreachable in v1 (every org is seeded); documented in §4.2.
- The calendar's business-day tz may **diverge** from `organization.timezone` (R8 effective-date tz) now
  that it is independently editable — accepted (§3 fork 1).

## 9. File-change inventory

**New:** `services/notifications/calendar_spec.py`, `services/notifications/calendar_admin.py`,
`apps/web/src/admin/WorkingCalendarEditor.tsx` (+ `.test.tsx`), `tests/unit/test_calendar_spec.py`, a
calendar-admin integration test.
**Edit:** `services/notifications/escalation.py` (import the shared parsers; delete the private
`_parse_working_days`; resolver byte-identical), `tests/unit/test_working_calendar_resolve.py` (repoint
the import to `calendar_spec.parse_working_days`), `api/config.py` (the 2 endpoints + body/view models),
`packages/contracts/openapi.yaml` (2 paths + 2 schemas), `apps/web/src/admin/hooks.ts` (2 hooks),
`apps/web/src/admin/ConfigAdmin.tsx` (mount the section), `apps/web/src/lib/types.ts`
(`WorkingCalendar`/`WorkingCalendarUpdate`), `apps/web/src/test/msw/handlers.ts` (2 base handlers).
**No migration. No new permission key. No WORM/append-only change.**

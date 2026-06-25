# S-notify-7 — Working-calendar admin editor (design)

> **Status:** spec (pre-validation). **Slice:** S-notify-7 (Notification family tail; R29 close-out).
> **Migration:** NONE (head stays `0067`). **New permission key:** NONE (reuse `config.update`; catalog stays 102).
> **Surfaces:** BE (2 endpoints + 1 shared pure module + openapi) + FE (one Config-tab section).

## 1. Goal & motivation

Close the R29-named residual: *"The admin editor (a `config.update`-gated GET/PUT + Config-tab UI) is
also deferred."* S-notify-6 wired the per-org `working_calendar` into the `timer_sweep` so reminder /
escalation offsets are computed as **business days** (skip weekends + holidays). But the calendar is
**frozen at the Mon–Fri / `[]` / `<org-tz>` seed** — there is no in-app way to set the org's working
days, add a holiday, or rename it. This slice adds the editor, so business-day SLAs become actually
configurable by a System Administrator (`config.update`).

It lives in the **existing** admin Config tab (`/admin/config`, shipped S-notify-5b), next to the two
notification toggles and the read-only delivery-health panel.

## 2. What already exists (reuse, do not reinvent)

- **`working_calendar` table** (`db/models/working_calendar.py`, migration 0067): `id, org_id, name,
  working_days` (jsonb ISO-weekday mask `1=Mon..7=Sun`), `holidays` (jsonb `YYYY-MM-DD` list),
  `timezone` (IANA TEXT, default `UTC`), `is_default` (bool; ≤1 default/org via the migration-managed
  partial-unique index `uq_working_calendar_one_default`), `created_at`/`updated_at`. **App-role
  grants: INSERT / SELECT / UPDATE; DELETE is REVOKEd** — the editor needs INSERT (no-row edge) +
  UPDATE only, so **no grant change, no migration.**
- **`resolve_working_calendar(session, org_id)`** + **`_parse_working_days(value)`**
  (`services/notifications/escalation.py`): the **fail-safe** resolver the sweep trusts. `_parse_working_days`
  is the strict ISO-weekday validator (non-empty list of real ints 1..7; rejects `bool`/`float`/JSON-string);
  the resolver also validates `timezone` via `zoneinfo.ZoneInfo` (unknown → UTC + warn) and parses each
  holiday via `date.fromisoformat` (per-entry skip-bad, kept-good).
- **`PATCH/GET /admin/config` + `GET /admin/notifications/health`** (`api/config.py`): the admin route
  precedent — `require("config.update")` (SYSTEM, admin-only), org from `caller.org_id`, `CONFIG_UPDATED`
  audit on `object_type=config`, `object_id=org_id`.
- **`set_org_profile`** (`services/setup/service.py`): already keeps the default calendar's `timezone`
  in sync with `organization.timezone` (the R8-authoritative tz). This is why the **timezone is slaved**
  (fork decision below), not editable here.
- **FE:** `admin/ConfigAdmin.tsx` (working-state + dirty-diff + Save form; parent gates no-access on the
  GET `/admin/config` **403 `forbidden` flag**, NOT `usePermissions` — the cold-`/admin`-cache flash
  trap), `admin/NotificationHealthPanel.tsx` (a self-contained child with its own loading/error states,
  relying on the parent's 403 gate), `admin/AdminShell.tsx` (the Config tab), `admin/hooks.ts`
  (`useOrgConfig`/`useNotificationHealth` — `retry:false`, `refetchOnWindowFocus:false`), and
  `features/notifications/timezones.ts` + `NotificationSettingsPage.tsx` (the curated+searchable tz Select
  idiom + the native `type=time`/`type=date` no-new-dependency precedent + the `onDropdownOpen` reset).

## 3. Confirmed forks (AskUserQuestion, 2026-06-25)

1. **Timezone → SLAVED to the org profile.** The editor shows it read-only with a note; the PUT does
   NOT accept `timezone`. Rationale: one source of truth (`organization.timezone`, R8-authoritative),
   `set_org_profile` already syncs it, and an editor override would be **silently clobbered** on the
   next org-profile save (footgun). The synthesized GET default (no-row case) uses `organization.timezone`.
2. **Holiday UX → native `<input type=date>` + Add, allow past.** A list deduped + **sorted ascending**,
   each row with a remove button. Any valid `YYYY-MM-DD` is allowed (a holiday calendar naturally spans
   the whole year incl. dates already passed). No new dependency (matches the `type=time` precedent).
3. **GET no-row → synthesized Mon–Fri default.** Return `{name:"Default", working_days:[1,2,3,4,5],
   holidays:[], timezone: <org tz>, exists:false}` so the editor opens on a sensible starting point; the
   **PUT upserts** (INSERT an `is_default=true` row on first Save).

## 4. Backend design

### 4.1 Shared pure validator module — `services/notifications/calendar_spec.py`

The **crux constraint** is *validation parity*: the PUT must reject **exactly** what
`resolve_working_calendar` would silently degrade to the Mon–Fri / UTC fallback, so a saved calendar
never quietly stops working. To prevent drift, extract the strict parsers into one pure, DB-free module
(the `timer.py` precedent) that **both** consumers import:

```python
# pure, stdlib-only, no DB
WEEKDAY_MIN, WEEKDAY_MAX = 1, 7

def parse_working_days(value: object) -> frozenset[int] | None:
    """Strict: non-empty list of real ints 1..7 (no bool/float/JSON-string). None ⇒ broken.
    MOVED verbatim from escalation._parse_working_days (byte-identical semantics)."""

def parse_holiday(value: object) -> datetime.date | None:
    """A single holiday entry → a date, or None if not a valid YYYY-MM-DD string."""

def is_valid_timezone(value: str) -> bool:
    """zoneinfo.ZoneInfo(value) succeeds."""
```

- `escalation.py` imports `parse_working_days` (replacing its private `_parse_working_days`; the resolver
  keeps its fail-safe wrapper — `None → Mon–Fri default + warn`) and `parse_holiday` (per-entry
  skip-bad). **Resolver behaviour stays byte-identical** (the existing S-notify-6 unit tests are the
  regression backstop). A thin module-level alias `_parse_working_days = parse_working_days` may be kept
  if any test references the private name — confirm and update the import instead.
- The **editor service** (below) wraps the SAME parsers fail-loud: any `None`/invalid → a 422
  `ProblemException`, never a silent coerce.

> **Why the editor is stricter on holidays than the resolver.** The resolver skips an unparseable
> holiday (kept-good — never crash the sweep on legacy data). The editor is the **authoring** surface:
> a typo'd holiday must be **rejected (422)**, not silently dropped, so the admin sees the error.

### 4.2 Service — `services/notifications/calendar_admin.py`

```python
async def get_working_calendar(session, org_id) -> dict:
    """The org's is_default working_calendar as a view dict, or the synthesized Mon–Fri default
    (timezone = organization.timezone) with exists=False when no default row exists."""

async def update_working_calendar(session, *, actor, name, working_days, holidays) -> dict:
    """Validate (fail-loud → 422) → upsert the is_default row (UPDATE in place, or INSERT
    is_default=True on the no-row edge, timezone = organization.timezone) → audit CONFIG_UPDATED →
    return the saved view. Holidays stored sorted-ascending, deduped, as 'YYYY-MM-DD' strings;
    working_days stored as a sorted unique int list."""
```

- **Validation (fail-loud, 422 on any):** `name` non-empty after strip + ≤255; `working_days` →
  `parse_working_days` non-None (non-empty, each int 1..7, no bool/float/string), stored sorted+unique;
  `holidays` — each entry → `parse_holiday` non-None (reject the whole request on the first bad one,
  with the offending value in the title), deduped, sorted ascending. **No `timezone` field accepted.**
- **Upsert:** `SELECT … WHERE org_id=:org AND is_default` → if present UPDATE `name/working_days/holidays`
  (+ `updated_at = now()`); else INSERT a new row `is_default=True, timezone = organization.timezone`.
  The no-row INSERT is safe against `uq_working_calendar_one_default` (no existing default to collide
  with). DELETE is never used (removing all holidays = empty list, not a row delete).
- **Audit:** one `CONFIG_UPDATED` `AuditEvent` (`object_type=config`, `object_id=org_id`,
  `actor_id=actor.id`, `actor_type=user`) with `before`/`after` carrying a `{"working_calendar": {...}}`
  discriminator so it is distinguishable from a toggle change. Reusing `CONFIG_UPDATED` keeps the slice
  **migration-free** (a new `event_type` value would need an `ALTER TYPE`).

### 4.3 Endpoints — `api/config.py` (alongside the health endpoint)

| Method | Path | Gate | Body | Returns |
|---|---|---|---|---|
| GET | `/admin/notifications/working-calendar` | `config.update` | — | `WorkingCalendar` view |
| PUT | `/admin/notifications/working-calendar` | `config.update` | `WorkingCalendarUpdate` | `WorkingCalendar` view |

- **PUT is a full replace** of the editable content (the whole calendar is the unit of edit; a partial
  merge of a holiday list is ill-defined). The FE sends the complete working state; "dirty" only gates
  the Save button.
- **Response view (`WorkingCalendar`):** `{ name: str, working_days: list[int], holidays: list[str],
  timezone: str, exists: bool }`. `exists=false` only on the synthesized no-row GET.
- **Request body (`WorkingCalendarUpdate`):** `{ name: str, working_days: list[int], holidays: list[str] }`
  (Pydantic types are permissive — `list[int]` would coerce; the SERVICE does the strict parity check so
  the validation is the SAME code path the resolver trusts, not Pydantic's coercion. e.g. a `working_days`
  of `[8]` or `[]` must reach the strict parser, not be silently accepted).

> ⚠ **Pydantic coercion caveat (spec-validation focus):** declare the body fields loosely enough that a
> bad value REACHES the strict service validator (e.g. `working_days: list[int]` rejects a JSON string
> at the Pydantic layer but accepts `[8]`/`[]`/`[1,1]` → the service must catch those). Consider
> `list[Any]`/`list[int]` deliberately and assert in tests that `[]`, `[8]`, `[0]`, `[true]`(if reachable),
> `[1.5]`(if reachable), and a duplicate all 422 **from the service**, with a parity test that the same
> values make the resolver fall back.

### 4.4 OpenAPI

Add the two paths under `/admin/notifications/working-calendar` + the `WorkingCalendar` /
`WorkingCalendarUpdate` schemas, mirroring the `/admin/notifications/health` block (tags `[admin]`,
`403 ProblemResponse`). Redocly-lint only.

## 5. Frontend design

A self-contained **`admin/WorkingCalendarEditor.tsx`** imported into `ConfigAdmin.tsx`, placed between
the Notifications toggles and the `NotificationHealthPanel`. It mirrors `NotificationHealthPanel`'s
contract: its own loading/error states, **relying on the parent ConfigAdmin's GET-`/admin/config`-403
gate** for no-access (the parent returns `NoAccessState` before the children mount, so the editor never
renders for a non-admin; an in-flight 403 falls to `ErrorState`).

- **Hooks** (`admin/hooks.ts`): `useWorkingCalendar()` (GET, `retry:false`,
  `refetchOnWindowFocus:false`, `refetchOnReconnect:false` — the unsaved-edit-clobber guard from #273)
  + `useUpdateWorkingCalendar()` (PUT; `onSuccess` invalidates `["working-calendar"]`).
- **Working state + dirty-diff + single Save** (the ConfigAdmin/NotificationSettingsPage idiom):
  - **Week mask:** 7 checkboxes Mon..Sun (ISO 1..7), colour-safe (Mantine `Checkbox` carries a check
    glyph + visible label — never colour alone). **≥1 working day enforced** (Save disabled + an inline
    error when none selected).
  - **Holidays:** a native `TextInput type="date"` + an **Add** button → appends to a deduped,
    **sorted-ascending** list; each entry renders as a removable row/`Badge`. Adding a date already in
    the list is a no-op. Past dates allowed.
  - **Name:** a `TextInput` (required; trims; non-empty to Save).
  - **Timezone:** a **read-only** `Text` line ("Business days are evaluated in {tz}. Change it on the
    organisation profile.") — not editable (slaved).
- **Save** sends the full `{name, working_days, holidays}` via PUT; the button is disabled unless dirty
  AND valid (name non-empty, ≥1 weekday). Success → "Saved." (the ConfigAdmin idiom).

## 6. Testing

- **API unit/integration** (`tests/integration/test_*` mirroring the config-endpoint idiom):
  - GET returns the seeded default; GET on a fresh org returns the synthesized Mon–Fri default with
    `exists=false`.
  - PUT updates name/working_days/holidays (sorted, deduped); a `CONFIG_UPDATED` audit is written.
  - PUT on a no-default-row org INSERTs the row (`is_default=true`, tz = org tz).
  - **Validation-parity matrix:** for each broken `working_days` (`[]`, `[8]`, `[0]`, dup, and — if a
    JSON value can reach it — `bool`/`float`) and each broken holiday (`"2026-13-01"`, `"nope"`), PUT
    422s; a companion unit test asserts the SAME value makes `resolve_working_calendar` fall back. This
    is the parity proof (the editor rejects exactly what the resolver degrades).
  - 403 for a caller without `config.update`.
- **Shared-module unit test** (`tests/unit/test_calendar_spec.py`): `parse_working_days`/`parse_holiday`/
  `is_valid_timezone` table-driven; assert the byte-identical move (the old escalation tests still pass).
- **FE component tests** (`WorkingCalendarEditor.test.tsx`, MSW pinned to the real serializer via
  `satisfies WorkingCalendar`): renders the seeded mask + holidays; toggling a weekday / adding +
  removing a holiday makes Save dirty; Save PUTs the full body; ≥1-weekday and non-empty-name gating;
  the timezone line is read-only; a jest-axe smoke. **Import `expect`/`it` from `vitest`** (the jest-dom
  × vitest trap). **Stateful MSW** for the save→refetch round-trip.
- **Gates:** `/check-api`, `/check-web`, `/check-contracts`. (No `/check-migrations` — no migration.)
- **Live-smoke:** grant `config.update` SYSTEM override for `demo`; open `/admin/config`; set the week
  mask + add a holiday + Save; verify the DB row; then run a `timer_sweep` against a task whose
  business-day reminder/escalation threshold straddles the new holiday and confirm the threshold SHIFTS
  (the end-to-end proof that a saved holiday changes SLA behaviour).

## 7. Non-negotiable constraints

- **R38 additive-only:** NO new permission key (reuse `config.update`). Catalog stays **102** — do NOT
  touch the `==102` assertions.
- **Validation parity:** the PUT rejects exactly what `resolve_working_calendar` treats as broken
  (shared strict parsers; the editor wraps them fail-loud, the resolver fail-safe).
- **No migration** (table + grants exist; audit reuses `CONFIG_UPDATED`). Confirm `alembic` head stays
  `0067`.
- **N9 / R53 / R32 unchanged:** the editor only edits config; it never fires, decides, reassigns, or
  delivers anything.
- **Timezone slaved** (not editable here) — single source of truth with the org profile.

## 8. Named residuals (carried forward, honest)

- The un-numbered **`due_at`-snap-at-materialize** reconcile (overdue can still fire on a non-working day).
- The **claim-threshold-filter tautology** (`remind_2_sent_at IS NULL` always true while `remind_2` unused).
- A distinct **`remind_2`** (second reminder), **`escalate_2`** / reassign, **`capa.overdue`**.
- **Multiple named calendars per org** (this editor governs the single `is_default` row only).
- **Holiday recurrence / bulk import** (e.g. an annual holiday or a country-holiday import).
- A weekday-name / locale-aware holiday label (the list is bare `YYYY-MM-DD`).

## 9. File-change inventory

**New:** `services/notifications/calendar_spec.py`, `services/notifications/calendar_admin.py`,
`apps/web/src/admin/WorkingCalendarEditor.tsx` (+ `.test.tsx`),
`tests/unit/test_calendar_spec.py`, a calendar-admin integration test.
**Edit:** `services/notifications/escalation.py` (import the shared parsers; keep resolver
byte-identical), `api/config.py` (the 2 endpoints), `packages/contracts/openapi.yaml` (2 paths + 2
schemas), `apps/web/src/admin/hooks.ts` (2 hooks), `apps/web/src/admin/ConfigAdmin.tsx` (mount the
section), `apps/web/src/lib/types.ts` (`WorkingCalendar`/`WorkingCalendarUpdate`).
**No migration. No new permission key. No WORM/append-only change.**

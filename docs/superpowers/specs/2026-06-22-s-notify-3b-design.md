# S-notify-3b — the notification preference matrix UI (Notification family, slice 3b)

> **Date:** 2026-06-22 · **Branch:** `feat/s-notify-3b` · **Type:** FE-only
> **Predecessor:** S-notify-3a (the BE digest *engine* — per-class cadence, the daily-digest Beat,
> quiet hours, the widened `GET/PUT /me/notification-preferences`; R54, migration 0064, merged
> `93b8a57`). This slice puts a **face** on that engine — it *consumes* the already-shipped endpoints.

## 1 · Context & goal

S-notify-3a shipped the backend digest engine: per-user **per-event-class** email cadence
(`immediate`/`daily`/`off`), an hourly digest-sweep Beat that bundles a user's due rows into one summary
email, quiet hours (with an org-gated critical pierce), and the **widened**
`GET/PUT /me/notification-preferences` contract. The engine runs today off **seeded code defaults** —
**there is no UI to see or change any of it.** The SPA's `/settings/notifications` page is still the
slice-2 stopgap: a single master email `Switch`.

This slice delivers the **preference matrix** (doc 10 §9.4 "Digests & quiet hours"): a per-event-class
email-cadence control, the daily-digest hour + timezone, and the quiet-hours window — all on the existing
`/settings/notifications` route, consuming the existing endpoints. It widens the FE's stopgap
`NotificationPreferences` type (`{ email_enabled }`) into the full effective shape and replaces the
single-toggle page with the full surface.

Doc 10 §9 draws a deliberate line that the page's copy must make legible: **in-app notifications are
"always on"; the cadence governs EMAIL only.** Per S-notify-3a, the in-app `notification` row is *always*
created — the per-class mode controls whether/when an **email** goes out, never the bell.

### Decomposition position (Notification family)

| Slice | Scope | Status |
|---|---|---|
| 1 — S-notify-1 | BE spine + email delivery (R53, migration 0063) | ✅ merged (`766dc55`) |
| 2 — S-notify-fe | In-app FE — bell + center + the minimal master email toggle | ✅ merged (`6893c3e`) |
| deeplink | Preserve the requested path through the Keycloak login round-trip | ✅ merged (`61dc442`) |
| 3a — S-notify-3a | The digest *engine* (R54, migration 0064) | ✅ merged (`93b8a57`) |
| **3b — this spec** | **The preference *matrix UI* (FE-only) on `/settings/notifications`** | **now** |
| 4 | Escalation timers (`SlaPolicy`/`working_calendar`/`timer_sweep`) → the `critical`-class events that exercise the quiet-hours pierce end-to-end | deferred |
| 5 | Awareness events (`doc.released`/…) read-scope filtered + the Health delivery-failure panel + SSE | deferred |

## 2 · Binding constraints (what this slice must NOT do)

- **FE-only.** No migration (head stays **0064**). No new permission key (catalog stays **102**, R38).
  **No contract change** → `packages/contracts/openapi.yaml` is untouched (the 3a `NotificationPreference`
  / `NotificationPreferenceUpdate` / `NotificationDigestMode` schemas are already there). **Zero
  `apps/api` change.**
- **No decisions-register entry.** This is a consumption slice under the existing R53/R54.
- **Authenticated-self only.** The preference read/write is self-scoped server-side. The SPA adds **zero
  gating** beyond "you are signed in" — no `usePermissions` probe, no `forbidden`/`NoAccessState` path (a
  self endpoint cannot 403 the caller). Errors are handled calmly (loading / error-with-retry), never as
  a no-access panel.
- **The org pierce toggle is OUT of scope** (owner decision — AskUserQuestion). There is **no admin-config
  FE surface at all** today (`grep notifications_email_enabled` over `apps/web` = zero; `AdminShell` has
  only Users/Roles/Processes). The org flag `notifications_escalation_pierce_quiet_hours` stays BE-only;
  its toggle is a **net-new admin Config tab**, built alongside slice 4 (when the `critical` class it
  governs first fires) or as a small standalone. This slice touches no admin surface.
- **Modes stay closed at `immediate | daily | off`.** `hourly`/`weekly` are doc-§9.4 future-work, **not**
  enum members — do NOT add them. The matrix shows exactly the three live modes.

## 3 · The contract consumed (verified against `apps/api/.../api/notifications.py::PreferenceView`)

```
GET /api/v1/me/notification-preferences → 200  (the fully-resolved EFFECTIVE view)
PUT /api/v1/me/notification-preferences → 200  (partial body; returns the same effective view)
```

The **effective view** (GET response and PUT 200 — identical shape):

```jsonc
{
  "email_enabled": true,                  // master kill-switch; default true when no row
  "digest_modes": {                       // EXACTLY 4 keys, always present, enum order
    "action_required": "daily",
    "awareness":       "daily",
    "critical":        "immediate",
    "admin_ops":       "immediate"
  },
  "digest_hour": 8,                        // 0..23, default 8
  "timezone":    "UTC",                    // IANA name, default "UTC"
  "quiet_start": null,                     // "HH:MM" | null  (null = no quiet window)
  "quiet_end":   null                      // "HH:MM" | null
}
```

GET resolves NULL columns (and a missing row) to **code defaults**, so the page shows real values before
the user has ever saved. The FE cannot distinguish "explicitly set to the default" from "unset" — there is
no "unset" verb; to revert a class the FE PUTs the explicit default value. That is fine for this UI (the
control always shows the effective value).

**PUT is a partial update** keyed on `model_fields_set` — a field changes only if its KEY is present in
the JSON body; `digest_modes` is itself a partial map (send only the classes you change). The four 422
cases and their **application-level `problem.code`s** (read `error.code`, NOT a Pydantic field path):

| `code` | Trigger | Reachable from this UI? |
|---|---|---|
| `invalid_class` | unknown class key | No (we only send the 4 known classes) |
| `invalid_mode` | mode ∉ `{immediate,daily,off}` | No (SegmentedControl) |
| `invalid_hour` | `digest_hour` ∉ `[0,23]` | No (24-slot Select) |
| `invalid_timezone` | tz ∉ `zoneinfo` | No (Intl zones ⊂ zoneinfo) |
| `invalid_time` | malformed `"HH:MM"` | No (`TimeInput` → `HH:MM`) |
| `invalid_quiet_hours` | exactly one of start/end set (presence-XOR **or** value-XOR) | **Prevented** by the Switch model (§4.3); defensive mapping only |

**Quiet-hours clearing** is the one place `null` is meaningful: to disable the window, PUT **both**
`quiet_start` and `quiet_end` as `null` (sending one alone → `invalid_quiet_hours`). The Switch model
makes the FE always send both together.

> The contract is **already in sync** — code, openapi, and FE will all agree once the FE type is widened.
> No openapi edit.

## 4 · The surface — `NotificationSettingsPage` rebuild

One page, `Container size="sm" py="xl"`, top → bottom. Title "Notification settings" + a "Back to app"
subtle button (kept). The body is one form with **local working state** seeded from the GET, a **dirty**
flag, and a single Save (§4.5).

### 4.1 · The in-app-vs-email banner (DP-5 legibility)

A calm one-line note directly under the title, before the controls:

> *"Your in-app bell is always immediate. These settings control **email** only."*

Rendered as a quiet `Alert`/`Text` (neutral surface, an info glyph from the canonical `TONE_GLYPH`, never
colour alone). This is the load-bearing legibility line — it disambiguates the whole page.

### 4.2 · The master email kill-switch

The `email_enabled` Mantine `Switch` (kept from slice 2), with its existing description ("summary + link
only — never controlled content — and requires your administrator to enable email delivery for the
organisation"). It is now part of the form (no longer auto-saves; saved via §4.5).

When `email_enabled` is **off**, a subtle inline note appears under the cadence matrix:

> *"Email is currently off — these per-type cadences apply once email is on."*

The matrix stays **editable** when email is off (so a user can pre-configure), just annotated.

### 4.3 · "Email cadence by type" — the 4-class matrix

Four **stacked rows** (a `Stack`), one per `NotificationClass`, each row:
- a bold class **label** + plain **helper text** (which events, what the cadence means);
- an **`Immediate / Daily / Off` SegmentedControl** (`fullWidth`, the `EditCommitmentModal` idiom).

The presentation copy lives in a new `classMeta.ts` (unit-testable; keeps copy out of JSX):

| Class | Label | Helper text | Tag |
|---|---|---|---|
| `action_required` | "Things you must act on" | "Tasks, reviews, approvals and acknowledgements routed to you." | — |
| `awareness` | "Awareness" | "Approvals, releases, audit milestones in your scope." | — |
| `critical` | "Critical" | "Overdues and integrity alarms — time-sensitive." | — |
| `admin_ops` | "Admin & operations" | "Backup and email-delivery failures." | "In-app only today" |

- **`admin_ops`** carries an "In-app only today" tag (its email template is empty until slice 5 — honest;
  the class is still a valid pref). The other three are clean (they'll fire in slices 4–5; no per-row
  "no events yet" noise).
- Helper text spells out the cadence meaning in the user's terms: *daily* = "bundled into your
  daily digest"; *off* = "in-app only — no email"; *immediate* = "email as it happens". (One shared
  sentence under the matrix rather than repeating per row is acceptable; see §4.6 copy.)
- **A11y / the `getByLabelText` trap:** each SegmentedControl's accessible name **folds in the class
  label** (e.g. `aria-label="Email cadence — Things you must act on"`) so the four controls have **distinct**
  accessible names. The selected option is conveyed by the SegmentedControl's native radiogroup semantics
  (`role="radio"` + checked) — shape/label, never colour.
- **Categorical, not RAG:** cadence is an ordinal preference, not an alarm status — style with the neutral
  accent/surface tokens, **not** the danger/warning RAG tones (the S-interested-parties influence-ramp
  precedent).

### 4.4 · "Daily digest" timing

A section below the matrix:

- **Send at** — a Mantine `Select` of 24 hour-slots labeled `00:00 … 23:00` (value = the hour int as a
  string), → `digest_hour`. (A Select, not a free NumberInput → no out-of-range value possible.)
- **Timezone** — a **searchable** `Select`. The control's **value is exactly the GET's `timezone`** (the
  truthful stored value — default `"UTC"`); we PUT `timezone` only if the user changes it (partial
  update). The resting `data` is a **curated common-zone shortlist** (~12: UTC, Europe/London,
  Europe/Berlin, Europe/Paris, America/New_York, America/Chicago, America/Denver, America/Los_Angeles,
  Asia/Kolkata, Asia/Shanghai, Asia/Tokyo, Australia/Sydney) with the **browser-detected zone prepended**
  (and the stored zone, if neither) so both are one click away; a helper line *"Type to search all time
  zones."* On a non-empty search the `data` expands to the full IANA set
  (`Intl.supportedValuesOf("timeZone")`, filtered by the query, `limit`-capped). **Detection is a
  suggestion, never an auto-override** — `Intl.DateTimeFormat().resolvedOptions().timeZone` is only
  *prepended into the list* so the user's likely zone is easy to pick; the displayed value never diverges
  from what is actually stored (so the UI can't imply a schedule the engine won't honour).

  > ⚠ `Intl.supportedValuesOf("timeZone")` (~418 canonical CLDR zones) is a **subset** of the server's
  > `zoneinfo.available_timezones()` (~600), so every FE-offered zone is server-valid → no `invalid_timezone`
  > 422. Node 22 / jsdom expose `Intl.supportedValuesOf`; a tiny guard falls back to the curated list if
  > absent.

- **Quiet hours** — a `Switch` "Enable quiet hours". **Off** ⇒ the window is cleared (PUT both
  `quiet_start` and `quiet_end` as `null`). **On** ⇒ reveals two Mantine `TimeInput`s (start / end,
  default `22:00` / `07:00` when first enabled, or the stored values), and PUT sends **both**. This Switch
  model **structurally enforces** the both-or-neither contract — the FE can never send a one-sided update,
  so `invalid_quiet_hours` is unreachable through normal use. When On, both inputs are required
  (non-empty) before Save is allowed; a blank input shows an inline "Required" message and blocks Save.
  Wrap-around windows (start > end, e.g. 22:00–07:00) are valid and supported by the engine — no FE
  ordering validation.

  - **Soft digest-in-quiet note (non-blocking):** when quiet hours are on and `digest_hour` falls inside
    the window, show a quiet `Text size="xs"`: *"Your digest hour is within your quiet hours; the daily
    digest still sends at this time."* (The engine sends the daily digest at `digest_hour` regardless —
    quiet hours hold *immediate* emails, not the scheduled digest. Informative, never a validation error.)

### 4.5 · Save model — one partial PUT

A single **"Save changes"** button (primary), enabled only when the working state differs from the loaded
GET (`dirty`). On click it assembles a **partial** `NotificationPreferencesUpdate` of only the changed
fields:

- `email_enabled` — included iff toggled.
- `digest_modes` — a partial map of only the classes whose mode changed.
- `digest_hour` / `timezone` — included iff changed.
- `quiet_start` + `quiet_end` — included **together** iff the quiet-hours state changed (both values, or
  both `null` when disabled).

`useUpdateNotificationPreferences` (PUT, invalidate `["notification-preferences"]`, **non-optimistic**)
sends it; on success the page shows "Saved." and resets `dirty` from the returned effective view; on error
`MutationErrorState` renders the unwrapped `ApiError`, and a known `error.code`
(`invalid_quiet_hours`/`invalid_hour`/…) maps to an inline field message (defensive — the controls
prevent these).

> **Why one Save (not per-control auto-save):** the quiet-hours both-or-neither constraint and the grouped
> timing fields make a single partial PUT cleaner and safer than N silent saves, and it folds the master
> toggle into one consistent surface. (Owner-confirmed; the one behavior change from the slice-2
> auto-saving toggle.)

### 4.6 · Copy approach (owner pick: top banner + per-row helper text)

- The §4.1 banner carries the in-app-always vs email-only model.
- Each class row carries its own helper text (§4.3). One shared sentence under the matrix explains the
  three cadence meanings (immediate / daily / off) so each row's text stays short.
- No `ⓘ` popover (the leaner of the offered options).

## 5 · Data layer

### 5.1 · Types (`apps/web/src/lib/types.ts`)

```ts
export type NotificationDigestMode = "immediate" | "daily" | "off";
export type NotificationClass = "action_required" | "awareness" | "critical" | "admin_ops";

export interface NotificationPreferences {
  email_enabled: boolean;
  digest_modes: Record<NotificationClass, NotificationDigestMode>;
  digest_hour: number;
  timezone: string;
  quiet_start: string | null;
  quiet_end: string | null;
}

export interface NotificationPreferencesUpdate {
  email_enabled?: boolean;
  digest_modes?: Partial<Record<NotificationClass, NotificationDigestMode>>;
  digest_hour?: number;
  timezone?: string;
  quiet_start?: string | null;
  quiet_end?: string | null;
}
```

(Widens the existing `{ email_enabled: boolean }`. The `NotificationClass` order = the enum order the
server iterates, so a `satisfies`-pinned fixture matches byte-for-byte.)

### 5.2 · Hooks (`features/notifications/hooks.ts`)

`useNotificationPreferences` is **unchanged** — same `queryKey: ["notification-preferences"]`, same
`api.get<NotificationPreferences>(...)`; the widened type flows through. (`retry: false` kept.)

### 5.3 · Mutations (`features/notifications/mutations.ts`)

Replace `useSetEmailEnabled` with:

```ts
export function useUpdateNotificationPreferences() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NotificationPreferencesUpdate) =>
      api.send<NotificationPreferences>("PUT", "/api/v1/me/notification-preferences", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });
}
```

(`useSetEmailEnabled` has exactly one consumer — the settings page — so it is safe to remove.)

### 5.4 · New helpers

- `features/notifications/classMeta.ts` — the ordered 4-class presentation map (`{ key, label, helper,
  tag? }[]`) from §4.3; pure data, a small unit test asserts all 4 classes present and order = enum order.
- `features/notifications/timezones.ts` — `detectTimeZone(): string`, `COMMON_ZONES: string[]`,
  `allTimeZones(): string[]` (Intl-sourced, dedup-merged with `COMMON_ZONES`, guarded fallback). A unit
  test asserts the detected zone is included in the resting list and that `allTimeZones()` is non-empty.

## 6 · Testing (vitest + MSW + jest-axe)

- **MSW** (`test/msw/handlers.ts`): widen the default GET fixture to the full effective shape, pinned
  `satisfies NotificationPreferences` (copy the shape from `PreferenceView` — do NOT hand-guess). Add a
  default PUT handler that merges the partial onto the current state and returns the full effective view
  (a small module-level closure, reset per test, or per-test `server.use`). The strict-tsc
  `HttpResponse.json(body as Record<string, unknown>)` cast for `body: unknown`.
- **`NotificationSettingsPage.test.tsx`** (rewrite):
  1. reflects loaded values (a non-default fixture → the right SegmentedControl option checked, the right
     hour/timezone, quiet hours shown);
  2. change a class cadence + Save → assert the **partial** PUT body (`{ digest_modes: { action_required:
     "off" } }`) and "Saved.";
  3. enable quiet hours, set 22:00/07:00, Save → asserts both `quiet_start`/`quiet_end` sent; disable +
     Save → asserts both `null`;
  4. timezone search surfaces + picks a non-curated zone (`userEvent.setup()` → open → type → pick the
     option), asserts it lands in the PUT;
  5. a 422 (`{ code: "invalid_quiet_hours" }`) maps to an inline message (defensive path);
  6. **jest-axe** smoke (`expect(await axe(container)).toHaveNoViolations()`) — a changed routed page.
- **`classMeta.test.ts` / `timezones.test.ts`** — the two small helper unit tests.
- **House traps carried:** every test file `import { describe, expect, it } from "vitest"`;
  `userEvent.setup()` + async `findByRole`/`findByLabelText` for the Select/SegmentedControl (the global
  `scrollIntoView` + `ResizeObserver` stubs already exist in `test/setup.ts`); **distinct** accessible
  names per cadence control; **never** add `transitionProps={{duration:0}}` to the production component to
  force a test green; the `satisfies`-pinned fixture; the `body as Record<string,unknown>` cast.
- **Gate:** the full `/check-web` (eslint + strict `tsc --noEmit` incl. `noUncheckedIndexedAccess` + build +
  the whole vitest suite) must be green before the PR.

## 7 · Out of scope (named, not faked)

- **The admin pierce-flag tab** — `notifications_escalation_pierce_quiet_hours` + the org
  `notifications_email_enabled` need a net-new admin Config surface (a `config.update`-gated AdminShell tab
  consuming `GET/PATCH /admin/config`); deferred to slice 4 or a standalone (owner decision).
- **`hourly` / `weekly` cadences** — doc §9.4 future-work, not enum members; not added.
- **The two BE tidy-ups** — the dead `Recipient.email_enabled` re-read in
  `services/notifications/recipients.py` and the symmetric MR-orphan-fallback test in
  `test_notification_subtype_routing.py` — left for a later BE slice (this slice stays strictly FE-only).
- **Per-control auto-save** — deliberately replaced by one explicit Save (§4.5).

## 8 · Risks & mitigations

| Risk | Mitigation |
|---|---|
| A MSW fixture drifts from the real serializer → a false-PASS that ships a wrong-shape read | Pin the fixture `satisfies NotificationPreferences`, shape copied from `PreferenceView`; the type is the contract. |
| `Intl.supportedValuesOf` absent in the runtime | Guard + fall back to `COMMON_ZONES`; the curated resting list is always available. |
| A one-sided quiet-hours PUT → `invalid_quiet_hours` 422 | The Switch model only ever sends both (or both null); a defensive `error.code` map covers the impossible case. |
| The four cadence controls share an accessible name → `getByLabelText` breaks | Fold the class label into each control's `aria-label`. |
| Folding the master toggle into one Save changes slice-2 behavior unexpectedly | Owner-confirmed; the page test asserts the new save flow; the bell/center are untouched. |
```

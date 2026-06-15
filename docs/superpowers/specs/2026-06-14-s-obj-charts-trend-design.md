# S-obj-charts — KPI trend charts (objectives, clause 6.2) · design

**Date:** 2026-06-14 · **Slice:** `s-obj-charts` · **Family:** Quality Objectives (clause 6.2), v1.1 roadmap item (`docs/16-roadmap.md:200`)
**Status:** DRAFT — awaiting owner approval before the plan.

## One-line

Replace the "Trend charts arrive in a later release" placeholder on the `/objectives/:id` detail page
(`MeasurementsSection.tsx:53`) with a calm, hand-rolled **SVG trend chart**: value-over-period + a
stepped target reference line + **per-reading RAG-coloured points**. The RAG per reading is computed
**server-side** (a small additive `rag` field on the `_measurement` serializer) so the rule stays in one
place (N9 — "status against a rule, never an FE re-derivation").

This is a **descriptive read-only trend view** — explicitly NOT SPC/forecast/operational-analytics (N6
stays out), and explicitly blessed by the roadmap.

## Owner F-decisions (recorded this session)

- **Slice** = KPI trend charts (the only FE-mostly, migration-free, roadmap-blessed candidate that closes a
  visible placeholder).
- **Charting approach** = **hand-rolled SVG** (zero new deps, D4-clean; the house pattern — the ⌘K palette,
  the redline/visual-diff viewers are all hand-rolled; **no existing `<svg>`/charting component anywhere in
  `apps/web`**, confirmed by grep → design fresh in the `BandPreview` inline-style idiom).
- **RAG layer** = **server-side RAG points** — add an additive `rag` to `_measurement` (reuses the existing
  `rag_status` rule + a contract update; **still no migration / no new permission key / no new endpoint**).
- **Surface** = **detail page only** (`/objectives/:id`). Register sparklines = a named deferral.

## Scope summary

- **Backend (Fork A):** one serializer enrichment (`_measurement` gains `rag`) + its two call sites + the
  contract + the FE `Measurement` type. **No migration, no new key, no new endpoint, no enum.** Head stays `0051`.
- **Frontend:** one new component (`ObjectiveTrendChart.tsx`) + the `MeasurementsSection` integration + one
  additive prop on the detail page.
- **Gates:** `/check-api` + `/check-contracts` + `/check-web`. (No `/check-migrations` — migration-free.)

---

## Part 1 — Backend: per-reading RAG on `_measurement` (Fork A)

### The RAG-per-reading semantics (the load-bearing design call)

`rag_status(*, current, target, direction, at_risk_threshold) -> "green"|"amber"|"red"|"unmeasured"` is
keyword-only and pure (`domain/objectives/rules.py:29`). For a **measurement** `current = m.value` is always
present → the result is always `green`/`amber`/`red` (never `unmeasured`).

The inputs:
- **`target`** = `m.target_at_capture` — **frozen per reading** at record time (`kpi_measurement.py:4` docstring:
  *"`target_at_capture` freezes the objective's then-target so a later target edit can't rewrite a past
  verdict."*). So **green-vs-not-green is historically EXACT.**
- **`direction` + `at_risk_threshold`** are **NOT** frozen on `kpi_measurement` (the model carries only
  `target_at_capture`). They come from the objective's **governing commitment** — resolved exactly as the rest
  of the page grades, via `resolve_commitment(governing, …)` (`api/objectives.py:165`,
  `services/objectives/queries.py:99`).

⚠ **Documented caveat (bless this):** the **amber boundary** uses the *current governing* threshold/direction
against the *historical* `target_at_capture`. For an objective whose commitment was **never revised** (the
overwhelming common case) this is exact. After an S-obj-4 commitment revision that **changed the threshold**,
the historical amber/red split is approximate (green-vs-not stays exact). This is acceptable because (a) the
chart is descriptive (N9), not a compliance verdict; (b) freezing a `threshold_at_capture` would require a
**migration** — out of the owner-approved migration-free scope; it's named as a deferral below. This matches the
page's existing "grade through the governing commitment" posture, just with the per-reading frozen target
substituted in.

### Serializer change

`api/objectives.py:122` — `_measurement` gains two **keyword-only required** params and emits `rag`:

```python
def _measurement(
    m: KpiMeasurement,
    *,
    direction: ObjectiveDirection,
    at_risk_threshold: Decimal | None,
) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "objective_id": str(m.objective_id) if m.objective_id else None,
        "record_id": str(m.record_id),
        "period": m.period.isoformat(),
        "value": str(m.value),
        "target_at_capture": str(m.target_at_capture),
        "unit": m.unit,
        "source": m.source,
        "created_at": m.created_at.isoformat(),
        "rag": rag_status(
            current=m.value,
            target=m.target_at_capture,
            direction=direction,
            at_risk_threshold=at_risk_threshold,
        ),
    }
```

⚠ **No new imports** — `rag_status` (line 36), `ObjectiveDirection` (line 22), `Decimal` (line 13),
`resolve_commitment`/`build_commitment` (line 35), `get_objective` (line 51) are **all already imported** in
`objectives.py`. (Avoids the format-hook strip-on-unused-import trap.)

### Call site 1 — `GET …/measurements` (`api/objectives.py:783`)

Resolve the governing commitment once, then map:

```python
@router.get("/objectives/{objective_id}/measurements")
async def list_measurements_endpoint(objective_id, caller=Depends(_kpi_read), session=…):
    row = await get_objective(session, objective_id)
    ms = await list_measurements(session, objective_id)
    if row is None:
        # No objective row ⇒ no measurements anyway; preserve the current 200 + empty-data behaviour
        # (do NOT introduce a 404 here — _kpi_read already gated; keep the contract).
        return {"data": [_measurement_no_rag…]}   # see note
    qo, _ident, _title, _state, gov = row
    c = resolve_commitment(
        gov, target_value=qo.target_value, unit=qo.unit, direction=qo.direction,
        due_date=qo.due_date, at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value, policy_id=qo.policy_id,
    )
    return {"data": [
        _measurement(m, direction=c.direction, at_risk_threshold=c.at_risk_threshold) for m in ms
    ]}
```

**`row is None` handling:** a non-existent / non-OBJ id has no measurements (`list_measurements` returns `[]`),
so `{"data": []}` is returned either way — **no behaviour change** (today the endpoint returns `200 {"data": []}`
for an unknown id; we keep that). The empty branch never calls `_measurement`, so there's nothing to serialise.
(Concretely: `if row is None: return {"data": []}`.)

### Call site 2 — `POST …/measurements` (`api/objectives.py:764`)

After `record_measurement(...)` returns `m`, resolve the commitment for the response `rag`:

```python
m = await record_measurement(…)
row = await get_objective(session, objective_id)
qo, _ident, _title, _state, gov = row   # exists — we just recorded against it
c = resolve_commitment(gov, … qo fields …)
return _measurement(m, direction=c.direction, at_risk_threshold=c.at_risk_threshold)
```

One extra indexed PK read on the POST path — negligible. (`row` cannot be None here; `# pragma: no cover`
guard if desired, mirroring the other endpoints' post-mutation re-reads.)

### Contract (`packages/contracts/openapi.yaml:7869`)

Add `rag` to the `Measurement` schema, mirroring the `Objective.rag` enum (line 7921), and add it to `required`
(a measurement always has a value → `rag` is always present):

```yaml
        rag:
          type: string
          enum: [green, amber, red, unmeasured]
          description: "Per-reading RAG: value vs target_at_capture, direction/threshold from the governing commitment. Never 'unmeasured' for a measurement (value always present)."
```

`required: [id, record_id, period, value, target_at_capture, unit, created_at, rag]`.

### FE type (`apps/web/src/lib/types.ts:1199`)

```ts
export interface Measurement {
  id: string;
  objective_id: string | null;
  record_id: string;
  period: string; // ISO date
  value: string; // decimal string
  target_at_capture: string; // decimal string
  unit: string;
  source: string | null;
  created_at: string; // ISO date-time
  rag: ObjectiveRag; // S-obj-charts — per-reading RAG (never "unmeasured" in practice)
}
```

`ObjectiveRag` already exists (`types.ts:1140`). **`tsc` will force every `Measurement` fixture in the web
suite to add `rag`** — update them in the same tasks (pin via `satisfies Measurement`).

---

## Part 2 — Frontend: the trend chart

### New component — `apps/web/src/features/objectives/ObjectiveTrendChart.tsx`

**Props:** `{ measurements: Measurement[]; unit: string; direction?: ObjectiveDirection }`
(`measurements` is the list as the API returns it — **newest-first**, `order_by(desc(period), desc(created_at))`).

**Data transform (pure, top of component):**
- `const series = [...measurements].reverse()` → **oldest-left, newest-right** (ascending; reversing the
  desc-sorted list yields ascending period).
- Per point: `x` = chronological **index** (categorical, evenly spaced — see x-axis note), `value = Number(m.value)`,
  `target = Number(m.target_at_capture)`, `rag = m.rag`.
- y-domain = `[min, max]` over `values ∪ targets`, padded ~8%. **Do not force a 0 baseline** (KPI ranges are
  often tight and far from 0; the target line is the reference; a forced 0 flattens the trend). Guard a
  degenerate domain (all equal) by padding ±1 (or ±|v|·0.1).

**Rendering (hand-rolled SVG, `BandPreview` idiom — inline styles + CSS-var colours):**
- A single `<svg role="img" aria-label={summary} viewBox="0 0 720 280" style={{ width: "100%", height: "auto" }}>`
  with `preserveAspectRatio="xMidYMid meet"` → responsive width.
- Plot margins ≈ `{ top: 16, right: 16, bottom: 28, left: 48 }`.
- **Axes:** a left y-axis with ~3–4 gridlines + numeric labels suffixed with `unit`; a bottom x-axis labelling
  periods (thinned: label first/last + every Nth so labels never crowd — show all when `series.length ≤ 8`).
  Muted gridlines (`var(--mantine-color-gray-3)`), text `var(--mantine-color-gray-6)`.
- **Target line:** a **stepped** (step-after) line through `target` values, dashed, `var(--mantine-color-gray-5)`.
  Degenerates to a flat line when the target never changed; visibly steps after a commitment revision.
- **Value line:** a neutral polyline through `value` points (`var(--mantine-color-blue-6)`, ~2px).
- **Points:** a circle per reading, **filled by RAG** via a local map (mirrors `BandPreview.tsx:5`
  `ZONE_COLOR`):
  ```ts
  const RAG_FILL: Record<ObjectiveRag, string> = {
    green: "var(--mantine-color-green-6)",
    amber: "var(--mantine-color-yellow-6)",
    red: "var(--mantine-color-red-6)",
    unmeasured: "var(--mantine-color-gray-5)", // unreachable for a measurement; kept total
  };
  ```
  Each point gets a native **`<title>`** child: `"{period}: {value} {unit} (target {target_at_capture}) — {RAG_LABEL[rag]}"`
  → dependency-free hover tooltip + screen-reader text.
- **Legend** (a small Mantine `Group` below the SVG, distinct accessible labels): "Value" (line swatch) ·
  "Target" (dashed swatch) · the three RAG dots (Green/Amber/Red) using `RAG_LABEL` (`labels.ts:10`).
- **Single-reading state** (`series.length === 1`): render one RAG dot + the target reference + the axes, **no
  polyline**, and a `Text size="xs" c="dimmed"` caption "One reading so far." (a two-point line is the minimum
  for a trend).
- **Direction hint** (optional polish, when `direction` provided): a small `Text size="xs" c="dimmed"` caption
  using `DIRECTION_LABEL` (`labels.ts:23`) with a ↑/↓ glyph (e.g. "↑ Higher is better").
- **`aria-label` summary:** e.g. `"KPI trend, {unit}: {n} readings from {firstPeriod} to {lastPeriod}; latest {latestValue} {unit}, status {RAG_LABEL[latestRag]}."`

**Empty (0 readings)** is NOT this component's concern — `MeasurementsSection` already renders "No measurements
recorded yet." and won't mount the chart.

### Integration — `MeasurementsSection.tsx`

- Replace the `<Text>Readings are append-only. Trend charts arrive in a later release.</Text>` placeholder
  (line 53). In the `data.length > 0` branch, render, in order: **`<ObjectiveTrendChart … />`** → the existing
  `<Table>` → `<Text c="dimmed" size="xs">Readings are append-only.</Text>` (the "Trend charts arrive…" clause
  is removed; "append-only" stays as the audit note).
- Add an **optional `direction?: ObjectiveDirection`** prop and thread it to the chart.

### Detail page — `ObjectiveDetailPage.tsx:178`

Pass the direction (additive, no other change):

```tsx
<MeasurementsSection objectiveId={o.id} unit={o.unit} direction={o.direction} />
```

---

## Part 3 — Test plan

### API (`/check-api`)

- **Unit (native on Windows — preferred):** call `_measurement(m, direction=…, at_risk_threshold=…)` directly
  with in-memory `KpiMeasurement(...)` instances (no session needed for serialisation). Assert `rag`:
  - HIGHER_IS_BETTER: value≥target → `green`; threshold≤value<target → `amber`; value<threshold → `red`;
    `at_risk_threshold=None` + value<target → `red` (no amber).
  - LOWER_IS_BETTER: value≤target → `green`; target<value≤threshold → `amber`; value>threshold → `red`.
  - Confirm the other fields are byte-unchanged (regression).
- **Integration (CI-only on this Windows box — write failing-first by reasoning, CI verifies):**
  - `GET …/measurements` returns `rag` per reading; a multi-reading objective spanning green/amber/red.
  - `POST …/measurements` response carries `rag`.
  - **Governing-commitment resolution:** an Effective objective whose target was revised (S-obj-4) → an old
    reading's `rag` grades against its **`target_at_capture`** (the frozen historical target), not the new
    governing target. (The headline correctness proof for the "frozen verdict" semantics.)
  - The `row is None`/unknown-id branch still returns `200 {"data": []}` (no new 404).

### Contracts (`/check-contracts`)

- redocly lint clean with the `Measurement.rag` addition.

### Web (`/check-web`; full suite via `--pool=forks --poolOptions.forks.singleFork=true`)

- **`ObjectiveTrendChart.test.tsx`** (new): props-fed, no MSW.
  - N-reading fixture renders N points, **oldest-left** (assert the first DOM point maps to the earliest
    period — the reverse).
  - Points coloured by `rag` (assert fill per the RAG map).
  - Target stepped line present; value line present.
  - Single-reading state: one point, no polyline, the "One reading so far." caption.
  - `role="img"` + a meaningful `aria-label`; a **jest-axe** smoke (no violations).
  - `import { expect, it } from "vitest"` (the jest-dom×tsc trap).
- **`MeasurementsSection.test.tsx`** (update): the old placeholder text assertion is removed; assert the chart
  renders above the table when readings exist; the empty/forbidden/error/loading branches are unchanged; the
  "Readings are append-only." note remains. Fixtures gain `rag` (pin `satisfies Measurement`).
- **Fixture sweep:** any other `Measurement` fixture (`hooks.test.tsx`, `RecordMeasurementModal.test.tsx`, …)
  gains `rag` — `tsc --noEmit` enforces it; pin via `satisfies`.

---

## Part 4 — Gates, review, smoke, ship

1. `/check-api` (ruff + format-check + mypy-strict + unit) · `/check-contracts` · `/check-web`.
2. **diff-critic** (the branch diff) + **web-test-trap-reviewer** (the `apps/web` diff). No migration → **no**
   migration-reviewer.
3. **Live smoke** (owner does the Keycloak login): on `/objectives/:id` for an objective with ≥2 readings,
   the chart renders with RAG-coloured points + the target line; record a new measurement → the chart updates
   (the list invalidates). Grant `objective.read`/`kpi.read`/`kpi.record` SYSTEM overrides to org-AHT users via
   `scripts/grant-overrides.py` (edit KEYS, **revert before the PR**). The `/objectives` register + the full
   `/objectives/:id` route drive fine via Chrome MCP (no drawer wall here — this is a full route, not a `/dcrs`
   drawer).
4. **PR** → green CI (9/9) → **`@codex review`** after CI (poll reviews + the 👍 reaction; COMMENTED = findings,
   👍 = clean; expect 1–5 rounds; verify each finding vs code, fix+reply+resolve).
5. Squash-merge on owner OK → `/finish-slice` → a **separate** finish-slice docs PR (the #137/#139 precedent;
   `main` is protected).

---

## Non-goals & named deferrals (not faked)

- **Register sparklines** (`/objectives` rows) — needs the per-objective series in the list response (a backend
  enrichment or N fetches). Deferred (Fork B = detail-only).
- **`threshold_at_capture` freeze** — would make the historical amber/red split exact after a threshold-changing
  commitment revision, but needs a migration (a new `kpi_measurement` column + `record_measurement` change).
  Out of the migration-free scope; the green-vs-not verdict is already exact. Named v1.x.
- **Time-proportional x-axis** — the chart uses categorical (per-reading) x; honest for the regular-cadence KPI
  case. A true time scale (honouring irregular gaps) is a possible refinement, not required.
- **SPC / forecast / control limits / operational analytics** — **permanently out (N6).** This chart is
  descriptive only.
- **Interactivity beyond native `<title>` tooltips** (crosshair, zoom, export-PNG) — out.

## Risks / traps carried

- **Backend:** `_measurement`'s new params are **required kwargs** → both call sites + every test caller must
  pass them (mypy-strict + the unit tests catch a miss). No new imports (all symbols already imported).
- **`row is None` branch** must keep the current `200 {"data": []}` for an unknown id — do **not** add a 404.
- **Web:** pin every `Measurement` fixture via `satisfies Measurement` (now with `rag`); `import { expect, it }
  from "vitest"` in new test files; run the full suite via `--pool=forks … singleFork=true`.
- **SVG colours** must be CSS vars (`var(--mantine-color-*-6)`), not Mantine colour *names* (`labels.ts` `RAG_COLOR`
  is name-typed for Mantine `color` props; the SVG needs the `BandPreview` `ZONE_COLOR` var idiom).
- **No FE rule duplication** — the chart NEVER recomputes RAG; it reads the server's `m.rag` verbatim (N9).

## File-change inventory

**Backend**
- `apps/api/src/easysynq_api/api/objectives.py` — `_measurement` + 2 call sites (~15 LOC).
- `packages/contracts/openapi.yaml` — `Measurement.rag` (+ `required`).

**Frontend**
- `apps/web/src/lib/types.ts` — `Measurement.rag`.
- `apps/web/src/features/objectives/ObjectiveTrendChart.tsx` — new.
- `apps/web/src/features/objectives/ObjectiveTrendChart.test.tsx` — new.
- `apps/web/src/features/objectives/MeasurementsSection.tsx` — chart integration + `direction` prop.
- `apps/web/src/features/objectives/MeasurementsSection.test.tsx` — updated.
- `apps/web/src/features/objectives/ObjectiveDetailPage.tsx` — pass `direction`.
- Measurement fixtures across the suite — `+ rag`.

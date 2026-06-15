# S-obj-charts — KPI trend charts · implementation plan

**Spec:** `docs/superpowers/specs/2026-06-14-s-obj-charts-trend-design.md` (owner-approved, incl. the amber caveat).
**Branch:** `feat/s-obj-charts`. **Migration-free** (head stays `0051`). No new key/endpoint/enum.

## Task graph (disjoint files → safe parallel; one dependency edge)

```
Task 1 (backend)   ─┐
                    ├─ parallel (disjoint files)
Task 2 (chart+type)─┘
                       └─► Task 3 (integration)   [needs Task 2: the chart + Measurement.rag]
Reviews: per-task quality review after each (parallel, read-only).
Gates + diff-critic + web-test-trap + live smoke: main loop, on the assembled tree.
```

Implementers **do not commit/push** — the main loop assembles, runs the gates, and commits.

---

### Task 1 — Backend: per-reading RAG (independent)

**Files:** `apps/api/src/easysynq_api/api/objectives.py`, `packages/contracts/openapi.yaml`,
`apps/api/tests/unit/` (objectives serializer tests), `apps/api/tests/integration/` (objectives endpoints).

**TDD:**
1. **Failing-first unit tests** calling `_measurement(m, direction=…, at_risk_threshold=…)` directly with
   in-memory `KpiMeasurement(...)` instances — assert `rag` across HIGHER/LOWER × green/amber/red × `None`
   threshold (green/red only). (These run natively on Windows.)
2. Implement: `_measurement` gains keyword-only `direction: ObjectiveDirection` + `at_risk_threshold: Decimal | None`
   and emits `rag = rag_status(current=m.value, target=m.target_at_capture, direction=…, at_risk_threshold=…)`.
   **No new imports** (all symbols already imported in `objectives.py`).
3. Both call sites resolve the governing commitment once via `get_objective` → `resolve_commitment(gov, …qo…)`:
   - `list_measurements_endpoint`: `row = get_objective(...)`; `if row is None: return {"data": []}` (preserve
     the current 200+empty for an unknown id — **no new 404**); else map with `c.direction`/`c.at_risk_threshold`.
   - `record_measurement_endpoint`: after `record_measurement`, `get_objective` → resolve → `_measurement(m, …)`.
4. Contract: add `rag` (enum `[green, amber, red, unmeasured]`, mirroring `Objective.rag`) to the `Measurement`
   schema + to `required`.
5. **Failing-first integration tests** (CI-only on this box — write by reasoning, CI verifies): GET/POST return
   `rag`; a multi-reading objective spans green/amber/red; **the headline frozen-verdict proof** — a revised-target
   Effective objective grades an old reading against its `target_at_capture`, not the new governing target; the
   unknown-id branch stays `200 {"data": []}`.

**Acceptance:** ruff + format-check + mypy-strict clean; unit tests green natively; integration written & sound.

---

### Task 2 — Frontend: chart component + type (independent of Task 1)

**Files:** `apps/web/src/lib/types.ts`, `apps/web/src/features/objectives/ObjectiveTrendChart.tsx` (new),
`apps/web/src/features/objectives/ObjectiveTrendChart.test.tsx` (new).

**TDD:**
1. Add `rag: ObjectiveRag` to `Measurement` (`types.ts:1199`).
2. **Failing-first** `ObjectiveTrendChart.test.tsx` (`import { expect, it } from "vitest"`; fixtures pinned
   `satisfies Measurement`): N points oldest-left (the reverse), points filled by `rag`, target stepped line +
   value line present, single-reading state (1 point, no polyline, the caption), `role="img"` + a meaningful
   `aria-label`, a jest-axe smoke.
3. Implement `ObjectiveTrendChart` per spec Part 2 (hand-rolled SVG, `BandPreview` idiom; `RAG_FILL` CSS-var map;
   reverse → ascending; `Number()`-parse; no-forced-0 y-domain; native `<title>` per point; legend; optional
   `direction` caption). **The chart NEVER recomputes RAG — it reads `m.rag` verbatim (N9).**

**Acceptance:** `ObjectiveTrendChart.test.tsx` green; `tsc --noEmit` clean for the new files + the type.

---

### Task 3 — Frontend: integration (needs Task 2)

**Files:** `apps/web/src/features/objectives/MeasurementsSection.tsx` + `.test.tsx`,
`apps/web/src/features/objectives/ObjectiveDetailPage.tsx`, + the Measurement-fixture sweep
(`hooks.test.tsx`, `RecordMeasurementModal.test.tsx`, any other `Measurement` fixture → `+ rag`).

**TDD:**
1. Update `MeasurementsSection.test.tsx`: the old "Trend charts arrive in a later release" assertion is removed;
   assert the chart renders **above** the table when readings exist; the empty/forbidden/error/loading branches
   unchanged; "Readings are append-only." remains. Fixtures `+ rag` (`satisfies Measurement`).
2. Implement: render `<ObjectiveTrendChart measurements={data} unit={unit} direction={direction} />` above the
   table; drop the "Trend charts arrive…" clause; add the optional `direction?: ObjectiveDirection` prop.
3. `ObjectiveDetailPage.tsx:178` → `<MeasurementsSection objectiveId={o.id} unit={o.unit} direction={o.direction} />`.
4. Fixture sweep — `tsc` forces every `Measurement` fixture to add `rag`.

**Acceptance:** `MeasurementsSection.test.tsx` green; `tsc --noEmit` clean across the suite.

---

## Verification (main loop, on the assembled tree)

1. `/check-api` · `/check-contracts` · `/check-web` (full web suite via `--pool=forks --poolOptions.forks.singleFork=true`).
2. **diff-critic** (branch diff) + **web-test-trap-reviewer** (`apps/web` diff). No migration-reviewer.
3. **Live smoke** (owner login): `/objectives/:id` with ≥2 readings → chart with RAG points + target line;
   record a measurement → chart updates. `scripts/grant-overrides.py` KEYS = `objective.read`,`kpi.read`,`kpi.record`
   (revert before the PR).
4. Commit (trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`) → PR (`--body-file`)
   → green CI (9/9) → `@codex review` after CI (1–5 rounds) → squash-merge on owner OK → `/finish-slice` → a
   separate finish-slice docs PR.

## Traps carried
- `_measurement` new params are **required kwargs** → both call sites + every test caller pass them.
- `row is None` keeps `200 {"data": []}` — no new 404.
- Web: `import { expect, it } from "vitest"`; pin `Measurement` fixtures `satisfies`; full-suite via forks/singleFork.
- SVG colours = CSS vars (`var(--mantine-color-*-6)`), not Mantine name tokens.
- No FE RAG re-derivation (N9) — `m.rag` verbatim.

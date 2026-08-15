# Records Read Console Final Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three load-bearing residuals from the `421977c` scoped re-review so PROCESS packs, pending search cancellation, and authorized source-label display agree with the shipped Records read contract.

**Architecture:** Preserve the canonical Records authorization rule: only a source-less root correction inherits, but its ancestor walk may pass through process-unbound source-backed intermediates until the first non-empty process tuple. In the SPA, make the current input value a guard on debounced writes and retain only a source label already returned by the row-filtered Documents endpoint for the current mounted selection. Correct current evidence prose only after the executable regressions pass.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async/PostgreSQL, pytest, React 19, TypeScript, Mantine, React Router, Vitest/Testing Library, Playwright Chromium.

## Global Constraints

- Start from reviewed branch commit `421977c7c45f3d895fe3799106491c19443fa88e` in the existing isolated worktree and preserve the primary checkout's untracked `.superdesign/` directory.
- Touch only the PROCESS pack candidate walk, its focused integration proof, Records filter state/tests, and the current status/history prose that overclaims those behaviors.
- Preserve deny-by-default, deny-always-wins, tenant isolation, calm hidden-row behavior, source-less-root correction semantics, WORM/source-store boundaries, and every authorization tuple fixed in `421977c`.
- A process-unbound source-backed correction is not itself allowed to inherit a predecessor process and is not selected solely by that predecessor. It may be traversed as an empty-tuple ancestor so a later source-less correction resolves exactly as `record_process_ids_effective` and `record_process_ids_effective_for` resolve it.
- Any non-empty own process tuple stops correction inheritance, whether it came from an evidence-for PROCESS link, a source `ProcessLink`, or `QualityObjective.process_id`.
- URL state is authoritative. Clearing or changing another criterion cancels a not-yet-settled local search, and a stale debounce callback may never restore it.
- A source-document label may be retained only after that exact id/label pair was returned by the row-filtered Documents endpoint in the current mounted view. An unseen or externally substituted id remains `Selected item unavailable`; never fetch a raw UUID or fabricate a label.
- No migration, OpenAPI change, generated-contract change, permission key, role default, dependency, endpoint, write capability, deployment action, push, PR, or merge.
- Existing `docs/current-status.md` numeric frontmatter and `baseline_commit` remain unchanged because no complete umbrella suite is part of this remediation.
- Required browser evidence remains Chromium-only, one worker, zero retries; it does not claim Firefox/WebKit, actual assistive technology, live-stack acceptance, deployment, or Fedora proof.
- Read `docs/debt/20260813144730-responsive-register-cohort.md`, `docs/debt/20260813234519-playwright-responsive-browser-harness.md`, and `docs/debt/20260813074918-native-row-control-pattern.md` before touching their broad web hotspots. Do not claim those debts paid.
- Use strict RED/GREEN TDD. Run the smallest focused test first, then affected gates. Register any new deliberate compromise immediately through `debt-ops:add`; do not register defects that this plan fixes.

---

### Task 1: Close the scoped re-review residuals

**Files:**

- Modify: `apps/api/src/easysynq_api/services/packs/repository.py`
- Modify: `apps/api/tests/integration/test_records_process_scope.py`
- Modify: `apps/web/src/features/records/RecordFilters.tsx`
- Modify: `apps/web/src/features/records/RecordsPage.test.tsx`
- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`

**Interfaces:**

- Consumes: `records_repo.record_process_ids_effective` / `record_process_ids_effective_for` semantics, PROCESS pack scope resolution, `RecordFilters`' `value` / `onChange` / `onClear` props, and the row-filtered `useRecordSourceDocuments(q, enabled)` response.
- Produces: PROCESS candidate discovery that can traverse an unbound source-backed bridge without selecting it, guarded debounced search writes, a mount-local authorized selected-source projection, and truthful current evidence prose.

- [ ] **Step 1: Add a failing end-to-end authorization parity test**

Extend `apps/api/tests/integration/test_records_process_scope.py` with one chain:

```text
process-bound original
  -> process-unbound source-backed correction (must stay hidden)
  -> source-less correction (must inherit through the empty bridge)
```

The PROCESS-scoped reader must observe these literal outcomes:

```python
assert original_id in listed_ids
assert source_backed_id not in listed_ids
assert source_less_id in listed_ids
assert detail_source_less.status_code == 200
assert preview_statuses.get(original_id) == "INCLUDED"
assert source_backed_id not in preview_statuses
assert preview_statuses.get(source_less_id) == "INCLUDED"
assert sealed_statuses.get(source_less_id) == "INCLUDED"
```

Use real API capture/correction/pack flows and the existing cleanup helpers. Do not test a mocked repository.

- [ ] **Step 2: Run the backend RED**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/integration/test_records_process_scope.py -k 'source_backed_bridge' -q
```

Expected: FAIL because the source-less tail is list/detail-readable but absent from PROCESS pack preview/build.

- [ ] **Step 3: Make PROCESS candidate traversal match the canonical ancestor walk**

In `_process_candidate_ids`, retain the direct selected-process base. Replace the source-less-only successor traversal with separate visited, traversal-frontier, and included-candidate sets:

```python
selected = set(leg_a) | set(leg_b)
visited = set(selected)
frontier = set(selected)

while frontier:
    successor_rows = list(
        (
            await session.scalars(
                select(Record).where(
                    Record.org_id == org_id,
                    Record.correction_of.in_(frontier),
                )
            )
        ).all()
    )
    fresh_rows = [row for row in successor_rows if row.id not in visited]
    visited.update(row.id for row in fresh_rows)
    fresh_ids = {row.id for row in fresh_rows}
    own_bound_ids = set(
        (
            await session.scalars(
                select(Record.id).where(
                    Record.id.in_(fresh_ids),
                    or_(
                        Record.id.in_(
                            select(EvidenceForLink.record_id).where(
                                EvidenceForLink.org_id == org_id,
                                EvidenceForLink.target_type
                                == EvidenceForTargetType.PROCESS,
                            )
                        ),
                        Record.source_document_id.in_(
                            select(ProcessLink.documented_information_id).where(
                                ProcessLink.org_id == org_id,
                            )
                        ),
                        Record.source_document_id.in_(
                            select(QualityObjective.id).where(
                                QualityObjective.org_id == org_id,
                                QualityObjective.process_id.is_not(None),
                            )
                        ),
                    ),
                )
            )
        ).all()
    )
    inheriting_rows = [row for row in fresh_rows if row.id not in own_bound_ids]
    selected.update(row.id for row in inheriting_rows if row.source_document_id is None)
    frontier = {row.id for row in inheriting_rows}
```

The own-binding check must consider any process, not only the selected process, because the first non-empty tuple terminates canonical inheritance. Keep every query tenant-constrained and cycle-safe. A source-backed empty-tuple row is traversal-only; a source-less empty-tuple row is traversal plus selection.

- [ ] **Step 4: Run the backend GREEN and neighboring parity tests**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/integration/test_records_process_scope.py -k 'source_backed_bridge or source_backed_correction or objective_source_process_binding or correction_chain' -q
cd apps/api && .venv/bin/pytest tests/integration/test_packs.py -k 'process_pack or lifecycle_predicate' -q
```

Expected: PASS, including the new bridge chain and the prior source-backed-hidden, source-less-inherited, Quality Objective, and lifecycle-DENY cases.

- [ ] **Step 5: Add failing real-component tests for both frontend residuals**

In `RecordsPage.test.tsx`, add these behaviors through the mounted page and real Mantine controls:

1. Start at `/records?record_type=EVIDENCE`, type `pending-search`, click `Clear all` before 150 ms, wait longer than 150 ms, then assert the search input is empty and the location contains neither `record_type` nor `q`.
2. Make a searched Documents response return `SOP-PUR-014 — Supplier Selection & Evaluation`, select it, then let the blank first-page response omit that id. Assert the closed selected control retains the authorized full label, remains free of `Selected item unavailable`, and no request uses the display label as `q`.
3. Preserve the existing deep-link test proving an id never observed in a row-filtered response remains neutral and never triggers a raw-UUID fetch.

- [ ] **Step 6: Run the frontend RED**

Run:

```bash
cd apps/web && npm test -- src/features/records/RecordsPage.test.tsx -t 'cancels a pending search|retains an authorized selected source label' --reporter=dot
```

Expected: two failures: stale `q` returns after Clear all, and the selected source display degrades after the blank result page replaces the searched options.

- [ ] **Step 7: Guard debounce writes and retain the selected authorized projection**

In `RecordFilters.tsx`:

- keep a ref of the current immediate `search` value and permit a settled write only when `settledSearch === searchRef.current`;
- when applying another criterion or Clear all, synchronously adopt the next URL-owned `q` into local search before calling the parent so the pending value is invalidated;
- retain one selected `{value, label}` projection obtained from current row-filtered options;
- when the current selected id is absent from a later result page, reuse that projection only if its id still matches; otherwise use `Selected item unavailable`;
- clear or replace the retained projection when selection is cleared or an external id replaces it.

Do not persist labels outside the mounted component, query a raw UUID, or let a selected display string become a server query.

- [ ] **Step 8: Run the frontend GREEN and neighboring Records tests**

Run:

```bash
cd apps/web && npm test -- src/features/records/RecordsPage.test.tsx src/features/records/RecordDownloadButton.test.tsx --reporter=dot
```

Expected: PASS with both new regressions plus the prior Back/Forward, blank picker, neutral deep-link, cursor recovery, accessibility, and 320-pixel semantics tests.

- [ ] **Step 9: Correct the derived authority overclaim**

After Steps 4 and 8 are green, revise only the 2026-08-15 final-review reconciliation prose in `docs/current-status.md` and `docs/slice-history.md` so it truthfully says the separate remediation:

- cancels a pending local search when another criterion/Clear all makes URL state authoritative;
- retains a selected label already returned by the safe row-filtered Documents endpoint while keeping unseen ids neutral; and
- restores PROCESS pack parity through an unbound source-backed correction bridge.

Do not change numeric frontmatter, `baseline_commit`, complete-suite counts, migration snapshots, or historical pre-remediation test results.

- [ ] **Step 10: Run affected verification from narrow to broad**

Run:

```bash
cd apps/api && .venv/bin/pytest tests/integration/test_records_process_scope.py tests/integration/test_packs.py -q
cd apps/api && .venv/bin/ruff format --check .
cd apps/api && .venv/bin/ruff check .
cd apps/api && .venv/bin/mypy src
cd apps/web && npm test -- src/features/records src/App.test.tsx src/app/shell/LeftRail.test.tsx src/app/shell/Breadcrumb.test.tsx --reporter=dot
cd apps/web && npm run lint
cd apps/web && npm run build
cd apps/web && npm run test:browser -- --project=chromium --workers=1 --retries=0
just authority-check
bash scripts/check-no-site-data.sh
git diff --check 421977c7c45f3d895fe3799106491c19443fa88e..HEAD
```

Expected: all affected gates PASS. Record exact fresh counts and known warnings. Do not convert absent full umbrella-suite evidence into a pass claim.

- [ ] **Step 11: Self-review and commit**

Inspect the full remediation diff for tenant constraints, process-tuple stop conditions, stale state writes, unauthorized label retention, unrelated formatting, site data, and documentation truth. Then run:

```bash
git add apps/api/src/easysynq_api/services/packs/repository.py apps/api/tests/integration/test_records_process_scope.py apps/web/src/features/records/RecordFilters.tsx apps/web/src/features/records/RecordsPage.test.tsx docs/current-status.md docs/slice-history.md docs/superpowers/plans/2026-08-15-s-records-read-console-remediation.md
git commit -m "fix: close records review residuals"
git status --short
```

Expected: one reviewable remediation commit and a clean tracked worktree. Do not push, open a PR, or merge.

- [ ] **Step 12: Independent scoped review**

Generate one review package from `421977c7c45f3d895fe3799106491c19443fa88e` to the remediation HEAD. The reviewer must verdict the three load-bearing residuals and inspect the remediation diff for new Critical/Important breakage. Any new load-bearing finding blocks integration; do not silently defer it.

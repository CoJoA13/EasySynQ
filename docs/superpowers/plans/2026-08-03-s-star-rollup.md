# S-star-rollup — subtree-aware ★ coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ★ mandatory clause whose documents map only to DESCENDANT clauses (9.2 covered via
9.2.2) must stop reading GAP on the compliance checklist — coverage becomes subtree-aware,
consistent with S-clause-rollup's filter semantics and the owner's 2026-08-03 decision ("fix it so
it doesn't read as a gap").

**Architecture:** ONE computation — `services/reports/checklist.py::compute_checklist` — feeds the
live endpoint, the Home/Compliance SPA reads, the MR compile input, and the import pre-commit
projection. The grouped query joins `ClauseMapping` on `clause_id == Clause.id` (exact); it gains a
`member` Clause alias (`member.id == star.id OR member.number LIKE star.number || '.%'`, same
framework — catalog-trusted numbers, no user input → no escape concern) with mappings joined on
`member.id`. `count(DISTINCT documented_information.id)` already dedupes a doc mapped to several
subtree members. The projected-membership test rolls up the same way (`review.py` passes
UNFILTERED keep-item clause codes, so a projected 9.2.2 mapping must project-cover ★ 9.2). Statuses
stay COVERED/PARTIAL/GAP with no "covered-via" badge (calm principle; considered and dropped —
counts still tell the story). ⚠ Reverse edge: ONLY descendants satisfy an ancestor ★ — a ★ leaf
(7.1.5.1) must never inherit from siblings (7.1.5.2) or its parent.

**Design check (done):** R30 locks only the ★ SET; exactness was the module docstring's own
"discrete-item intuition," not register text → a new **R63** entry records the owner's decision
(range sweep R62→R63, same 10 live refs pattern; historical bump notes untouched).

## Global Constraints

- Branch `feat/s-star-rollup` from main `958f00d`; the pending S-clause-rollup finish-slice doc
  edits (CLAUDE.md + docs/slice-history.md) ride as the FIRST commit.
- NO migration (head stays `0084`); NO new permission key (catalog 102); response SHAPE unchanged
  (values change ⇒ contract prose only → runs the `contracts` job).
- Shared-DB integration discipline: run-scoped assertions only.

---

### Task 1: Branch + the riding finish-slice records

- [ ] `git checkout -b feat/s-star-rollup`; commit the pending CLAUDE.md + docs/slice-history.md
  edits as `docs: record S-clause-rollup (finish-slice) — slice-history narrative + learnings`.

### Task 2: R63 register entry + range sweep

**Files:** `docs/decisions-register.md` (intro, Part 3 heading, new entry after R62),
`CLAUDE.md` ×2, `docs/00-overview.md` ×4, `docs/18-mvp-implementation-plan.md` ×2.

- [ ] R63 entry (R60-62 house format): ★ coverage is subtree-inclusive — a `clause_mapping` to a ★
  clause OR any of its descendants counts toward that ★ clause's Mapped/Effective coverage;
  descendants only (never siblings/ancestors); statuses and response shape unchanged; the import
  pre-commit projection applies the same membership; rationale = the S-clause-rollup filter made
  the checklist's exact-★ counts contradict their own click-through, and a descendant mapping IS
  the obligation being addressed at its most specific requirement node. Back-propagation: 13 §5.1
  bullet, OpenAPI description, `services/reports/checklist.py` docstring. Bump line
  `R1–R62 → R1–R63`.
- [ ] Sweep the 10 live `R1–R62` refs → `R1–R63`; verify with grep (only historical bump lines
  remain).

### Task 3: The computation (TDD)

**Files:** `apps/api/src/easysynq_api/services/reports/checklist.py`,
`apps/api/tests/integration/test_reports.py`.

- [ ] **Integration tests first (RED).** In `test_reports.py`, following the file's existing
  `test_checklist_gap_to_partial_to_covered` style:

```python
async def test_checklist_star_coverage_rolls_up_descendants(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """R63: a doc mapped ONLY to a descendant of a ★ clause moves that ★ row off GAP, and an
    Effective descendant doc makes it COVERED."""
    # create a doc mapped to 9.2.2 (non-★ child of ★ 9.2): 9.2 row → PARTIAL (delta-based:
    # capture the 9.2 row before, assert mapped_count grew and status != "GAP")
    # drive it Effective: 9.2 row → COVERED with effective_count grown


async def test_checklist_star_leaf_never_inherits_from_siblings_or_parent(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """R63 reverse edge: mapping a doc to 7.1.5 (parent) or 7.1.5.2 must not change ★ 7.1.5.1's
    counts (descendants only — capture-before/assert-unchanged)."""
```

  (Exact bodies written against the file's real helpers at implementation time — the file already
  drives docs to Effective and reads the checklist.)

- [ ] **Implement:** `star = Clause` (the selected row), `member = aliased(Clause)`; join chain
  `star LEFT JOIN member ON member.framework_id == star.framework_id AND (member.id == star.id OR
  member.number.like(star.number + '.%')) LEFT JOIN ClauseMapping ON clause_id == member.id AND
  org … LEFT JOIN DocumentedInformation …`; the projection membership becomes
  `any(p == number or p.startswith(number + ".") for p in projected)`. Update the module docstring
  (drop "no subtree rollup — the discrete-item intuition"; state R63 + descendants-only).
- [ ] GREEN on the whole `test_reports.py` + the ingestion review-checklist projection tests +
  `test_mgmt_review.py` (the MR star_coverage consumer) via `sg docker`.

### Task 4: Contract + docs prose

- [ ] openapi `/reports/compliance-checklist` description: coverage counts mappings to the ★
  clause **or any descendant** (R63; subtree-aware, matching the clause filter). redocly green.
- [ ] `docs/13` §5.1 checklist bullet: add the subtree sentence. `docs/02 §2.1` intro: one
  sentence if it claims exact counting (verify first — don't invent an edit).

### Task 5: Gates, reviewers, PR

- [ ] `/check-api` full · `/check-web` full (no web code change expected — MSW checklist fixtures
  are shape-pinned; verify no web test asserts exact-coverage VALUES that changed) ·
  `/check-contracts` + site-data script. NO migration → no `/check-migrations`.
- [ ] diff-critic on the branch diff (hunt: other checklist consumers assuming exact counts — MR
  compile numbers, import projection, Home card RAG thresholds; the LIKE on catalog numbers;
  double-count risk through multi-member mappings). web-test-trap only if apps/web changes.
- [ ] `/pr` → green CI → Codex round(s) → owner-established merge sequence.

# S-clause-rollup — `?clause=` subtree-rollup filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `filter[clause_refs][has]=8` matches documents mapped to clause `8` **or any descendant**
(`8.x`, `8.x.y`, …) so top-level and mid-level clause filters stop returning zero documents.

**Architecture:** ONE chokepoint — `_filter_condition("clause_refs","has",…)` in
`apps/api/src/easysynq_api/api/documents.py:610-625` — serves BOTH published surfaces
(`GET /documents` directly and `GET /reports/document-control` via
`parse_document_filters_for_snapshot_with_applied`). The rollup is
`or_(Clause.number == value, Clause.number.startswith(value + ".", autoescape=True))`:
the `.`-anchored prefix implements the handoff's ⚠ (`'8.%'`, never `'8%'` — clause `1` would match
`10`), and `autoescape=True` keeps user-supplied `%`/`_` literal. Per-row authz filtering is
untouched (rollup is just a wider WHERE). NOT in scope: per-clause counts (deferred,
authz-sensitive aggregation).

**Owner-approved source:** docs/superpowers/plans/2026-08-02-product-handoff.md §2C. Runs the
`contracts` CI job (openapi prose changes).

## Global Constraints

- Branch `feat/s-clause-rollup` from main `9bef384`; the S-clause7-ia finish-slice doc updates
  (CLAUDE.md + docs/slice-history.md, already edited) ride as the FIRST commit.
- NO migration (head stays `0084`); NO new permission key (catalog 102).
- The openapi unit test pins `"logical AND"` and the repeat-example in the param description —
  keep both phrases in the rewritten prose.
- Shared-DB integration discipline: run-scoped assertions (assert OUR ids in/absent, never
  absolute counts); a post-rollup membership loop must accept descendant refs.

---

### Task 1: Commit the S-clause7-ia finish-slice records

- [ ] **Step 1:** `git checkout -b feat/s-clause-rollup`, commit the pending CLAUDE.md +
  docs/slice-history.md edits as `docs: record S-clause7-ia (finish-slice) — slice-history narrative + learnings + head 0084`.

### Task 2: API rollup (TDD) + contract/docs prose

**Files:** Modify `apps/api/src/easysynq_api/api/documents.py:610-625`,
`apps/api/tests/integration/test_documents_list.py`, `apps/api/tests/unit/test_document_filters.py`,
`packages/contracts/openapi.yaml` (~line 956), `docs/15-api-design.md:121,125-130`.

- [ ] **Step 1: Integration tests (RED first).** In `test_documents_list.py`, adjust
  `test_filter_clause_refs_has`'s final loop to subtree-aware membership
  (`any(ref == "8.4" or ref.startswith("8.4.") for ref in d["clause_refs"])`), and add:

```python
async def test_filter_clause_refs_subtree_rollup(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """R62 PR 2: filter[clause_refs][has]=N matches N AND its descendants at every level."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    deep = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, deep, await _clause_by_number("7.1.5.1"))
    sibling = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, sibling, await _clause_by_number("7.2"))

    for parent, hit, miss in (("7", deep, None), ("7.1", deep, sibling), ("7.1.5", deep, sibling)):
        r = await app_client.get(
            f"/api/v1/documents?limit=100&filter[clause_refs][has]={parent}", headers=ha
        )
        assert r.status_code == 200, r.text
        ids = [d["id"] for d in r.json()["data"]]
        assert hit in ids, parent
        if miss is not None:
            assert miss not in ids, parent  # 7.2 is NOT under 7.1/7.1.5


async def test_filter_clause_refs_rollup_is_dot_anchored_and_literal(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The two injection edges: '1' must NOT match a 10.x mapping (the LIKE '1%' trap), and
    user-supplied LIKE metacharacters stay literal (autoescape)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    ten = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, ten, await _clause_by_number("10.2"))
    eight = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, eight, await _clause_by_number("8.4"))

    for value, absent in (("1", ten), ("8%", eight), ("_", eight), ("8._", eight)):
        r = await app_client.get(
            f"/api/v1/documents?limit=100&filter[clause_refs][has]={quote(value)}", headers=ha
        )
        assert r.status_code == 200, r.text
        assert absent not in [d["id"] for d in r.json()["data"]], value
```

  (`from urllib.parse import quote` at the top.) Run the file → the rollup test FAILS (exact match).

- [ ] **Step 2: Implement** in `_filter_condition`:

```python
    if field == "clause_refs":  # filter[clause_refs][has]=8 — clause-number subtree membership
        # Constrain to the document's OWN framework (clause.number is unique only per framework —
        # uq_clause_framework_id_number): multi-standard safety (D3), matching the clause-map write
        # guard + the checklist query. Today the map guard already keeps a doc's mappings
        # framework-consistent, so this is defense-in-depth against a future second seeded standard.
        # Subtree rollup (handoff §2C): N matches N or N.<anything> — the '.'-anchored prefix keeps
        # clause 1 from matching 10 (never a bare LIKE 'N%'), and autoescape keeps user-supplied
        # %/_ literal.
        return (
            select(1)
            .select_from(ClauseMapping)
            .join(Clause, ClauseMapping.clause_id == Clause.id)
            .where(
                ClauseMapping.documented_information_id == DocumentedInformation.id,
                Clause.framework_id == DocumentedInformation.framework_id,
                or_(
                    Clause.number == value,
                    Clause.number.startswith(value + ".", autoescape=True),
                ),
            )
            .exists()
        )
```

- [ ] **Step 3: Unit condition test** — extend `test_document_filters.py`'s
  `test_clause_refs_builds_a_condition` with a compiled-SQL pin of the dot-anchored escaped prefix:

```python
def test_clause_refs_rollup_pattern_is_dot_anchored() -> None:
    cond = _filter_condition("clause_refs", "has", "8")
    sql = str(cond.compile(compile_kwargs={"literal_binds": True}))
    assert "'8.%'" in sql  # subtree prefix, dot-anchored — never a bare '8%'
    assert "= '8'" in sql  # the exact arm survives
```

- [ ] **Step 4: Contract + docs prose.** openapi `filter[clause_refs][has]` description →
  subtree semantics, KEEPING "logical AND" + the repeat example:

```
Documents mapped to every supplied clause number OR any of its sub-clauses (logical AND across
repeated keys; each value rolls up its subtree — 8 matches 8, 8.4, 8.5.6). Repeat the query key
for each clause, for example filter[clause_refs][has]=8.4&filter[clause_refs][has]=7.5.3.
```

  docs/15 line 121 → `subtree membership (N or N.…)`; §125-130 prose → "ANDs every supplied
  subtree-membership condition" with the rollup sentence. Run the openapi unit test file.

- [ ] **Step 5:** Full file runs green (integration via `sg docker`) → commit.

### Task 3: Register report wiring proof

**Files:** `apps/api/tests/integration/test_report_document_control.py` (one test).

- [ ] **Step 1:** Add a rollup test mirroring the file's existing filter-test style: a doc mapped
  to a deep clause (reuse `7.1.5.1`), `GET /reports/document-control?filter[clause_refs][has]=7`
  includes it, and the provenance echo carries the raw key/value unchanged. Run → commit.

### Task 4: Web faithfulness — MSW rollup + ClauseTree comment + LibraryPage upgrade

**Files:** `apps/web/src/test/msw/handlers.ts:428-429`,
`apps/web/src/features/library/ClauseTree.tsx` (comment only),
`apps/web/src/features/library/LibraryPage.test.tsx`.

- [ ] **Step 1: MSW handler mirrors the real backend** (fixture-pinning rule):

```ts
  const clause = sp.get("filter[clause_refs][has]");
  if (clause)
    rows = rows.filter((d) =>
      (d.clause_refs ?? []).some((ref) => ref === clause || ref.startsWith(clause + ".")),
    );
```

- [ ] **Step 2: ClauseTree comment** — replace "the GET /documents clause filter is an EXACT
  number match (no subtree rollup), so the sub-clauses must stay pickable" with: the filter rolls
  up sub-clauses (S-clause-rollup), so a top-level pick returns its whole subtree; sub-clauses
  stay pickable to NARROW within it.

- [ ] **Step 3: LibraryPage test upgrade** — in the clause-filter test, after clicking
  `8 Operation` assert BOTH 8.x fixture docs remain (rollup — the top-level click now returns
  results; SOP-PRD-007 maps 8.5, SOP-PUR-014 maps 8.4 — verify against the fixture), then click
  `8.4 …` and keep the existing narrowing assertions. Run library tests → commit.

### Task 5: Gates, reviewers, PR

- [ ] **Step 1:** `/check-api` · `/check-web` · `/check-contracts` + `scripts/check-no-site-data.sh`
  (NO migration → no `/check-migrations`, no migration-reviewer).
- [ ] **Step 2:** diff-critic on the branch diff + web-test-trap-reviewer on the web diff (parallel).
- [ ] **Step 3:** `/pr` (clean squash-ready body, R61-clean) → green CI → Codex round(s) per
  protocol → report to the owner before merge.

# S-ing-4b — Ingestion Review UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the browser front-end that closes UJ-2 — the human-in-the-loop **Ingestion Review** surface over the complete S-ing import pipeline: runs landing → New-Import + scan-progress → review cockpit (triage table, multi-select bulk actions, R10 kind-confirm, merge/split, item detail) → pre-commit checklist → commit + resume + terminal summary.

**Architecture:** Front-end only — **no migration, no permission key, no `openapi.yaml` change**. One new feature module `apps/web/src/features/ingestion/` over the already-contracted `/admin/imports/*` surface, plus thin shell wiring (a gated LeftRail entry + two routes). Dependency direction `features/ingestion/* → app/shell → lib` (acyclic). The triage list uses **server `offset`/`limit` pagination** (no virtualization, no new dep): the DOM never holds more than one page (~100 rows). Reuses `useApi`, `usePermissions`, `DetailDrawer`, `TaskStateBadge`, `CoverageBadge`, and the Library `FacetBar`/`Pagination` idioms (by copying the pattern, not importing internals).

**Tech Stack:** React 18 + Mantine 7 + TanStack Query 5 + react-router 7; tests = vitest + @testing-library/react + @testing-library/user-event + MSW 2 + jest-axe. Spec: `docs/superpowers/specs/2026-06-08-web-track-s-ing-4b-ingestion-review-design.md`. **No new permission key; no migration; no contract change.**

**Conventions (read once, apply to every task):**
- Run one test file with `npm --prefix apps/web test -- <path-substring>`. Run the full local gate (lint + typecheck + build + test) before the PR — `tsc --noEmit` is strict (`noUncheckedIndexedAccess`), so **every array index + map lookup must degrade to a defined fallback** (the per-file vitest run won't catch index nits the full `typecheck` does).
- Every component test that renders UI also asserts `expect(await axe(container)).toHaveNoViolations()` (the release a11y gate).
- Commit after each green task with a `feat(s-ing-4b): …` subject.
- MSW: `onUnhandledRequest: "error"`, so a request with no handler **fails the test** — add the handler in Task 1 first, override per-test with `server.use(...)` for 403/empty/error cases.
- Any actor/owner comparison uses `useMe().id` (the `app_user.id`), **never** `user.profile.sub` (the S-web-5 diff-critic CRITICAL).
- Tolerate `run.status` strings beyond the enum (additive commit stages) — a missing case degrades calmly, never crashes.

---

## Contract constraints discovered (bind the whole plan)

1. **`GET /admin/imports/{id}/files` filters are exactly `disposition | kind | band | review_status | limit(≤200,d100) | offset`** — there is **no** clause/process/type server filter. So the facet bar exposes only the **confidence band** (segmented) + a **kind** select; the mockup's clause/process/type group-by chips are **deferred** (would need a new backend param — out of a FE-only slice). The list returns a bare `{run_id, files[]}` envelope with **no total/`has_more`** — derive `hasMore = files.length === limit`; the bucket total comes from `run.counts`.
2. **Several bodies are untyped (`object`/`additionalProperties: true`) in OpenAPI** — pinned here from the backend (`review.py`): the per-file folded `review` block, the `checklist`, `run.counts`, the decision log, and the decision/merge/split mutation results. We type the FE against these real shapes **without changing the contract**.
3. **Permission gating is prose-only in the contract** — wire `import.review` (reads + decision/merge/split), `import.execute` (create/cancel), `import.commit` (commit) from the route docstrings. The `demo` System Administrator holds all three. No `hidden_by_scope` for import (403 → calm no-access; 404 → foreign/missing run).
4. **Commit-enable predicate** = `checklist.ready && checklist.review.commit_ready >= 1`. Unconfirmed `kind` is **advisory** (the item is silently skipped at commit), **never** a hard block. The button reads **"Commit N confirmed"** (the `commit_ready` count).
5. **Merge/split are server-authoritative + structural** — submit the intent, then invalidate + refetch (files + clusters + families + checklist + run). **No optimistic client reshape.** Per-file `decision` rejects merge/split with 422.
6. **"Already in vault" tab** has no clean `/files` filter — ships as a documented v1 partial (count from `run.counts`; the tab body shows a calm explainer rather than faking a filter). Resolve the exact mapping in Task 7.

---

## File structure

**New — `apps/web/src/features/ingestion/`** (each `*.tsx`/`.ts` has a sibling `*.test.tsx`):
- `hooks.ts` — all read queries + write mutations (Tasks 3–4).
- `filters.ts` — queue/band/kind URL state + the queue→API-filter mapping + `buildFilesQuery` (Task 2).
- Presentational leaves: `ImportStatusBadge.tsx`, `KindCell.tsx`, `ConfidenceCell.tsx`, `IdentifierCell.tsx`, `TypeCell.tsx` (Task 5); `RunSummaryTiles.tsx`, `ImportPlanBanner.tsx` (Task 6).
- Triage surface: `QueueTabs.tsx`, `IngestionFacetBar.tsx` (Task 7); `TriageTable.tsx` (Task 8); `BulkActionBar.tsx` (Task 9); `ItemDetailDrawer.tsx` (Task 10); `MergeMenu.tsx` (Task 11).
- Gate + commit: `PreCommitChecklist.tsx` (Task 12); `CommitCard.tsx` (Task 13).
- Composition + lifecycle faces: `ReviewCockpit.tsx` (Task 14); `NewImportModal.tsx`, `ScanProgress.tsx`, `CommitProgress.tsx`, `RunTerminalSummary.tsx` (Task 15); `IngestionRunPage.tsx` (Task 16); `IngestionRunsPage.tsx` (Task 17).

**Modify:**
- `apps/web/src/lib/types.ts` — the `// ---- S-ing-4b` block (Task 1).
- `apps/web/src/test/msw/handlers.ts` — import fixtures + the filter-aware list handler + run/checklist/cluster/family/decision/mutation handlers (Task 1).
- `apps/web/src/App.tsx` — `ingestion` + `ingestion/:runId` routes (Task 18).
- `apps/web/src/app/shell/LeftRail.tsx` — the gated **Import** nav entry (Task 18).

**Docs (Task 19):** `docs/slice-history.md`, `CLAUDE.md`, `docs/15-api-design.md`.

---

## Phase 0 — Foundation (the locked contract every later task references)

### Task 1: Types + MSW fixtures & handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append the block below)
- Modify: `apps/web/src/test/msw/handlers.ts` (add fixtures + handlers)

- [ ] **Step 1: Append the response + request types**

Add to the end of `apps/web/src/lib/types.ts`:

```ts
// ---- S-ing-4b (Ingestion Review UI) — types for the /admin/imports/* surface ----------------
// Several response bodies are `object`/additionalProperties:true in openapi.yaml; the shapes below
// are pinned from the backend (apps/api/.../services/ingestion/review.py) and typed here WITHOUT a
// contract change. The UI must tolerate `status` strings beyond ImportRunStatus (additive stages).

export type ImportRunStatus =
  | "Created" | "Scanning" | "Scanned" | "Extracting" | "Classifying" | "Classified"
  | "Deduping" | "Proposing" | "Proposed" | "Reviewing"
  | "Committing" | "Completed" | "PartiallyCommitted" | "Failed" | "Cancelled";

export type ImportKind = "DOCUMENT" | "RECORD" | "UNKNOWN";
export type ConfirmedKind = "DOCUMENT" | "RECORD"; // R10: confirmable kind, never UNKNOWN
export type ImportConfidenceBand = "HIGH" | "MEDIUM" | "LOW" | "AMBIGUOUS";
export type ImportDisposition = "included" | "excluded" | "quarantine"; // scan_flags.disposition
export type ImportReviewStatus = "included" | "excluded" | "deferred" | "undecided"; // folded
export type ImportDecisionAction = "accept" | "correct" | "exclude" | "defer";

export interface ImportRun {
  id: string;
  status: ImportRunStatus | string; // tolerate additive stages
  source_root: string;
  profile: string | null;
  ocr_enabled: boolean;
  classifier_version: string | null;
  counts: Record<string, unknown> | null; // stage-namespaced; read via narrow accessors
  error: string | null;
  created_by: string;
  committed_by: string | null;
  report_record_id: string | null;
  created_at: string | null;
  scan_started_at: string | null;
  completed_at: string | null;
}

export interface ImportClassificationEvidence {
  dimension: string;
  candidate: string;
  signal_type: string;
  weight: number;
  explanation: string;
}

export interface ImportClassification {
  kind: ImportKind;
  kind_conf: number;
  type_code: string | null;
  type_conf: number;
  clause_numbers: string[];
  clause_conf: number;
  process_names: string[] | null;
  process_conf: number;
  pdca_phase: "PLAN" | "DO" | "CHECK" | "ACT" | null;
  band: ImportConfidenceBand;
  ambiguous: boolean;
  top2_margin: number;
  classifier_version: string;
  evidence?: ImportClassificationEvidence[]; // detail endpoint only
}

// The S-ing-4 folded effective state (EffectiveFileState.as_dict()). `kind === "UNCONFIRMED"` until
// a human confirms (R10); `commit_ready === (disposition === "included" && kind in DOCUMENT|RECORD)`.
export interface ImportFileReview {
  disposition: ImportReviewStatus;
  kind: ImportKind | "UNCONFIRMED";
  identifier: string | null;
  identifier_source: string | null;
  type_code: string | null;
  clause_numbers: string[];
  process_names: string[];
  owner: string | null;
  decided: boolean;
  last_action: ImportDecisionAction | null;
  commit_ready: boolean;
  identifier_collidable: boolean;
}

export interface ImportFileScanFlags {
  disposition: ImportDisposition;
  reason?: string | null;
  detail?: string | null;
}

export interface ImportFile {
  id: string;
  rel_path: string;
  filename: string;
  ext: string | null;
  size_bytes: number;
  mime_type: string | null;
  sha256: string | null;
  staged_blob_uri: string | null;
  scan_flags: ImportFileScanFlags;
  included_candidate: boolean;
  mtime: string | null;
  ctime: string | null;
  classification: ImportClassification | null;
  review: ImportFileReview | null;
}

export interface ImportFileList {
  run_id: string;
  files: ImportFile[];
}

export interface ImportDedupMembership {
  in_exact_cluster: boolean;
  in_near_cluster: boolean;
  is_canonical: boolean | null;
  redundant_of_file_id: string | null;
  in_version_family: boolean;
  is_effective: boolean | null;
  superseded_by_file_id: string | null;
}

export interface ImportProposalNode {
  proposed_identifier: string | null;
  identifier_source: string | null;
  target_ia_path: string | null;
  proposed_owner: string | null;
  owner_source: string | null;
  conflict_flags: Record<string, unknown>;
}

export interface ImportExtract {
  status: "extracted" | "ocr" | "empty" | "failed";
  full_text: string | null;
  text_truncated: boolean;
  header_block: string | null;
  language: string | null;
  ocr_used: boolean;
  ocr_confidence: number | null;
  char_count: number | null;
  page_count: number | null;
  error: string | null;
  extractor_version: string | null;
}

export interface ImportFileDetail extends ImportFile {
  run_id: string;
  extract: ImportExtract | null;
  dedup: ImportDedupMembership;
  proposal: ImportProposalNode | null;
}

export interface ImportDupeCluster {
  id: string;
  method: "exact" | "near";
  member_file_ids: string[];
  canonical_file_id: string;
  jaccard: number | null;
  evidence: Record<string, unknown>;
}
export interface ImportDupeClusterList {
  run_id: string;
  clusters: ImportDupeCluster[];
}

export interface ImportVersionFamily {
  id: string;
  family_key: string;
  base_name: string;
  doc_code: string | null;
  ordered_member_file_ids: string[];
  effective_file_id: string;
  reconstruct_revision_chain: boolean;
  evidence: Record<string, unknown>;
}
export interface ImportVersionFamilyList {
  run_id: string;
  families: ImportVersionFamily[];
}

// GET /admin/imports/{id}/checklist (review.py:983-994). `ready === blocking.length === 0`; advisory
// never affects ready. A blocker carries a `code` + code-specific members (kept loose).
export interface ImportChecklistBlocker {
  code: string;
  [k: string]: unknown;
}
export interface ImportChecklistReviewStats {
  keep_items: number;
  decided: number;
  accepted: number;
  corrected: number;
  excluded: number;
  deferred: number;
  undecided: number;
  kind_confirmed: number;
  commit_ready: number;
}
export interface ImportChecklist {
  run_id: string;
  status: string;
  ready: boolean;
  blocking: ImportChecklistBlocker[];
  advisory: {
    star_coverage?: { total?: number; satisfied?: number; [k: string]: unknown } | null;
    unknown_low?: number;
    kind_unconfirmed?: number;
  };
  review: ImportChecklistReviewStats;
}

export interface ImportDecision {
  id: string;
  action: string; // accept|correct|merge|split|exclude|defer
  file_id: string | null;
  cluster_id: string | null;
  target_kind: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  decided_by: string;
  decided_at: string;
}
export interface ImportDecisionLog {
  run_id: string;
  decisions: ImportDecision[];
}

// ---- request bodies ----
export interface ImportDecisionAfter {
  kind?: ConfirmedKind;
  type_code?: string;
  clause_numbers?: string[];
  process_names?: string[];
  identifier?: string;
  owner?: string;
}
export interface ImportFileDecisionRequest {
  action: ImportDecisionAction;
  after?: ImportDecisionAfter;
  reason?: string | null;
}
export interface ImportBulkSelector {
  kind?: string | null;
  band?: string | null;
  disposition?: string | null;
}
export interface ImportBulkDecisionRequest {
  action: ImportDecisionAction;
  file_ids?: string[] | null;
  selector?: ImportBulkSelector | null;
  after?: ImportDecisionAfter;
  reason?: string | null;
}
export interface ImportMergeRequest {
  file_ids: string[];
  effective_file_id?: string | null;
  reconstruct_revision_chain?: boolean | null;
  reason?: string | null;
}
export interface ImportSplitRequest {
  target_kind: "dupe_cluster" | "version_family";
  target_id: string;
  separate_file_ids: string[];
  reason?: string | null;
}
export interface ImportRunCreate {
  source_root: string;
  profile?: string | null;
  ocr_enabled?: boolean;
  classifier_version?: string | null;
}
// Decision/merge/split results are loosely typed — the UI invalidates + refetches rather than reading
// the body; keep a permissive shape so a handler can return e.g. {applied: 3} or the family/split row.
export type ImportMutationResult = Record<string, unknown>;
```

- [ ] **Step 2: Add fixtures to MSW**

In `apps/web/src/test/msw/handlers.ts`, add these exported fixtures just above `export const handlers = [`. (IDs use a stable `aaaa…`/`bbbb…` scheme so cross-file tests can reference them.)

```ts
// ---- S-ing-4b ingestion fixtures (a tiny Proposed run spanning the row states) ----
export const ingestionRunFixture = {
  id: "10000000-0000-0000-0000-000000000001",
  status: "Proposed",
  source_root: "/srv/import/legacy-qms-share",
  profile: null,
  ocr_enabled: true,
  classifier_version: "rules-heuristic v1.4",
  counts: {
    scan: { total_files: 6 },
    classify: { band: { HIGH: 2, MEDIUM: 1, LOW: 2, AMBIGUOUS: 0 } },
    review: { undecided: 4, kind_confirmed: 1, commit_ready: 1 },
    queues: { needs: 4, medium: 1, high: 2, quarantine: 1, vault: 0 },
  },
  error: null,
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  committed_by: null,
  report_record_id: null,
  created_at: "2026-06-08T10:00:00+00:00",
  scan_started_at: "2026-06-08T10:00:01+00:00",
  completed_at: null,
};

function ingFile(over: Record<string, unknown>) {
  return {
    id: "00000000-0000-0000-0000-000000000000",
    rel_path: "x.docx",
    filename: "x.docx",
    ext: "docx",
    size_bytes: 1024,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    sha256: "abc",
    staged_blob_uri: "s3://import-staging/abc",
    scan_flags: { disposition: "included" },
    included_candidate: true,
    mtime: null,
    ctime: null,
    classification: null,
    review: null,
    ...over,
  };
}

const HIGH_DOC = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a1",
  rel_path: "SOP-PUR-014 Purchasing.docx",
  filename: "SOP-PUR-014 Purchasing.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 92, type_code: "SOP", type_conf: 90,
    clause_numbers: ["8.4"], clause_conf: 88, process_names: ["Purchasing"], process_conf: 80,
    pdca_phase: "DO", band: "HIGH", ambiguous: false, top2_margin: 30, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "SOP-PUR-014",
    identifier_source: "preserved_doc_code", type_code: "SOP", clause_numbers: ["8.4"],
    process_names: ["Purchasing"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const MED_DOC = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a2",
  rel_path: "Final Inspection WI rev1.docx",
  filename: "Final Inspection WI rev1.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 73, type_code: "WI", type_conf: 70,
    clause_numbers: ["8.6"], clause_conf: 65, process_names: ["Production"], process_conf: 60,
    pdca_phase: "DO", band: "MEDIUM", ambiguous: false, top2_margin: 15, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "WI-PRD-022",
    identifier_source: "preserved_doc_code", type_code: "WI", clause_numbers: ["8.6"],
    process_names: ["Production"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const LOW_UNKNOWN = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a3",
  rel_path: "scan0421.pdf",
  filename: "scan0421.pdf",
  classification: {
    kind: "UNKNOWN", kind_conf: 22, type_code: null, type_conf: 0,
    clause_numbers: [], clause_conf: 0, process_names: null, process_conf: 0,
    pdca_phase: null, band: "LOW", ambiguous: false, top2_margin: 5, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: null, identifier_source: null,
    type_code: null, clause_numbers: [], process_names: [], owner: null, decided: false,
    last_action: null, commit_ready: false, identifier_collidable: false,
  },
});
const DUP_FILE = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a4",
  rel_path: "SOP-PUR v2 FINAL.docx",
  filename: "SOP-PUR v2 FINAL.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 90, type_code: "SOP", type_conf: 88,
    clause_numbers: ["8.4"], clause_conf: 85, process_names: ["Purchasing"], process_conf: 78,
    pdca_phase: "DO", band: "HIGH", ambiguous: false, top2_margin: 25, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "SOP-PUR-014",
    identifier_source: "preserved_doc_code", type_code: "SOP", clause_numbers: ["8.4"],
    process_names: ["Purchasing"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const QUARANTINE_FILE = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a5",
  rel_path: "broken.bin",
  filename: "broken.bin",
  sha256: null,
  staged_blob_uri: null,
  scan_flags: { disposition: "quarantine", reason: "sniff_failed", detail: "unrecognized content" },
  included_candidate: false,
});

export const ingestionFilesFixture = [HIGH_DOC, DUP_FILE, MED_DOC, LOW_UNKNOWN, QUARANTINE_FILE];

export const ingestionFileDetailFixture = {
  ...HIGH_DOC,
  run_id: ingestionRunFixture.id,
  extract: {
    status: "extracted", full_text: "Purchasing procedure…", text_truncated: false,
    header_block: "SOP-PUR-014", language: "en", ocr_used: false, ocr_confidence: null,
    char_count: 4200, page_count: 3, error: null, extractor_version: "tika-2",
  },
  dedup: {
    in_exact_cluster: false, in_near_cluster: true, is_canonical: true, redundant_of_file_id: null,
    in_version_family: true, is_effective: true, superseded_by_file_id: null,
  },
  proposal: {
    proposed_identifier: "SOP-PUR-014", identifier_source: "preserved_doc_code",
    target_ia_path: "DO/08-Operation", proposed_owner: null, owner_source: null,
    conflict_flags: { duplicate_identifier_within_import: ["f0000000-0000-0000-0000-0000000000a4"] },
  },
};

export const ingestionDupeClustersFixture = {
  run_id: ingestionRunFixture.id,
  clusters: [
    {
      id: "c0000000-0000-0000-0000-0000000000c1", method: "near",
      member_file_ids: [HIGH_DOC.id, DUP_FILE.id], canonical_file_id: HIGH_DOC.id,
      jaccard: 0.91, evidence: {},
    },
  ],
};

export const ingestionVersionFamiliesFixture = {
  run_id: ingestionRunFixture.id,
  families: [
    {
      id: "v0000000-0000-0000-0000-0000000000v1", family_key: "SOP-PUR-014",
      base_name: "SOP-PUR-014 Purchasing", doc_code: "SOP-PUR-014",
      ordered_member_file_ids: [HIGH_DOC.id, DUP_FILE.id], effective_file_id: HIGH_DOC.id,
      reconstruct_revision_chain: false, evidence: {},
    },
  ],
};

export const ingestionChecklistFixture = {
  run_id: ingestionRunFixture.id,
  status: "Proposed",
  ready: false,
  blocking: [
    { code: "duplicate_identifier_within_import", identifier: "SOP-PUR-014",
      file_ids: [HIGH_DOC.id, DUP_FILE.id] },
  ],
  advisory: {
    star_coverage: { total: 20, satisfied: 17 },
    unknown_low: 2,
    kind_unconfirmed: 4,
  },
  review: {
    keep_items: 4, decided: 0, accepted: 0, corrected: 0, excluded: 0, deferred: 0,
    undecided: 4, kind_confirmed: 1, commit_ready: 1,
  },
};

export const ingestionDecisionsFixture = { run_id: ingestionRunFixture.id, decisions: [] };
```

- [ ] **Step 3: Add the handlers (filter-aware list + the rest)**

Inside the `handlers` array in `apps/web/src/test/msw/handlers.ts` (near the other `http.get`s), add:

```ts
  // ---- S-ing-4b ingestion (default happy-path; per-test override for 403/empty/error) ----
  http.get("/api/v1/admin/imports", () => HttpResponse.json([ingestionRunFixture])),
  http.get("/api/v1/admin/imports/:id", () => HttpResponse.json(ingestionRunFixture)),
  http.get("/api/v1/admin/imports/:id/files", ({ request }) => {
    const url = new URL(request.url);
    const band = url.searchParams.get("band");
    const disposition = url.searchParams.get("disposition");
    const reviewStatus = url.searchParams.get("review_status");
    const kind = url.searchParams.get("kind");
    let files = ingestionFilesFixture;
    if (band) files = files.filter((f) => f.classification?.band === band);
    if (disposition) files = files.filter((f) => f.scan_flags.disposition === disposition);
    if (reviewStatus) files = files.filter((f) => f.review?.disposition === reviewStatus);
    if (kind) files = files.filter((f) => f.classification?.kind === kind);
    return HttpResponse.json({ run_id: ingestionRunFixture.id, files });
  }),
  http.get("/api/v1/admin/imports/:id/files/:fid", () =>
    HttpResponse.json(ingestionFileDetailFixture),
  ),
  http.get("/api/v1/admin/imports/:id/dupe-clusters", () =>
    HttpResponse.json(ingestionDupeClustersFixture),
  ),
  http.get("/api/v1/admin/imports/:id/version-families", () =>
    HttpResponse.json(ingestionVersionFamiliesFixture),
  ),
  http.get("/api/v1/admin/imports/:id/checklist", () =>
    HttpResponse.json(ingestionChecklistFixture),
  ),
  http.get("/api/v1/admin/imports/:id/decisions", () =>
    HttpResponse.json(ingestionDecisionsFixture),
  ),
  http.post("/api/v1/admin/imports", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Created" }, { status: 202 }),
  ),
  http.post("/api/v1/admin/imports/:id/files/:fid/decision", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/decisions", () => HttpResponse.json({ applied: 1 })),
  http.post("/api/v1/admin/imports/:id/merge", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/split", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/cancel", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Cancelled" }),
  ),
  http.post("/api/v1/admin/imports/:id/commit", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }, { status: 202 }),
  ),
```

- [ ] **Step 4: Verify typecheck + the whole suite still pass**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test`
Expected: PASS (types compile; the new handlers are additive + unused so far, so the existing suite is unaffected).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-ing-4b): import-review response/request types + MSW fixtures & handlers"
```

---

### Task 2: `filters.ts` — queue/band URL state + the queue→API-filter mapping

**Files:**
- Create: `apps/web/src/features/ingestion/filters.ts`
- Test: `apps/web/src/features/ingestion/filters.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/filters.test.ts`:

```ts
import { expect, test } from "vitest";
import {
  QUEUES,
  buildFilesQuery,
  parseRunUrl,
  queueToFilesQuery,
} from "./filters";

test("QUEUES lists the five tabs in mockup order", () => {
  expect(QUEUES.map((q) => q.value)).toEqual(["needs", "medium", "high", "quarantine", "vault"]);
});

test("queueToFilesQuery maps each queue to the server-supported /files filter", () => {
  expect(queueToFilesQuery("needs")).toEqual({ review_status: "undecided" });
  expect(queueToFilesQuery("medium")).toEqual({ band: "MEDIUM" });
  expect(queueToFilesQuery("high")).toEqual({ band: "HIGH" });
  expect(queueToFilesQuery("quarantine")).toEqual({ disposition: "quarantine" });
  // "vault" has no clean /files filter (v1 partial) → no server filter.
  expect(queueToFilesQuery("vault")).toEqual({});
});

test("a confidence override narrows the band within a queue", () => {
  expect(queueToFilesQuery("needs", "LOW")).toEqual({ review_status: "undecided", band: "LOW" });
});

test("parseRunUrl reads queue + confidence + offset with safe defaults", () => {
  const a = parseRunUrl(new URLSearchParams("queue=high&conf=MEDIUM&offset=200"));
  expect(a).toEqual({ queue: "high", conf: "MEDIUM", offset: 200 });
  const b = parseRunUrl(new URLSearchParams(""));
  expect(b).toEqual({ queue: "needs", conf: "ALL", offset: 0 });
  // a bogus queue/conf/offset degrades to the default
  const c = parseRunUrl(new URLSearchParams("queue=bogus&conf=bogus&offset=-3"));
  expect(c).toEqual({ queue: "needs", conf: "ALL", offset: 0 });
});

test("buildFilesQuery serializes the filter + pagination", () => {
  const qs = buildFilesQuery({ band: "HIGH", review_status: "undecided" }, { limit: 100, offset: 0 });
  const p = new URLSearchParams(qs);
  expect(p.get("band")).toBe("HIGH");
  expect(p.get("review_status")).toBe("undecided");
  expect(p.get("limit")).toBe("100");
  expect(p.get("offset")).toBe("0");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/filters.test.ts`
Expected: FAIL — `./filters` does not exist.

- [ ] **Step 3: Implement `filters.ts`**

Create `apps/web/src/features/ingestion/filters.ts`:

```ts
import type {
  ImportConfidenceBand,
  ImportDisposition,
  ImportKind,
  ImportReviewStatus,
} from "../../lib/types";

// The five queue tabs (mockup order). `countKey` indexes run.counts.queues for the tab badge.
export type IngestionQueue = "needs" | "medium" | "high" | "quarantine" | "vault";
export const QUEUES: { value: IngestionQueue; label: string; countKey: string }[] = [
  { value: "needs", label: "Needs decision", countKey: "needs" },
  { value: "medium", label: "Medium", countKey: "medium" },
  { value: "high", label: "High", countKey: "high" },
  { value: "quarantine", label: "Quarantine", countKey: "quarantine" },
  { value: "vault", label: "Already in vault", countKey: "vault" },
];

export type ConfidenceChoice = ImportConfidenceBand | "ALL";
const CONF_CHOICES: ConfidenceChoice[] = ["ALL", "HIGH", "MEDIUM", "LOW", "AMBIGUOUS"];
const QUEUE_VALUES = QUEUES.map((q) => q.value);

// The server-supported /files filter (the ONLY filterable dimensions; clause/process/type are not).
export interface FilesFilter {
  disposition?: ImportDisposition;
  kind?: ImportKind;
  band?: ImportConfidenceBand;
  review_status?: ImportReviewStatus;
}

export interface RunUrlState {
  queue: IngestionQueue;
  conf: ConfidenceChoice;
  offset: number;
}

export const FILES_PAGE_SIZE = 100;

export function parseRunUrl(p: URLSearchParams): RunUrlState {
  const q = p.get("queue");
  const queue = q && QUEUE_VALUES.includes(q as IngestionQueue) ? (q as IngestionQueue) : "needs";
  const c = p.get("conf");
  const conf = c && CONF_CHOICES.includes(c as ConfidenceChoice) ? (c as ConfidenceChoice) : "ALL";
  const n = Number(p.get("offset") ?? "0");
  const offset = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  return { queue, conf, offset };
}

// Map a queue (+ an optional confidence narrowing) to the server /files filter. "vault" has no clean
// filter in v1 (resolved as a documented partial in Task 7) → an empty filter.
export function queueToFilesQuery(queue: IngestionQueue, conf?: ConfidenceChoice): FilesFilter {
  const base: FilesFilter =
    queue === "needs"
      ? { review_status: "undecided" }
      : queue === "medium"
        ? { band: "MEDIUM" }
        : queue === "high"
          ? { band: "HIGH" }
          : queue === "quarantine"
            ? { disposition: "quarantine" }
            : {};
  if (conf && conf !== "ALL") return { ...base, band: conf };
  return base;
}

export function buildFilesQuery(filter: FilesFilter, page: { limit: number; offset: number }): string {
  const p = new URLSearchParams();
  p.set("limit", String(page.limit));
  p.set("offset", String(page.offset));
  if (filter.disposition) p.set("disposition", filter.disposition);
  if (filter.kind) p.set("kind", filter.kind);
  if (filter.band) p.set("band", filter.band);
  if (filter.review_status) p.set("review_status", filter.review_status);
  return p.toString();
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/filters.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/filters.ts apps/web/src/features/ingestion/filters.test.ts
git commit -m "feat(s-ing-4b): ingestion queue/band URL filters + queue→API mapping"
```

---

### Task 3: `hooks.ts` — read queries

**Files:**
- Create: `apps/web/src/features/ingestion/hooks.ts`
- Test: `apps/web/src/features/ingestion/hooks.read.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/hooks.read.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import {
  useChecklist,
  useDupeClusters,
  useImportFiles,
  useImportRun,
  useImportRuns,
  useVersionFamilies,
} from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}
const RID = ingestionRunFixture.id;

test("useImportRuns returns the run list", async () => {
  const { result } = renderHook(() => useImportRuns(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.[0]?.id).toBe(RID);
});

test("useImportRun returns one run", async () => {
  const { result } = renderHook(() => useImportRun(RID), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Proposed");
});

test("useImportFiles applies the queue→filter mapping (band=HIGH returns the 2 high rows)", async () => {
  const { result } = renderHook(() => useImportFiles(RID, { band: "HIGH" }, 0), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.files).toHaveLength(2);
});

test("useChecklist returns the gate shape", async () => {
  const { result } = renderHook(() => useChecklist(RID), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.ready).toBe(false);
  expect(result.current.data?.blocking).toHaveLength(1);
  expect(result.current.data?.review.commit_ready).toBe(1);
});

test("useDupeClusters + useVersionFamilies return their lists", async () => {
  const clusters = renderHook(() => useDupeClusters(RID), { wrapper });
  await waitFor(() => expect(clusters.result.current.isSuccess).toBe(true));
  expect(clusters.result.current.data?.clusters).toHaveLength(1);
  const families = renderHook(() => useVersionFamilies(RID), { wrapper });
  await waitFor(() => expect(families.result.current.isSuccess).toBe(true));
  expect(families.result.current.data?.families).toHaveLength(1);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/hooks.read.test.tsx`
Expected: FAIL — `./hooks` does not exist.

- [ ] **Step 3: Implement the read hooks**

Create `apps/web/src/features/ingestion/hooks.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  ImportChecklist,
  ImportDecisionLog,
  ImportDupeClusterList,
  ImportFileDetail,
  ImportFileList,
  ImportRun,
  ImportVersionFamilyList,
} from "../../lib/types";
import { FILES_PAGE_SIZE, buildFilesQuery, type FilesFilter } from "./filters";

// A run is "settling" (poll it) while the engine is scanning/classifying/etc OR committing; it RESTS
// at Proposed/Reviewing (human review) and at every terminal status.
const POLLING_STATUSES = new Set([
  "Created", "Scanning", "Scanned", "Extracting", "Classifying", "Classified",
  "Deduping", "Proposing", "Committing",
]);
export function isRunSettling(status: string | undefined): boolean {
  return status !== undefined && POLLING_STATUSES.has(status);
}

export function useImportRuns() {
  const api = useApi();
  return useQuery({
    queryKey: ["import-runs"],
    queryFn: () => api.get<ImportRun[]>("/api/v1/admin/imports"),
  });
}

export function useImportRun(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-run", runId],
    queryFn: () => api.get<ImportRun>(`/api/v1/admin/imports/${runId}`),
    enabled: runId !== null,
    // Poll while the engine is working (scan/commit); halt at a rest/terminal status.
    refetchInterval: (q) => (isRunSettling(q.state.data?.status) ? 2500 : false),
  });
}

export function useImportFiles(runId: string | null, filter: FilesFilter, offset: number) {
  const api = useApi();
  const qs = buildFilesQuery(filter, { limit: FILES_PAGE_SIZE, offset });
  return useQuery({
    queryKey: ["import-files", runId, filter, offset],
    queryFn: () => api.get<ImportFileList>(`/api/v1/admin/imports/${runId}/files?${qs}`),
    enabled: runId !== null,
  });
}

export function useImportFile(runId: string | null, fileId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-file", runId, fileId],
    queryFn: () => api.get<ImportFileDetail>(`/api/v1/admin/imports/${runId}/files/${fileId}`),
    enabled: runId !== null && fileId !== null,
  });
}

export function useDupeClusters(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-dupe-clusters", runId],
    queryFn: () => api.get<ImportDupeClusterList>(`/api/v1/admin/imports/${runId}/dupe-clusters`),
    enabled: runId !== null,
  });
}

export function useVersionFamilies(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-version-families", runId],
    queryFn: () =>
      api.get<ImportVersionFamilyList>(`/api/v1/admin/imports/${runId}/version-families`),
    enabled: runId !== null,
  });
}

export function useChecklist(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-checklist", runId],
    queryFn: () => api.get<ImportChecklist>(`/api/v1/admin/imports/${runId}/checklist`),
    enabled: runId !== null,
  });
}

export function useDecisions(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-decisions", runId],
    queryFn: () => api.get<ImportDecisionLog>(`/api/v1/admin/imports/${runId}/decisions`),
    enabled: runId !== null,
  });
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/hooks.read.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/hooks.ts apps/web/src/features/ingestion/hooks.read.test.tsx
git commit -m "feat(s-ing-4b): ingestion read hooks (runs/run/files/file/clusters/families/checklist/decisions)"
```

---

### Task 4: `hooks.ts` — write mutations (append to the same file)

**Files:**
- Modify: `apps/web/src/features/ingestion/hooks.ts` (append the mutations)
- Test: `apps/web/src/features/ingestion/hooks.write.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/hooks.write.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { useBulkDecision, useCommitRun, useCreateImportRun, useMerge } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}
const RID = ingestionRunFixture.id;

test("useBulkDecision sends the body + an Idempotency-Key header", async () => {
  let seenKey: string | null = null;
  let seenBody: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/decisions", async ({ request }) => {
      seenKey = request.headers.get("Idempotency-Key");
      seenBody = await request.json();
      return HttpResponse.json({ applied: 2 });
    }),
  );
  const { result } = renderHook(() => useBulkDecision(RID), { wrapper });
  result.current.mutate({
    body: { action: "accept", selector: { band: "HIGH" } },
    idempotencyKey: "key-1",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(seenKey).toBe("key-1");
  expect(seenBody).toEqual({ action: "accept", selector: { band: "HIGH" } });
});

test("useMerge posts file_ids + the effective member", async () => {
  let body: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  const { result } = renderHook(() => useMerge(RID), { wrapper });
  result.current.mutate({
    body: { file_ids: ["a", "b"], effective_file_id: "a", reconstruct_revision_chain: true },
    idempotencyKey: "m-1",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(body).toEqual({ file_ids: ["a", "b"], effective_file_id: "a", reconstruct_revision_chain: true });
});

test("useCreateImportRun returns the created run", async () => {
  const { result } = renderHook(() => useCreateImportRun(), { wrapper });
  result.current.mutate({ source_root: "/srv/import/x", ocr_enabled: true });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Created");
});

test("useCommitRun posts to the commit verb", async () => {
  const { result } = renderHook(() => useCommitRun(RID), { wrapper });
  result.current.mutate();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.status).toBe("Committing");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/hooks.write.test.tsx`
Expected: FAIL — the mutation hooks don't exist yet.

- [ ] **Step 3: Append the mutations to `hooks.ts`**

Append to `apps/web/src/features/ingestion/hooks.ts` (add `useMutation, useQueryClient` to the `@tanstack/react-query` import, and import the request body types):

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  ImportBulkDecisionRequest,
  ImportFileDecisionRequest,
  ImportMergeRequest,
  ImportMutationResult,
  ImportRun,
  ImportRunCreate,
  ImportSplitRequest,
} from "../../lib/types";

// Invalidate everything a write can move: the row list, the checklist gate, the run counts, and (for
// structural ops) the cluster/family lists. Merge/split are server-authoritative — never reshape the
// client cache optimistically (D-5); just refetch.
function useRunInvalidator(runId: string | null) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["import-files", runId] });
    void qc.invalidateQueries({ queryKey: ["import-file", runId] });
    void qc.invalidateQueries({ queryKey: ["import-checklist", runId] });
    void qc.invalidateQueries({ queryKey: ["import-run", runId] });
    void qc.invalidateQueries({ queryKey: ["import-decisions", runId] });
    void qc.invalidateQueries({ queryKey: ["import-dupe-clusters", runId] });
    void qc.invalidateQueries({ queryKey: ["import-version-families", runId] });
  };
}

export function useFileDecision(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({
      fileId,
      body,
      idempotencyKey,
    }: {
      fileId: string;
      body: ImportFileDecisionRequest;
      idempotencyKey: string;
    }) =>
      api.send<ImportMutationResult>(
        "POST",
        `/api/v1/admin/imports/${runId}/files/${fileId}/decision`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: invalidate,
  });
}

// ONE Idempotency-Key per bulk op (the S-ing-4 "stamp the key on a SINGLE row" rule).
export function useBulkDecision(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({
      body,
      idempotencyKey,
    }: {
      body: ImportBulkDecisionRequest;
      idempotencyKey: string;
    }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/decisions`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useMerge(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: ImportMergeRequest; idempotencyKey: string }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/merge`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useSplit(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: ImportSplitRequest; idempotencyKey: string }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/split`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useCreateImportRun() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ImportRunCreate) =>
      api.send<ImportRun>("POST", "/api/v1/admin/imports", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["import-runs"] }),
  });
}

export function useCancelRun(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: () => api.send<ImportRun>("POST", `/api/v1/admin/imports/${runId}/cancel`),
    onSuccess: invalidate,
  });
}

export function useCommitRun(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: () => api.send<ImportRun>("POST", `/api/v1/admin/imports/${runId}/commit`),
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/hooks.write.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/hooks.ts apps/web/src/features/ingestion/hooks.write.test.tsx
git commit -m "feat(s-ing-4b): ingestion write mutations (decision/bulk/merge/split/create/cancel/commit)"
```

---

## Component Contract Registry (LOCKED — every Phase 1–5 task binds to these signatures)

> A fresh implementer building any component below MUST use these exact prop names, hook signatures
> (Tasks 3–4), type field names (Task 1), filter API (Task 2), and queryKeys. Do not rename or
> re-shape. Selection state + the active-drawer id live in `ReviewCockpit` (Task 14) and flow down as
> props — child components are presentational and call mutations via the Task 3–4 hooks.

**Hooks (Tasks 3–4):** `useImportRuns()` · `useImportRun(runId)` (polls while settling) · `useImportFiles(runId, filter: FilesFilter, offset)` · `useImportFile(runId, fileId)` · `useDupeClusters(runId)` · `useVersionFamilies(runId)` · `useChecklist(runId)` · `useDecisions(runId)` · `useFileDecision(runId)` · `useBulkDecision(runId)` · `useMerge(runId)` · `useSplit(runId)` · `useCreateImportRun()` · `useCancelRun(runId)` · `useCommitRun(runId)`. Mutations take `{ body, idempotencyKey }` (file decision also `fileId`); generate the key with `crypto.randomUUID()` once per user action. `isRunSettling(status)` is exported from `hooks.ts`.

**Component props (LOCKED):**
- `ImportStatusBadge({ status: string })` — glyph+label+`aria-label="Run status: <label>"`; tolerant `?? fallback` for unknown statuses (TaskStateBadge template).
- `KindCell({ review: ImportFileReview | null, classification: ImportClassification | null, onConfirm: (kind: ConfirmedKind) => void, busy?: boolean })` — UNCONFIRMED → dimmed guess (`Document?`/`🔒 Record?`/`Unknown`) + a `Confirm` button (calls `onConfirm("DOCUMENT")` by default, with a small menu to pick Document/Record); confirmed → a solid badge. `aria-label`s distinct per row.
- `ConfidenceCell({ classification: ImportClassification | null })` — bars+label per band (HIGH/MEDIUM/LOW) or a chip; `⚖ ambiguous` caption when `classification.ambiguous`; `aria-label="Confidence: <band> <conf>%"`. Null → `—`.
- `IdentifierCell({ review: ImportFileReview | null, dupeOf: string | null })` — effective `review.identifier`, or `Duplicate of <dupeOf>` (danger) when `dupeOf` set, or `— suggest needed` (no id) / `— record (no code)` (RECORD, no id).
- `TypeCell({ classification: ImportClassification | null })` — `type_code`→label + an `alt`/`ambiguous` caption; null → `—`.
- `RunSummaryTiles({ run: ImportRun })` — 4 tiles read from `run.counts` (use a narrow `countAt(run.counts, "classify","band","HIGH")`-style safe accessor; missing → `0`/`—`).
- `ImportPlanBanner({})` — static drift-safe explainer (informational, D-6).
- `QueueTabs({ counts: Record<string, number>, value: IngestionQueue, onChange: (q: IngestionQueue) => void })` — Mantine `Tabs`, 5 tabs from `QUEUES`, count badge per tab (missing key → 0).
- `IngestionFacetBar({ conf: ConfidenceChoice, onConf: (c: ConfidenceChoice) => void })` — confidence `SegmentedControl` (All/High/Medium/Low). (Kind filter optional; clause/process/type facets are deferred — not server-filterable.)
- `TriageTable({ files: ImportFile[], dupeMap: Map<string,string>, familyMap: Map<string,number>, loading: boolean, selected: Set<string>, onToggle: (id: string) => void, onToggleAllOnPage: () => void, allOnPageSelected: boolean, onConfirmKind: (fileId: string, kind: ConfirmedKind) => void, onOpenDetail: (fileId: string) => void, onRowAction: (file: ImportFile, action: ImportDecisionAction) => void })` — the 9-column paged table; `dupeMap` (fileId→canonical identifier) + `familyMap` (fileId→member count) come from the cluster/family join done in `ReviewCockpit`. Quarantine rows render simplified. Empty → `es-empty`. loading → skeleton.
- `BulkActionBar({ count: number, onBulk: (action: ImportDecisionAction, after?: ImportDecisionAfter) => void, onConfirmKind: (kind: ConfirmedKind) => void, onAcceptAllHigh: () => void })` — appears when `count > 0`; the 5 bulk actions + the separate "Bulk accept all High" (selector-based, does NOT confirm kind).
- `TriagePagination({ offset: number, hasMore: boolean, onOffset: (o: number) => void, total?: number, pageCount?: number })` — offset pager at `FILES_PAGE_SIZE`; "Showing X–Y of N" (N from run.counts `total`, X/Y from `offset`+`pageCount`=rows on this page) shown when both `total` and `pageCount` are set. (Mirror the Library `Pagination` shape but with a fixed page size.)
- `ItemDetailDrawer({ runId: string, fileId: string | null, onClose: () => void, onConfirmKind, onDecision, onSplit })` — reuses `app/shell/DetailDrawer`; `useImportFile(runId, fileId)` (enabled when `fileId` set); shows classification evidence, extract, dedup members, proposal/conflicts, decision history; per-item actions.
- `MergeMenu({ runId: string, selectedFileIds: string[], onDone: () => void })` — pick effective member + reconstruct opt-in → `useMerge`. (Inline row `Merge ▾` reuses this for the dup-of case.)
- `PreCommitChecklist({ checklist: ImportChecklist, onShowBlocker: (blocker: ImportChecklistBlocker) => void })` — blocking RAG rows (danger) + advisory rows (CoverageBadge for ★) + review stats. Advisory never reads as a blocker.
- `CommitCard({ checklist: ImportChecklist, canCommit: boolean, committing: boolean, onCommit: () => void })` — ready count + provenance dl + dynamic "Commit N confirmed" button; **enabled iff `checklist.ready && checklist.review.commit_ready >= 1 && canCommit`** (`canCommit` = `can("import.commit")`); when `!canCommit`, a calm "held by another role" note instead.
- `ReviewCockpit({ runId: string, run: ImportRun })` — owns `selected: Set<string>` + the active drawer id + URL state (queue/conf/offset); composes everything; does the cluster/family join into `dupeMap`/`familyMap`.
- `NewImportModal({ opened: boolean, onClose: () => void, onCreated: (runId: string) => void })` — source_root + ocr toggle + profile → `useCreateImportRun` → `onCreated(run.id)`.
- `ScanProgress({ run: ImportRun, onCancel: () => void })` — stepper of pipeline stages + counts + Cancel.
- `CommitProgress({ run: ImportRun })` — progress + `counts.commit`.
- `RunTerminalSummary({ run: ImportRun, onResume?: () => void })` — committed/failed counts + report link + (PartiallyCommitted) resume.
- `IngestionRunPage()` / `IngestionRunsPage()` — route components (read `useParams`/`useImportRun(s)`).

**Calm-state copy bank (reuse verbatim):** 403 → "You don't have access to import review." · 404 → "Import run not found." · empty queue → "Nothing in this queue." · vault tab → "Files already controlled in the vault are skipped on commit; per-file listing isn't available in this view yet." · commit held by another role → "Commit is held by another role (import.commit)."


---

### Task 5: ImportStatusBadge + KindCell

**Files:**
- Create: `apps/web/src/features/ingestion/ImportStatusBadge.tsx`
- Create: `apps/web/src/features/ingestion/KindCell.tsx`
- Test: `apps/web/src/features/ingestion/ImportStatusBadge.test.tsx`
- Test: `apps/web/src/features/ingestion/KindCell.test.tsx`

Two presentational leaves in one task. `ImportStatusBadge` mirrors `TaskStateBadge` exactly (glyph + label + tolerant `?? fallback` for additive commit-stage statuses). `KindCell` renders the R10 kind state: the engine guess **dimmed with a trailing `?`** when `review.kind === "UNCONFIRMED"` (derived from `classification.kind` → `Document?` / `🔒 Record?` / `Unknown`) plus a `Confirm ▾` Mantine `Menu` offering Document / Record (`onConfirm("DOCUMENT" | "RECORD")`), or a solid `Badge` when the kind is already confirmed. Both are pure props-in components — no hooks, no state.

- [ ] **Step 1: Write the failing test for `ImportStatusBadge`**

Create `apps/web/src/features/ingestion/ImportStatusBadge.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { MantineProvider } from "@mantine/core";
import { ImportStatusBadge } from "./ImportStatusBadge";

function renderBadge(status: string) {
  return render(
    <MantineProvider>
      <ImportStatusBadge status={status} />
    </MantineProvider>,
  );
}

test("renders the label + an aria-label for a known status", () => {
  const { getByText, getByLabelText } = renderBadge("Proposed");
  expect(getByText("Proposed")).toBeInTheDocument();
  expect(getByLabelText("Run status: Proposed")).toBeInTheDocument();
});

test("maps the additive commit stages (Committing, Completed)", () => {
  expect(renderBadge("Committing").getByLabelText("Run status: Committing")).toBeInTheDocument();
  expect(renderBadge("Completed").getByLabelText("Run status: Completed")).toBeInTheDocument();
});

test("degrades calmly for an unknown/additive status (no crash, raw label)", () => {
  const { getByText, getByLabelText } = renderBadge("SomeFutureStage");
  expect(getByText("SomeFutureStage")).toBeInTheDocument();
  expect(getByLabelText("Run status: SomeFutureStage")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderBadge("Reviewing");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/ImportStatusBadge.test.tsx`
Expected: FAIL — `./ImportStatusBadge` does not exist.

- [ ] **Step 3: Implement `ImportStatusBadge.tsx`**

Create `apps/web/src/features/ingestion/ImportStatusBadge.tsx`:

```tsx
import { Badge, type MantineSize } from "@mantine/core";
import type { ImportRunStatus } from "../../lib/types";

// Maps an import-run status to a label + a leading glyph + a token color. Status is NEVER color-only
// (DP-7): the text label carries the meaning, the glyph adds a second non-color channel. Mirrors
// TaskStateBadge. The `status` prop is a plain string (not the enum) so the badge tolerates additive
// commit stages beyond ImportRunStatus — an unknown status degrades to a `?? fallback`, never crashes.
const META: Record<ImportRunStatus, { label: string; mark: string; color: string }> = {
  Created: { label: "Created", mark: "○", color: "var(--es-text-muted)" },
  Scanning: { label: "Scanning", mark: "◔", color: "var(--es-info)" },
  Scanned: { label: "Scanned", mark: "◑", color: "var(--es-info)" },
  Extracting: { label: "Extracting", mark: "◔", color: "var(--es-info)" },
  Classifying: { label: "Classifying", mark: "◑", color: "var(--es-info)" },
  Classified: { label: "Classified", mark: "◕", color: "var(--es-info)" },
  Deduping: { label: "Deduping", mark: "◑", color: "var(--es-info)" },
  Proposing: { label: "Proposing", mark: "◕", color: "var(--es-info)" },
  Proposed: { label: "Proposed", mark: "◆", color: "var(--es-warning)" },
  Reviewing: { label: "Reviewing", mark: "✎", color: "var(--es-warning)" },
  Committing: { label: "Committing", mark: "◔", color: "var(--es-info)" },
  Completed: { label: "Completed", mark: "★", color: "var(--es-success)" },
  PartiallyCommitted: { label: "Partially committed", mark: "◐", color: "var(--es-warning)" },
  Failed: { label: "Failed", mark: "▲", color: "var(--es-danger)" },
  Cancelled: { label: "Cancelled", mark: "⊘", color: "var(--es-text-muted)" },
};

export function ImportStatusBadge({ status, size = "sm" }: { status: string; size?: MantineSize }) {
  const meta = META[status as ImportRunStatus] ?? {
    label: status,
    mark: "•",
    color: "var(--es-text-muted)",
  };
  return (
    <Badge
      variant="light"
      color={meta.color}
      size={size}
      leftSection={<span aria-hidden="true">{meta.mark}</span>}
      aria-label={`Run status: ${meta.label}`}
    >
      {meta.label}
    </Badge>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/ImportStatusBadge.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing test for `KindCell`**

Create `apps/web/src/features/ingestion/KindCell.test.tsx`:

```tsx
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type {
  ConfirmedKind,
  ImportClassification,
  ImportFileReview,
} from "../../lib/types";
import { KindCell } from "./KindCell";

const DOC_CLASS: ImportClassification = {
  kind: "DOCUMENT",
  kind_conf: 92,
  type_code: "SOP",
  type_conf: 90,
  clause_numbers: ["8.4"],
  clause_conf: 88,
  process_names: ["Purchasing"],
  process_conf: 80,
  pdca_phase: "DO",
  band: "HIGH",
  ambiguous: false,
  top2_margin: 30,
  classifier_version: "v1.4",
};

const RECORD_CLASS: ImportClassification = { ...DOC_CLASS, kind: "RECORD", type_code: "FRM" };
const UNKNOWN_CLASS: ImportClassification = { ...DOC_CLASS, kind: "UNKNOWN", type_code: null };

const UNCONFIRMED_REVIEW: ImportFileReview = {
  disposition: "undecided",
  kind: "UNCONFIRMED",
  identifier: "SOP-PUR-014",
  identifier_source: "preserved_doc_code",
  type_code: "SOP",
  clause_numbers: ["8.4"],
  process_names: ["Purchasing"],
  owner: null,
  decided: false,
  last_action: null,
  commit_ready: false,
  identifier_collidable: true,
};

const CONFIRMED_DOC_REVIEW: ImportFileReview = { ...UNCONFIRMED_REVIEW, kind: "DOCUMENT" };

function renderCell(props: {
  review: ImportFileReview | null;
  classification: ImportClassification | null;
  onConfirm: (kind: ConfirmedKind) => void;
  busy?: boolean;
}) {
  return render(
    <MantineProvider>
      <KindCell {...props} />
    </MantineProvider>,
  );
}

test("UNCONFIRMED renders the engine guess dimmed with a '?' + a Confirm affordance", () => {
  renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  expect(screen.getByText("Document?")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm kind" })).toBeInTheDocument();
});

test("UNCONFIRMED RECORD guess shows the lock glyph; UNKNOWN shows 'Unknown'", () => {
  const rec = renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: RECORD_CLASS,
    onConfirm: vi.fn(),
  });
  expect(rec.getByText("🔒 Record?")).toBeInTheDocument();
  rec.unmount();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: UNKNOWN_CLASS, onConfirm: vi.fn() });
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("choosing Document from the Confirm menu calls onConfirm('DOCUMENT')", async () => {
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: DOC_CLASS, onConfirm });
  await user.click(screen.getByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirm).toHaveBeenCalledWith("DOCUMENT");
});

test("choosing Record from the Confirm menu calls onConfirm('RECORD')", async () => {
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  renderCell({ review: UNCONFIRMED_REVIEW, classification: DOC_CLASS, onConfirm });
  await user.click(screen.getByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Record" }));
  expect(onConfirm).toHaveBeenCalledWith("RECORD");
});

test("a confirmed kind renders a solid badge with no '?' and no Confirm button", () => {
  renderCell({
    review: CONFIRMED_DOC_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  expect(screen.getByLabelText("Kind: Document")).toBeInTheDocument();
  expect(screen.queryByText("Document?")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm kind" })).not.toBeInTheDocument();
});

test("busy disables the Confirm affordance", () => {
  renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
    busy: true,
  });
  expect(screen.getByRole("button", { name: "Confirm kind" })).toBeDisabled();
});

test("a null review/classification degrades to 'Unknown' (no crash)", () => {
  renderCell({ review: null, classification: null, onConfirm: vi.fn() });
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderCell({
    review: UNCONFIRMED_REVIEW,
    classification: DOC_CLASS,
    onConfirm: vi.fn(),
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm kind" })).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/KindCell.test.tsx`
Expected: FAIL — `./KindCell` does not exist.

- [ ] **Step 7: Implement `KindCell.tsx`**

Create `apps/web/src/features/ingestion/KindCell.tsx`:

```tsx
import { Badge, Button, Group, Menu, Text } from "@mantine/core";
import type {
  ConfirmedKind,
  ImportClassification,
  ImportFileReview,
  ImportKind,
} from "../../lib/types";

// R10: the kind is an ALWAYS-HUMAN confirm. While the folded review.kind is "UNCONFIRMED" we render the
// engine's guess DIMMED with a trailing "?" (derived from the immutable classification.kind) plus a
// Confirm affordance — a small Menu so the human can override the guess (Document vs Record). The
// confirmed kind lives only on review.kind (never written back to classification); once it is
// DOCUMENT|RECORD we render a solid badge with no "?" and no Confirm. Bulk-accept does NOT route here —
// kind-confirm is a separate act (D-5). `busy` disables the affordance during an in-flight confirm.

const CONFIRMED_META: Record<ConfirmedKind, { label: string; mark: string; color: string }> = {
  DOCUMENT: { label: "Document", mark: "📄", color: "var(--es-info)" },
  RECORD: { label: "Record", mark: "🔒", color: "var(--es-success)" },
};

// The dimmed engine guess text for the UNCONFIRMED state. Records carry the WORM lock glyph; an UNKNOWN
// or absent classification reads "Unknown" (no "?", since there is no guess to confirm against).
function guessLabel(kind: ImportKind | undefined): string {
  if (kind === "DOCUMENT") return "Document?";
  if (kind === "RECORD") return "🔒 Record?";
  return "Unknown";
}

export function KindCell({
  review,
  classification,
  onConfirm,
  busy = false,
}: {
  review: ImportFileReview | null;
  classification: ImportClassification | null;
  onConfirm: (kind: ConfirmedKind) => void;
  busy?: boolean;
}) {
  const kind = review?.kind;

  // Confirmed (DOCUMENT|RECORD) → a solid badge, no "?", no Confirm.
  if (kind === "DOCUMENT" || kind === "RECORD") {
    const meta = CONFIRMED_META[kind];
    return (
      <Badge
        variant="filled"
        color={meta.color}
        size="sm"
        leftSection={<span aria-hidden="true">{meta.mark}</span>}
        aria-label={`Kind: ${meta.label}`}
      >
        {meta.label}
      </Badge>
    );
  }

  // UNCONFIRMED (or a null review) → the dimmed engine guess + a Confirm menu (Document / Record).
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm" c="dimmed" span aria-label={`Engine guess: ${guessLabel(classification?.kind)}`}>
        {guessLabel(classification?.kind)}
      </Text>
      <Menu position="bottom-start" withinPortal>
        <Menu.Target>
          <Button
            variant="subtle"
            size="compact-xs"
            disabled={busy}
            aria-label="Confirm kind"
            rightSection={<span aria-hidden="true">▾</span>}
          >
            Confirm
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item onClick={() => onConfirm("DOCUMENT")}>Document</Menu.Item>
          <Menu.Item onClick={() => onConfirm("RECORD")}>Record</Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}
```

- [ ] **Step 8: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/KindCell.test.tsx`
Expected: PASS (8 tests).

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/features/ingestion/ImportStatusBadge.tsx apps/web/src/features/ingestion/ImportStatusBadge.test.tsx apps/web/src/features/ingestion/KindCell.tsx apps/web/src/features/ingestion/KindCell.test.tsx
git commit -m "feat(s-ing-4b): ImportStatusBadge (tolerant run-status badge) + KindCell (R10 kind-confirm)"
```

---

### Task 6: ConfidenceCell + IdentifierCell + TypeCell

**Files:**
- Create: `apps/web/src/features/ingestion/ConfidenceCell.tsx`
- Create: `apps/web/src/features/ingestion/IdentifierCell.tsx`
- Create: `apps/web/src/features/ingestion/TypeCell.tsx`
- Test: `apps/web/src/features/ingestion/Cells.test.tsx` (one combined test file for all three leaf cells)

> Three small presentational table cells (the locked registry signatures). `ConfidenceCell({ classification })` renders a band-styled label `"<Band> · <conf>%"` (the `kind_conf` %) per the mockup `.es-confidence__label` (HIGH=success, MEDIUM=warning, LOW/AMBIGUOUS=danger — **color is the third channel; the label carries the band**) + a `⚖ ambiguous` caption when `classification.ambiguous`; null → `—`. `IdentifierCell({ review, dupeOf })` shows `Duplicate of <dupeOf>` (danger) / the mono identifier / `— record (no code)` / `— suggest needed` / `—`. `TypeCell({ classification })` renders `type_code` verbatim (+ an `ambiguous` caption); null/no-code → `—`. Every cell carries a distinct `aria-label` (no duplicate label across cells — the S-web-6 `getByLabelText` single-match lesson). `noUncheckedIndexedAccess` is on, so the band→meta lookup degrades with `?? <fallback>`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/Cells.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import type { ImportClassification, ImportFileReview } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ConfidenceCell } from "./ConfidenceCell";
import { IdentifierCell } from "./IdentifierCell";
import { TypeCell } from "./TypeCell";

function classification(over: Partial<ImportClassification> = {}): ImportClassification {
  return {
    kind: "DOCUMENT",
    kind_conf: 92,
    type_code: "SOP",
    type_conf: 90,
    clause_numbers: ["8.4"],
    clause_conf: 88,
    process_names: ["Purchasing"],
    process_conf: 80,
    pdca_phase: "DO",
    band: "HIGH",
    ambiguous: false,
    top2_margin: 30,
    classifier_version: "v1.4",
    ...over,
  };
}

function review(over: Partial<ImportFileReview> = {}): ImportFileReview {
  return {
    disposition: "undecided",
    kind: "UNCONFIRMED",
    identifier: "SOP-PUR-014",
    identifier_source: "preserved_doc_code",
    type_code: "SOP",
    clause_numbers: ["8.4"],
    process_names: ["Purchasing"],
    owner: null,
    decided: false,
    last_action: null,
    commit_ready: false,
    identifier_collidable: true,
    ...over,
  };
}

// ---- ConfidenceCell ----
test("ConfidenceCell renders the band label with the kind_conf percentage", () => {
  renderWithProviders(<ConfidenceCell classification={classification({ band: "HIGH", kind_conf: 92 })} />);
  const badge = screen.getByLabelText("Confidence: High 92%");
  expect(badge).toHaveTextContent("High · 92%");
});

test("ConfidenceCell labels MEDIUM and LOW bands", () => {
  const { unmount } = renderWithProviders(
    <ConfidenceCell classification={classification({ band: "MEDIUM", kind_conf: 73 })} />,
  );
  expect(screen.getByLabelText("Confidence: Medium 73%")).toHaveTextContent("Medium · 73%");
  unmount();
  renderWithProviders(<ConfidenceCell classification={classification({ band: "LOW", kind_conf: 22 })} />);
  expect(screen.getByLabelText("Confidence: Low 22%")).toHaveTextContent("Low · 22%");
});

test("ConfidenceCell adds an ambiguous caption when classification.ambiguous", () => {
  renderWithProviders(
    <ConfidenceCell classification={classification({ band: "LOW", kind_conf: 41, ambiguous: true })} />,
  );
  expect(screen.getByLabelText("Confidence: Ambiguous 41%")).toBeInTheDocument();
  expect(screen.getByText("⚖ ambiguous")).toBeInTheDocument();
});

test("ConfidenceCell renders a dash for a null classification", () => {
  renderWithProviders(<ConfidenceCell classification={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.queryByLabelText(/^Confidence:/)).not.toBeInTheDocument();
});

// ---- IdentifierCell ----
test("IdentifierCell shows a danger 'Duplicate of' line when dupeOf is set", () => {
  renderWithProviders(<IdentifierCell review={review()} dupeOf="SOP-PUR-014" />);
  expect(screen.getByText("Duplicate of SOP-PUR-014")).toBeInTheDocument();
});

test("IdentifierCell shows the mono identifier when present and no dupe", () => {
  renderWithProviders(<IdentifierCell review={review({ identifier: "WI-PRD-022" })} dupeOf={null} />);
  expect(screen.getByText("WI-PRD-022")).toBeInTheDocument();
});

test("IdentifierCell shows the record-no-code hint for a RECORD with no identifier", () => {
  renderWithProviders(
    <IdentifierCell review={review({ kind: "RECORD", identifier: null })} dupeOf={null} />,
  );
  expect(screen.getByText("— record (no code)")).toBeInTheDocument();
});

test("IdentifierCell shows 'suggest needed' for a non-record with no identifier", () => {
  renderWithProviders(
    <IdentifierCell review={review({ kind: "UNCONFIRMED", identifier: null })} dupeOf={null} />,
  );
  expect(screen.getByText("— suggest needed")).toBeInTheDocument();
});

test("IdentifierCell renders a dash for a null review", () => {
  renderWithProviders(<IdentifierCell review={null} dupeOf={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

// ---- TypeCell ----
test("TypeCell renders the type_code verbatim", () => {
  renderWithProviders(<TypeCell classification={classification({ type_code: "SOP" })} />);
  expect(screen.getByText("SOP")).toBeInTheDocument();
});

test("TypeCell adds an ambiguous caption when classification.ambiguous", () => {
  renderWithProviders(
    <TypeCell classification={classification({ type_code: "WI", ambiguous: true })} />,
  );
  expect(screen.getByText("WI")).toBeInTheDocument();
  expect(screen.getByText("ambiguous")).toBeInTheDocument();
});

test("TypeCell renders a dash for a null classification or a missing type_code", () => {
  const { unmount } = renderWithProviders(<TypeCell classification={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
  unmount();
  renderWithProviders(<TypeCell classification={classification({ type_code: null })} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});

// ---- a11y: a small wrapper rendering all three cells together ----
test("all three cells together have no axe violations", async () => {
  const { container } = renderWithProviders(
    <table>
      <tbody>
        <tr>
          <td>
            <IdentifierCell review={review()} dupeOf={null} />
          </td>
          <td>
            <TypeCell classification={classification({ ambiguous: true })} />
          </td>
          <td>
            <ConfidenceCell classification={classification({ band: "LOW", kind_conf: 41, ambiguous: true })} />
          </td>
        </tr>
      </tbody>
    </table>,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/Cells.test.tsx`
Expected: FAIL — `./ConfidenceCell`, `./IdentifierCell`, `./TypeCell` do not exist.

- [ ] **Step 3: Implement the three cells**

Create `apps/web/src/features/ingestion/ConfidenceCell.tsx`:

```tsx
import { Badge, Stack, Text } from "@mantine/core";
import type { ImportClassification, ImportConfidenceBand } from "../../lib/types";

// DP-7: the band is NEVER color-only — the text label ("<Band> · <conf>%") carries the meaning and
// color is the third redundant channel (mirrors StateBadge). HIGH=success, MEDIUM=warning,
// LOW/AMBIGUOUS=danger — matching the mockup's .es-confidence band hues. The % is kind_conf (the
// dimension the human confirms). `classification.ambiguous` adds a `⚖ ambiguous` caption.
const BAND_META: Record<ImportConfidenceBand, { label: string; color: string }> = {
  HIGH: { label: "High", color: "var(--es-success)" },
  MEDIUM: { label: "Medium", color: "var(--es-warning)" },
  LOW: { label: "Low", color: "var(--es-danger)" },
  AMBIGUOUS: { label: "Ambiguous", color: "var(--es-danger)" },
};

export function ConfidenceCell({ classification }: { classification: ImportClassification | null }) {
  if (!classification) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  // noUncheckedIndexedAccess: an unexpected band string degrades to a calm neutral label.
  const meta = BAND_META[classification.band] ?? { label: classification.band, color: "var(--es-text-muted)" };
  const pct = Math.round(classification.kind_conf);
  return (
    <Stack gap={2} align="flex-start">
      <Badge variant="light" color={meta.color} aria-label={`Confidence: ${meta.label} ${pct}%`}>
        {meta.label} · {pct}%
      </Badge>
      {classification.ambiguous && (
        <Text span size="xs" c="dimmed">
          ⚖ ambiguous
        </Text>
      )}
    </Stack>
  );
}
```

Create `apps/web/src/features/ingestion/IdentifierCell.tsx`:

```tsx
import { Text } from "@mantine/core";
import type { ImportFileReview } from "../../lib/types";

// The proposed identifier cell. Order: a within-import duplicate (danger) wins; else the folded
// effective identifier (mono); else a tertiary hint that depends on the kind — a RECORD legitimately
// has no doc code, a document still needs one suggested. Null review → a plain dash.
export function IdentifierCell({
  review,
  dupeOf,
}: {
  review: ImportFileReview | null;
  dupeOf: string | null;
}) {
  if (dupeOf) {
    return (
      <Text span size="sm" c="var(--es-danger)">
        Duplicate of {dupeOf}
      </Text>
    );
  }
  if (review?.identifier) {
    return (
      <Text span size="sm" ff="monospace">
        {review.identifier}
      </Text>
    );
  }
  if (review?.kind === "RECORD") {
    return (
      <Text span size="sm" c="dimmed">
        — record (no code)
      </Text>
    );
  }
  if (review) {
    return (
      <Text span size="sm" c="dimmed">
        — suggest needed
      </Text>
    );
  }
  return (
    <Text span size="sm" c="dimmed">
      —
    </Text>
  );
}
```

Create `apps/web/src/features/ingestion/TypeCell.tsx`:

```tsx
import { Stack, Text } from "@mantine/core";
import type { ImportClassification } from "../../lib/types";

// The proposed type cell — renders the engine's type_code verbatim (no label lookup; the code is the
// label here). `classification.ambiguous` adds a small `ambiguous` caption (the mockup's alt-type
// hint). A null classification or a missing type_code degrades to a plain dash.
export function TypeCell({ classification }: { classification: ImportClassification | null }) {
  if (!classification || !classification.type_code) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  return (
    <Stack gap={2} align="flex-start">
      <Text span size="sm">
        {classification.type_code}
      </Text>
      {classification.ambiguous && (
        <Text span size="xs" c="dimmed">
          ambiguous
        </Text>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/Cells.test.tsx`
Expected: PASS (13 tests, incl. the combined-wrapper axe assertion).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/ConfidenceCell.tsx apps/web/src/features/ingestion/IdentifierCell.tsx apps/web/src/features/ingestion/TypeCell.tsx apps/web/src/features/ingestion/Cells.test.tsx
git commit -m "feat(s-ing-4b): ConfidenceCell + IdentifierCell + TypeCell triage leaf cells"
```

---

### Task 7: RunSummaryTiles + ImportPlanBanner

**Files:**
- Create: `apps/web/src/features/ingestion/RunSummaryTiles.tsx`
- Test: `apps/web/src/features/ingestion/RunSummaryTiles.test.tsx`
- Create: `apps/web/src/features/ingestion/ImportPlanBanner.tsx`
- Test: `apps/web/src/features/ingestion/ImportPlanBanner.test.tsx`

> Both are presentational leaves (Component Contract Registry: `RunSummaryTiles({ run: ImportRun })`, `ImportPlanBanner({})`). `RunSummaryTiles` reads `run.counts` (typed `Record<string, unknown> | null` in Task 1) through a **local** `countAt(...)` accessor that walks the nested object and returns `0` on any miss — never an array index without a `?? 0` fallback (`noUncheckedIndexedAccess` is ON). `ImportPlanBanner` is static drift-safe copy (D-6: informational only — **no** interactive "Change plan"). The four tiles map to the Task-1 `ingestionRunFixture.counts` shape: `classify.band.{HIGH,MEDIUM}`, `queues.needs`, and `review.{kind_confirmed,keep_items}`.

- [ ] **Step 1: Write the failing test for `RunSummaryTiles`**

Create `apps/web/src/features/ingestion/RunSummaryTiles.test.tsx`:

```tsx
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import type { ImportRun } from "../../lib/types";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { RunSummaryTiles } from "./RunSummaryTiles";

const run = ingestionRunFixture as unknown as ImportRun;

test("renders the four metric tiles from run.counts", async () => {
  const { container } = renderWithProviders(<RunSummaryTiles run={run} />);
  // (1) Auto-classified · High = classify.band.HIGH = 2
  expect(screen.getByLabelText("Auto-classified · High: 2")).toBeInTheDocument();
  // (2) Medium = classify.band.MEDIUM = 1
  expect(screen.getByLabelText("Medium: 1")).toBeInTheDocument();
  // (3) Needs decision = queues.needs = 4
  expect(screen.getByLabelText("Needs decision: 4")).toBeInTheDocument();
  // (4) Kind confirmed = review.kind_confirmed / review.keep_items = 1 / 4
  expect(screen.getByLabelText("Kind confirmed: 1 of 4")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("the values render verbatim (2, 1, 4, and '1 / 4')", () => {
  renderWithProviders(<RunSummaryTiles run={run} />);
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByText("1 / 4")).toBeInTheDocument();
});

test("a tile whose count key is missing degrades to 0 (never crashes, never NaN)", async () => {
  const sparse = { ...run, counts: { classify: { band: { HIGH: 2 } } } } as unknown as ImportRun;
  const { container } = renderWithProviders(<RunSummaryTiles run={sparse} />);
  expect(screen.getByLabelText("Auto-classified · High: 2")).toBeInTheDocument();
  expect(screen.getByLabelText("Medium: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Needs decision: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind confirmed: 0 of 0")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("a null counts object degrades every tile to 0", () => {
  const empty = { ...run, counts: null } as unknown as ImportRun;
  renderWithProviders(<RunSummaryTiles run={empty} />);
  expect(screen.getByLabelText("Auto-classified · High: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind confirmed: 0 of 0")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/RunSummaryTiles.test.tsx`
Expected: FAIL — `./RunSummaryTiles` does not exist (module-not-found).

- [ ] **Step 3: Implement `RunSummaryTiles`**

Create `apps/web/src/features/ingestion/RunSummaryTiles.tsx`:

```tsx
import { Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// run.counts is stage-namespaced + loosely typed (Record<string, unknown> | null). Walk it safely:
// every step degrades to 0 on a missing/non-object node, so a partial or null counts never crashes
// and never yields NaN (noUncheckedIndexedAccess — no bare index without a fallback). DP-7: every
// tile is glyph + label + value (never color-only); aria-labels are distinct per tile.
function countAt(counts: Record<string, unknown> | null, ...path: string[]): number {
  let node: unknown = counts;
  for (const key of path) {
    if (node === null || typeof node !== "object") return 0;
    node = (node as Record<string, unknown>)[key];
  }
  return typeof node === "number" && Number.isFinite(node) ? node : 0;
}

function MetricTile({
  glyph,
  glyphColor,
  label,
  value,
  ariaValue,
}: {
  glyph: string;
  glyphColor: string;
  label: string;
  value: string;
  ariaValue: string;
}) {
  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      role="group"
      aria-label={`${label}: ${ariaValue}`}
    >
      <Stack gap={4}>
        <Group gap="xs" justify="space-between" wrap="nowrap">
          <Text size="sm" c="dimmed">
            {label}
          </Text>
          <Text aria-hidden c={glyphColor}>
            {glyph}
          </Text>
        </Group>
        <Text fz="1.75rem" fw={700} ff="monospace">
          {value}
        </Text>
      </Stack>
    </Paper>
  );
}

export function RunSummaryTiles({ run }: { run: ImportRun }) {
  const counts = run.counts;
  const high = countAt(counts, "classify", "band", "HIGH");
  const medium = countAt(counts, "classify", "band", "MEDIUM");
  const needs = countAt(counts, "queues", "needs");
  const kindConfirmed = countAt(counts, "review", "kind_confirmed");
  const keepItems = countAt(counts, "review", "keep_items");

  return (
    <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
      <MetricTile
        glyph="●"
        glyphColor="var(--es-success)"
        label="Auto-classified · High"
        value={String(high)}
        ariaValue={String(high)}
      />
      <MetricTile
        glyph="▲"
        glyphColor="var(--es-warning)"
        label="Medium"
        value={String(medium)}
        ariaValue={String(medium)}
      />
      <MetricTile
        glyph="✕"
        glyphColor="var(--es-danger)"
        label="Needs decision"
        value={String(needs)}
        ariaValue={String(needs)}
      />
      <MetricTile
        glyph="☑"
        glyphColor="var(--es-accent)"
        label="Kind confirmed"
        value={`${kindConfirmed} / ${keepItems}`}
        ariaValue={`${kindConfirmed} of ${keepItems}`}
      />
    </SimpleGrid>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/RunSummaryTiles.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing test for `ImportPlanBanner`**

Create `apps/web/src/features/ingestion/ImportPlanBanner.test.tsx`:

```tsx
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { ImportPlanBanner } from "./ImportPlanBanner";

test("renders the drift-safe import-plan explainer (the verbatim baseline copy)", async () => {
  const { container } = renderWithProviders(<ImportPlanBanner />);
  expect(screen.getByText("Import plan")).toBeInTheDocument();
  expect(screen.getByText(/Default · drift-safe/)).toBeInTheDocument();
  // the load-bearing drift-safety phrases
  expect(screen.getByText(/Import the current version only/i)).toBeInTheDocument();
  expect(screen.getByText(/Rev A · Effective/)).toBeInTheDocument();
  expect(screen.getByText(/archived as provenance/i)).toBeInTheDocument();
  expect(screen.getByText(/Revision-chain reconstruction is opt-in per family/i)).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("is informational only — no interactive 'Change plan' control (D-6)", () => {
  renderWithProviders(<ImportPlanBanner />);
  expect(screen.queryByRole("button", { name: /change plan/i })).toBeNull();
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/ImportPlanBanner.test.tsx`
Expected: FAIL — `./ImportPlanBanner` does not exist (module-not-found).

- [ ] **Step 7: Implement `ImportPlanBanner`**

Create `apps/web/src/features/ingestion/ImportPlanBanner.tsx`:

```tsx
import { Badge, Group, Paper, Stack, Text } from "@mantine/core";

// The drift-safe import-default explainer. INFORMATIONAL ONLY (D-6) — the real per-family
// revision-chain opt-in lives in the merge flow (reconstruct_revision_chain), NOT a global toggle
// here, so there is deliberately NO "Change plan" control. Copy mirrors mockup §3 verbatim. The
// shield glyph is aria-hidden; the heading carries the meaning for assistive tech.
export function ImportPlanBanner() {
  return (
    <Paper withBorder p="md" radius="md">
      <Group gap="sm" align="flex-start" wrap="nowrap">
        <Text aria-hidden c="var(--es-accent)" fz="lg">
          🛡
        </Text>
        <Stack gap={4}>
          <Group gap="xs" align="center">
            <Text fw={600}>Import plan</Text>
            <Badge variant="light" color="var(--es-accent)">
              Default · drift-safe
            </Badge>
          </Group>
          <Text size="sm" c="dimmed" maw="72ch">
            Import the current version only as the controlled baseline (
            <Text span ff="monospace">
              Rev A · Effective
            </Text>
            ); older copies in each version family are archived as provenance, never asserted as
            approved history. Revision-chain reconstruction is opt-in per family and confirmed at
            commit. Exactly one Effective version per document — drift is eliminated at the source.
          </Text>
        </Stack>
      </Group>
    </Paper>
  );
}
```

- [ ] **Step 8: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/ImportPlanBanner.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/features/ingestion/RunSummaryTiles.tsx apps/web/src/features/ingestion/RunSummaryTiles.test.tsx apps/web/src/features/ingestion/ImportPlanBanner.tsx apps/web/src/features/ingestion/ImportPlanBanner.test.tsx
git commit -m "feat(s-ing-4b): RunSummaryTiles (run.counts metric tiles) + ImportPlanBanner (drift-safe explainer)"
```

---

### Task 8: QueueTabs + IngestionFacetBar

**Files:**
- Create: `apps/web/src/features/ingestion/QueueTabs.tsx`
- Test: `apps/web/src/features/ingestion/QueueTabs.test.tsx`
- Create: `apps/web/src/features/ingestion/IngestionFacetBar.tsx`
- Test: `apps/web/src/features/ingestion/IngestionFacetBar.test.tsx`

> Both are presentational leaves: state lives in `ReviewCockpit` (Task 14) and flows in via props. `QueueTabs` iterates `QUEUES` from `./filters` (so the 5 tabs + their order + `countKey`s are single-sourced), reads each badge count from the passed `counts` record (`counts[q.countKey] ?? 0` — `noUncheckedIndexedAccess` fallback), and reports the picked queue via `onChange`. `IngestionFacetBar` is the confidence-band `SegmentedControl` only (per the contract constraint: band is the sole server-filterable dimension here — **do not** add clause/process/type facets; they are deferred). It uses real tab/radiogroup semantics, distinct `aria-label`s, and theme tokens only — no hardcoded hex.

- [ ] **Step 1: Write the failing `QueueTabs` test**

Create `apps/web/src/features/ingestion/QueueTabs.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { QueueTabs } from "./QueueTabs";

const COUNTS = { needs: 4, medium: 1, high: 2, quarantine: 1, vault: 0 };

test("renders the five queue tabs with their labels + counts (mockup order)", () => {
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={() => {}} />,
  );
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.textContent)).toEqual([
    "Needs decision4",
    "Medium1",
    "High2",
    "Quarantine1",
    "Already in vault0",
  ]);
});

test("marks the active tab selected from `value`", () => {
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="high" onChange={() => {}} />,
  );
  expect(screen.getByRole("tab", { name: /High/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /Needs decision/ })).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

test("clicking a tab reports its queue value via onChange", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={onChange} />,
  );
  await user.click(screen.getByRole("tab", { name: /High/ }));
  expect(onChange).toHaveBeenCalledWith("high");
});

test("a missing count key renders 0 (noUncheckedIndexedAccess fallback)", () => {
  renderWithProviders(<QueueTabs counts={{}} value="needs" onChange={() => {}} />);
  // every tab badge degrades to 0 when its count key is absent
  expect(screen.getByRole("tab", { name: /Needs decision/ })).toHaveTextContent("0");
  expect(screen.getByRole("tab", { name: /Quarantine/ })).toHaveTextContent("0");
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <QueueTabs counts={COUNTS} value="needs" onChange={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/QueueTabs.test.tsx`
Expected: FAIL — `./QueueTabs` does not exist.

- [ ] **Step 3: Implement `QueueTabs`**

Create `apps/web/src/features/ingestion/QueueTabs.tsx`:

```tsx
import { Badge, Tabs } from "@mantine/core";
import { QUEUES, type IngestionQueue } from "./filters";

// The five confidence/decision queue tabs (Needs-decision / Medium / High / Quarantine / Already-in-
// vault), single-sourced from QUEUES (so order + countKey live in filters.ts). Presentational: the
// active queue comes in via `value`, a pick is reported via `onChange`; each tab badge reads its count
// from `counts[q.countKey]` with a `?? 0` fallback (noUncheckedIndexedAccess + a missing key). Real
// Mantine Tabs gives tablist/tab semantics for keyboard + screen-reader navigation.
export function QueueTabs({
  counts,
  value,
  onChange,
}: {
  counts: Record<string, number>;
  value: IngestionQueue;
  onChange: (q: IngestionQueue) => void;
}) {
  return (
    <Tabs
      value={value}
      onChange={(v) => {
        if (v) onChange(v as IngestionQueue);
      }}
      aria-label="Review queues"
    >
      <Tabs.List>
        {QUEUES.map((q) => (
          <Tabs.Tab
            key={q.value}
            value={q.value}
            rightSection={
              <Badge size="sm" variant="light" circle>
                {counts[q.countKey] ?? 0}
              </Badge>
            }
          >
            {q.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/QueueTabs.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/QueueTabs.tsx apps/web/src/features/ingestion/QueueTabs.test.tsx
git commit -m "feat(s-ing-4b): QueueTabs (5 queue tabs from QUEUES, count badges, tablist a11y)"
```

- [ ] **Step 6: Write the failing `IngestionFacetBar` test**

Create `apps/web/src/features/ingestion/IngestionFacetBar.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { IngestionFacetBar } from "./IngestionFacetBar";

test("renders the four confidence options (All / High / Medium / Low)", () => {
  renderWithProviders(<IngestionFacetBar conf="ALL" onConf={() => {}} />);
  for (const label of ["All", "High", "Medium", "Low"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("selecting High reports the HIGH band via onConf", async () => {
  const user = userEvent.setup();
  const onConf = vi.fn();
  renderWithProviders(<IngestionFacetBar conf="ALL" onConf={onConf} />);
  await user.click(screen.getByText("High"));
  expect(onConf).toHaveBeenCalledWith("HIGH");
});

test("reflects the current confidence from `conf`", () => {
  renderWithProviders(<IngestionFacetBar conf="MEDIUM" onConf={() => {}} />);
  // the Medium radio is the checked option of the segmented control
  expect(screen.getByRole("radio", { name: "Medium" })).toBeChecked();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <IngestionFacetBar conf="ALL" onConf={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionFacetBar.test.tsx`
Expected: FAIL — `./IngestionFacetBar` does not exist.

- [ ] **Step 8: Implement `IngestionFacetBar`**

Create `apps/web/src/features/ingestion/IngestionFacetBar.tsx`:

```tsx
import { SegmentedControl } from "@mantine/core";
import type { ConfidenceChoice } from "./filters";

// The confidence-band facet. Per the contract constraint, band is the ONLY server-filterable dimension
// for the /files list (clause/process/type facets are deferred — not server-filterable), so this bar is
// a single confidence SegmentedControl. The `value`/`onChange` are the ConfidenceChoice values
// (ALL/HIGH/MEDIUM/LOW) with friendlier visible labels; SegmentedControl gives radiogroup semantics.
const CONF_DATA: { value: ConfidenceChoice; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "HIGH", label: "High" },
  { value: "MEDIUM", label: "Medium" },
  { value: "LOW", label: "Low" },
];

export function IngestionFacetBar({
  conf,
  onConf,
}: {
  conf: ConfidenceChoice;
  onConf: (c: ConfidenceChoice) => void;
}) {
  return (
    <SegmentedControl
      value={conf}
      onChange={(v) => onConf(v as ConfidenceChoice)}
      data={CONF_DATA}
      size="sm"
      aria-label="Confidence band"
    />
  );
}
```

- [ ] **Step 9: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionFacetBar.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/features/ingestion/IngestionFacetBar.tsx apps/web/src/features/ingestion/IngestionFacetBar.test.tsx
git commit -m "feat(s-ing-4b): IngestionFacetBar (confidence band SegmentedControl; clause/process/type deferred)"
```

---

### Task 9: TriageTable + TriagePagination

**Files:**
- Create: `apps/web/src/features/ingestion/TriageTable.tsx`
- Test: `apps/web/src/features/ingestion/TriageTable.test.tsx`
- Create: `apps/web/src/features/ingestion/TriagePagination.tsx`
- Test: `apps/web/src/features/ingestion/TriagePagination.test.tsx`

> The two presentational pieces of the cockpit grid. `TriageTable` is a semantic 9-column Mantine `<Table>` (header select-all-on-page checkbox + Source file · Proposed identifier · Kind · Type · Clause · Process · Confidence · Action) over the current page of `ImportFile[]`; it owns no state — selection, the dupe/family joins, and every handler arrive as props (from `ReviewCockpit`, Task 14). It reuses the Task 5–6 cells **by their LOCKED registry props** (`KindCell` / `ConfidenceCell` / `IdentifierCell` / `TypeCell`). `TriagePagination` mirrors the Library `Pagination` but with the fixed `FILES_PAGE_SIZE` (the `/files` contract returns no total/`has_more`, so `hasMore` is derived by the caller as `files.length === FILES_PAGE_SIZE`; the bucket `total` comes from `run.counts`). Both bind to Task 1 types, Task 2 `FILES_PAGE_SIZE`, and the Tasks 3–4 hooks only via the parent (these are pure leaves). Every render asserts `axe` clean; assertions are by role/label; every array index + `Map.get` degrades with `?? <fallback>` under `noUncheckedIndexedAccess`.

- [ ] **Step 1: Write the failing `TriagePagination` test**

Create `apps/web/src/features/ingestion/TriagePagination.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { FILES_PAGE_SIZE } from "./filters";
import { TriagePagination } from "./TriagePagination";

test("Prev is disabled at offset 0; Next disabled when !hasMore", () => {
  renderWithProviders(
    <TriagePagination offset={0} hasMore={false} onOffset={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Prev/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
});

test("clicking Next advances the offset by FILES_PAGE_SIZE", async () => {
  const user = userEvent.setup();
  const onOffset = vi.fn();
  renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={onOffset} />,
  );
  await user.click(screen.getByRole("button", { name: /Next/ }));
  expect(onOffset).toHaveBeenCalledWith(FILES_PAGE_SIZE);
});

test("clicking Prev steps back one page, clamped at 0", async () => {
  const user = userEvent.setup();
  const onOffset = vi.fn();
  renderWithProviders(
    <TriagePagination offset={FILES_PAGE_SIZE} hasMore onOffset={onOffset} />,
  );
  await user.click(screen.getByRole("button", { name: /Prev/ }));
  expect(onOffset).toHaveBeenCalledWith(0);
});

test("shows 'Showing X–Y of N' when total is provided (page 2, 3 rows on page)", () => {
  // offset=100, 3 rows on this page (hasMore false), total 103 → "Showing 101–103 of 103"
  renderWithProviders(
    <TriagePagination offset={FILES_PAGE_SIZE} hasMore={false} onOffset={() => {}} pageCount={3} total={103} />,
  );
  expect(screen.getByText(/Showing 101–103 of 103/)).toBeInTheDocument();
});

test("omits the count line when total is undefined", () => {
  renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={() => {}} pageCount={100} />,
  );
  expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={() => {}} pageCount={100} total={281} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/TriagePagination.test.tsx`
Expected: FAIL — `./TriagePagination` does not exist.

- [ ] **Step 3: Implement `TriagePagination`**

Create `apps/web/src/features/ingestion/TriagePagination.tsx`:

```tsx
import { Button, Group, Text } from "@mantine/core";
import { FILES_PAGE_SIZE } from "./filters";

// Offset pager at the FIXED FILES_PAGE_SIZE (mirrors the Library Pagination shape, but the /files
// contract returns no total/has_more — the caller derives `hasMore = files.length === FILES_PAGE_SIZE`
// and passes the page-row count + the bucket total from run.counts). "Showing X–Y of N" only when
// `total` is provided; otherwise the honest prev/next-only pager. Prev/Next disable at the ends
// (queue state, not a permission gate).
export function TriagePagination({
  offset,
  hasMore,
  onOffset,
  pageCount,
  total,
}: {
  offset: number;
  hasMore: boolean;
  onOffset: (offset: number) => void;
  pageCount?: number;
  total?: number;
}) {
  const onPage = pageCount ?? 0;
  const from = onPage > 0 ? offset + 1 : 0;
  const to = offset + onPage;
  return (
    <Group justify="space-between">
      {total !== undefined && onPage > 0 ? (
        <Text size="sm" c="dimmed">
          Showing {from}–{to} of {total} in this queue
        </Text>
      ) : (
        <span />
      )}
      <Group gap="xs">
        <Button
          variant="default"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - FILES_PAGE_SIZE))}
        >
          ‹ Prev
        </Button>
        <Button
          variant="default"
          size="sm"
          disabled={!hasMore}
          onClick={() => onOffset(offset + FILES_PAGE_SIZE)}
        >
          Next ›
        </Button>
      </Group>
    </Group>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/TriagePagination.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing `TriageTable` test**

Create `apps/web/src/features/ingestion/TriageTable.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportFile } from "../../lib/types";
import { ingestionFilesFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { TriageTable } from "./TriageTable";

// The Task-1 fixture order is [HIGH_DOC, DUP_FILE, MED_DOC, LOW_UNKNOWN, QUARANTINE_FILE].
const FILES = ingestionFilesFixture as unknown as ImportFile[];
const HIGH = FILES[0]!;
const DUP = FILES[1]!;
const QUAR = FILES[4]!;

// DUP (a4) is the redundant member of the near-cluster whose canonical id is SOP-PUR-014; HIGH (a1)
// is the canonical effective member of a 2-member family. The maps are the ReviewCockpit join output.
const DUPE_MAP = new Map<string, string>([[DUP.id, "SOP-PUR-014"]]);
const FAMILY_MAP = new Map<string, number>([[HIGH.id, 2]]);

function baseProps(over: Partial<Parameters<typeof TriageTable>[0]> = {}) {
  return {
    files: FILES,
    dupeMap: DUPE_MAP,
    familyMap: FAMILY_MAP,
    loading: false,
    selected: new Set<string>(),
    onToggle: vi.fn(),
    onToggleAllOnPage: vi.fn(),
    allOnPageSelected: false,
    onConfirmKind: vi.fn(),
    onOpenDetail: vi.fn(),
    onRowAction: vi.fn(),
    ...over,
  };
}

test("renders the high-confidence row with its identifier and a 'High' confidence", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText("SOP-PUR-014 Purchasing.docx")).toBeInTheDocument();
  // ConfidenceCell (Task 6) carries aria-label="Confidence: High 92%" (title-cased band label).
  expect(screen.getByLabelText("Confidence: High 92%")).toBeInTheDocument();
});

test("shows the family member-count meta line for a file in familyMap", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText(/2 versions in family/)).toBeInTheDocument();
});

test("the dup row shows 'Duplicate of SOP-PUR-014' (from dupeMap)", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  // IdentifierCell (Task 5) renders the dupeOf danger text when dupeMap has the file.
  expect(screen.getByText(/Duplicate of SOP-PUR-014/)).toBeInTheDocument();
});

test("the quarantine row shows the reason and offers no Accept action", () => {
  renderWithProviders(<TriageTable {...baseProps()} />);
  expect(screen.getByText("broken.bin")).toBeInTheDocument();
  expect(screen.getByText(/Quarantined: sniff_failed/)).toBeInTheDocument();
  // the quarantine row carries no per-row Accept button
  const cell = screen.getByText("broken.bin").closest("tr")!;
  expect(within(cell).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
});

test("toggling a row checkbox calls onToggle(file.id)", async () => {
  const user = userEvent.setup();
  const onToggle = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onToggle })} />);
  await user.click(screen.getByRole("checkbox", { name: "Select SOP-PUR-014 Purchasing.docx" }));
  expect(onToggle).toHaveBeenCalledWith(HIGH.id);
});

test("the header 'Select all on page' checkbox calls onToggleAllOnPage", async () => {
  const user = userEvent.setup();
  const onToggleAllOnPage = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onToggleAllOnPage })} />);
  await user.click(screen.getByRole("checkbox", { name: "Select all on page" }));
  expect(onToggleAllOnPage).toHaveBeenCalledTimes(1);
});

test("a row checkbox reflects `selected.has(id)`", () => {
  renderWithProviders(<TriageTable {...baseProps({ selected: new Set([HIGH.id]) })} />);
  expect(
    screen.getByRole("checkbox", { name: "Select SOP-PUR-014 Purchasing.docx" }),
  ).toBeChecked();
});

test("clicking a row's Accept calls onRowAction(file, 'accept')", async () => {
  const user = userEvent.setup();
  const onRowAction = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onRowAction })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Accept" }));
  expect(onRowAction).toHaveBeenCalledWith(HIGH, "accept");
});

test("clicking a row's Open calls onOpenDetail(file.id)", async () => {
  const user = userEvent.setup();
  const onOpenDetail = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onOpenDetail })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Open" }));
  expect(onOpenDetail).toHaveBeenCalledWith(HIGH.id);
});

test("confirming kind on a row calls onConfirmKind(file.id, 'DOCUMENT')", async () => {
  const user = userEvent.setup();
  const onConfirmKind = vi.fn();
  renderWithProviders(<TriageTable {...baseProps({ onConfirmKind })} />);
  const row = screen.getByText("SOP-PUR-014 Purchasing.docx").closest("tr")!;
  // KindCell (Task 5) renders Confirm as a Menu trigger: open it, then choose Document. TriageTable
  // wraps the cell's onConfirm(kind) with file.id, so it arrives as onConfirmKind(file.id, "DOCUMENT").
  await user.click(within(row).getByRole("button", { name: /Confirm/ }));
  await user.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirmKind).toHaveBeenCalledWith(HIGH.id, "DOCUMENT");
});

test("loading renders skeleton rows, not the empty state", () => {
  renderWithProviders(<TriageTable {...baseProps({ files: [], loading: true })} />);
  expect(screen.getByLabelText("Loading files")).toBeInTheDocument();
  expect(screen.queryByText("Nothing in this queue.")).not.toBeInTheDocument();
});

test("an empty file list shows the calm empty state", () => {
  renderWithProviders(<TriageTable {...baseProps({ files: [], loading: false })} />);
  expect(screen.getByText("Nothing in this queue.")).toBeInTheDocument();
});

test("no axe violations (populated table)", async () => {
  const { container } = renderWithProviders(<TriageTable {...baseProps()} />);
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/TriageTable.test.tsx`
Expected: FAIL — `./TriageTable` does not exist.

- [ ] **Step 7: Implement `TriageTable`**

Create `apps/web/src/features/ingestion/TriageTable.tsx`:

```tsx
import { Button, Checkbox, Group, Skeleton, Stack, Table, Text } from "@mantine/core";
import type {
  ConfirmedKind,
  ImportDecisionAction,
  ImportFile,
} from "../../lib/types";
import { ConfidenceCell } from "./ConfidenceCell";
import { IdentifierCell } from "./IdentifierCell";
import { KindCell } from "./KindCell";
import { TypeCell } from "./TypeCell";

const COLUMNS = 9;

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The paged triage grid. Presentational: selection, the dupe/family join maps, and every handler
// arrive as props from ReviewCockpit (Task 14). A quarantine row (scan_flags.disposition ===
// "quarantine") renders simplified — filename + a reason cell spanning the classification columns,
// no classification cells, no Accept. Every array/Map access degrades to a defined fallback
// (noUncheckedIndexedAccess). Checkbox aria-labels are distinct per row ("Select <filename>") and
// the header ("Select all on page") so getByLabelText stays single-match (the S-web-6 lesson).
export function TriageTable({
  files,
  dupeMap,
  familyMap,
  loading,
  selected,
  onToggle,
  onToggleAllOnPage,
  allOnPageSelected,
  onConfirmKind,
  onOpenDetail,
  onRowAction,
}: {
  files: ImportFile[];
  dupeMap: Map<string, string>;
  familyMap: Map<string, number>;
  loading: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleAllOnPage: () => void;
  allOnPageSelected: boolean;
  onConfirmKind: (fileId: string, kind: ConfirmedKind) => void;
  onOpenDetail: (fileId: string) => void;
  onRowAction: (file: ImportFile, action: ImportDecisionAction) => void;
}) {
  if (loading) {
    return (
      <Stack gap="xs" aria-label="Loading files">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={44} />
        ))}
      </Stack>
    );
  }
  if (files.length === 0) {
    return <Text c="dimmed">Nothing in this queue.</Text>;
  }

  return (
    <Table highlightOnHover stickyHeader verticalSpacing="sm" aria-label="Triage queue">
      <Table.Thead>
        <Table.Tr>
          <Table.Th w={36}>
            <Checkbox
              aria-label="Select all on page"
              checked={allOnPageSelected}
              onChange={onToggleAllOnPage}
            />
          </Table.Th>
          <Table.Th scope="col">Source file</Table.Th>
          <Table.Th scope="col">Proposed identifier</Table.Th>
          <Table.Th scope="col">Kind</Table.Th>
          <Table.Th scope="col">Type</Table.Th>
          <Table.Th scope="col" ta="center">
            Clause
          </Table.Th>
          <Table.Th scope="col">Process</Table.Th>
          <Table.Th scope="col">Confidence</Table.Th>
          <Table.Th scope="col">Action</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {files.map((file) => {
          const isQuarantine = file.scan_flags.disposition === "quarantine";
          const memberCount = familyMap.get(file.id);
          const meta = [file.rel_path, humanSize(file.size_bytes)];
          if (memberCount !== undefined && memberCount > 0) {
            meta.push(`${memberCount} versions in family`);
          }
          const sourceCell = (
            <div>
              <Text size="sm" fw={500}>
                {file.filename}
              </Text>
              <Text size="xs" c="dimmed" ff="monospace">
                {meta.join(" · ")}
              </Text>
            </div>
          );

          if (isQuarantine) {
            const reason = file.scan_flags.reason ?? "unreadable";
            return (
              <Table.Tr key={file.id}>
                <Table.Td>
                  <Checkbox
                    aria-label={`Select ${file.filename}`}
                    checked={selected.has(file.id)}
                    onChange={() => onToggle(file.id)}
                  />
                </Table.Td>
                <Table.Td>{sourceCell}</Table.Td>
                {/* span the 6 classification columns (identifier..confidence) */}
                <Table.Td colSpan={6}>
                  <Text size="sm" c="dimmed">
                    Quarantined: {reason}
                    {file.scan_flags.detail ? ` — ${file.scan_flags.detail}` : ""}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Button variant="subtle" size="compact-sm" onClick={() => onOpenDetail(file.id)}>
                    Open
                  </Button>
                </Table.Td>
              </Table.Tr>
            );
          }

          return (
            <Table.Tr key={file.id}>
              <Table.Td>
                <Checkbox
                  aria-label={`Select ${file.filename}`}
                  checked={selected.has(file.id)}
                  onChange={() => onToggle(file.id)}
                />
              </Table.Td>
              <Table.Td>{sourceCell}</Table.Td>
              <Table.Td>
                <IdentifierCell review={file.review} dupeOf={dupeMap.get(file.id) ?? null} />
              </Table.Td>
              <Table.Td>
                <KindCell
                  review={file.review}
                  classification={file.classification}
                  onConfirm={(kind) => onConfirmKind(file.id, kind)}
                />
              </Table.Td>
              <Table.Td>
                <TypeCell classification={file.classification} />
              </Table.Td>
              <Table.Td ta="center">
                <Text size="sm" ff="monospace">
                  {file.review?.clause_numbers.length
                    ? file.review.clause_numbers.join(", ")
                    : "—"}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {file.review?.process_names.length
                    ? file.review.process_names.join(", ")
                    : "—"}
                </Text>
              </Table.Td>
              <Table.Td>
                <ConfidenceCell classification={file.classification} />
              </Table.Td>
              <Table.Td>
                <Group gap={4} wrap="nowrap">
                  <Button variant="subtle" size="compact-sm" onClick={() => onRowAction(file, "accept")}>
                    Accept
                  </Button>
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    onClick={() => onRowAction(file, "correct")}
                  >
                    Correct ▾
                  </Button>
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    onClick={() => onRowAction(file, "exclude")}
                  >
                    Exclude
                  </Button>
                  <Button variant="subtle" size="compact-sm" onClick={() => onOpenDetail(file.id)}>
                    Open
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}
```

> Note: `COLUMNS` (=9) documents the header width for the quarantine `colSpan` math (1 select + 1 source + 6 spanned classification + 1 action). If the per-file cell components (`KindCell`/`ConfidenceCell`/`IdentifierCell`/`TypeCell`, Tasks 5–6) aren't yet created when running this task standalone, run Tasks 5–6 first — they are upstream in the plan order, so under sequential execution they already exist.

- [ ] **Step 8: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/TriageTable.test.tsx`
Expected: PASS (13 tests).

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/features/ingestion/TriageTable.tsx apps/web/src/features/ingestion/TriageTable.test.tsx apps/web/src/features/ingestion/TriagePagination.tsx apps/web/src/features/ingestion/TriagePagination.test.tsx
git commit -m "feat(s-ing-4b): TriageTable (9-col paged grid, quarantine/empty/loading) + TriagePagination"
```

---

### Task 10: BulkActionBar

**Files:**
- Create: `apps/web/src/features/ingestion/BulkActionBar.tsx`
- Test: `apps/web/src/features/ingestion/BulkActionBar.test.tsx`

`BulkActionBar` is the selection-active context bar (mockup `#screen-ingestion` §6, ~lines 4347–4357). It is **purely presentational** — it receives the live selection `count` and four handlers as props (selection state + the actual mutation calls live in `ReviewCockpit`, Task 14, which wires these handlers to `useBulkDecision`). It renders **only when `count > 0`** (`return null` otherwise). It surfaces the five bulk actions over the current selection plus the separate selector-based **Bulk accept all High** affordance, and reinforces R10 with the "setting kind here counts as your confirmation" caption near the Confirm-kind menu.

Binds to the LOCKED registry signature:
`BulkActionBar({ count: number, onBulk: (action: ImportDecisionAction, after?: ImportDecisionAfter) => void, onConfirmKind: (kind: ConfirmedKind) => void, onAcceptAllHigh: () => void })`.

Behavior contract (from the spec §5.4 + D-5 + the registry):
- **Confirm kind ▾** → a Mantine `Menu` with `Document` / `Record` items → `onConfirmKind("DOCUMENT")` / `onConfirmKind("RECORD")`. (This is a kind-confirm over the *selection*, distinct from Bulk-accept-all-High which never confirms kind.)
- **Correct to type ▾** → a small `Menu` of representative type codes → `onBulk("correct", { type_code })`.
- **Reassign owner ▾** / **Set clause ▾** → `onBulk("correct", { owner: … })` / `onBulk("correct", { clause_numbers: [...] })` stubs (a `Menu` with one representative item each so the test can trigger them with a representative `after`).
- **Exclude** (danger) → `onBulk("exclude")` (no `after`).
- **Bulk accept all High ✓** (separate, secondary) → `onAcceptAllHigh()` — the selector-based whole-bucket accept; does **NOT** confirm kind.
- A caption "setting kind here counts as your confirmation" sits near the Confirm-kind menu (R10 reinforcement).
- All controls use Mantine props / `var(--es-*)` tokens — no hardcoded hex. Distinct `aria-label`s (the bar's labels must not collide with row-badge labels — keep them action-scoped, e.g. `"Confirm kind for selected"`).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/BulkActionBar.test.tsx`:

```tsx
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { BulkActionBar } from "./BulkActionBar";

function setup(count: number) {
  const onBulk = vi.fn();
  const onConfirmKind = vi.fn();
  const onAcceptAllHigh = vi.fn();
  const utils = renderWithProviders(
    <BulkActionBar
      count={count}
      onBulk={onBulk}
      onConfirmKind={onConfirmKind}
      onAcceptAllHigh={onAcceptAllHigh}
    />,
  );
  return { ...utils, onBulk, onConfirmKind, onAcceptAllHigh };
}

test("renders nothing when no rows are selected", () => {
  const { container } = setup(0);
  expect(container).toBeEmptyDOMElement();
});

test("shows the selected-item count when rows are selected", () => {
  const { getByText } = setup(3);
  expect(getByText(/3 items selected/)).toBeInTheDocument();
});

test("Confirm kind → Document confirms DOCUMENT for the selection (R10 act)", async () => {
  const u = userEvent.setup();
  const { getByRole, onConfirmKind } = setup(3);
  await u.click(getByRole("button", { name: /confirm kind/i }));
  await u.click(getByRole("menuitem", { name: "Document" }));
  expect(onConfirmKind).toHaveBeenCalledExactlyOnceWith("DOCUMENT");
});

test("Exclude posts a bulk exclude over the selection", async () => {
  const u = userEvent.setup();
  const { getByRole, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /exclude/i }));
  expect(onBulk).toHaveBeenCalledExactlyOnceWith("exclude");
});

test("Correct to type → an item posts a correct decision with the chosen type", async () => {
  const u = userEvent.setup();
  const { getByRole, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /correct to type/i }));
  await u.click(getByRole("menuitem", { name: "SOP" }));
  expect(onBulk).toHaveBeenCalledExactlyOnceWith("correct", { type_code: "SOP" });
});

test("Bulk accept all High triggers the selector-based accept (does NOT confirm kind)", async () => {
  const u = userEvent.setup();
  const { getByRole, onAcceptAllHigh, onConfirmKind, onBulk } = setup(3);
  await u.click(getByRole("button", { name: /bulk accept all high/i }));
  expect(onAcceptAllHigh).toHaveBeenCalledOnce();
  expect(onConfirmKind).not.toHaveBeenCalled();
  expect(onBulk).not.toHaveBeenCalled();
});

test("reinforces R10 — setting kind counts as confirmation", () => {
  const { getByText } = setup(3);
  expect(getByText(/setting .*kind.* counts as your confirmation/i)).toBeInTheDocument();
});

test("has no a11y violations", async () => {
  const { container } = setup(3);
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/BulkActionBar.test.tsx`
Expected: FAIL — `./BulkActionBar` does not exist (module-not-found).

- [ ] **Step 3: Implement `BulkActionBar.tsx`**

Create `apps/web/src/features/ingestion/BulkActionBar.tsx`:

```tsx
import { Button, Group, Menu, Paper, Text } from "@mantine/core";
import type { ImportDecisionAction, ImportDecisionAfter } from "../../lib/types";

// The selection-active context bar (mockup #screen-ingestion §6). Presentational only: ReviewCockpit
// (Task 14) owns the selection Set + wires these handlers to the Task-4 mutations (one Idempotency-Key
// per bulk op). Renders nothing when nothing is selected. R10 (D-5): "Confirm kind" is a kind-confirm
// over the *selection*; "Bulk accept all High" is the selector-based whole-bucket accept and must NOT
// confirm kind. Theme tokens via Mantine props / var(--es-*) only — never hardcoded hex.

// Representative corrective choices for the v1 bulk menus. The full picklist (driven by reference-data)
// is a follow-on; these cover the operator journey + keep the bar entirely client-side.
const TYPE_CHOICES = ["SOP", "WI", "FORM", "POLICY"] as const;

export function BulkActionBar({
  count,
  onBulk,
  onConfirmKind,
  onAcceptAllHigh,
}: {
  count: number;
  onBulk: (action: ImportDecisionAction, after?: ImportDecisionAfter) => void;
  onConfirmKind: (kind: "DOCUMENT" | "RECORD") => void;
  onAcceptAllHigh: () => void;
}) {
  if (count <= 0) return null;

  return (
    <Paper
      component="section"
      aria-label="Bulk actions"
      withBorder
      p="sm"
      mb="md"
      style={{ borderColor: "var(--es-accent)", background: "var(--es-surface-2)" }}
    >
      <Group gap="sm" wrap="wrap" align="center">
        <Text size="sm">
          <Text span fw={700}>
            {count} items selected
          </Text>{" "}
          in this view
        </Text>

        {/* Confirm kind — the R10 human act over the selection. */}
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Confirm kind for selected">
              Confirm kind ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onConfirmKind("DOCUMENT")}>Document</Menu.Item>
            <Menu.Item onClick={() => onConfirmKind("RECORD")}>Record</Menu.Item>
          </Menu.Dropdown>
        </Menu>

        {/* Correct to type — a representative type picklist → correct decision with `after.type_code`. */}
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Correct to type for selected">
              Correct to type ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            {TYPE_CHOICES.map((code) => (
              <Menu.Item key={code} onClick={() => onBulk("correct", { type_code: code })}>
                {code}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>

        {/* Reassign owner — representative item; the full owner picker is a follow-on. */}
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Reassign owner for selected">
              Reassign owner ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onBulk("correct", { owner: "Quality Manager" })}>
              Quality Manager
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>

        {/* Set clause — representative item; the full clause tree is a follow-on. */}
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Set clause for selected">
              Set clause ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onBulk("correct", { clause_numbers: ["8.4"] })}>
              8.4 — Control of external provision
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>

        <Button
          variant="subtle"
          size="xs"
          color="red"
          aria-label="Exclude selected"
          onClick={() => onBulk("exclude")}
        >
          Exclude
        </Button>

        {/* Selector-based whole-bucket accept — distinct from Confirm kind; never confirms kind (D-5). */}
        <Button
          variant="default"
          size="xs"
          aria-label="Bulk accept all High"
          onClick={onAcceptAllHigh}
        >
          Bulk accept all High ✓
        </Button>

        <Text size="xs" c="dimmed" style={{ marginInlineStart: "auto" }}>
          Bulk actions are fully keyboard-driven · setting <b>kind</b> here counts as your confirmation
        </Text>
      </Group>
    </Paper>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/BulkActionBar.test.tsx`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/BulkActionBar.tsx apps/web/src/features/ingestion/BulkActionBar.test.tsx
git commit -m "feat(s-ing-4b): BulkActionBar — selection context bar with R10-safe confirm-kind + selector accept"
```

---

### Task 11: ItemDetailDrawer

**Files:**
- Create: `apps/web/src/features/ingestion/ItemDetailDrawer.tsx`
- Test: `apps/web/src/features/ingestion/ItemDetailDrawer.test.tsx`

> Binds to: the **Component Contract Registry** `ItemDetailDrawer({ runId, fileId, onClose, onConfirmKind, onDecision, onSplit })` (Task-14 `ReviewCockpit` owns `fileId`/handlers; this leaf is presentational and reads its own detail). Reuses `app/shell/DetailDrawer` (`opened`/`onClose`/`title`/`children`), `useImportFile(runId, fileId)` + `useDecisions(runId)` (Task 3), and the Task-1 types (`ImportFileDetail`, `ImportClassificationEvidence`, `ImportExtract`, `ImportDedupMembership`, `ImportProposalNode`, `ImportDecision`) + fixtures (`ingestionFileDetailFixture`, `ingestionDecisionsFixture`). The drawer is **open iff `fileId !== null`**; `useImportFile` is `enabled` only when `fileId` is set, so a `null` file mounts nothing. Per-item actions call the handlers the cockpit passes down — this leaf never calls a mutation hook itself. Split is shown **only** when `detail.dedup.in_version_family || detail.dedup.in_exact_cluster || detail.dedup.in_near_cluster`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/ItemDetailDrawer.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import {
  ingestionFileDetailFixture,
  ingestionRunFixture,
} from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ItemDetailDrawer } from "./ItemDetailDrawer";

const RID = ingestionRunFixture.id;
const FID = ingestionFileDetailFixture.id;

function noop() {}

test("renders nothing actionable when fileId is null (drawer closed)", () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={null}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // No detail dialog is shown for a null file.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("renders the filename, identifier, and a classification evidence explanation", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  expect(
    await screen.findByText("SOP-PUR-014 Purchasing.docx"),
  ).toBeInTheDocument();
  // The proposed identifier surfaces.
  expect(screen.getAllByText(/SOP-PUR-014/).length).toBeGreaterThan(0);
  // The classification dimension/explanation list is present (evidence array, guarded for null).
  expect(screen.getByText(/preserved/i)).toBeInTheDocument();
});

test("renders the version-family / dedup membership", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // ingestionFileDetailFixture.dedup.in_version_family === true → membership copy shows.
  expect(await screen.findByText(/version family/i)).toBeInTheDocument();
});

test("renders the extraction status (page count) and the proposal target path", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // extract.page_count === 3
  expect(await screen.findByText(/3 pages/i)).toBeInTheDocument();
  // proposal.target_ia_path
  expect(screen.getByText(/DO\/08-Operation/)).toBeInTheDocument();
});

test("clicking Accept calls onDecision with action \"accept\"", async () => {
  const user = userEvent.setup();
  const onDecision = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={onDecision}
      onSplit={noop}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Accept item" }));
  expect(onDecision).toHaveBeenCalledWith({ action: "accept" });
});

test("clicking Confirm kind calls onConfirmKind with DOCUMENT", async () => {
  const user = userEvent.setup();
  const onConfirmKind = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={onConfirmKind}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Confirm kind as Document" }));
  expect(onConfirmKind).toHaveBeenCalledWith("DOCUMENT");
});

test("the Split control shows for a grouped file and calls onSplit", async () => {
  const user = userEvent.setup();
  const onSplit = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={onSplit}
    />,
  );
  const split = await screen.findByRole("button", { name: "Split out of group" });
  await user.click(split);
  expect(onSplit).toHaveBeenCalledTimes(1);
});

test("the Split control is hidden for an ungrouped file", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id/files/:fid", () =>
      HttpResponse.json({
        ...ingestionFileDetailFixture,
        dedup: {
          in_exact_cluster: false,
          in_near_cluster: false,
          is_canonical: null,
          redundant_of_file_id: null,
          in_version_family: false,
          is_effective: null,
          superseded_by_file_id: null,
        },
      }),
    ),
  );
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(screen.queryByRole("button", { name: "Split out of group" })).not.toBeInTheDocument();
});

test("renders a decision-history entry filtered to this file", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id/decisions", () =>
      HttpResponse.json({
        run_id: RID,
        decisions: [
          {
            id: "d1",
            action: "accept",
            file_id: FID,
            cluster_id: null,
            target_kind: "DOCUMENT",
            before: null,
            after: { kind: "DOCUMENT" },
            reason: null,
            decided_by: "bbbb1111-1111-1111-1111-111111111111",
            decided_at: "2026-06-08T11:00:00+00:00",
          },
          {
            id: "d2",
            action: "exclude",
            file_id: "f0000000-0000-0000-0000-0000000000a9",
            cluster_id: null,
            target_kind: "DOCUMENT",
            before: null,
            after: null,
            reason: null,
            decided_by: "bbbb1111-1111-1111-1111-111111111111",
            decided_at: "2026-06-08T11:01:00+00:00",
          },
        ],
      }),
    ),
  );
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // This file's "accept" decision is listed; the other file's "exclude" is filtered out.
  expect(await screen.findByText(/accept/)).toBeInTheDocument();
  expect(screen.queryByText(/exclude/)).not.toBeInTheDocument();
});

test("has no axe violations when open", async () => {
  const { container } = renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/ItemDetailDrawer.test.tsx`
Expected: FAIL — `./ItemDetailDrawer` does not exist.

- [ ] **Step 3: Implement `ItemDetailDrawer`**

Create `apps/web/src/features/ingestion/ItemDetailDrawer.tsx`:

```tsx
import { Badge, Button, Divider, Group, Loader, Stack, Text } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import type {
  ConfirmedKind,
  ImportClassificationEvidence,
  ImportDecision,
  ImportDecisionAction,
  ImportDedupMembership,
  ImportExtract,
  ImportProposalNode,
} from "../../lib/types";
import { useDecisions, useImportFile } from "./hooks";

// The per-item review detail (DP-3, reuses app/shell/DetailDrawer for focus-trap + Esc + ARIA dialog).
// Presentational: ReviewCockpit (Task 14) owns the active fileId + the decision handlers; this leaf
// reads its own detail (useImportFile, enabled only when fileId is set) + the run decision log
// (filtered to this file). The detail read is import.review-gated like the page → no separate 403.
// Per-item actions call the handlers passed down (the cockpit threads them through the Task 3-4 hooks);
// Split is offered only when the file belongs to a cluster/family (server-authoritative — D-4).
export function ItemDetailDrawer({
  runId,
  fileId,
  onClose,
  onConfirmKind,
  onDecision,
  onSplit,
}: {
  runId: string;
  fileId: string | null;
  onClose: () => void;
  onConfirmKind: (kind: ConfirmedKind) => void;
  onDecision: (input: { action: ImportDecisionAction }) => void;
  onSplit: () => void;
}) {
  const { data: detail, isLoading } = useImportFile(runId, fileId);
  const { data: decisionLog } = useDecisions(runId);
  // Filter the append-only run decision log to this file (guard the array under noUncheckedIndexedAccess).
  const history: ImportDecision[] = (decisionLog?.decisions ?? []).filter(
    (d) => d.file_id === fileId,
  );

  return (
    <DetailDrawer opened={fileId !== null} onClose={onClose} title="Item detail">
      {isLoading || !detail ? (
        <Loader />
      ) : (
        <Stack gap="md">
          {/* Header — filename + proposed identifier (DP-5 shape, quiet absence → "—"). */}
          <Stack gap={2}>
            <Text fw={600}>{detail.filename}</Text>
            <Text ff="monospace" size="sm" c="dimmed">
              {detail.review?.identifier ?? detail.proposal?.proposed_identifier ?? "— no identifier"}
            </Text>
            <Text size="xs" c="dimmed">
              {detail.rel_path}
            </Text>
          </Stack>

          {/* Per-item actions — call the handlers the cockpit threads through the hooks (Task 3-4). */}
          <Group gap="xs">
            <Button size="xs" aria-label="Accept item" onClick={() => onDecision({ action: "accept" })}>
              Accept
            </Button>
            <Button
              size="xs"
              variant="default"
              aria-label="Exclude item"
              onClick={() => onDecision({ action: "exclude" })}
            >
              Exclude
            </Button>
            <Button
              size="xs"
              variant="default"
              aria-label="Defer item"
              onClick={() => onDecision({ action: "defer" })}
            >
              Defer
            </Button>
            <Button
              size="xs"
              variant="light"
              aria-label="Confirm kind as Document"
              onClick={() => onConfirmKind("DOCUMENT")}
            >
              Confirm kind
            </Button>
            {(detail.dedup.in_version_family ||
              detail.dedup.in_exact_cluster ||
              detail.dedup.in_near_cluster) && (
              <Button
                size="xs"
                variant="default"
                aria-label="Split out of group"
                onClick={onSplit}
              >
                Split out of group
              </Button>
            )}
          </Group>

          <Divider label="Classification" labelPosition="left" />
          <ClassificationEvidence evidence={detail.classification?.evidence} />

          <Divider label="Extraction" labelPosition="left" />
          <ExtractSummary extract={detail.extract} />

          <Divider label="Group membership" labelPosition="left" />
          <DedupSummary dedup={detail.dedup} />

          <Divider label="Proposal" labelPosition="left" />
          <ProposalSummary proposal={detail.proposal} />

          <Divider label="Decision history" labelPosition="left" />
          {history.length === 0 ? (
            <Text size="sm" c="dimmed">
              No decisions yet for this item.
            </Text>
          ) : (
            <Stack gap={4}>
              {history.map((d) => (
                <Group key={d.id} gap="xs" wrap="nowrap">
                  <Badge variant="light" size="sm">
                    {d.action}
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {d.decided_at.slice(0, 10)}
                  </Text>
                </Group>
              ))}
            </Stack>
          )}
        </Stack>
      )}
    </DetailDrawer>
  );
}

// The 4-dimension classifier signals (evidence array is detail-endpoint-only → guard for null/empty).
function ClassificationEvidence({
  evidence,
}: {
  evidence: ImportClassificationEvidence[] | undefined;
}) {
  if (!evidence || evidence.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No classification evidence.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {evidence.map((e, i) => (
        <Group key={`${e.dimension}-${i}`} gap="xs" wrap="nowrap" align="flex-start">
          <Badge variant="outline" size="sm">
            {e.dimension}
          </Badge>
          <Text size="sm">{e.candidate}</Text>
          <Text size="xs" c="dimmed">
            {e.explanation} (weight {e.weight})
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

function ExtractSummary({ extract }: { extract: ImportExtract | null }) {
  if (!extract) {
    return (
      <Text size="sm" c="dimmed">
        Not extracted.
      </Text>
    );
  }
  return (
    <Group gap="lg">
      <Text size="sm">Status: {extract.status}</Text>
      <Text size="sm">{extract.page_count ?? 0} pages</Text>
      {extract.ocr_used && (
        <Text size="sm" c="dimmed">
          OCR
        </Text>
      )}
    </Group>
  );
}

function DedupSummary({ dedup }: { dedup: ImportDedupMembership }) {
  const inGroup = dedup.in_version_family || dedup.in_exact_cluster || dedup.in_near_cluster;
  if (!inGroup) {
    return (
      <Text size="sm" c="dimmed">
        Not part of a group.
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      {dedup.in_version_family && (
        <Text size="sm">
          In a version family{dedup.is_effective ? " (the effective version)" : ""}.
        </Text>
      )}
      {(dedup.in_exact_cluster || dedup.in_near_cluster) && (
        <Text size="sm">
          In a duplicate cluster{dedup.is_canonical ? " (the canonical copy)" : ""}.
        </Text>
      )}
    </Stack>
  );
}

function ProposalSummary({ proposal }: { proposal: ImportProposalNode | null }) {
  if (!proposal) {
    return (
      <Text size="sm" c="dimmed">
        No proposal.
      </Text>
    );
  }
  const conflicts = Object.keys(proposal.conflict_flags);
  return (
    <Stack gap={2}>
      <Text size="sm">Identifier: {proposal.proposed_identifier ?? "—"}</Text>
      <Text size="sm">Target path: {proposal.target_ia_path ?? "—"}</Text>
      {conflicts.length > 0 && (
        <Group gap={4}>
          {conflicts.map((c) => (
            <Badge key={c} variant="light" color="var(--es-danger)" size="sm">
              {c}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/ItemDetailDrawer.test.tsx`
Expected: PASS (10 tests — null-closed, header+evidence, family membership, extract+proposal, Accept→onDecision, Confirm-kind→onConfirmKind, Split shown+called, Split hidden ungrouped, filtered decision history, axe clean).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/ItemDetailDrawer.tsx apps/web/src/features/ingestion/ItemDetailDrawer.test.tsx
git commit -m "feat(s-ing-4b): ItemDetailDrawer (classification evidence · extract · dedup · proposal · history · per-item actions)"
```

---

### Task 12: MergeMenu

**Files:**
- Create: `apps/web/src/features/ingestion/MergeMenu.tsx`
- Test: `apps/web/src/features/ingestion/MergeMenu.test.tsx`

The `Merge ▾` control (mockup `#screen-ingestion` §8b — the conflict/duplicate row). Given the `selectedFileIds`, it opens a Mantine `Popover` to pick the **effective member** (a `Radio.Group` over `selectedFileIds`, defaulting to the first) and toggle **Reconstruct revision chain** (default OFF — R10), then submits `useMerge(runId).mutate({ body: { file_ids, effective_file_id, reconstruct_revision_chain }, idempotencyKey: crypto.randomUUID() })` and calls `onDone()` on success. Merge is server-authoritative: the hook invalidates + refetches — **never** reshape the cache here. The trigger is disabled (with a hint) when fewer than 2 files are selected.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/MergeMenu.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { MergeMenu } from "./MergeMenu";

const RID = ingestionRunFixture.id;
const A = "f0000000-0000-0000-0000-0000000000a1";
const B = "f0000000-0000-0000-0000-0000000000a4";

test("submitting posts file_ids + the chosen effective member + reconstruct flag, then calls onDone", async () => {
  const user = userEvent.setup();
  let body: unknown = null;
  let seenKey: string | null = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      seenKey = request.headers.get("Idempotency-Key");
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  const onDone = vi.fn();
  renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={onDone} />,
  );
  await user.click(screen.getByRole("button", { name: "Merge" }));
  // default effective member is the first id; choose the second instead.
  await user.click(await screen.findByRole("radio", { name: `Effective: ${B}` }));
  await user.click(screen.getByRole("checkbox", { name: "Reconstruct revision chain" }));
  await user.click(screen.getByRole("button", { name: "Merge into one family" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(body).toEqual({
    file_ids: [A, B],
    effective_file_id: B,
    reconstruct_revision_chain: true,
  });
  expect(seenKey).not.toBeNull();
});

test("defaults the effective member to the first id and reconstruct OFF (R10)", async () => {
  const user = userEvent.setup();
  let body: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />,
  );
  await user.click(screen.getByRole("button", { name: "Merge" }));
  await user.click(await screen.findByRole("button", { name: "Merge into one family" }));
  await waitFor(() =>
    expect(body).toEqual({
      file_ids: [A, B],
      effective_file_id: A,
      reconstruct_revision_chain: false,
    }),
  );
});

test("the trigger is disabled with under 2 selected files", () => {
  renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A]} onDone={() => {}} />,
  );
  expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
  expect(screen.getByText("Select 2 or more files to merge.")).toBeInTheDocument();
});

test("has no axe violations (closed + open)", async () => {
  const user = userEvent.setup();
  const view = renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />,
  );
  expect(await axe(view.container)).toHaveNoViolations();
  await user.click(screen.getByRole("button", { name: "Merge" }));
  await screen.findByRole("button", { name: "Merge into one family" });
  expect(await axe(document.body)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/MergeMenu.test.tsx`
Expected: FAIL — `./MergeMenu` does not exist.

- [ ] **Step 3: Implement `MergeMenu`**

Create `apps/web/src/features/ingestion/MergeMenu.tsx`:

```tsx
import { Button, Checkbox, Popover, Radio, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useMerge } from "./hooks";

// The mockup §8b "Merge ▾" control. Given ≥2 selected files, pick the effective member (Radio over
// the selection, default the first) + the reconstruct-revision-chain opt-in (default OFF — R10), then
// submit a merge intent. Merge is server-authoritative: useMerge invalidates + refetches; we NEVER
// reshape the cache here. ONE Idempotency-Key per merge (crypto.randomUUID()). Disabled (with a hint)
// under 2 selected files.
export function MergeMenu({
  runId,
  selectedFileIds,
  onDone,
}: {
  runId: string;
  selectedFileIds: string[];
  onDone: () => void;
}) {
  const [opened, setOpened] = useState(false);
  // Default the effective member to the first selected id (degrade to "" under noUncheckedIndexedAccess).
  const [effective, setEffective] = useState<string>(selectedFileIds[0] ?? "");
  const [reconstruct, setReconstruct] = useState(false);
  const merge = useMerge(runId);
  const tooFew = selectedFileIds.length < 2;

  function submit() {
    const effective_file_id = effective || (selectedFileIds[0] ?? "");
    merge.mutate(
      {
        body: {
          file_ids: selectedFileIds,
          effective_file_id,
          reconstruct_revision_chain: reconstruct,
        },
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: () => {
          setOpened(false);
          onDone();
        },
      },
    );
  }

  return (
    <Stack gap={4}>
      <Popover opened={opened} onChange={setOpened} position="bottom-start" withArrow trapFocus>
        <Popover.Target>
          <Button
            size="xs"
            variant="default"
            disabled={tooFew}
            onClick={() => setOpened((o) => !o)}
          >
            Merge
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap="sm" w={320}>
            <Radio.Group
              label="Effective member"
              description="The version that stays Effective; the rest are superseded."
              value={effective}
              onChange={setEffective}
            >
              <Stack gap={4} mt={4}>
                {selectedFileIds.map((id) => (
                  <Radio key={id} value={id} label={id} aria-label={`Effective: ${id}`} />
                ))}
              </Stack>
            </Radio.Group>
            <Checkbox
              label="Reconstruct revision chain"
              description="Opt-in (off by default). Materializes the prior versions as a revision history."
              checked={reconstruct}
              onChange={(e) => setReconstruct(e.currentTarget.checked)}
            />
            <Button onClick={submit} loading={merge.isPending}>
              Merge into one family
            </Button>
          </Stack>
        </Popover.Dropdown>
      </Popover>
      {tooFew && (
        <Text size="xs" c="dimmed">
          Select 2 or more files to merge.
        </Text>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/MergeMenu.test.tsx`
Expected: PASS (4 tests). Note: the radio `aria-label` (`Effective: <id>`) is distinct per row — no duplicate label across the group, so `getByRole("radio", { name })` resolves to a single match.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/MergeMenu.tsx apps/web/src/features/ingestion/MergeMenu.test.tsx
git commit -m "feat(s-ing-4b): MergeMenu — pick effective member + reconstruct opt-in → useMerge"
```

---

### Task 13: PreCommitChecklist

**Files:**
- Create: `apps/web/src/features/ingestion/PreCommitChecklist.tsx`
- Test: `apps/web/src/features/ingestion/PreCommitChecklist.test.tsx`

The locked registry signature is `PreCommitChecklist({ checklist: ImportChecklist, onShowBlocker: (blocker: ImportChecklistBlocker) => void })` — blocking RAG rows (danger) at the top + advisory rows (never danger). It is purely presentational: it reads the already-fetched `checklist` (the parent passes `useChecklist(runId).data`) and bubbles a "Show items" intent up via `onShowBlocker(blocker)` (the cockpit owns the table-filter side-effect). Every `advisory.*` field is guarded for `undefined` (the shape is `additionalProperties: true` in the contract); the ★ coverage reuses the `CoverageBadge` glyph language as a `"<satisfied> / <total> satisfied"` caption, guarded when `star_coverage` is missing. Advisory rows are **never** a hard commit block — the kind-confirm row is a warning, never danger (D-3 / R10).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/PreCommitChecklist.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportChecklist } from "../../lib/types";
import { ingestionChecklistFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { PreCommitChecklist } from "./PreCommitChecklist";

const CHECKLIST = ingestionChecklistFixture as unknown as ImportChecklist;

test("renders the header + calm advisory subtitle", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  expect(screen.getByText("Pre-commit checklist")).toBeInTheDocument();
  expect(
    screen.getByText(
      "A calm gate before anything becomes controlled — advisory, never an auto-compliance judgment.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("Commit can proceed with gaps.")).toBeInTheDocument();
});

test("renders the duplicate-identifier blocking row with a Show items button that calls onShowBlocker", async () => {
  const user = userEvent.setup();
  const onShowBlocker = vi.fn();
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={onShowBlocker} />);
  const blockerRow = screen.getByLabelText("Blocking: Duplicate-identifier conflicts");
  expect(blockerRow).toBeInTheDocument();
  await user.click(within(blockerRow).getByRole("button", { name: "Show items" }));
  expect(onShowBlocker).toHaveBeenCalledTimes(1);
  expect(onShowBlocker).toHaveBeenCalledWith(CHECKLIST.blocking[0]);
});

test("renders the ★ mandatory ISO clause coverage as '17 / 20 satisfied' (advisory, not a blocker)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const coverageRow = screen.getByLabelText("Advisory: Mandatory ISO clause coverage");
  expect(within(coverageRow).getByText("17 / 20 satisfied")).toBeInTheDocument();
});

test("renders the kind-confirmed advisory row as '1 / 4' (warning, never danger)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const kindRow = screen.getByLabelText("Advisory: Kind confirmed on every item");
  expect(within(kindRow).getByText("1 / 4")).toBeInTheDocument();
  // it carries no Show-items affordance — advisory rows never read as blockers
  expect(within(kindRow).queryByRole("button", { name: "Show items" })).not.toBeInTheDocument();
});

test("renders the Unknown / Low triaged advisory row from unknown_low", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  const triagedRow = screen.getByLabelText("Advisory: Unknown / Low triaged");
  expect(within(triagedRow).getByText("2")).toBeInTheDocument();
});

test("only blocking rows expose a Show items button (advisory rows do not)", () => {
  renderWithProviders(<PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />);
  // exactly one blocker → exactly one Show items button across the whole card
  expect(screen.getAllByRole("button", { name: "Show items" })).toHaveLength(1);
});

test("degrades calmly when advisory.star_coverage is undefined", () => {
  const noCoverage: ImportChecklist = {
    ...CHECKLIST,
    advisory: { unknown_low: 0, kind_unconfirmed: 4 },
  };
  renderWithProviders(<PreCommitChecklist checklist={noCoverage} onShowBlocker={() => {}} />);
  const coverageRow = screen.getByLabelText("Advisory: Mandatory ISO clause coverage");
  expect(within(coverageRow).getByText("— / — satisfied")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <PreCommitChecklist checklist={CHECKLIST} onShowBlocker={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/PreCommitChecklist.test.tsx`
Expected: FAIL — `./PreCommitChecklist` does not exist (module not found).

- [ ] **Step 3: Implement `PreCommitChecklist`**

Create `apps/web/src/features/ingestion/PreCommitChecklist.tsx`:

```tsx
import { Button, Card, Group, Stack, Text } from "@mantine/core";
import type { ImportChecklist, ImportChecklistBlocker } from "../../lib/types";

// A human label for each known blocker `code`; an unknown code degrades to a title-cased fallback so
// a future backend blocker type never renders a raw enum token (the "tolerate additive" rule).
const BLOCKER_LABELS: Record<string, string> = {
  duplicate_identifier_within_import: "Duplicate-identifier conflicts",
  collides_with_vault_doc: "Collides with an existing vault document",
  singleton_type_already_effective: "Singleton type already Effective",
  ambiguous_unresolved: "Ambiguous classification unresolved",
};

function blockerLabel(code: string): string {
  return (
    BLOCKER_LABELS[code] ??
    code.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  );
}

// One danger row per blocking[] entry: a ✕ glyph + the human label + a "Show items" button that
// bubbles the blocker up (ReviewCockpit filters the table to the offenders — this leaf is purely
// presentational). DP-7: the glyph carries the meaning, color is the redundant third channel.
function BlockerRow({
  blocker,
  onShowBlocker,
}: {
  blocker: ImportChecklistBlocker;
  onShowBlocker: (b: ImportChecklistBlocker) => void;
}) {
  const label = blockerLabel(blocker.code);
  return (
    <Group
      justify="space-between"
      wrap="nowrap"
      py={6}
      aria-label={`Blocking: ${label}`}
      style={{ borderBottom: "1px solid var(--es-border)" }}
    >
      <Group gap="xs" wrap="nowrap">
        <Text span aria-hidden="true" c="var(--es-danger)" fw={700}>
          ✕
        </Text>
        <Text span size="sm">
          {label}
        </Text>
      </Group>
      <Button variant="light" color="var(--es-danger)" size="compact-sm" onClick={() => onShowBlocker(blocker)}>
        Show items
      </Button>
    </Group>
  );
}

// A non-blocking advisory row: a leading glyph + a label + a right-aligned value caption. Never
// danger — these are completeness signals, not commit blocks (D-3 / R10). `tone` picks the calm
// glyph/color; the value is always pre-formatted + undefined-guarded by the caller.
function AdvisoryRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "neutral";
}) {
  const glyph = tone === "ok" ? "✓" : tone === "warn" ? "▲" : "•";
  const color =
    tone === "ok" ? "var(--es-success)" : tone === "warn" ? "var(--es-warning)" : "var(--es-text-muted)";
  return (
    <Group
      justify="space-between"
      wrap="nowrap"
      py={6}
      aria-label={`Advisory: ${label}`}
      style={{ borderBottom: "1px solid var(--es-border)" }}
    >
      <Group gap="xs" wrap="nowrap">
        <Text span aria-hidden="true" c={color} fw={700}>
          {glyph}
        </Text>
        <Text span size="sm">
          {label}
        </Text>
      </Group>
      <Text span size="sm" fw={600} c="dimmed">
        {value}
      </Text>
    </Group>
  );
}

export function PreCommitChecklist({
  checklist,
  onShowBlocker,
}: {
  checklist: ImportChecklist;
  onShowBlocker: (blocker: ImportChecklistBlocker) => void;
}) {
  const blocking = checklist.blocking ?? [];
  const review = checklist.review;
  const advisory = checklist.advisory ?? {};

  // ★ coverage — guard the whole sub-object AND each field (additionalProperties:true → may be absent).
  const cov = advisory.star_coverage ?? undefined;
  const covSatisfied = cov?.satisfied ?? undefined;
  const covTotal = cov?.total ?? undefined;
  const covValue = `${covSatisfied ?? "—"} / ${covTotal ?? "—"} satisfied`;

  // kind-confirmed — warn while any item is still unconfirmed (advisory, never a hard block).
  const kindConfirmed = review?.kind_confirmed ?? 0;
  const keepItems = review?.keep_items ?? 0;
  const kindIncomplete = kindConfirmed < keepItems;

  const unknownLow = advisory.unknown_low ?? 0;

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap={2} mb="sm">
        <Text fw={600}>Pre-commit checklist</Text>
        <Text size="sm" c="dimmed">
          A calm gate before anything becomes controlled — advisory, never an auto-compliance judgment.
        </Text>
      </Stack>

      <Stack gap={0}>
        {blocking.map((b, i) => (
          <BlockerRow key={`${b.code}-${i}`} blocker={b} onShowBlocker={onShowBlocker} />
        ))}
        <AdvisoryRow
          label="Kind confirmed on every item"
          value={`${kindConfirmed} / ${keepItems}`}
          tone={kindIncomplete ? "warn" : "ok"}
        />
        <AdvisoryRow label="Mandatory ISO clause coverage" value={covValue} tone="neutral" />
        <AdvisoryRow label="Unknown / Low triaged" value={String(unknownLow)} tone="neutral" />
      </Stack>

      <Text size="sm" c="dimmed" mt="sm" maw="70ch">
        Mandatory-coverage is a non-blocking projection of the Compliance Checklist onto the confirmed
        set — missing items may simply not exist yet. Commit can proceed with gaps.
      </Text>
    </Card>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/PreCommitChecklist.test.tsx`
Expected: PASS (8 tests; blocking row + Show items callback, ★ coverage "17 / 20 satisfied", kind "1 / 4" warning with no Show-items button, unknown-low "2", single Show-items button across the card, undefined-coverage degrades to "— / — satisfied", axe clean).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/PreCommitChecklist.tsx apps/web/src/features/ingestion/PreCommitChecklist.test.tsx
git commit -m "feat(s-ing-4b): PreCommitChecklist (blocking RAG rows + advisory coverage/kind/triage, onShowBlocker)"
```

---

### Task 14: CommitCard

**Files:**
- Create: `apps/web/src/features/ingestion/CommitCard.tsx`
- Test: `apps/web/src/features/ingestion/CommitCard.test.tsx`

> Binds to the LOCKED registry signature `CommitCard({ checklist: ImportChecklist, canCommit: boolean, committing: boolean, onCommit: () => void })`. The component is **presentational** — `ReviewCockpit` (Task 14-composition) computes `canCommit = can("import.commit")` and `committing` (from the `useCommitRun` mutation state) and passes them down. CommitCard does **NOT** call `usePermissions` or any hook; it only reads the props. The commit-enable predicate is the spec's D-3 rule: `checklist.ready && checklist.review.commit_ready >= 1 && canCommit` (and not `committing`). Unconfirmed kind is **advisory only** — it never appears here as a hard block. Mockup: `#screen-ingestion` §10 "On commit" card (lines 4671–4690).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/CommitCard.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportChecklist } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { CommitCard } from "./CommitCard";

// A not-ready checklist (the Task-1 fixture shape): one blocking conflict, commit_ready = 1.
const NOT_READY: ImportChecklist = {
  run_id: "10000000-0000-0000-0000-000000000001",
  status: "Reviewing",
  ready: false,
  blocking: [{ code: "duplicate_identifier_within_import" }],
  advisory: { star_coverage: { total: 20, satisfied: 17 }, unknown_low: 2, kind_unconfirmed: 4 },
  review: {
    keep_items: 4, decided: 0, accepted: 0, corrected: 0, excluded: 0, deferred: 0,
    undecided: 4, kind_confirmed: 1, commit_ready: 1,
  },
};
// A ready checklist: zero blocking, commit_ready = 3.
const READY: ImportChecklist = {
  ...NOT_READY,
  ready: true,
  blocking: [],
  review: { ...NOT_READY.review, commit_ready: 3 },
};

test("the button is disabled when the checklist is not ready", () => {
  renderWithProviders(
    <CommitCard checklist={NOT_READY} canCommit committing={false} onCommit={() => {}} />,
  );
  const btn = screen.getByRole("button", { name: /Commit 1 confirmed/ });
  expect(btn).toBeDisabled();
});

test("the button is enabled and clicking it calls onCommit when ready + commit_ready >= 1 + canCommit", async () => {
  const onCommit = vi.fn();
  const user = userEvent.setup();
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={onCommit} />,
  );
  const btn = screen.getByRole("button", { name: /Commit 3 confirmed/ });
  expect(btn).toBeEnabled();
  await user.click(btn);
  expect(onCommit).toHaveBeenCalledTimes(1);
});

test("the button is disabled while committing (and shows a loading state)", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 3 confirmed/ })).toBeDisabled();
});

test("the button is disabled when commit_ready is 0 (nothing to commit)", () => {
  const none: ImportChecklist = { ...READY, review: { ...READY.review, commit_ready: 0 } };
  renderWithProviders(
    <CommitCard checklist={none} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 0 confirmed/ })).toBeDisabled();
});

test("renders the held-by-another-role note (no enabled button) when !canCommit", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit={false} committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByText("Commit is held by another role (import.commit).")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Commit/ })).not.toBeInTheDocument();
});

test("the button label includes the commit_ready count", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Commit 3 confirmed/ })).toBeInTheDocument();
});

test("renders the provenance definition list (baseline · signature · storage · provenance)", () => {
  renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(screen.getByText("On commit")).toBeInTheDocument();
  expect(screen.getByText("Per-item, transactional, audited.")).toBeInTheDocument();
  expect(screen.getByText("Effective Rev A")).toBeInTheDocument();
  expect(screen.getByText("import_baseline")).toBeInTheDocument();
  expect(screen.getByText("WORM vault blob · content-addressed")).toBeInTheDocument();
  expect(screen.getByText("source path · sha256 · run · decided-by")).toBeInTheDocument();
  expect(screen.getByText(/3 ready/)).toBeInTheDocument();
});

test("has no axe violations (enabled, disabled, and held-by-another-role)", async () => {
  const enabled = renderWithProviders(
    <CommitCard checklist={READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(await axe(enabled.container)).toHaveNoViolations();
  enabled.unmount();

  const disabled = renderWithProviders(
    <CommitCard checklist={NOT_READY} canCommit committing={false} onCommit={() => {}} />,
  );
  expect(await axe(disabled.container)).toHaveNoViolations();
  disabled.unmount();

  const held = renderWithProviders(
    <CommitCard checklist={READY} canCommit={false} committing={false} onCommit={() => {}} />,
  );
  expect(await axe(held.container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/CommitCard.test.tsx`
Expected: FAIL — `./CommitCard` does not exist (module resolution error).

- [ ] **Step 3: Implement `CommitCard`**

Create `apps/web/src/features/ingestion/CommitCard.tsx`:

```tsx
import { Alert, Badge, Button, Card, Group, Progress, Stack, Text } from "@mantine/core";
import type { ImportChecklist } from "../../lib/types";

// The "On commit" card (mockup #screen-ingestion §10). Presentational: ReviewCockpit owns the
// permission + mutation state and passes `canCommit` (= can("import.commit")) and `committing`
// (the useCommitRun mutation's isPending) down. The commit-enable predicate is the spec D-3 rule —
// commit is enabled iff the run is `ready` (zero blocking conflicts) AND ≥1 item is commit_ready AND
// the caller holds import.commit AND a commit isn't already in flight. Unconfirmed kind is ADVISORY
// (surfaced in PreCommitChecklist), never a hard block here. When the caller lacks import.commit a
// deployment may split SoD (Mara reviews, Avery commits) — render a calm note, not a dead button.
export function CommitCard({
  checklist,
  canCommit,
  committing,
  onCommit,
}: {
  checklist: ImportChecklist;
  canCommit: boolean;
  committing: boolean;
  onCommit: () => void;
}) {
  const ready = checklist.review.commit_ready;
  const keep = checklist.review.keep_items;
  // Progress fraction: commit-ready over the keep set; guard the zero-divide under strict checks.
  const pct = keep > 0 ? Math.min(100, Math.round((ready / keep) * 100)) : 0;
  const enabled = checklist.ready && ready >= 1 && canCommit && !committing;

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap={2} mb="sm">
        <Text fw={600}>On commit</Text>
        <Text size="sm" c="dimmed">
          Per-item, transactional, audited.
        </Text>
      </Stack>

      <Group gap="sm" mb="sm" wrap="nowrap">
        <Progress
          value={pct}
          color="var(--es-success)"
          aria-label={`${ready} of ${keep} items commit-ready`}
          style={{ flex: 1 }}
        />
        <Text size="sm" c="dimmed" style={{ whiteSpace: "nowrap" }}>
          {ready} ready
        </Text>
      </Group>

      <Stack gap={6} component="dl" mb="md">
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Baseline
          </Text>
          <Text component="dd" size="sm" m={0}>
            <Badge variant="light" color="var(--es-do)" mr={6}>
              Effective Rev A
            </Badge>
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Signature
          </Text>
          <Text component="dd" size="sm" ff="monospace" m={0}>
            import_baseline
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Storage
          </Text>
          <Text component="dd" size="sm" m={0}>
            WORM vault blob · content-addressed
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Text component="dt" size="sm" c="dimmed" w={96}>
            Provenance
          </Text>
          <Text component="dd" size="sm" m={0}>
            source path · sha256 · run · decided-by
          </Text>
        </Group>
      </Stack>

      {canCommit ? (
        <Button
          fullWidth
          color="var(--es-do)"
          onClick={onCommit}
          disabled={!enabled}
          loading={committing}
        >
          Commit {ready} confirmed
        </Button>
      ) : (
        <Alert color="gray" variant="light" title="Commit held">
          Commit is held by another role (import.commit).
        </Alert>
      )}
    </Card>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/CommitCard.test.tsx`
Expected: PASS (8 tests, incl. axe).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/CommitCard.tsx apps/web/src/features/ingestion/CommitCard.test.tsx
git commit -m "feat(s-ing-4b): CommitCard (provenance dl + dynamic Commit-N-confirmed button, D-3 gate, held-by-role calm note)"
```

---

### Task 15: ReviewCockpit

**Files:**
- Create: `apps/web/src/features/ingestion/ReviewCockpit.tsx`
- Test: `apps/web/src/features/ingestion/ReviewCockpit.test.tsx`

> The integration spine of the review face. `ReviewCockpit` OWNS: `selected: Set<string>` (multi-select), the active drawer `fileId` (`string | null`), and the queue/conf/offset URL state (`useSearchParams` + `parseRunUrl`, the `LibraryPage` `patchFilters` idiom — reset `offset` on a queue/conf change; clear `selected` on a queue change). It joins `useDupeClusters` + `useVersionFamilies` into a `dupeMap` (`Map<fileId, canonicalIdentifier>` for non-canonical members) + `familyMap` (`Map<fileId, memberCount>`), passes selection/handlers down to the LOCKED-prop children (Tasks 5–14), and calls `useFileDecision` / `useBulkDecision` / `useCommitRun` with a fresh `crypto.randomUUID()` key per user action (ONE key per bulk op). Merge/split flow through the child hooks, which invalidate + refetch — never an optimistic reshape. `canCommit = usePermissions().can("import.commit")`. Keep the test to one happy-path (queue switch + bulk-bar reveal) + the gate-disabled assertion + axe; child internals are covered in their own tasks.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/ReviewCockpit.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { ReviewCockpit } from "./ReviewCockpit";

const RID = ingestionRunFixture.id;

function renderCockpit(route = `/ingestion/${RID}?queue=high`) {
  return renderWithProviders(<ReviewCockpit runId={RID} run={ingestionRunFixture} />, { route });
}

test("the High tab shows the 2 high-band rows", async () => {
  renderCockpit();
  const table = await screen.findByRole("table", { name: "Triage queue" });
  // SOP-PUR-014 (HIGH_DOC) + SOP-PUR v2 FINAL (DUP_FILE) are the two band=HIGH rows.
  expect(await within(table).findByText("SOP-PUR-014 Purchasing.docx")).toBeInTheDocument();
  expect(within(table).getByText("SOP-PUR v2 FINAL.docx")).toBeInTheDocument();
  expect(within(table).queryByText("Final Inspection WI rev1.docx")).not.toBeInTheDocument();
});

test("switching to the Needs-decision tab refetches the undecided rows", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByRole("tab", { name: /Needs decision/ }));
  // review_status=undecided returns all four classified rows (the quarantine row is excluded).
  expect(await screen.findByText("Final Inspection WI rev1.docx")).toBeInTheDocument();
  expect(await screen.findByText("scan0421.pdf")).toBeInTheDocument();
});

test("selecting a row reveals the bulk action bar", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  expect(await screen.findByRole("region", { name: "Bulk actions" })).toBeInTheDocument();
});

test("the commit button is disabled when the run is not ready (fixture ready=false)", async () => {
  // Grant import.commit so CommitCard renders the button (without the key it shows the held-by-role
  // note instead). The button is then disabled because the fixture checklist.ready === false.
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.commit", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const commit = await screen.findByRole("button", { name: /Commit/ });
  expect(commit).toBeDisabled();
});

test("has no axe violations", async () => {
  const { container } = renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/ReviewCockpit.test.tsx`
Expected: FAIL — `./ReviewCockpit` does not exist.

- [ ] **Step 3: Implement `ReviewCockpit`**

Create `apps/web/src/features/ingestion/ReviewCockpit.tsx`:

```tsx
import { Stack } from "@mantine/core";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import type {
  ConfirmedKind,
  ImportDecisionAction,
  ImportDecisionAfter,
  ImportFile,
  ImportRun,
} from "../../lib/types";
import { BulkActionBar } from "./BulkActionBar";
import { CommitCard } from "./CommitCard";
import { IngestionFacetBar } from "./IngestionFacetBar";
import { ImportPlanBanner } from "./ImportPlanBanner";
import { ItemDetailDrawer } from "./ItemDetailDrawer";
import { MergeMenu } from "./MergeMenu";
import { PreCommitChecklist } from "./PreCommitChecklist";
import { QueueTabs } from "./QueueTabs";
import { RunSummaryTiles } from "./RunSummaryTiles";
import { TriagePagination } from "./TriagePagination";
import { TriageTable } from "./TriageTable";
import {
  FILES_PAGE_SIZE,
  parseRunUrl,
  queueToFilesQuery,
  type ConfidenceChoice,
  type IngestionQueue,
} from "./filters";
import {
  useBulkDecision,
  useChecklist,
  useCommitRun,
  useDupeClusters,
  useFileDecision,
  useImportFiles,
  useSplit,
  useVersionFamilies,
} from "./hooks";

// The review-face spine. Owns the selection Set, the active drawer file id, and the queue/conf/offset
// URL state; joins clusters/families into the per-row dupe/family maps; threads handlers down to the
// presentational children. Every write generates a fresh Idempotency-Key (one per bulk op).
export function ReviewCockpit({ runId, run }: { runId: string; run: ImportRun }) {
  const [params, setParams] = useSearchParams();
  const { queue, conf, offset } = parseRunUrl(params);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeFileId, setActiveFileId] = useState<string | null>(null);

  const filter = useMemo(() => queueToFilesQuery(queue, conf), [queue, conf]);
  const filesQuery = useImportFiles(runId, filter, offset);
  const clustersQuery = useDupeClusters(runId);
  const familiesQuery = useVersionFamilies(runId);
  const checklistQuery = useChecklist(runId);

  const fileDecision = useFileDecision(runId);
  const bulkDecision = useBulkDecision(runId);
  const commitRun = useCommitRun(runId);
  const splitRun = useSplit(runId);
  const { can } = usePermissions();
  const canCommit = can("import.commit");

  const files = useMemo(() => filesQuery.data?.files ?? [], [filesQuery.data]);
  const queueCounts = useMemo(() => {
    const q = (run.counts?.["queues"] ?? {}) as Record<string, unknown>;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(q)) out[k] = typeof v === "number" ? v : 0;
    return out;
  }, [run.counts]);

  // dupeMap: each NON-canonical member fileId → the canonical member's review.identifier (or "—").
  const dupeMap = useMemo(() => {
    const idById = new Map<string, string>();
    for (const f of files) if (f.review?.identifier) idById.set(f.id, f.review.identifier);
    const m = new Map<string, string>();
    for (const c of clustersQuery.data?.clusters ?? []) {
      for (const fid of c.member_file_ids) {
        if (fid !== c.canonical_file_id) m.set(fid, idById.get(c.canonical_file_id) ?? "—");
      }
    }
    return m;
  }, [files, clustersQuery.data]);

  // familyMap: each member fileId → its family's ordered-member count.
  const familyMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const fam of familiesQuery.data?.families ?? []) {
      const n = fam.ordered_member_file_ids.length;
      for (const fid of fam.ordered_member_file_ids) m.set(fid, n);
    }
    return m;
  }, [familiesQuery.data]);

  // splitTargetMap: each member fileId → the group it can be split out of (a version family takes
  // priority over a dupe cluster). Used by the drawer's "Split out of group" action.
  const splitTargetMap = useMemo(() => {
    const m = new Map<string, { target_kind: "version_family" | "dupe_cluster"; target_id: string }>();
    for (const fam of familiesQuery.data?.families ?? [])
      for (const fid of fam.ordered_member_file_ids)
        m.set(fid, { target_kind: "version_family", target_id: fam.id });
    for (const c of clustersQuery.data?.clusters ?? [])
      for (const fid of c.member_file_ids)
        if (!m.has(fid)) m.set(fid, { target_kind: "dupe_cluster", target_id: c.id });
    return m;
  }, [familiesQuery.data, clustersQuery.data]);

  // ---- URL patch helpers (the LibraryPage idiom: a queue/conf change resets offset) ----
  const onQueue = useCallback(
    (q: IngestionQueue) => {
      setSelected(new Set()); // a queue change drops a stale cross-queue selection
      setParams((p) => {
        if (q === "needs") p.delete("queue");
        else p.set("queue", q);
        p.delete("offset");
        return p;
      });
    },
    [setParams],
  );
  const onConf = useCallback(
    (c: ConfidenceChoice) => {
      setParams((p) => {
        if (c === "ALL") p.delete("conf");
        else p.set("conf", c);
        p.delete("offset");
        return p;
      });
    },
    [setParams],
  );
  const onOffset = useCallback(
    (o: number) => {
      setParams((p) => {
        if (o > 0) p.set("offset", String(o));
        else p.delete("offset");
        return p;
      });
    },
    [setParams],
  );

  // ---- selection ----
  const onToggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const pageIds = useMemo(() => files.map((f) => f.id), [files]);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const onToggleAllOnPage = useCallback(() => {
    setSelected((prev) => {
      const allSelected = pageIds.length > 0 && pageIds.every((id) => prev.has(id));
      if (allSelected) {
        const next = new Set(prev);
        for (const id of pageIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of pageIds) next.add(id);
      return next;
    });
  }, [pageIds]);

  // ---- writes (a fresh key per user action; one key per bulk op) ----
  const onConfirmKind = useCallback(
    (fileId: string, kind: ConfirmedKind) => {
      fileDecision.mutate({
        fileId,
        body: { action: "accept", after: { kind } },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [fileDecision],
  );
  const onRowAction = useCallback(
    (file: ImportFile, action: ImportDecisionAction) => {
      fileDecision.mutate({
        fileId: file.id,
        body: { action },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [fileDecision],
  );
  const onBulk = useCallback(
    (action: ImportDecisionAction, after?: ImportDecisionAfter) => {
      bulkDecision.mutate({
        body: { action, file_ids: [...selected], after },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [bulkDecision, selected],
  );
  const onBulkConfirmKind = useCallback(
    (kind: ConfirmedKind) => {
      bulkDecision.mutate({
        body: { action: "accept", file_ids: [...selected], after: { kind } },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [bulkDecision, selected],
  );
  const onAcceptAllHigh = useCallback(() => {
    bulkDecision.mutate({
      body: { action: "accept", selector: { band: "HIGH" } },
      idempotencyKey: crypto.randomUUID(),
    });
  }, [bulkDecision]);

  const checklist = checklistQuery.data;
  const total = queueCounts[queue] ?? 0;
  const hasMore = files.length === FILES_PAGE_SIZE;

  return (
    <Stack gap="md" component="section" aria-label="Review cockpit">
      <RunSummaryTiles run={run} />
      <ImportPlanBanner />
      <QueueTabs counts={queueCounts} value={queue} onChange={onQueue} />
      <IngestionFacetBar conf={conf} onConf={onConf} />

      {selected.size > 0 && (
        <BulkActionBar
          count={selected.size}
          onBulk={onBulk}
          onConfirmKind={onBulkConfirmKind}
          onAcceptAllHigh={onAcceptAllHigh}
        />
      )}
      {selected.size >= 2 && (
        <MergeMenu runId={runId} selectedFileIds={[...selected]} onDone={() => setSelected(new Set())} />
      )}

      <TriageTable
        files={files}
        dupeMap={dupeMap}
        familyMap={familyMap}
        loading={filesQuery.isLoading}
        selected={selected}
        onToggle={onToggle}
        onToggleAllOnPage={onToggleAllOnPage}
        allOnPageSelected={allOnPageSelected}
        onConfirmKind={onConfirmKind}
        onOpenDetail={setActiveFileId}
        onRowAction={onRowAction}
      />
      <TriagePagination
        offset={offset}
        hasMore={hasMore}
        onOffset={onOffset}
        total={total}
        pageCount={files.length}
      />

      {checklist && (
        <>
          <PreCommitChecklist checklist={checklist} onShowBlocker={() => onQueue("needs")} />
          <CommitCard
            checklist={checklist}
            canCommit={canCommit}
            committing={commitRun.isPending}
            onCommit={() => commitRun.mutate()}
          />
        </>
      )}

      <ItemDetailDrawer
        runId={runId}
        fileId={activeFileId}
        onClose={() => setActiveFileId(null)}
        onConfirmKind={(kind) => {
          if (activeFileId) onConfirmKind(activeFileId, kind);
        }}
        onDecision={({ action }) => {
          if (activeFileId)
            fileDecision.mutate({
              fileId: activeFileId,
              body: { action },
              idempotencyKey: crypto.randomUUID(),
            });
        }}
        onSplit={() => {
          if (!activeFileId) return;
          const target = splitTargetMap.get(activeFileId);
          if (target)
            splitRun.mutate({
              body: { ...target, separate_file_ids: [activeFileId] },
              idempotencyKey: crypto.randomUUID(),
            });
        }}
      />
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/ReviewCockpit.test.tsx`
Expected: PASS (5 tests). The High tab renders the 2 band=HIGH rows; the Needs-decision tab refetches `review_status=undecided` (the 4 classified rows); selecting a row reveals the bulk bar; the commit button is disabled (`checklist.ready === false`); axe clean.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/ReviewCockpit.tsx apps/web/src/features/ingestion/ReviewCockpit.test.tsx
git commit -m "feat(s-ing-4b): ReviewCockpit — the review-face spine (selection/URL state + cluster/family join)"
```

---

### Task 16: NewImportModal + ScanProgress + CommitProgress + RunTerminalSummary

Four small run-lifecycle faces, one task. Each is presentational + receives its handlers as props (the `IngestionRunPage` controller, Task 17, owns the switch on `run.status` and passes `onCancel`/`onResume`/`onCreated`). All four bind to the LOCKED registry signatures, the Task-1 `ImportRun` type, the Task-3/4 hooks (`useCreateImportRun()` → `ImportRun`, `useCancelRun(runId)`), `ApiError.status` from `lib/api`, and the §7 calm-state copy bank. `run.counts.commit` is read via a local `safeCount(...)` that degrades to `0` under `noUncheckedIndexedAccess`. The four components live in one file pair group but are tested in four sibling test files for isolation. Mantine `Stepper`/`Progress`/`Alert`/`Modal`/`Switch`/`TextInput` + theme `var(--es-*)` tokens only — never hardcoded hex. Every rendering test asserts `await axe(container)` is clean; assert by role/label with distinct `aria-label`s.

**Files:**
- Create: `apps/web/src/features/ingestion/NewImportModal.tsx`
- Create: `apps/web/src/features/ingestion/NewImportModal.test.tsx`
- Create: `apps/web/src/features/ingestion/ScanProgress.tsx`
- Create: `apps/web/src/features/ingestion/ScanProgress.test.tsx`
- Create: `apps/web/src/features/ingestion/CommitProgress.tsx`
- Create: `apps/web/src/features/ingestion/CommitProgress.test.tsx`
- Create: `apps/web/src/features/ingestion/RunTerminalSummary.tsx`
- Create: `apps/web/src/features/ingestion/RunTerminalSummary.test.tsx`

- [ ] **Step 1: Write the failing `NewImportModal` test**

Create `apps/web/src/features/ingestion/NewImportModal.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { NewImportModal } from "./NewImportModal";

test("submitting a source_root posts the body and calls onCreated with the new run id", async () => {
  const user = userEvent.setup();
  let seenBody: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports", async ({ request }) => {
      seenBody = await request.json();
      return HttpResponse.json({ ...ingestionRunFixture, status: "Created" }, { status: 202 });
    }),
  );
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(<NewImportModal opened onClose={onClose} onCreated={onCreated} />);

  await user.type(screen.getByLabelText("Source folder path"), "/srv/import/legacy-qms-share");
  await user.click(screen.getByLabelText("Run OCR on scanned files"));
  await user.click(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(ingestionRunFixture.id));
  expect(onClose).toHaveBeenCalled();
  expect(seenBody).toEqual({
    source_root: "/srv/import/legacy-qms-share",
    ocr_enabled: true,
  });
});

test("the Start button is disabled until a source folder is typed", async () => {
  const user = userEvent.setup();
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  expect(screen.getByRole("button", { name: "Start import" })).toBeDisabled();
  await user.type(screen.getByLabelText("Source folder path"), "/srv/x");
  expect(screen.getByRole("button", { name: "Start import" })).toBeEnabled();
});

test("a 409 (a scan is already active) renders a calm inline message — no crash", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports", () =>
      HttpResponse.json(
        { code: "active_run", title: "An import is already in progress", active_run_id: "x" },
        { status: 409 },
      ),
    ),
  );
  const onCreated = vi.fn();
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={onCreated} />);
  await user.type(screen.getByLabelText("Source folder path"), "/srv/import/x");
  await user.click(screen.getByRole("button", { name: "Start import" }));
  expect(await screen.findByText(/An import is already in progress/)).toBeInTheDocument();
  expect(onCreated).not.toHaveBeenCalled();
});

test("a 422 (bad source root) renders the returned detail calmly", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports", () =>
      HttpResponse.json(
        { code: "bad_source_root", title: "Invalid path", detail: "source_root escapes the import mount" },
        { status: 422 },
      ),
    ),
  );
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  await user.type(screen.getByLabelText("Source folder path"), "/etc/passwd");
  await user.click(screen.getByRole("button", { name: "Start import" }));
  expect(await screen.findByText(/escapes the import mount/)).toBeInTheDocument();
});

test("has no axe violations when open", async () => {
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  await screen.findByLabelText("Source folder path");
  expect(await axe(document.body)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/NewImportModal.test.tsx`
Expected: FAIL — `./NewImportModal` does not exist.

- [ ] **Step 3: Implement `NewImportModal`**

Create `apps/web/src/features/ingestion/NewImportModal.tsx`:

```tsx
import { Alert, Button, Group, Modal, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ImportRunCreate } from "../../lib/types";
import { useCreateImportRun } from "./hooks";

// The New-Import form (D-1): a typed source_root within the configured import mount (no directory
// picker — §10), an OCR toggle, and an optional profile. On 202 we hand the new run id up to the
// page controller (it then routes to /ingestion/:runId and polls ScanProgress). A 409 (a scan is
// already active) or a 422 (bad/escaping source root) is a calm inline message read from
// ApiError.message (the RFC 9457 detail/title) — never a red toast or a thrown stack (DP-6).
export function NewImportModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const [sourceRoot, setSourceRoot] = useState("");
  const [ocr, setOcr] = useState(false);
  const [profile, setProfile] = useState("");
  const create = useCreateImportRun();

  function reset() {
    setSourceRoot("");
    setOcr(false);
    setProfile("");
    create.reset();
  }
  function close() {
    reset();
    onClose();
  }
  function submit() {
    const root = sourceRoot.trim();
    if (root.length === 0) return;
    const body: ImportRunCreate = { source_root: root, ocr_enabled: ocr };
    const p = profile.trim();
    if (p.length > 0) body.profile = p;
    create.mutate(body, {
      onSuccess: (run) => {
        const id = run.id;
        reset();
        onClose();
        onCreated(id);
      },
    });
  }

  const errorMessage =
    create.error instanceof ApiError
      ? create.error.message
      : create.isError
        ? "Couldn’t start the import. Please try again."
        : null;

  return (
    <Modal opened={opened} onClose={close} title="New import" size="lg">
      <Stack gap="md">
        <TextInput
          data-autofocus
          label="Source folder path"
          aria-label="Source folder path"
          placeholder="/srv/import/legacy-qms-share"
          description="A path within the configured import mount. The engine scans it read-only — nothing is controlled until you commit."
          value={sourceRoot}
          onChange={(e) => setSourceRoot(e.currentTarget.value)}
          required
        />
        <Switch
          label="Run OCR on scanned files"
          aria-label="Run OCR on scanned files"
          checked={ocr}
          onChange={(e) => setOcr(e.currentTarget.checked)}
        />
        <TextInput
          label="Profile (optional)"
          aria-label="Import profile"
          placeholder="default"
          value={profile}
          onChange={(e) => setProfile(e.currentTarget.value)}
        />
        {errorMessage && (
          <Alert color="gray" title="Couldn’t start the import">
            {errorMessage}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={sourceRoot.trim().length === 0}
            loading={create.isPending}
          >
            Start import
          </Button>
        </Group>
        <Text size="xs" c="dimmed">
          The tool organizes; you decide. Review every item before committing to the vault.
        </Text>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/NewImportModal.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/NewImportModal.tsx apps/web/src/features/ingestion/NewImportModal.test.tsx
git commit -m "feat(s-ing-4b): NewImportModal (source_root + OCR + profile → create, 409/422 calm)"
```

- [ ] **Step 6: Write the failing `ScanProgress` test**

Create `apps/web/src/features/ingestion/ScanProgress.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { ScanProgress } from "./ScanProgress";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("a Scanning run shows the human stage label, the caption, and a Cancel button", async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  renderWithProviders(<ScanProgress run={runWith({ status: "Scanning" })} onCancel={onCancel} />);
  expect(screen.getByText("Scanning files")).toBeInTheDocument();
  expect(screen.getByText(/Scanning…/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cancel import" }));
  expect(onCancel).toHaveBeenCalled();
});

test("a Classifying run names the classify stage", () => {
  renderWithProviders(<ScanProgress run={runWith({ status: "Classifying" })} onCancel={() => {}} />);
  expect(screen.getByText("Classifying content")).toBeInTheDocument();
});

test("a Failed run shows a calm error alert with run.error (no Cancel)", () => {
  renderWithProviders(
    <ScanProgress run={runWith({ status: "Failed", error: "extractor crashed on broken.bin" })} onCancel={() => {}} />,
  );
  expect(screen.getByText(/extractor crashed on broken.bin/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel import" })).not.toBeInTheDocument();
});

test("an unknown additive stage degrades calmly (no crash)", () => {
  renderWithProviders(<ScanProgress run={runWith({ status: "Renaming" })} onCancel={() => {}} />);
  expect(screen.getByText(/Working…/)).toBeInTheDocument();
});

test("has no axe violations (scanning + failed)", async () => {
  const scanning = renderWithProviders(
    <ScanProgress run={runWith({ status: "Scanning" })} onCancel={() => {}} />,
  );
  expect(await axe(scanning.container)).toHaveNoViolations();
  scanning.unmount();
  const failed = renderWithProviders(
    <ScanProgress run={runWith({ status: "Failed", error: "boom" })} onCancel={() => {}} />,
  );
  expect(await axe(failed.container)).toHaveNoViolations();
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/ScanProgress.test.tsx`
Expected: FAIL — `./ScanProgress` does not exist.

- [ ] **Step 8: Implement `ScanProgress`**

Create `apps/web/src/features/ingestion/ScanProgress.tsx`:

```tsx
import { Alert, Button, Card, Group, Loader, Stepper, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// The pre-Proposed "watch" face (§3 step 2): a calm stepper of the auto-chained pipeline stages with
// the current stage highlighted + a Cancel (import.execute). The run page polls run.status (the
// useImportRun refetchInterval) and re-renders this until the run rests at Proposed or Failed. A
// Failed run is a calm Alert with run.error — never a thrown error or a red crash (DP-6). Stages
// beyond the known set (additive engine stages) degrade to a generic "Working…" rather than crash.

// Ordered pipeline stages → the stepper rows. `status` maps to the active step; a "*-ed" rest status
// (Scanned/Classified/…) sits between two stages and reads as the later one being in flight.
const STAGES: { key: string; label: string; caption: string }[] = [
  { key: "scan", label: "Scanning files", caption: "Scanning the source folder…" },
  { key: "extract", label: "Reading text", caption: "Extracting text (and OCR where enabled)…" },
  { key: "classify", label: "Classifying content", caption: "Classifying kind, type, and clauses…" },
  { key: "dedup", label: "Finding duplicates", caption: "Grouping duplicates and version families…" },
  { key: "propose", label: "Proposing a plan", caption: "Proposing identifiers and placement…" },
];

const STATUS_TO_STEP: Record<string, number> = {
  Created: 0,
  Scanning: 0,
  Scanned: 1,
  Extracting: 1,
  Classifying: 2,
  Classified: 3,
  Deduping: 3,
  Proposing: 4,
  Proposed: 4,
};

export function ScanProgress({ run, onCancel }: { run: ImportRun; onCancel: () => void }) {
  if (run.status === "Failed") {
    return (
      <Card withBorder padding="lg">
        <Alert color="gray" title="The import couldn’t finish scanning">
          {run.error ?? "The engine stopped before proposing a plan. You can start a new import."}
        </Alert>
      </Card>
    );
  }

  // A KNOWN status maps to a stage; an unknown/additive status (a future engine stage) has no stage →
  // render the generic "Working…" rather than mislabel it as the first scan stage.
  const known = run.status in STATUS_TO_STEP;
  const step = STATUS_TO_STEP[run.status] ?? 0;
  const current = known ? (STAGES[step] ?? null) : null;
  const caption = current?.caption ?? "Working…";

  return (
    <Card withBorder padding="lg">
      <Group justify="space-between" mb="md" wrap="nowrap">
        <Group gap="sm" wrap="nowrap">
          <Loader size="sm" aria-hidden="true" />
          <Text fw={600}>
            {current ? current.label : "Working…"}
          </Text>
        </Group>
        <Button variant="default" onClick={onCancel} aria-label="Cancel import">
          Cancel import
        </Button>
      </Group>
      <Stepper active={step} size="sm" aria-label="Import pipeline progress">
        {STAGES.map((s) => (
          <Stepper.Step key={s.key} label={s.label} />
        ))}
      </Stepper>
      <Text c="dimmed" size="sm" mt="md">
        {caption} — Scanning… nothing touches the vault yet.
      </Text>
    </Card>
  );
}
```

- [ ] **Step 9: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/ScanProgress.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/features/ingestion/ScanProgress.tsx apps/web/src/features/ingestion/ScanProgress.test.tsx
git commit -m "feat(s-ing-4b): ScanProgress pipeline stepper (stage labels, Cancel, Failed-calm)"
```

- [ ] **Step 11: Write the failing `CommitProgress` test**

Create `apps/web/src/features/ingestion/CommitProgress.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { CommitProgress } from "./CommitProgress";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("renders a Committing state with the committed/failed counts from run.counts.commit", () => {
  renderWithProviders(
    <CommitProgress
      run={runWith({
        status: "Committing",
        counts: { commit: { committed: 3, failed: 1 } },
      })}
    />,
  );
  expect(screen.getByText("Committing to the vault")).toBeInTheDocument();
  expect(screen.getByLabelText("Committed so far: 3")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed so far: 1")).toBeInTheDocument();
});

test("missing commit counts degrade to zero (no crash under noUncheckedIndexedAccess)", () => {
  renderWithProviders(<CommitProgress run={runWith({ status: "Committing", counts: null })} />);
  expect(screen.getByLabelText("Committed so far: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed so far: 0")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <CommitProgress run={runWith({ status: "Committing", counts: { commit: { committed: 2, failed: 0 } } })} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 12: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/CommitProgress.test.tsx`
Expected: FAIL — `./CommitProgress` does not exist.

- [ ] **Step 13: Implement `CommitProgress`**

Create `apps/web/src/features/ingestion/CommitProgress.tsx`:

```tsx
import { Card, Group, Loader, Progress, Stack, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// The Committing face (§3 step 5): the confirmed subset is driven item-by-item into the vault; the
// run page polls run.status to terminal (Completed/PartiallyCommitted) and re-renders this with the
// live counts.commit {committed, failed}. run.counts is loosely typed (Record<string, unknown>) so
// every hop is read through a defined fallback (noUncheckedIndexedAccess) → 0 when absent.
function commitCount(counts: ImportRun["counts"], key: "committed" | "failed"): number {
  if (!counts || typeof counts !== "object") return 0;
  const commit = (counts as Record<string, unknown>)["commit"];
  if (!commit || typeof commit !== "object") return 0;
  const value = (commit as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

export function CommitProgress({ run }: { run: ImportRun }) {
  const committed = commitCount(run.counts, "committed");
  const failed = commitCount(run.counts, "failed");
  const done = committed + failed;

  return (
    <Card withBorder padding="lg">
      <Group gap="sm" mb="md" wrap="nowrap">
        <Loader size="sm" aria-hidden="true" />
        <Text fw={600}>Committing to the vault</Text>
      </Group>
      {/* Indeterminate-ish: we know how many have landed, not the live total — show an animated bar
          and the running tallies, never a misleading percent. */}
      <Progress value={done > 0 ? 100 : 0} animated aria-label="Commit in progress" mb="md" />
      <Stack gap={4}>
        <Text size="sm" aria-label={`Committed so far: ${committed}`}>
          ✓ {committed} committed
        </Text>
        <Text size="sm" c="dimmed" aria-label={`Failed so far: ${failed}`}>
          ✕ {failed} failed
        </Text>
      </Stack>
      <Text c="dimmed" size="xs" mt="md">
        Each confirmed item becomes an Effective Rev A controlled document or an immutable Record. A
        partial run can be resumed.
      </Text>
    </Card>
  );
}
```

- [ ] **Step 14: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/CommitProgress.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 15: Commit**

```bash
git add apps/web/src/features/ingestion/CommitProgress.tsx apps/web/src/features/ingestion/CommitProgress.test.tsx
git commit -m "feat(s-ing-4b): CommitProgress (live committed/failed counts, safe accessors)"
```

- [ ] **Step 16: Write the failing `RunTerminalSummary` test**

Create `apps/web/src/features/ingestion/RunTerminalSummary.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { RunTerminalSummary } from "./RunTerminalSummary";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("a Completed run shows the committed/failed counts and links to the Import Report record", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({
        status: "Completed",
        counts: { commit: { committed: 5, failed: 0 } },
        report_record_id: "r0000000-0000-0000-0000-0000000000r1",
      })}
    />,
  );
  expect(screen.getByText("Import complete")).toBeInTheDocument();
  expect(screen.getByLabelText("Committed: 5")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed: 0")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /Import Report/ });
  expect(link).toHaveAttribute("href", "/records/r0000000-0000-0000-0000-0000000000r1");
});

test("a Completed run with no report record shows a calm note instead of a link", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 1, failed: 0 } }, report_record_id: null })}
    />,
  );
  expect(screen.queryByRole("link", { name: /Import Report/ })).not.toBeInTheDocument();
  expect(screen.getByText(/report isn’t available/)).toBeInTheDocument();
});

test("a PartiallyCommitted run shows a Resume commit button that calls onResume", async () => {
  const user = userEvent.setup();
  const onResume = vi.fn();
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "PartiallyCommitted", counts: { commit: { committed: 4, failed: 2 } } })}
      onResume={onResume}
    />,
  );
  expect(screen.getByText("Import partially committed")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Resume commit" }));
  expect(onResume).toHaveBeenCalled();
});

test("a Completed run does NOT show Resume", () => {
  renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 5, failed: 0 } } })}
      onResume={() => {}}
    />,
  );
  expect(screen.queryByRole("button", { name: "Resume commit" })).not.toBeInTheDocument();
});

test("has no axe violations (completed + partial)", async () => {
  const completed = renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "Completed", counts: { commit: { committed: 5, failed: 0 } }, report_record_id: "r1" })}
    />,
  );
  expect(await axe(completed.container)).toHaveNoViolations();
  completed.unmount();
  const partial = renderWithProviders(
    <RunTerminalSummary
      run={runWith({ status: "PartiallyCommitted", counts: { commit: { committed: 4, failed: 2 } } })}
      onResume={() => {}}
    />,
  );
  expect(await axe(partial.container)).toHaveNoViolations();
});
```

- [ ] **Step 17: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/RunTerminalSummary.test.tsx`
Expected: FAIL — `./RunTerminalSummary` does not exist.

- [ ] **Step 18: Implement `RunTerminalSummary`**

Create `apps/web/src/features/ingestion/RunTerminalSummary.tsx`:

```tsx
import { Alert, Anchor, Button, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { ImportRun } from "../../lib/types";

// The terminal face (§3 step 5): committed/failed tallies, a link to the Import Report record (when
// run.report_record_id is set), and — for PartiallyCommitted — a calm "Resume commit" affordance
// (the page wires onResume to useCommitRun; idempotent re-commit is a no-op for the already-landed
// subset). All four hops into run.counts.commit degrade to 0 (noUncheckedIndexedAccess). DP-6: a
// partial run is a calm summary, never an error.
function commitCount(counts: ImportRun["counts"], key: "committed" | "failed"): number {
  if (!counts || typeof counts !== "object") return 0;
  const commit = (counts as Record<string, unknown>)["commit"];
  if (!commit || typeof commit !== "object") return 0;
  const value = (commit as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

export function RunTerminalSummary({
  run,
  onResume,
}: {
  run: ImportRun;
  onResume?: () => void;
}) {
  const committed = commitCount(run.counts, "committed");
  const failed = commitCount(run.counts, "failed");
  const partial = run.status === "PartiallyCommitted";
  const heading = partial ? "Import partially committed" : "Import complete";

  return (
    <Card withBorder padding="lg">
      <Stack gap="md">
        <Text fw={700} size="lg">
          {heading}
        </Text>
        <Group gap="lg">
          <Text aria-label={`Committed: ${committed}`}>✓ {committed} committed</Text>
          <Text c="dimmed" aria-label={`Failed: ${failed}`}>
            ✕ {failed} failed
          </Text>
        </Group>

        {run.report_record_id ? (
          <Anchor component={Link} to={`/records/${run.report_record_id}`}>
            View the Import Report record →
          </Anchor>
        ) : (
          <Text c="dimmed" size="sm">
            The Import Report record isn’t available for this run.
          </Text>
        )}

        {partial && (
          <Alert color="gray" title="Some items weren’t committed">
            <Stack gap="sm">
              <Text size="sm">
                {failed} item{failed === 1 ? "" : "s"} couldn’t be committed. Resuming re-attempts the
                remaining subset; items already in the vault are skipped.
              </Text>
              {onResume && (
                <Group>
                  <Button onClick={onResume}>Resume commit</Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}

        <Group>
          <Anchor component={Link} to="/library">
            View the committed documents in the Library →
          </Anchor>
        </Group>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 19: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/RunTerminalSummary.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 20: Run all four lifecycle-face suites together + typecheck**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test -- src/features/ingestion/NewImportModal.test.tsx src/features/ingestion/ScanProgress.test.tsx src/features/ingestion/CommitProgress.test.tsx src/features/ingestion/RunTerminalSummary.test.tsx`
Expected: PASS (tsc clean under strict `noUncheckedIndexedAccess`; all four suites green, jest-axe clean). Fix any index/lookup nit the full typecheck surfaces (the per-file vitest run won't catch them) and re-run.

- [ ] **Step 21: Commit**

```bash
git add apps/web/src/features/ingestion/RunTerminalSummary.tsx apps/web/src/features/ingestion/RunTerminalSummary.test.tsx
git commit -m "feat(s-ing-4b): RunTerminalSummary (counts, report-record link, resume on partial)"
```

---

### Task 17: IngestionRunPage (four-faces controller)

**Files:**
- Create: `apps/web/src/features/ingestion/IngestionRunPage.tsx`
- Test: `apps/web/src/features/ingestion/IngestionRunPage.test.tsx`

This is the run-lifecycle router: it reads `runId` from `useParams`, runs `useImportRun(runId)` (which already polls while the run is settling/committing per Task 3), and switches on `run.status` to mount exactly one of the four faces — `ScanProgress` (Task 15, pre-`Proposed` and any non-rest/non-terminal stage), `ReviewCockpit` (Task 14, `Proposed`/`Reviewing`), `CommitProgress` (Task 15, `Committing`), or `RunTerminalSummary` (Task 15, the four terminal states). It owns no selection/queue state — that lives in `ReviewCockpit`. It renders the calm error faces itself: a `Loader` while loading, the no-access panel on a 403 `ApiError`, the not-found panel (with a link back to `/ingestion`) on a 404. Status routing tolerates additive stages (the default/unknown branch falls through to `ScanProgress`, never crashes).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/IngestionRunPage.test.tsx`:

```tsx
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { IngestionRunPage } from "./IngestionRunPage";

const RID = ingestionRunFixture.id;

function renderPage(route = `/ingestion/${RID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="ingestion/:runId" element={<IngestionRunPage />} />
    </Routes>,
    { route },
  );
}

test("IngestionRunPage shows a loader before the run resolves", () => {
  renderPage();
  expect(screen.getByLabelText("Loading import run")).toBeInTheDocument();
});

test("IngestionRunPage renders the review cockpit for a Proposed run", async () => {
  renderPage();
  // a cockpit-only affordance: the queue tablist (QueueTabs, Task 7)
  expect(await screen.findByRole("tab", { name: /Needs decision/ })).toBeInTheDocument();
});

test("IngestionRunPage renders the commit-progress face for a Committing run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Committing to the vault/)).toBeInTheDocument();
});

test("IngestionRunPage renders the scan-progress face for a pre-Proposed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Scanning" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Scanning the source/)).toBeInTheDocument();
});

test("an unknown/additive status degrades calmly to the scan-progress face (invariant 6)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "SomeFutureStage" }),
    ),
  );
  renderPage();
  // the default switch branch routes an unknown status to ScanProgress, which shows the generic
  // "Working…" caption (Task 16 fix) rather than crashing or going blank.
  expect(await screen.findByText(/Working…/)).toBeInTheDocument();
});

test("IngestionRunPage renders the terminal summary for a Completed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Completed" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Import complete/)).toBeInTheDocument();
});

test("IngestionRunPage shows a calm not-found panel on a 404", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("Import run not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to imports/ })).toBeInTheDocument();
});

test("IngestionRunPage shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to import review.")).toBeInTheDocument();
});

test("IngestionRunPage has no a11y violations (cockpit)", async () => {
  const { container } = renderPage();
  await screen.findByRole("tab", { name: /Needs decision/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("IngestionRunPage has no a11y violations (404)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  const { container } = renderPage();
  await screen.findByText("Import run not found.");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionRunPage.test.tsx`
Expected: FAIL — `./IngestionRunPage` does not exist (module-not-found).

- [ ] **Step 3: Implement `IngestionRunPage.tsx`**

> Depends on the locked face components: `ScanProgress`/`CommitProgress`/`RunTerminalSummary` (Task 15) and `ReviewCockpit` (Task 14), plus `useImportRun`/`isRunSettling` (Task 3) and `useCancelRun` (Task 4). Bind to their exact registry props: `ScanProgress({ run, onCancel })`, `CommitProgress({ run })`, `RunTerminalSummary({ run, onResume? })`, `ReviewCockpit({ runId, run })`.

Create `apps/web/src/features/ingestion/IngestionRunPage.tsx`:

```tsx
import { Alert, Anchor, Container, Loader, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { CommitProgress } from "./CommitProgress";
import { ReviewCockpit } from "./ReviewCockpit";
import { RunTerminalSummary } from "./RunTerminalSummary";
import { ScanProgress } from "./ScanProgress";
import { useCancelRun, useImportRun } from "./hooks";

// The human-paced rest states (review cockpit) + the commit + terminal states. Anything NOT in this
// set (Created/Scanning/Extracting/… and any additive stage) is "the engine is still settling" →
// ScanProgress. The switch is exhaustive-by-fallthrough so an unknown additive status degrades calmly.
const REVIEW_STATES = new Set(["Proposed", "Reviewing"]);
const TERMINAL_STATES = new Set(["Completed", "PartiallyCommitted", "Failed", "Cancelled"]);

// S-ing-4b: the four-faces controller for /ingestion/:runId. Reads the run, polls it while settling
// (useImportRun owns the refetchInterval), and mounts exactly one lifecycle face by status. Per-view
// permission is the server's job (403 → calm); a foreign/missing run is a 404 → calm. Selection/queue
// state lives in ReviewCockpit, not here.
export function IngestionRunPage() {
  const { runId = null } = useParams();
  const { data: run, isLoading, isError, error } = useImportRun(runId);
  const cancelRun = useCancelRun(runId);

  if (isLoading && !run) {
    return (
      <Container size="lg" py="md" aria-label="Loading import run">
        <Loader />
      </Container>
    );
  }

  if (isError || !run) {
    const forbidden = error instanceof ApiError && error.status === 403;
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Import review
        </Title>
        {forbidden ? (
          <Alert color="gray" title="No access">
            You don&rsquo;t have access to import review.
          </Alert>
        ) : (
          <Alert color="gray" title="Not found">
            Import run not found.{" "}
            <Anchor component={Link} to="/ingestion">
              Back to imports
            </Anchor>
          </Alert>
        )}
      </Container>
    );
  }

  const status = run.status;
  if (REVIEW_STATES.has(status)) {
    return <ReviewCockpit runId={run.id} run={run} />;
  }
  if (status === "Committing") {
    return <CommitProgress run={run} />;
  }
  if (TERMINAL_STATES.has(status)) {
    return <RunTerminalSummary run={run} />;
  }
  // pre-Proposed (Created/Scanning/Extracting/Classifying/… ) and any additive stage → scan progress.
  return <ScanProgress run={run} onCancel={() => cancelRun.mutate()} />;
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionRunPage.test.tsx`
Expected: PASS (9 tests). The cockpit/scan/commit/terminal copy assertions match the Task 14–15 faces (`QueueTabs` "Needs decision" tab, `ScanProgress` "Scanning the source", `CommitProgress` "Committing to the vault", `RunTerminalSummary` "Import complete"); both calm panels and both axe checks are clean.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/IngestionRunPage.tsx apps/web/src/features/ingestion/IngestionRunPage.test.tsx
git commit -m "feat(s-ing-4b): IngestionRunPage four-faces lifecycle controller (scan/cockpit/commit/terminal + 403/404 calm)"
```

---

### Task 18: IngestionRunsPage (runs landing)

**Files:**
- Create: `apps/web/src/features/ingestion/IngestionRunsPage.tsx`
- Test: `apps/web/src/features/ingestion/IngestionRunsPage.test.tsx`

> Binds to: `useImportRuns()` (Task 3), `ImportStatusBadge({ status })` (Task 5), `NewImportModal({ opened, onClose, onCreated })` (Task 16), `usePermissions().can` (`app/shell/usePermissions.ts`), and the `ImportRun` / `ApiError` types (Task 1 / `lib/api`). The landing list mirrors the Library list idiom (Title + a gated primary action + a calm empty state). The page is presentational over the hooks; it holds only the modal-disclosure state. `import.execute` is NOT in the default test `/me/permissions` (it returns `permissions: []`), so the "New import" test must grant it via `server.use`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/ingestion/IngestionRunsPage.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { IngestionRunsPage } from "./IngestionRunsPage";

const RID = ingestionRunFixture.id;

// Grant import.execute (the default /me/permissions returns []), so the "New import" button shows.
function grantExecute() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.execute", effect: "ALLOW" }],
      }),
    ),
  );
}

// Render the page under a sentinel route so a navigate('/ingestion/<id>') is observable.
function renderPage(route = "/ingestion") {
  return renderWithProviders(
    <Routes>
      <Route path="/ingestion" element={<IngestionRunsPage />} />
      <Route path="/ingestion/:runId" element={<div>RUN PAGE</div>} />
    </Routes>,
    { route },
  );
}

test("renders the fixture run with its source_root, a status badge, and a link to /ingestion/<id>", async () => {
  renderPage();
  const link = await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(link).toHaveAttribute("href", `/ingestion/${RID}`);
  expect(screen.getByLabelText("Run status: Proposed")).toBeInTheDocument();
});

test('shows the "New import" button only when can("import.execute")', async () => {
  // Default permissions ([]) → no button.
  renderPage();
  await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(screen.queryByRole("button", { name: /New import/ })).not.toBeInTheDocument();
});

test('clicking "New import" opens the NewImportModal (the source root field appears)', async () => {
  grantExecute();
  renderPage();
  const button = await screen.findByRole("button", { name: /New import/ });
  await userEvent.click(button);
  expect(await screen.findByLabelText(/Source folder path/i)).toBeInTheDocument();
});

test("an empty run list shows the calm empty state", async () => {
  server.use(http.get("/api/v1/admin/imports", () => HttpResponse.json([])));
  renderPage();
  expect(await screen.findByText("No imports yet.")).toBeInTheDocument();
});

test("a 403 renders the calm no-access panel (no red error)", async () => {
  server.use(
    http.get("/api/v1/admin/imports", () =>
      HttpResponse.json({ code: "forbidden", detail: "no access" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to import review.")).toBeInTheDocument();
});

test("has no axe violations (list, empty, and no-access)", async () => {
  const list = renderPage();
  await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(await axe(list.container)).toHaveNoViolations();
  list.unmount();

  server.use(http.get("/api/v1/admin/imports", () => HttpResponse.json([])));
  const empty = renderPage();
  await screen.findByText("No imports yet.");
  expect(await axe(empty.container)).toHaveNoViolations();
  empty.unmount();

  server.use(
    http.get("/api/v1/admin/imports", () =>
      HttpResponse.json({ code: "forbidden", detail: "no access" }, { status: 403 }),
    ),
  );
  const denied = renderPage();
  await screen.findByText("You don't have access to import review.");
  await waitFor(() => expect(denied.container.querySelector("table")).not.toBeInTheDocument());
  expect(await axe(denied.container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionRunsPage.test.tsx`
Expected: FAIL — `./IngestionRunsPage` does not exist.

- [ ] **Step 3: Implement `IngestionRunsPage.tsx`**

Create `apps/web/src/features/ingestion/IngestionRunsPage.tsx`:

```tsx
import { Alert, Anchor, Button, Group, Skeleton, Stack, Table, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { Link, useNavigate } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { ImportStatusBadge } from "./ImportStatusBadge";
import { NewImportModal } from "./NewImportModal";
import { useImportRuns } from "./hooks";

// The runs landing (D-1): a calm list of import runs + a gated New-Import entry. Presentational over
// useImportRuns(); a 403 → the calm no-access panel (import has no hidden_by_scope — full deny).
export function IngestionRunsPage() {
  const { data, isLoading, isError, error } = useImportRuns();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [modalOpen, modal] = useDisclosure(false);

  const forbidden = error instanceof ApiError && error.status === 403;
  const runs = data ?? [];

  if (forbidden) {
    return (
      <Stack gap="md">
        <Title order={1}>Import</Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to import review.
        </Alert>
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={1}>Import</Title>
          <Text size="sm" c="dimmed">
            {isLoading ? "Loading…" : `${runs.length} import run${runs.length === 1 ? "" : "s"}`}
          </Text>
        </div>
        {can("import.execute") && (
          <Button size="sm" onClick={modal.open}>
            ＋ New import
          </Button>
        )}
      </Group>

      {isError && !forbidden && (
        <Alert color="red" title="Couldn't load import runs">
          Please try again.
        </Alert>
      )}

      {isLoading && (
        <Stack gap="xs" aria-label="Loading import runs">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={36} />
          ))}
        </Stack>
      )}

      {!isLoading && !isError && runs.length === 0 && <Text>No imports yet.</Text>}

      {!isLoading && !isError && runs.length > 0 && (
        <Table highlightOnHover aria-label="Import runs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source root</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Created</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {runs.map((run) => (
              <Table.Tr key={run.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/ingestion/${run.id}`} ff="monospace" size="sm">
                    {run.source_root}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <ImportStatusBadge status={run.status} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{run.created_at ? run.created_at.slice(0, 10) : "—"}</Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <NewImportModal
        opened={modalOpen}
        onClose={modal.close}
        onCreated={(runId) => {
          modal.close();
          navigate(`/ingestion/${runId}`);
        }}
      />
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/ingestion/IngestionRunsPage.test.tsx`
Expected: PASS (6 tests). (Requires Task 5 `ImportStatusBadge` + Task 16 `NewImportModal` already merged; they expose the locked props the page binds to.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/ingestion/IngestionRunsPage.tsx apps/web/src/features/ingestion/IngestionRunsPage.test.tsx
git commit -m "feat(s-ing-4b): IngestionRunsPage runs landing (gated New-Import + calm states)"
```

---

### Task 19: Routes + gated LeftRail Import entry

**Files:**
- Modify: `apps/web/src/App.tsx` (import the two page components + add the two `ingestion` routes under the operational `AppShell` `path="/"` block)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (add the gated **Import** `NavLink`, mirroring the S-web-6 Compliance entry)
- Modify: `apps/web/src/app/shell/LeftRail.test.tsx` (add the hidden/shown gating cases for `import.review`)
- Modify: `apps/web/src/App.test.tsx` (add the two route-resolution cases)

> Wires the slice into the shell. The two page components (`IngestionRunsPage`, `IngestionRunPage` — Tasks 16–17) and every hook/handler they need (Tasks 1, 3–4) already exist by this point, so both routes resolve against the live MSW happy-path handlers. The Import nav entry gates on `can("import.review")` (the admin-only SYSTEM key), exactly mirroring the existing `can("report.compliance_checklist.read")` Compliance entry in `LeftRail.tsx:28-36`. Route-level gating stays `operational ? … : <Navigate to="/setup">` (App.tsx:102); per-view 403/404-calm gating lives **inside** the page components (spec §5.3), never at the route.

- [ ] **Step 1: Write the failing LeftRail tests**

Append these two cases to `apps/web/src/app/shell/LeftRail.test.tsx`. The existing imports (`screen`, `waitFor`, `http`, `HttpResponse`, `server`, `renderWithProviders`, `LeftRail`) already cover everything — no new imports needed (verified against the file's current head).

```tsx
test("hides the Import entry when the caller lacks import.review", async () => {
  // default MSW /me/permissions returns no key → the admin-only Import entry is hidden
  renderWithProviders(<LeftRail />, { route: "/" });
  await screen.findByText("Library");
  expect(screen.queryByText("Import")).not.toBeInTheDocument();
});

test("shows the gated Import entry when the caller holds import.review", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.review", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/ingestion" });
  const link = await screen.findByRole("link", { name: "Import" });
  expect(link).toHaveAttribute("href", "/ingestion");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/app/shell/LeftRail.test.tsx`
Expected: FAIL — the "shows the gated Import entry" case can't find an `Import` link (no entry rendered yet).

- [ ] **Step 3: Add the gated Import entry to `LeftRail`**

In `apps/web/src/app/shell/LeftRail.tsx`, add the **Import** `NavLink` immediately after the existing Compliance block (before the `PHASES.map(...)`). The full file:

```tsx
import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import { usePermissions } from "./usePermissions";
import { useClauses } from "./useClauses";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

export function LeftRail() {
  const { pathname } = useLocation();
  const { data: clauses } = useClauses();
  const { can } = usePermissions();
  return (
    <Stack gap="xs" p="sm">
      <NavLink component={Link} to="/" label="Home" active={pathname === "/"} />
      <NavLink
        component={Link}
        to="/library"
        label="Library"
        active={pathname.startsWith("/library")}
      />
      <NavLink
        component={Link}
        to="/tasks"
        label="Review & Approve"
        active={pathname.startsWith("/tasks")}
      />
      {can("report.compliance_checklist.read") && (
        // S-web-6: gated — only QMS Owner / Internal Auditor hold the SYSTEM report key.
        <NavLink
          component={Link}
          to="/compliance"
          label="Compliance"
          active={pathname.startsWith("/compliance")}
        />
      )}
      {can("import.review") && (
        // S-ing-4b: gated — import review is an admin-only SYSTEM key (no ABAC scope).
        <NavLink
          component={Link}
          to="/ingestion"
          label="Import"
          active={pathname.startsWith("/ingestion")}
        />
      )}
      {PHASES.map((phase) => {
        const top = (clauses ?? []).filter((c) => c.pdca_phase === phase && c.parent_id === null);
        if (top.length === 0) return null;
        return (
          <Box key={phase} mt="sm">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs">
              {phase}
            </Text>
            {top.map((c) => (
              // S-web-2: a clause link filters the Library by that exact clause number.
              <NavLink
                key={c.id}
                component={Link}
                to={`/library?clause=${encodeURIComponent(c.number)}`}
                label={`${c.number} ${c.title}`}
              />
            ))}
          </Box>
        );
      })}
    </Stack>
  );
}
```

- [ ] **Step 4: Run the LeftRail suite to verify it passes**

Run: `npm --prefix apps/web test -- src/app/shell/LeftRail.test.tsx`
Expected: PASS (the existing Home/Library/Review/Compliance cases + the 2 new Import gating cases).

- [ ] **Step 5: Write the failing App route tests**

Append these two cases to `apps/web/src/App.test.tsx` (the file already imports `screen`, `waitFor`, `expect`, `test`, `renderWithProviders`, and `App` — no new imports needed). They assert the runs landing renders its **"Import"** heading and the `:runId` route renders the review cockpit (the run fixture rests at `Proposed`, so `IngestionRunPage` shows `ReviewCockpit` per spec §5.4):

```tsx
test("the /ingestion route renders the runs landing", async () => {
  renderWithProviders(<App />, { route: "/ingestion" });
  expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
});

test("the /ingestion/:runId route renders the run page cockpit", async () => {
  renderWithProviders(<App />, {
    route: "/ingestion/10000000-0000-0000-0000-000000000001",
  });
  // the Proposed run fixture rests at the review cockpit (IngestionRunPage → ReviewCockpit)
  expect(await screen.findByRole("region", { name: "Review cockpit" })).toBeInTheDocument();
});
```

> Contract note for Tasks 16–17 (already locked when this task runs): `IngestionRunsPage` MUST render an `<h?>` whose accessible name is exactly `Import`, and `ReviewCockpit`'s root MUST be a labelled landmark with `aria-label="Review cockpit"` (e.g. `<Box component="section" aria-label="Review cockpit">` → `role="region"`). These two anchors are how the route resolution is asserted here; if those tasks chose different copy, align this test to their actual locked anchor rather than re-shaping the page.

- [ ] **Step 6: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/App.test.tsx`
Expected: FAIL — `/ingestion` resolves to the `<Route path="*" element={<Navigate to="/" replace />}>` fallthrough (no `ingestion` route mounted yet), so neither the `Import` heading nor the `Review cockpit` region is found.

- [ ] **Step 7: Add the two routes to `App.tsx`**

In `apps/web/src/App.tsx`, add the two feature-page imports next to the other `features/*` imports (after the `CompliancePage` import on line 17):

```tsx
import { IngestionRunsPage } from "./features/ingestion/IngestionRunsPage";
import { IngestionRunPage } from "./features/ingestion/IngestionRunPage";
```

Then add the two `<Route>` lines inside the operational `<Route path="/" element={operational ? <AppShell /> : <Navigate to="/setup" replace />}>` block, immediately after the `compliance` route (App.tsx:110):

```tsx
        <Route path="ingestion" element={<IngestionRunsPage />} />
        <Route path="ingestion/:runId" element={<IngestionRunPage />} />
```

For reference, the block now reads:

```tsx
      <Route path="/" element={operational ? <AppShell /> : <Navigate to="/setup" replace />}>
        <Route index element={<HomePage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/new" element={<NewDocumentWizard />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="tasks" element={<TasksInbox />} />
        <Route path="tasks/:id" element={<ReviewApprovePage />} />
        <Route path="search" element={<SearchResultsPage />} />
        <Route path="compliance" element={<CompliancePage />} />
        <Route path="ingestion" element={<IngestionRunsPage />} />
        <Route path="ingestion/:runId" element={<IngestionRunPage />} />
      </Route>
```

- [ ] **Step 8: Run both suites to verify they pass**

Run: `npm --prefix apps/web test -- src/App.test.tsx src/app/shell/LeftRail.test.tsx`
Expected: PASS — `/ingestion` renders the runs landing (`Import` heading), `/ingestion/:runId` renders the cockpit (`Review cockpit` region), and the gated Import entry is hidden by default / shown with `import.review`. (The existing App + LeftRail cases stay green.)

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx
git commit -m "feat(s-ing-4b): /ingestion routes + gated LeftRail Import entry"
```


---

## Phase 6 — Gate, docs, handoff

### Task 20: Full web gate + docs + handoff

**Files:**
- Modify: `docs/slice-history.md` (append the S-ing-4b entry)
- Modify: `CLAUDE.md` (Recent learnings + Current status)
- Modify: `docs/15-api-design.md` (note the `/admin/imports/*` surface is now UI-backed; no endpoint change)

- [ ] **Step 1: Run the full web CI loop**

Run: `npm --prefix apps/web run lint && npm --prefix apps/web run typecheck && npm --prefix apps/web run build && npm --prefix apps/web test`
Expected: all green — eslint clean, `tsc --noEmit` clean (strict `noUncheckedIndexedAccess` — fix any array-index/`Map.get` nit the per-file vitest run didn't catch), `vite build` OK, and the full vitest suite incl. the new ingestion tests + every `axe()` assertion. Remove any unused test imports the linter flags and re-run until green.

- [ ] **Step 2: Append the slice-history entry**

Append an `- **S-ing-4b**` bullet under the web-UI track section of `docs/slice-history.md` capturing: front-end only (no migration / no key / no `openapi.yaml` change — the S-ing-1..5 `/admin/imports/*` surface was already contracted); **CLOSES UJ-2** (import an existing QMS, end-to-end: runs landing → New-Import + scan-progress → review cockpit → pre-commit checklist → commit + resume); server-pagination (no virtualization / no new dep — the DOM never exceeds one page); the locked decisions (commit-enable = `ready && commit_ready ≥ 1`; unconfirmed-kind advisory-not-blocking; R10 kind-confirm a separate human act; merge/split server-authoritative; the `/files` filter limited to band/kind/disposition/review_status → clause/process/type facets + in-drawer preview + source-root browser deferred; "Already in vault" a documented v1-partial tab); the demo precondition (the `demo` System Administrator holds all three import keys, so it drives the whole loop — no personas needed); and the new test count. Bump the running web-test total.

- [ ] **Step 3: Refresh CLAUDE.md**

Add a `2026-06-08 — **S-ing-4b …**` bullet to **Recent learnings** (newest first; demote the oldest if the list exceeds ~12). Update **Current status**: mark S-web-6 **MERGED (#98/#99)** if still listed as open, add S-ing-4b as the new web-track head (UJ-2 closed), and confirm migration head stays `0044`.

- [ ] **Step 4: Update docs/15-api-design.md**

Add a one-line note in the import section that the `/admin/imports/*` endpoints are now surfaced by the S-ing-4b web UI (no endpoint/contract change).

- [ ] **Step 5: Commit the docs**

```bash
git add docs/slice-history.md CLAUDE.md docs/15-api-design.md
git commit -m "docs(s-ing-4b): slice-history + CLAUDE.md + api-design note (UJ-2 closed)"
```

- [ ] **Step 6: Handoff for review (orchestrator, outside this plan)**

This is front-end only, so the materially-exercised local gate is **web** (the api / migrations / integration jobs are unaffected and Linux-CI-only on this Windows box). Then: run the **diff-critic** agent on the branch diff (`Agent`, `subagent_type: diff-critic`) — hunt the false-PASS direction on the load-bearing invariants (R10 kind-confirm a separate act + advisory-not-blocking; the commit-enable predicate; one Idempotency-Key per bulk op; `useMe().id` not `sub`; no optimistic merge/split reshape; `status` strings beyond the enum tolerated; grouping-join + indexed-access fallbacks). Fold only confirmed findings. Open the PR with `/pr` (5 CI jobs; only `web` is materially exercised — the other four should be green/no-ops). After green CI, address any Codex review comments (verify, fix the legitimate ones, reply + resolve each thread via `gh api`), then squash-merge.

---

## Self-review (spec coverage)

Each spec section → the Task(s) that deliver it:

- **Spec §1 (journey) / §5.3 (routes & nav)** → Task 19 (routes + gated LeftRail) + Task 17/18 (run page + runs landing).
- **§5.1 (types)** → Task 1. **§5.5 (filters / queue mapping)** → Task 2. **§5.2 (hooks)** → Tasks 3–4.
- **§5.4 components:** RunSummaryTiles/ImportPlanBanner → Task 7; QueueTabs/IngestionFacetBar → Task 8; TriageTable (+ cells) → Tasks 5/6/9; BulkActionBar → Task 10; TriagePagination → Task 9; ItemDetailDrawer → Task 11; MergeMenu → Task 12; PreCommitChecklist → Task 13; CommitCard → Task 14; ReviewCockpit → Task 15; NewImportModal/ScanProgress/CommitProgress/RunTerminalSummary → Task 16; IngestionRunPage → Task 17; IngestionRunsPage → Task 18.
- **§3 (R10 kind-confirm a separate act)** → Tasks 5 (KindCell), 10 (bulk Confirm-kind, not auto), 13 (advisory `kind_unconfirmed`), 14 (commit-enable does NOT gate on unconfirmed kind), 15 (per-row/bulk confirm wiring).
- **§3 (commit gate = checklist `blocking[]`)** → Tasks 13 (blocking rows) + 14 (enable predicate) + the 422 calm handling in 14/17.
- **§4 (merge/split server-authoritative)** → Tasks 4 (invalidating mutations), 11 (split), 12 (merge), 15 (no optimistic reshape).
- **§6 (SoD gating)** → Tasks 14 (`import.commit`), 18 (`import.execute`), 19 (`import.review` nav), 17 (403-calm).
- **§7 (calm states)** → the empty/403/404/409/422 cases asserted in Tasks 9/11/13/14/16/17/18 (copy bank in the registry).
- **§8 (a11y)** → every component task asserts `axe(container)` clean.
- **§9 (open details)** → queue→filter mapping resolved in Task 2; "Already in vault" v1-partial in Tasks 2/8/15; `run.counts` keys pinned via the Task-1 fixture + the `countAt` accessor in Task 7/16; `onShowBlocker` (Task 13) jumps to the Needs-decision queue where conflicts are triaged (Task 15) — the `/files` contract has no per-id filter, so a precise jump-to-offenders is the v1 affordance.
- **§10 (out of scope)** → no tasks for the source-root browser, in-drawer preview, clause/process/type facets, process-scoped ABAC, saved presets (deferred by design).

**Type consistency:** all components bind to the Task-1 types + the Task-3/4 hook signatures + the registry prop names (verified in the adversarial pass below). Mutations uniformly take `{ body, idempotencyKey }` (+ `fileId` for the per-file decision); `crypto.randomUUID()` mints the key per action. queryKeys (`import-runs`/`import-run`/`import-files`/`import-file`/`import-dupe-clusters`/`import-version-families`/`import-checklist`/`import-decisions`) are used consistently by reads + the `useRunInvalidator`.

**No new backend / contract / migration / key** — consistent with the spec's front-end-only shape.

**Adversarial verification (folded 2026-06-08).** A 4-lens review pass (foundation-binding drift · codebase accuracy · cross-task composition · false-PASS on the load-bearing invariants) ran over the assembled plan; all confirmed findings were folded inline: the ReviewCockpit↔ItemDetailDrawer `onConfirmKind`/`onDecision`/`onSplit` arity (now adapter-wired with the active file id, split resolved via a `splitTargetMap`); cross-task aria-label/anchor alignment (ConfidenceCell `High` casing, the `Triage queue` table name, the `Bulk actions` + `Review cockpit` region landmarks, the `Source folder path` modal label, the KindCell menu click-through); two dead CSS tokens (`--es-bg-sunken`→`--es-surface-2`, `--es-text-secondary`→`--es-text-muted`); the commit-disabled cockpit test now grants `import.commit` so it exercises the `ready=false` branch (not the held-by-role branch); `ScanProgress` now degrades an unknown/additive status to a generic "Working…"; a Task-17 test pins the unknown-status→ScanProgress route (invariant 6); and `TriagePagination` receives `pageCount` so "Showing X–Y of N" renders.

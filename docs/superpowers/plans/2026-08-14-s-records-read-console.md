# Records Read Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give authorized operators a responsive, read-only Records register and stable detail route with authorization-correct search, filtering, cursor pagination, related labels, and fresh evidence downloads.

**Architecture:** Replace the incomplete pre-production `GET /records` array contract in place with a typed cursor page. Keep cursor encoding and query normalization pure, keep SQL responsible only for tenant/search/filter/keyset candidate ordering, and retain the canonical Python PDP as the authorization source of truth while scanning deterministic batches. Hydrate display labels only after record authorization and independently authorize every related navigation target. The React routes own URL state and compose focused register/detail components; existing API presign endpoints remain the only download path.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, pytest, OpenAPI, React 19, TypeScript 6, React Router 7, TanStack Query 5, Mantine 7, Vitest 4, MSW 2, Playwright Test Chromium.

## Global Constraints

- Work only in `/home/cjones/Desktop/EasySynQ/.worktrees/evidence-operations` on branch `codex/evidence-operations`; preserve the primary checkout's unrelated untracked `.superdesign/` directory and do not prune or modify either `/tmp` worktree record.
- Treat `docs/superpowers/specs/2026-08-14-s-records-read-console-design.md`, R3, R21, R26, R27, R39, R59, R65, ADR 0003, and ADR 0004 as binding.
- Read every relevant `docs/debt/` record before editing its hotspot. In particular, re-read `20260813234519-playwright-responsive-browser-harness.md`, `20260814022608-browser-probe-hardening.md`, `20260814035922-forced-colors-containment-proof.md`, and `20260813144730-responsive-register-cohort.md` before browser work.
- Replace the old bare-array `GET /records` response in place and migrate every repository consumer in the same slice. Do not add a compatibility endpoint, response shim, feature flag, or duplicate list path.
- Keep the slice read-only: no capture, correction, evidence-link, retention, disposition, legal-hold, WORM-destroy, or upload affordance.
- Preserve tenant isolation, deny-by-default, deny-always-wins, process binding, correction fallback, predicates, source-version pinning, append-only audit, WORM, and source-store boundaries.
- The API never emits SPA paths. Related-object labels and links require the related object's own read decision; a hidden target produces `target_readable=false` and `target_label=null`.
- Use the API default register page size of 50. Do not add totals, hidden counts, offset pagination, adjustable page sizes, general sorting, bulk selection, saved views, exports, or a universal table abstraction.
- Search is a trimmed, maximum-200-character, case-insensitive substring over identifier/title. Backslash, `%`, and `_` are literal user characters.
- Cursor order is `(captured_at DESC, id DESC)`. Cursor version and normalized-query fingerprint mismatches return the canonical `422 validation_error` problem.
- Browser evidence remains Chromium-only, one worker, zero retries, central fail-closed fixtures, and the dedicated authenticated test entry. It does not claim Firefox, WebKit, assistive-technology sessions, a live application stack, deployment, or Fedora proof.
- Start every behavior change with the smallest focused failing proof. Run the adjacent affected gate after GREEN, inspect the bounded diff, and make one scoped commit per task.
- At every task boundary, use an independent requirements review followed by an independent code-quality review when executing subagent-driven; when executing inline, stop for the same two review checkpoints before the task commit.
- Invoke `debt-ops:add` immediately for any newly deferred decision. Do not add an ADR unless a new architecturally significant choice has at least two credible alternatives; ADR 0004 already owns this slice's public-interface choice.
- Do not change `docs/current-status.md` test counts, CI topology, or `baseline_commit` until the corresponding full gate is freshly verified. Never rewrite `baseline_commit` merely because this branch began at `9b33f95`.
- Do not push, open a PR, or merge without explicit owner approval.

## File Ownership Map

- `packages/contracts/openapi.yaml`, `packages/contracts/dist/openapi.json`, `packages/contracts/.contract.lock`, `apps/api/src/easysynq_api/_generated/models.py`, `apps/web/src/api/_generated/schema.d.ts` — public request/response authority and generated artifacts.
- `apps/api/src/easysynq_api/services/records/listing.py` — normalized list criteria, query fingerprint, opaque cursor codec, and cursor validation.
- `apps/api/src/easysynq_api/services/records/repository.py` — deterministic candidate SQL and bounded label-source queries; no PDP reimplementation.
- `apps/api/src/easysynq_api/services/records/presentation.py` — post-authorization display hydration and independent related-target authorization.
- `apps/api/src/easysynq_api/api/records.py` — FastAPI query boundary, authorization-correct scan orchestration, summary/detail serialization, and canonical problems.
- `apps/api/src/easysynq_api/db/models/record.py`, `migrations/versions/0086_record_page_index.py` — matching deterministic-page index.
- `apps/api/tests/unit/test_record_list_cursor.py`, `test_record_list_contract.py`, `test_record_list_index.py`, `test_deploy_configuration.py` — pure/structural proofs.
- `apps/api/tests/integration/test_records.py`, `test_records_process_scope.py`, `test_record_schema.py`, `test_migration_coherence.py`, `test_contract_response_schemas.py` — API, authz, labels, migration, and response-contract proofs.
- `apps/web/src/lib/types.ts`, `apps/web/src/features/records/recordUrlState.ts`, `hooks.ts` — handwritten UI types, URL/query serialization, and record queries.
- `apps/web/src/features/records/RecordFilters.tsx`, `RecordsTable.tsx`, `RecordsPage.tsx` — register composition.
- `apps/web/src/features/records/RecordDownloadButton.tsx`, `RecordDetailSections.tsx`, `RecordDetailPage.tsx` — isolated download actions and detail composition.
- `apps/web/src/features/capa/hooks.ts`, `EvidenceLinker.tsx`, and their tests — atomic migration of the existing picker.
- `apps/web/src/App.tsx`, `apps/web/src/app/shell/LeftRail.tsx`, `Breadcrumb.tsx`, and tests — routes and permission-aware chrome.
- `apps/web/src/test/msw/handlers.ts` — canonical synthetic record page/detail/download fixtures.
- `apps/web/e2e/support/api.ts`, `registers.ts`, browser specs, and `apps/api/tests/unit/test_deploy_configuration.py` — Records browser evidence plus triggered harness hardening.
- `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`, `docs/debt/`, `docs/current-status.md`, and `docs/slice-history.md` — final reassessment, paid debt, and freshly verified evidence.

---

### Task 1: Publish the Record page and detail enrichment contract

**Files:**

- Create: `apps/api/tests/unit/test_record_list_contract.py`
- Modify: `packages/contracts/openapi.yaml`
- Regenerate: `packages/contracts/dist/openapi.json`
- Regenerate: `packages/contracts/.contract.lock`
- Regenerate: `apps/api/src/easysynq_api/_generated/models.py`
- Regenerate: `apps/web/src/api/_generated/schema.d.ts`

**Interfaces:**

- Consumes: approved request, `RecordSummary`, `RecordPage`, related-label, and error contracts from the design.
- Produces: `GET /records -> RecordPage`; enriched `Record`; enriched `EvidenceLink`; generated Python and TypeScript schema types.
- `RecordPage.page` is exactly `{limit, returned, next_cursor}` and exposes no total or hidden-row information.
- Detail-only lineage navigation flags are `correction_of_readable` and `superseded_by_correction_readable`; they default false when the related record is absent or independently unreadable.

- [ ] **Step 1: Write the failing structural contract test**

Create `apps/api/tests/unit/test_record_list_contract.py`:

```python
from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"


def test_records_list_publishes_cursor_page_without_hidden_counts() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation = spec["paths"]["/records"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert names == {
        "limit", "cursor", "q", "record_type", "source_document_id",
        "captured_by", "disposition_state", "legal_hold",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RecordPage"
    }
    page = spec["components"]["schemas"]["RecordPage"]
    assert page["required"] == ["data", "page"]
    assert set(page["properties"]["page"]["properties"]) == {
        "limit", "returned", "next_cursor"
    }


def test_record_contract_publishes_safe_related_navigation_fields() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    summary = spec["components"]["schemas"]["RecordSummary"]["properties"]
    assert {
        "captured_by_display_name", "source_document_identifier",
        "source_document_title", "source_document_readable",
        "source_version_label", "retention_policy_name",
    } <= summary.keys()
    record = spec["components"]["schemas"]["Record"]["properties"]
    assert record["correction_of_readable"] == {"type": "boolean"}
    assert record["superseded_by_correction_readable"] == {"type": "boolean"}
    link = spec["components"]["schemas"]["EvidenceLink"]
    assert {"target_label", "target_readable"} <= link["required"]
```

- [ ] **Step 2: Run RED**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_contract.py -q
```

Expected: FAIL because `/records` still returns an array and the new schemas/fields do not exist.

- [ ] **Step 3: Replace the list contract and enrich the detail schemas**

In `packages/contracts/openapi.yaml`:

- describe `q` with `maxLength: 200`, `cursor` as opaque/versioned, and every `422` condition;
- replace the `200` array schema with `$ref: '#/components/schemas/RecordPage'`;
- define `RecordSummary` with every field from design §5.4;
- define `RecordPage` with `additionalProperties: false`, `data`, and page metadata;
- add display-label and source readability fields to `Record`;
- add required boolean lineage readability fields to `Record`;
- add required `target_label` (`string | null`) and `target_readable` (`boolean`) to `EvidenceLink`;
- retain the existing `Record` evidence blobs, form values, hashes, lifecycle fields, and all write contracts unchanged.

Use these page definitions verbatim:

```yaml
RecordPage:
  type: object
  additionalProperties: false
  required: [data, page]
  properties:
    data:
      type: array
      items: { $ref: "#/components/schemas/RecordSummary" }
    page:
      type: object
      additionalProperties: false
      required: [limit, returned, next_cursor]
      properties:
        limit: { type: integer, minimum: 1, maximum: 100 }
        returned: { type: integer, minimum: 0 }
        next_cursor: { type: [string, "null"] }
```

- [ ] **Step 4: Regenerate only through the repository command**

Run:

```bash
just contracts
cd apps/api && uv run pytest tests/unit/test_record_list_contract.py -q
just contracts-check
```

Expected: contract test PASS; generated artifacts and lock match the edited OpenAPI source.

- [ ] **Step 5: Review and commit the contract checkpoint**

Run:

```bash
git diff --check -- packages/contracts/openapi.yaml packages/contracts/dist/openapi.json packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py apps/web/src/api/_generated/schema.d.ts apps/api/tests/unit/test_record_list_contract.py
git diff --stat
git add packages/contracts/openapi.yaml packages/contracts/dist/openapi.json packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py apps/web/src/api/_generated/schema.d.ts apps/api/tests/unit/test_record_list_contract.py
git commit -m "feat: publish records read page contract"
```

---

### Task 2: Implement normalized criteria and the opaque cursor codec

**Files:**

- Create: `apps/api/src/easysynq_api/services/records/listing.py`
- Create: `apps/api/tests/unit/test_record_list_cursor.py`

**Interfaces:**

- Consumes: `RecordType`, `RecordDispositionState`, UUID filter values, and normalized search.
- Produces: `RecordListCriteria`, `RecordListCursor`, `InvalidRecordCursor`, `normalize_record_search`, `escape_ilike_literal`, `encode_record_cursor`, and `decode_record_cursor`.
- Cursor JSON has exact keys `v`, `captured_at`, `id`, and `query`; version is integer `1`; `query` is a SHA-256 fingerprint, not raw search/filter data.

- [ ] **Step 1: Write cursor/search tests**

Create tests covering round-trip, UTC-aware timestamps, stable fingerprints, blank search, 200/201 trimmed characters, literal wildcard escaping, malformed base64/JSON, extra/missing payload keys, unsupported version, naive timestamp, and query mismatch. The core test is:

```python
def test_cursor_round_trips_and_binds_normalized_query() -> None:
    criteria = RecordListCriteria(q="needle", legal_hold=False)
    boundary = RecordListCursor(
        captured_at=datetime.datetime(2026, 8, 14, 12, 0, tzinfo=datetime.UTC),
        record_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    token = encode_record_cursor(boundary, criteria)
    assert decode_record_cursor(token, criteria) == boundary
    with pytest.raises(InvalidRecordCursor, match="query"):
        decode_record_cursor(token, dataclasses.replace(criteria, q="different"))


def test_search_escape_treats_like_metacharacters_literally() -> None:
    assert escape_ilike_literal(r"50%_done\\") == r"50\%\_done\\\\"
```

- [ ] **Step 2: Run RED**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_cursor.py -q
```

Expected: collection FAIL because `services.records.listing` does not exist.

- [ ] **Step 3: Implement the exact pure surface**

Use frozen, slotted dataclasses and URL-safe base64 without retained padding:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class RecordListCriteria:
    q: str | None = None
    record_type: RecordType | None = None
    source_document_id: uuid.UUID | None = None
    captured_by: uuid.UUID | None = None
    disposition_state: RecordDispositionState | None = None
    legal_hold: bool | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RecordListCursor:
    captured_at: datetime.datetime
    record_id: uuid.UUID


class InvalidRecordCursor(ValueError):
    pass
```

Normalize `q` with `.strip()` and convert blank to `None`; validate the normalized length. Preserve the trimmed text for SQL, but casefold it in the sorted, compact fingerprint JSON because search itself is case-insensitive. Include all six criteria in that JSON. Decode with strict payload-key equality, `validate=True`-equivalent URL-safe handling, an aware ISO timestamp, UUID parsing, version equality, and `hmac.compare_digest` for the fingerprint. Convert every decode failure into `InvalidRecordCursor("invalid cursor")`; use `InvalidRecordCursor("cursor does not match query")` for the fingerprint mismatch.

- [ ] **Step 4: Run GREEN and static checks**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_cursor.py -q
cd apps/api && uv run ruff check src/easysynq_api/services/records/listing.py tests/unit/test_record_list_cursor.py
cd apps/api && uv run mypy src/easysynq_api/services/records/listing.py
```

Expected: all commands PASS.

- [ ] **Step 5: Review and commit**

```bash
git diff --check -- apps/api/src/easysynq_api/services/records/listing.py apps/api/tests/unit/test_record_list_cursor.py
git add apps/api/src/easysynq_api/services/records/listing.py apps/api/tests/unit/test_record_list_cursor.py
git commit -m "feat: add records cursor contract"
```

---

### Task 3: Add migration 0086 and deterministic candidate SQL

**Files:**

- Create: `migrations/versions/0086_record_page_index.py`
- Modify: `apps/api/src/easysynq_api/db/models/record.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Create: `apps/api/tests/unit/test_record_list_index.py`
- Modify: `apps/api/tests/integration/test_migration_coherence.py`
- Modify: `apps/api/tests/integration/test_records.py`

**Interfaces:**

- Consumes: `RecordListCriteria` and optional `RecordListCursor` from Task 2.
- Produces: `list_record_candidates(session, org_id, *, criteria, after, limit) -> list[tuple[Record, DocumentedInformation]]`.
- Migration/model index name: `ix_record_org_id_captured_at_id_desc`.

- [ ] **Step 1: Confirm the live migration head before writing**

Run:

```bash
cd apps/api && uv run alembic heads
```

Expected: exactly `0085_user_credential_issued (head)`. If it differs, stop this task and reconcile the new head before choosing a revision id.

- [ ] **Step 2: Write failing index and candidate-order tests**

Create `test_record_list_index.py` to assert the model contains the named three-expression index. Extend `test_migration_coherence.py` to assert the populated `upgrade head -> downgrade 0085 -> upgrade head` cycle removes and restores only that index and that `pg_indexes.indexdef` contains `org_id`, `captured_at DESC`, and `id DESC` in that order.

Add an integration test that inserts/captures records with an equal `captured_at` and asserts candidate IDs sort descending, then passes a boundary and asserts only rows strictly after it remain.

- [ ] **Step 3: Run RED**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_index.py -q
cd apps/api && uv run pytest tests/integration/test_records.py -k 'candidate_order' -q
```

Expected: FAIL because the index and candidate function are absent.

- [ ] **Step 4: Add the index and repository query**

Add the matching SQLAlchemy index and create migration 0086 with:

```python
revision = "0086_record_page_index"
down_revision = "0085_user_credential_issued"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_record_org_id_captured_at_id_desc "
        "ON record (org_id, captured_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.drop_index("ix_record_org_id_captured_at_id_desc", table_name="record")
```

Replace the old pre-authorization `list_records` repository function with `list_record_candidates`. Build tenant, five filter, escaped `ILIKE`, and keyset predicates in SQL. The keyset predicate is:

```python
or_(
    Record.captured_at < after.captured_at,
    and_(Record.captured_at == after.captured_at, Record.id < after.record_id),
)
```

Order by both descending columns and apply only the caller-supplied candidate batch `limit`. Do not add authorization joins or a scan cap.

- [ ] **Step 5: Run focused migration/repository GREEN**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_index.py -q
cd apps/api && uv run pytest tests/integration/test_records.py -k 'candidate_order or literal_search_filters' -q
cd apps/api && uv run alembic heads
```

Expected: focused tests PASS and only `0086_record_page_index` is head.

- [ ] **Step 6: Review and commit**

```bash
git diff --check -- migrations/versions/0086_record_page_index.py apps/api/src/easysynq_api/db/models/record.py apps/api/src/easysynq_api/services/records/repository.py apps/api/tests/unit/test_record_list_index.py apps/api/tests/integration/test_migration_coherence.py apps/api/tests/integration/test_records.py
git add migrations/versions/0086_record_page_index.py apps/api/src/easysynq_api/db/models/record.py apps/api/src/easysynq_api/services/records/repository.py apps/api/tests/unit/test_record_list_index.py apps/api/tests/integration/test_migration_coherence.py apps/api/tests/integration/test_records.py
git commit -m "feat: add deterministic records candidate paging"
```

---

### Task 4: Build authorization-correct readable pages

**Files:**

- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/tests/integration/test_records.py`
- Modify: `apps/api/tests/integration/test_records_process_scope.py`

**Interfaces:**

- Consumes: `RecordListCriteria`, cursor codec, `list_record_candidates`, `gather_grants`, `record_process_ids_for`, `record_process_ids_effective`, and canonical `authorize`.
- Produces: `GET /records` envelope with post-auth page boundaries and canonical `422 validation_error` cursor failures.
- Candidate batch size is `max(100, min(500, limit * 4))`; this is an internal fetch size only and never appears in a response. The registered `record-authz-page-scan` debt owns the simple-correct-first payoff trigger.

- [ ] **Step 1: Convert and extend the HTTP tests before the endpoint**

First change every existing list assertion from `listed.json()` to `listed.json()["data"]`. Then add focused tests for:

```python
body = listed.json()
assert body["page"] == {"limit": 2, "returned": 2, "next_cursor": body["page"]["next_cursor"]}
assert body["page"]["next_cursor"] is not None
second = await app_client.get(
    "/api/v1/records", headers=h, params={"limit": 2, "cursor": body["page"]["next_cursor"]}
)
assert not ({row["id"] for row in body["data"]} & {row["id"] for row in second.json()["data"]})
```

Also prove: default/max limits, equal-timestamp tie-break, exact-page/no-readable endings, blank and trimmed search, case-insensitive identifier/title matching, literal `%`/`_`/backslash, a known search hit behind more than 100 unrelated or hidden candidates, each filter and a representative combination, malformed/unsupported/mismatched cursor, empty grants, explicit deny, time predicate, hidden candidates between visible rows, process grants, and multi-hop source-less correction fallback.

- [ ] **Step 2: Run RED on the smallest page tests**

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_records.py -k 'list_page_envelope or cursor_query_mismatch or hidden_candidates' -q
```

Expected: FAIL because the endpoint still returns a pre-auth capped array.

- [ ] **Step 3: Replace the endpoint orchestration**

Add a Pydantic query model whose `q` pre-validator strips before enforcing 200 characters and whose enums/UUIDs/limit use typed validation. At the route boundary:

```python
criteria = RecordListCriteria(
    q=params.q,
    record_type=params.record_type,
    source_document_id=params.source_document_id,
    captured_by=params.captured_by,
    disposition_state=params.disposition_state,
    legal_hold=params.legal_hold,
)
try:
    client_after = decode_record_cursor(params.cursor, criteria) if params.cursor else None
except InvalidRecordCursor as exc:
    raise ProblemException(
        status=422, code="validation_error", title="Invalid records cursor"
    ) from exc
```

Gather `record.read` grants once. Return an immediate empty page when the grant sequence is empty. Otherwise scan deterministic candidate batches, batch-load process IDs once per batch, run the existing correction fallback only for source-less corrected rows with an empty own union, and call canonical `authorize` with `RequestContext(now=..., source_ip=...)`. Stop at `limit + 1` readable rows or candidate exhaustion. Return the first `limit`; encode `next_cursor` from the last returned readable row only when the extra readable row exists.

Do not return a cursor when zero rows are readable, do not advance the client boundary to a hidden candidate, and do not translate a denied row into a 403.

- [ ] **Step 4: Run authz/page GREEN**

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_records.py -k 'list or cursor or search or filter' -q
cd apps/api && uv run pytest tests/integration/test_records_process_scope.py -k 'record_list or correction_chain' -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Review and commit**

```bash
git diff --check -- apps/api/src/easysynq_api/api/records.py apps/api/tests/integration/test_records.py apps/api/tests/integration/test_records_process_scope.py
git add apps/api/src/easysynq_api/api/records.py apps/api/tests/integration/test_records.py apps/api/tests/integration/test_records_process_scope.py
git commit -m "feat: page readable records after authorization"
```

---

### Task 5: Hydrate list/detail labels without widening authority

**Files:**

- Create: `apps/api/src/easysynq_api/services/records/presentation.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/tests/integration/test_records.py`
- Modify: `apps/api/tests/integration/test_records_process_scope.py`

**Interfaces:**

- Consumes: authorized record rows and evidence links only.
- Produces:
  - `hydrate_record_labels(session, caller, rows, ctx) -> dict[UUID, RecordLabels]`;
  - `hydrate_evidence_target_labels(session, caller, links, ctx) -> dict[UUID, EvidenceTargetLabel]`;
  - list-safe summaries and enriched full details.
- Labels: source document `identifier`/`title`; source version `revision_label`; captured actor `display_name`; retention policy `name`; clause `number — title`; process `name`; finding/CAPA-stage labels from their authorized shared record/CAPA context.
- Existing SPA target mapping is available only for `document` (`/documents/:id`); the backend still returns no route.

- [ ] **Step 1: Write failing hydration and restricted-target tests**

Add tests that capture a source-backed record and assert one list/detail response contains all display labels without per-row follow-up. Add SQL statement counting around hydration and assert the query count stays constant when the page grows from one to several rows.

Add two principals so the current record is readable but its source document, predecessor/successor, or evidence target is not. Assert:

```python
assert detail["source_document_readable"] is False
assert detail["source_document_identifier"] is None
assert detail["source_document_title"] is None
assert detail["correction_of_readable"] is False
assert detail["evidence_links"][0]["target_readable"] is False
assert detail["evidence_links"][0]["target_label"] is None
```

Also prove independently readable targets return labels/readable true, cross-org targets never hydrate, and the current record's own read grant never substitutes for `document.read`, `process.read`, `clauseMap.read`, `finding.read`, or `capa.read`.

- [ ] **Step 2: Run RED**

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_records.py -k 'display_labels or evidence_target_labels' -q
cd apps/api && uv run pytest tests/integration/test_records_process_scope.py -k 'related_readable or related_restricted' -q
```

Expected: FAIL because serializers emit raw IDs only.

- [ ] **Step 3: Implement bounded source loading and independent decisions**

Define:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class RecordLabels:
    captured_by_display_name: str | None
    source_document_identifier: str | None
    source_document_title: str | None
    source_document_readable: bool
    source_version_label: str | None
    retention_policy_name: str | None
    correction_of_readable: bool
    superseded_by_correction_readable: bool


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceTargetLabel:
    label: str | None
    readable: bool
```

Repository helpers must load each entity family with one `IN (...)` query per family, constrained to the caller's organization directly or through its tenant anchor. Gather each permission family at most once. Reuse `vault_repo.process_ids_for_docs` plus `resource_from_doc` for source documents, `record_process_ids_for` plus correction fallback for lineage records, PROCESS resource tuples for processes/CAPAs, SYSTEM for `clauseMap.read`/`finding.read`, and the CAPA parent process for stage targets. Apply predicates and deny-always-wins through `authorize`; do not call route dependencies or emit authz-audit events for display probes. Emit `source_version_label` only when the pinned version belongs to the source document and that document independently passed `document.read`.

Hydrate only the readable page, not hidden candidates. Hydrate full-detail links only after the current record's `_read` dependency passes.

- [ ] **Step 4: Extend serializers**

Split `_record` into `_record_summary(record, base, labels)` and `_record_detail(record, base, labels, *, evidence_blobs, evidence_links)`. Keep hashes/form values/evidence out of summaries. For every evidence link merge:

```python
{
    **_evidence_link(link),
    "target_label": target_labels[link.id].label,
    "target_readable": target_labels[link.id].readable,
}
```

Return null labels rather than IDs or fabricated text when a related object is hidden.

- [ ] **Step 5: Run GREEN and the existing record gates**

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_records.py -q
cd apps/api && uv run pytest tests/integration/test_records_process_scope.py -q
cd apps/api && uv run pytest tests/integration/test_record_schema.py -q
```

Expected: all commands PASS; existing capture/detail/download behavior remains intact.

- [ ] **Step 6: Review and commit**

```bash
git diff --check -- apps/api/src/easysynq_api/services/records/presentation.py apps/api/src/easysynq_api/services/records/repository.py apps/api/src/easysynq_api/api/records.py apps/api/tests/integration/test_records.py apps/api/tests/integration/test_records_process_scope.py
git add apps/api/src/easysynq_api/services/records/presentation.py apps/api/src/easysynq_api/services/records/repository.py apps/api/src/easysynq_api/api/records.py apps/api/tests/integration/test_records.py apps/api/tests/integration/test_records_process_scope.py
git commit -m "feat: hydrate authorized record labels"
```

---

### Task 6: Migrate the CAPA evidence picker off the array contract

**Files:**

- Modify: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/features/records/hooks.ts`
- Modify: `apps/web/src/features/capa/hooks.ts`
- Modify: `apps/web/src/features/capa/EvidenceLinker.tsx`
- Modify: `apps/web/src/features/capa/EvidenceLinker.test.tsx`
- Modify: `apps/web/src/features/capa/hooks.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts`

**Interfaces:**

- Consumes: new `RecordPage` response.
- Produces: `useRecords(request: RecordListRequest)` and `useRecord(recordId)` in the Records feature; CAPA maps `page.data` and keeps its 100-row bounded picker behavior.
- Deletes the old CAPA-owned bare-array hook and every “bare array” comment/fixture assumption.

- [ ] **Step 1: Change the MSW response to a page and write the failing picker test**

Define handwritten UI types matching the contract and return:

```ts
export const recordsFixture: RecordPage = {
  data: [/* the existing two synthetic records with the full summary shape */],
  page: { limit: 100, returned: 2, next_cursor: null },
};
```

Add a hook test that records the request URL and a picker test that selects `REC-000041` from `data`.

- [ ] **Step 2: Run RED**

Run:

```bash
npm --prefix apps/web test -- src/features/capa/EvidenceLinker.test.tsx src/features/capa/hooks.test.tsx
```

Expected: FAIL because `EvidenceLinker` maps the page object as an array.

- [ ] **Step 3: Add shared Records queries and migrate CAPA**

In `features/records/hooks.ts`:

```ts
export function useRecords(request: RecordListRequest) {
  const api = useApi();
  const query = buildRecordsQuery(request);
  return useQuery({
    queryKey: ["records", query],
    queryFn: () => api.get<RecordPage>(`/api/v1/records?${query}`),
    retry: false,
  });
}

export function useRecord(recordId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["record", recordId],
    queryFn: () => api.get<RecordDetail>(`/api/v1/records/${recordId!}`),
    enabled: recordId !== null,
    retry: false,
  });
}
```

For this task, implement `buildRecordsQuery` locally with `limit` and defined values in stable contract order; Task 7 moves the full URL/query logic into its pure module. In CAPA call `useRecords({limit: 100})` and map `(page?.data ?? [])`.

- [ ] **Step 4: Run GREEN and typecheck**

Run:

```bash
npm --prefix apps/web test -- src/features/capa/EvidenceLinker.test.tsx src/features/capa/hooks.test.tsx
npm --prefix apps/web run typecheck
rg -n 'bare array|RecordSummary\[\].*/api/v1/records|json\(recordsFixture\.data\)' apps/web/src
```

Expected: tests/typecheck PASS; search finds no obsolete array-contract comment or consumer.

- [ ] **Step 5: Review and commit**

```bash
git diff --check -- apps/web/src/lib/types.ts apps/web/src/features/records/hooks.ts apps/web/src/features/capa/hooks.ts apps/web/src/features/capa/EvidenceLinker.tsx apps/web/src/features/capa/EvidenceLinker.test.tsx apps/web/src/features/capa/hooks.test.tsx apps/web/src/test/msw/handlers.ts
git add apps/web/src/lib/types.ts apps/web/src/features/records/hooks.ts apps/web/src/features/capa/hooks.ts apps/web/src/features/capa/EvidenceLinker.tsx apps/web/src/features/capa/EvidenceLinker.test.tsx apps/web/src/features/capa/hooks.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "refactor: migrate evidence picker to record pages"
```

---

### Task 7: Make register URL state and query serialization deterministic

**Files:**

- Create: `apps/web/src/features/records/recordUrlState.ts`
- Create: `apps/web/src/features/records/recordUrlState.test.ts`
- Modify: `apps/web/src/features/records/hooks.ts`
- Create: `apps/web/src/features/records/hooks.test.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/lib/api.test.ts`

**Interfaces:**

- Consumes: URL keys `q`, `record_type`, `disposition_state`, `legal_hold`, `source_document_id`, `captured_by`, `cursor`.
- Produces: `parseRecordUrlState`, `buildRecordsQuery`, `replaceRecordCriteria`, `pushRecordCursor`, cancellable API GETs, and hooks for source-document typeahead.
- Search/filter replacement clears cursor; cursor advance preserves criteria and uses a new history entry.

- [ ] **Step 1: Write pure failing tests**

Cover unique-value parsing, invalid local enum/boolean values retained for server validation rather than silently changed, stable serialization, blank omission, cursor reset, cursor-only removal, and preservation of unrelated query keys. Pin history intent with return values:

```ts
expect(replaceRecordCriteria(new URLSearchParams("q=old&cursor=abc"), { q: "new" }).toString())
  .toBe("q=new");
expect(pushRecordCursor(new URLSearchParams("q=new"), "next-token").toString())
  .toBe("q=new&cursor=next-token");
expect(clearRecordCursor(new URLSearchParams("q=new&cursor=bad")).toString())
  .toBe("q=new");
```

Add `api.test.ts` with a pre-aborted `AbortController`, call `apiGet("/api/v1/records", "token", {signal})`, and assert the fetch rejects with `AbortError` without completing a response. In the hook test, change URL criteria while the first request is pending and assert its received signal becomes aborted.

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web test -- src/features/records/recordUrlState.test.ts src/features/records/hooks.test.tsx src/lib/api.test.ts
```

Expected: FAIL because the pure module does not exist.

- [ ] **Step 3: Implement the pure state contract and hook queries**

Define:

```ts
export interface RecordUrlState {
  q?: string;
  record_type?: string;
  disposition_state?: string;
  legal_hold?: string;
  source_document_id?: string;
  captured_by?: string;
  cursor?: string;
}
```

Serialize API parameters in this exact order: `limit`, `cursor`, `q`, `record_type`, `source_document_id`, `captured_by`, `disposition_state`, `legal_hold`. Use `URLSearchParams`; never concatenate user values.

Keep raw URL filter strings so an invalid deep link reaches the API and receives the canonical 422 instead of being silently rewritten. UI controls narrow their own selected values to the published enum/boolean choices.

Extend `request`, `apiGet`, and `useApi().get` with an optional `{signal?: AbortSignal}` argument passed directly to `fetch`. In every Records query use React Query's `queryFn: ({signal}) => api.get(url, {signal})`, so a superseded URL query cannot complete into the newer view.

Add `useRecordSourceDocuments(q, enabled)` over `/api/v1/documents?limit=20&offset=0` plus `q` when nonblank. Enable it while the picker is open, debounce its typed term, and accept only rows returned by the row-filtered Documents endpoint. A selected deep-link UUID absent from the returned options keeps the neutral `Selected item unavailable` fallback; never query a raw UUID through an endpoint that searches identifier/title. Continue using `useUserDirectory` for captured-by labels.

- [ ] **Step 4: Run GREEN**

```bash
npm --prefix apps/web test -- src/features/records/recordUrlState.test.ts src/features/records/hooks.test.tsx src/lib/api.test.ts
npm --prefix apps/web run typecheck
```

Expected: PASS.

- [ ] **Step 5: Review and commit**

```bash
git diff --check -- apps/web/src/features/records/recordUrlState.ts apps/web/src/features/records/recordUrlState.test.ts apps/web/src/features/records/hooks.ts apps/web/src/features/records/hooks.test.tsx apps/web/src/lib/api.ts apps/web/src/lib/api.test.ts
git add apps/web/src/features/records/recordUrlState.ts apps/web/src/features/records/recordUrlState.test.ts apps/web/src/features/records/hooks.ts apps/web/src/features/records/hooks.test.tsx apps/web/src/lib/api.ts apps/web/src/lib/api.test.ts
git commit -m "feat: add records URL state"
```

---

### Task 8: Build and route the responsive Records register

**Files:**

- Create: `apps/web/src/features/records/RecordFilters.tsx`
- Create: `apps/web/src/features/records/RecordsTable.tsx`
- Create: `apps/web/src/features/records/RecordsPage.tsx`
- Create: `apps/web/src/features/records/RecordsPage.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.test.tsx`
- Modify: `apps/web/src/app/shell/LeftRail.tsx`
- Modify: `apps/web/src/app/shell/LeftRail.test.tsx`
- Modify: `apps/web/src/app/shell/Breadcrumb.tsx`
- Modify: `apps/web/src/app/shell/Breadcrumb.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts`

**Interfaces:**

- Consumes: Task 7 URL helpers, `useRecords({limit: 50, ...state})`, directory/source-document label sources, shared state components, time formatting, status glyphs, and `Table.ScrollContainer`.
- Produces: `/records`, one semantic six-column table, native identifier links, stacked 320-pixel toolbar, returned-count copy, filters/chips, cursor Next, and complete page states.

- [ ] **Step 1: Write route/register RED tests**

Cover:

- route renders at `/records`;
- Records rail link appears with effective SYSTEM `record.read` and is hidden without it;
- direct route still renders and relies on the API if the rail link is hidden;
- breadcrumb labels `/records` as `Records`;
- search debounce replaces URL and clears cursor;
- each filter serializes UUID/enum/bool and Clear all removes only record criteria;
- Next pushes a cursor history entry and a native detail link carries `{from: pathname + search}`;
- Back returns to the exact filtered cursor URL;
- loading, unfiltered empty, filtered empty, retryable error, and invalid-cursor page states;
- one `Table.ScrollContainer` at `minWidth={840}` with headers Identifier, Title, Type, Captured by, Captured, State;
- structural rows and exactly one `a[data-rownav]` per row;
- axe has no serious or critical violation.

The structural assertion must include:

```ts
const table = expectResponsiveTable(840);
expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
  "Identifier", "Title", "Type", "Captured by", "Captured", "State",
]);
const row = within(table).getByRole("link", { name: /open record REC-000041/i }).closest("tr")!;
expect(row).not.toHaveAttribute("role");
expect(row).not.toHaveAttribute("tabindex");
expect(within(row).getAllByRole("link")).toHaveLength(1);
```

- [ ] **Step 2: Run the smallest RED**

```bash
npm --prefix apps/web test -- src/features/records/RecordsPage.test.tsx
```

Expected: FAIL because the route and components do not exist.

- [ ] **Step 3: Implement filters and table**

`RecordFilters` renders a debounced accessible search plus record type, disposition, legal-hold, source-document, and captured-by selects. Use a wrapping/stacking Mantine layout whose controls have `miw={0}`, `w={{base: "100%", sm: "auto"}}`, and `mih={44}` where interactive. Render removable active-filter chips and Clear all. An unresolved selected UUID displays `Selected item unavailable` without fetching an unauthorized label.

`RecordsTable` uses one `Table.ScrollContainer minWidth={840}` and one `Table`. The identifier cell is:

```tsx
<Anchor
  component={Link}
  to={`/records/${record.id}`}
  state={{ from: `${location.pathname}${location.search}` }}
  data-rownav
  aria-label={`Open record ${record.identifier ?? record.id}`}
>
  {record.identifier ?? "Record"}
</Anchor>
```

State text combines a non-color glyph, disposition label, and `Legal hold` when true. Do not add sorting controls.

- [ ] **Step 4: Implement page state/history and app chrome**

`RecordsPage` always keeps the toolbar mounted after initial render. Debounce search to URL replacement, use replacement for filters, use a normal history push for Next, and detect `ApiError(status=422, code="validation_error")` with a cursor as invalid-page. The invalid-page action is a native link/button that removes only `cursor`. Error Retry calls the current query's `refetch` without changing the URL.

Add routes for `/records` and later `/records/:recordId`. Add `Records` under DO in the rail, gated by the current coarse effective `record.read` result; retain direct-route API enforcement. Add breadcrumb labels and a `records` detail fallback.

- [ ] **Step 5: Run GREEN and affected chrome tests**

```bash
npm --prefix apps/web test -- src/features/records/RecordsPage.test.tsx src/App.test.tsx src/app/shell/LeftRail.test.tsx src/app/shell/Breadcrumb.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
```

Expected: all commands PASS.

- [ ] **Step 6: Review and commit**

```bash
git diff --check -- apps/web/src/features/records/RecordFilters.tsx apps/web/src/features/records/RecordsTable.tsx apps/web/src/features/records/RecordsPage.tsx apps/web/src/features/records/RecordsPage.test.tsx apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx apps/web/src/app/shell/Breadcrumb.tsx apps/web/src/app/shell/Breadcrumb.test.tsx apps/web/src/test/msw/handlers.ts
git add apps/web/src/features/records/RecordFilters.tsx apps/web/src/features/records/RecordsTable.tsx apps/web/src/features/records/RecordsPage.tsx apps/web/src/features/records/RecordsPage.test.tsx apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx apps/web/src/app/shell/Breadcrumb.tsx apps/web/src/app/shell/Breadcrumb.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat: add records register"
```

---

### Task 9: Build the record detail and isolated presigned downloads

**Files:**

- Create: `apps/web/src/features/records/RecordDownloadButton.tsx`
- Create: `apps/web/src/features/records/RecordDownloadButton.test.tsx`
- Create: `apps/web/src/features/records/RecordDetailSections.tsx`
- Create: `apps/web/src/features/records/RecordDetailPage.tsx`
- Create: `apps/web/src/features/records/RecordDetailPage.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/app/shell/Breadcrumb.tsx`
- Modify: `apps/web/src/app/shell/Breadcrumb.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts`

**Interfaces:**

- Consumes: `useRecord`, evidence download endpoint, rendition endpoint, authorized related labels/flags, and origin location state.
- Produces: `/records/:recordId`, five detail groups, conditional lineage/source links, safe structured-value rendering, per-action download state, and 403/404/retry states.
- `RecordDownloadButton` takes `{label, endpoint, pendingIsNormal?: boolean}` and owns only its request/error state.

- [ ] **Step 1: Write failing download tests**

Prove each activation makes a fresh API request, calls:

```ts
expect(openSpy).toHaveBeenCalledWith(
  "https://objects.example.test/records/evidence.pdf",
  "_blank",
  "noopener,noreferrer",
);
```

Assert `fetch` is called only for the EasySynQ presign endpoint and is never called for the returned object-store URL; `window.open` receives only that returned URL, so no EasySynQ bearer can be attached to it. Prove one failed evidence action leaves another enabled, retry clears only that action's error, `403` reports unavailable access, `404` reports missing evidence, storage/presign failure reports a retryable download error, and `409 rendition_pending` renders `Structured PDF is not ready yet` rather than a record-level error.

- [ ] **Step 2: Run download RED**

```bash
npm --prefix apps/web test -- src/features/records/RecordDownloadButton.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement isolated download actions**

On click, call the EasySynQ API with `useApi().get<RecordDownload>(endpoint)`, then pass only `download_url` to `window.open`. Keep `loading`, `error`, and pending-rendition copy in component state keyed by component instance. Never fetch the presign on render and never use `api.get` on the returned object-store URL.

- [ ] **Step 4: Write detail RED tests**

Cover header identity/state, provenance, lifecycle, evidence metadata, structured values, evidence-for links, optional-section omission, source/correction/document links only when readable, neutral `Restricted related item`, origin-aware Back, bookmark fallback `/records`, route-focus restoration through the existing route chrome, detail 403, detail 404, generic retry, no stale detail content after a forbidden/not-found parameter change, and axe.

- [ ] **Step 5: Run detail RED**

```bash
npm --prefix apps/web test -- src/features/records/RecordDetailPage.test.tsx
```

Expected: FAIL because the detail route is not implemented.

- [ ] **Step 6: Implement detail composition**

Use a responsive `SimpleGrid cols={{base: 1, md: 2}}` for paired sections. Render structured values recursively as React definition/list structures: humanize keys by replacing `_` with spaces; strings/numbers/booleans/null become text; arrays become list items; nested objects become nested key/value lists. React text escaping remains the safety boundary. Do not use raw JSON or `dangerouslySetInnerHTML` as primary presentation.

Map only `target_type === "document" && target_readable` to `/documents/:target_id`. Use readable flags for source and lineage links. Back action uses `location.state.from` only when it is a string beginning with `/records`; otherwise link to `/records`.

- [ ] **Step 7: Run GREEN and affected route tests**

```bash
npm --prefix apps/web test -- src/features/records/RecordDownloadButton.test.tsx src/features/records/RecordDetailPage.test.tsx src/App.test.tsx src/app/shell/Breadcrumb.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
```

Expected: PASS.

- [ ] **Step 8: Review and commit**

```bash
git diff --check -- apps/web/src/features/records/RecordDownloadButton.tsx apps/web/src/features/records/RecordDownloadButton.test.tsx apps/web/src/features/records/RecordDetailSections.tsx apps/web/src/features/records/RecordDetailPage.tsx apps/web/src/features/records/RecordDetailPage.test.tsx apps/web/src/App.tsx apps/web/src/app/shell/Breadcrumb.tsx apps/web/src/app/shell/Breadcrumb.test.tsx apps/web/src/test/msw/handlers.ts
git add apps/web/src/features/records/RecordDownloadButton.tsx apps/web/src/features/records/RecordDownloadButton.test.tsx apps/web/src/features/records/RecordDetailSections.tsx apps/web/src/features/records/RecordDetailPage.tsx apps/web/src/features/records/RecordDetailPage.test.tsx apps/web/src/App.tsx apps/web/src/app/shell/Breadcrumb.tsx apps/web/src/app/shell/Breadcrumb.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat: add record evidence detail"
```

---

### Task 10: Extend Chromium evidence and pay the triggered harness debts

**Files:**

- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `apps/web/e2e/harness-fail-closed-meta.spec.ts`
- Modify: `apps/web/e2e/playwright.probe.config.ts`
- Create: `apps/web/e2e/harness-timeout.probe.spec.ts`
- Modify: `apps/web/e2e/support/api.ts`
- Modify: `apps/web/e2e/support/registers.ts`
- Modify: `apps/web/e2e/register-geometry.spec.ts`
- Modify: `apps/web/e2e/register-recovery.spec.ts`
- Modify: `apps/web/e2e/register-accessibility.spec.ts`
- Modify: `apps/web/e2e/smoke.spec.ts`

**Interfaces:**

- Consumes: production Records routes/components and synthetic record fixtures.
- Produces: Records register/detail browser evidence, self-contained forced-colors containment, bounded probe-child termination, and normalized Docker protected-root comparison.
- Keeps `apps/web/playwright.config.ts` Chromium-only with `workers: 1` and `retries: 0`.

- [ ] **Step 1: Re-read the four browser debt records and ADR 0003**

Read the exact files named in Global Constraints plus `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`. Confirm `responsive-register-cohort` stays open and the ADR 0003 boundaries stay unchanged.

- [ ] **Step 2: Write Docker normalization RED**

Extend `test_web_image_invariant_rejects_exact_descendant_and_wildcard_reinclusions` with `!./e2e`, `!././e2e/nested`, and `!./playwright.config.ts`. Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -k 'browser_harness or reinclusions' -q
```

Expected: FAIL because leading `./` bypasses the protected-root comparison.

- [ ] **Step 3: Normalize protected paths**

Before exact/wildcard comparison, reject any raw `..` component and repeatedly remove leading `./` after the existing `!` and leading-slash handling:

```python
while reinclusion.startswith("./"):
    reinclusion = reinclusion[2:]
```

Re-run the focused deployment test; expect PASS.

- [ ] **Step 4: Write probe-timeout RED**

Change the probe config to match `*.probe.spec.ts` and pass the desired probe file positionally from the parent. Add a timeout probe that waits 60 seconds. Add a parent test that calls `runProbe("harness-timeout.probe.spec.ts", 1_000)` and expects `timedOut=true`, a non-null terminating signal/exit, and elapsed time below 8 seconds.

Run:

```bash
npm --prefix apps/web run test:browser -- e2e/harness-fail-closed-meta.spec.ts
```

Expected: FAIL or time out because the parent does not terminate the child process group.

- [ ] **Step 5: Terminate timed-out probe groups**

Spawn the probe as its own process group on POSIX (`detached: true`). Validate that `child.pid` is a positive integer before any signal. Add one timeout that sends `SIGTERM` to `-child.pid`, falls back to `child.kill("SIGTERM")` when group signaling is unavailable, and schedules `SIGKILL` after 2 seconds only if `close` has not fired. Clear both timers on `error`/`close`; resolve one `ChildRun` with `timedOut` and never leave an unhandled rejection. Preserve the existing exact fail-closed report assertions.

Re-run the meta spec; expect both fail-closed and timeout tests PASS with no lingering Playwright child.

- [ ] **Step 6: Add Records fixtures and register manifest**

Extend `RegisterCase["key"]` with `"records"` and add:

```ts
{
  key: "records",
  path: "/records",
  floor: 840,
  headers: ["Identifier", "Title", "Type", "Captured by", "Captured", "State"],
  finalHeader: "State",
  searchPlaceholder: "Search identifier or title…",
  firstFilter: { role: "textbox", name: "Record type" },
  primaryAction: { role: "link", name: "Open record REC-000041" },
}
```

Add exact request handlers for the initial `limit=50` record page, directory, detail, and shell APIs. Keep all undeclared API/external requests fatal.

- [ ] **Step 7: Add register/detail/recovery/forced-colors proofs**

Let the manifest drive narrow/desktop register geometry. Add explicit tests for:

- record identifier native-link/table semantics;
- toolbar stacking and one localized scroll owner at 320 pixels;
- detail at 320 and desktop with no document overflow and one-column/two-column section layout;
- axe serious/critical checks on register and populated detail;
- Records HTTP 503 -> Retry -> loaded while URL/controls persist;
- focus on the far-edge Records control; and
- the existing forced-colors DCR test calling `measureActiveElementWithinRegister` and asserting `inside === true` after checking exact focus styles.

- [ ] **Step 8: Run focused then full Chromium GREEN**

```bash
npm --prefix apps/web run test:browser -- e2e/register-geometry.spec.ts --grep records
npm --prefix apps/web run test:browser -- e2e/register-accessibility.spec.ts --grep 'record|forced colors'
npm --prefix apps/web run test:browser -- e2e/register-recovery.spec.ts --grep Records
npm --prefix apps/web run test:browser
```

Expected: all commands PASS on Chromium, one worker, zero retries. Record exact fresh test totals from the final command only.

- [ ] **Step 9: Review and commit**

```bash
git diff --check -- apps/api/tests/unit/test_deploy_configuration.py apps/web/e2e/harness-fail-closed-meta.spec.ts apps/web/e2e/playwright.probe.config.ts apps/web/e2e/harness-timeout.probe.spec.ts apps/web/e2e/support/api.ts apps/web/e2e/support/registers.ts apps/web/e2e/register-geometry.spec.ts apps/web/e2e/register-recovery.spec.ts apps/web/e2e/register-accessibility.spec.ts apps/web/e2e/smoke.spec.ts
git add apps/api/tests/unit/test_deploy_configuration.py apps/web/e2e/harness-fail-closed-meta.spec.ts apps/web/e2e/playwright.probe.config.ts apps/web/e2e/harness-timeout.probe.spec.ts apps/web/e2e/support/api.ts apps/web/e2e/support/registers.ts apps/web/e2e/register-geometry.spec.ts apps/web/e2e/register-recovery.spec.ts apps/web/e2e/register-accessibility.spec.ts apps/web/e2e/smoke.spec.ts
git commit -m "test: require records browser evidence"
```

---

### Task 11: Run affected/full gates and record only fresh delivery evidence

**Files:**

- Modify: `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`
- Delete after proof: `docs/debt/20260814022608-browser-probe-hardening.md`
- Delete after proof: `docs/debt/20260814035922-forced-colors-containment-proof.md`
- Preserve: `docs/debt/20260813234519-playwright-responsive-browser-harness.md`
- Preserve: `docs/debt/20260813144730-responsive-register-cohort.md`
- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`

**Interfaces:**

- Consumes: all task commits and fresh command output.
- Produces: delivery evidence, head/next migration snapshot, paid-debt state, ADR reassessment, and honest unverified boundaries.
- Does not alter the implementation `baseline_commit` solely because current main/branch SHAs differ.

- [ ] **Step 1: Run formatting/static/contract checks**

```bash
cd apps/api && uv run ruff format --check .
cd apps/api && uv run ruff check .
cd apps/api && uv run mypy src
npm --prefix apps/web run lint
npm --prefix apps/web run build
just contracts-check
cd apps/api && uv run alembic heads
```

Expected: all PASS; Alembic reports only `0086_record_page_index (head)`.

- [ ] **Step 2: Run focused affected API and migration gates**

```bash
cd apps/api && uv run pytest tests/unit/test_record_list_cursor.py tests/unit/test_record_list_contract.py tests/unit/test_record_list_index.py tests/unit/test_deploy_configuration.py -q
cd apps/api && uv run pytest tests/integration/test_records.py tests/integration/test_records_process_scope.py tests/integration/test_record_schema.py -q
cd apps/api && uv run pytest tests/migration/test_migration_coherence.py -q
cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract -q
```

Expected: all PASS, including populated migration downgrade/re-upgrade and published response validation.

- [ ] **Step 3: Run focused affected web and Chromium gates**

```bash
npm --prefix apps/web test -- src/features/records src/features/capa/EvidenceLinker.test.tsx src/features/capa/hooks.test.tsx src/App.test.tsx src/app/shell/LeftRail.test.tsx src/app/shell/Breadcrumb.test.tsx
npm --prefix apps/web run test:browser
```

Expected: PASS. Record the Chromium total from this run, one worker, zero retries.

- [ ] **Step 4: Run complete suites as durable jobs**

Use the `codex-process-jobs:start` skill for each genuinely long command so the process survives the assigning turn. Start and later retrieve final results for:

```bash
cd apps/api && uv run pytest tests/unit -m unit
cd apps/api && uv run pytest tests/integration -m integration
cd apps/web && npm test
```

Do not infer totals from prior snapshots. If any complete suite cannot run, leave its count unchanged and record the exact limitation; do not declare the slice complete.

- [ ] **Step 5: Reassess ADR 0003 and remove only proven-paid debt files**

Append a dated `## Reassessment — 2026-08-14` section to ADR 0003 stating that Records expands the focused cohort while retaining the dedicated entry, central fixtures, Chromium-only engine, one worker, zero retries, and live-stack boundary. State that the payoff trigger has not otherwise fired.

After the timeout/Docker and forced-colors tests are green, remove the two paid debt files with `apply_patch`. Keep the Playwright harness and responsive-register-cohort debt records unchanged unless their own payoff triggers were actually met.

- [ ] **Step 6: Update current status and slice history from evidence**

Add an Evidence Operations / `S-records-read-console` delivery block with exact commands/results, migration head `0086` and next `0087`, and freshly measured counts only. Preserve the existing `baseline_commit` field. Explicitly state these unclaimed boundaries: Firefox/WebKit, actual assistive technology, live backend/database/object-store/Keycloak acceptance, Docker-backed application acceptance, deployment, disposable Fedora proof, and any migration round-trip not executed by the recorded gate.

- [ ] **Step 7: Run authority, site-data, and final diff gates**

```bash
just authority-check
bash scripts/check-no-site-data.sh
git diff --check
git status --short
git diff --stat main...HEAD
```

Expected: authority/site-data/diff gates PASS; only intended slice files differ; the primary `.superdesign/` and `/tmp` worktree records remain untouched.

- [ ] **Step 8: Commit the delivery evidence**

```bash
git add docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/current-status.md docs/slice-history.md docs/debt/20260814022608-browser-probe-hardening.md docs/debt/20260814035922-forced-colors-containment-proof.md
git commit -m "docs: record records console evidence"
```

If the two debt files were removed, `git add` stages those deletions. Then re-run `git status --short`, `just authority-check`, and `bash scripts/check-no-site-data.sh`. Stop at a clean review-ready branch and request explicit owner approval before any push or PR action.

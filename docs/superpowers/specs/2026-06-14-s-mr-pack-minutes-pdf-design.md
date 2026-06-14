# S-mr-pack — Management Review minutes pack (PDF), clause 9.3 → doc 13 §7.3

> **Status:** owner-approved design (brainstorm 2026-06-14). Closes the named S-mr-1 deferral
> *"the rendered Management-Review-Pack PDF (v1.1)"* (R45). Sibling deferrals **stay deferred**:
> Top-Management approval routing, MR-minutes revision, `improvement_initiative`.

## 1. Summary

A Management Review's controlled record is a frozen `application/json` minutes blob (R26 →
`no_controlled_rendition`), so the filed 9.3 record has **no human-readable rendition**. This slice
adds a **single, printable PDF of the filed minutes**, downloadable from the MR detail page once the
review is released. The PDF is rendered on demand from the **released version's frozen snapshot**
(`metadata_snapshot["mgmt_review_minutes"]`) plus that version's e-signatures — it reproduces the
*filed* minutes, immutably.

This is the **lightweight "printable view"** shape (owner pick #1 of 3): a synchronous, in-request
**reportlab** render streamed straight back as `application/pdf`. No Celery task, no status row, no
reaper, no cache, no new evidence record, no WORM re-seal — the MR's released version is *already*
the canonical WORM record; the pack is a derived rendering of it, like a controlled-copy.

**Non-Gotenberg, so sync is legal.** The "API can't render" rule is about `GotenbergRenderSink`
(HTML→PDF via an external service). A reportlab text-PDF over a few-KB JSON dict is pure Python,
deterministic (`invariant=1`), and runs in milliseconds — fine in a request handler. This is *why*
the pack exists: Gotenberg can't render JSON, so we render the minutes ourselves.

## 2. Locked decisions (the brainstorm F-forks)

| # | Decision | Choice |
|---|----------|--------|
| Shape | sync printable / async-cached / sealed-bundle | **Sync printable minutes** (single reportlab PDF, streamed) |
| Source | live `review_input`/`review_output` rows vs. frozen snapshot | **Frozen snapshot** of the released version (`metadata_snapshot["mgmt_review_minutes"]`) |
| Delivery | blob+presign vs. direct stream | **Direct `StreamingResponse(application/pdf)`** — no blob, no cache; FE fetches authed bytes → objectURL |
| Lifecycle gate | released-only vs. draft-preview | **Released only** — `doc.current_effective_version_id is not None`; else 409 |
| Permission key | `mgmtReview.read` vs. `report.export` | **`mgmtReview.read`** — the PDF shows only data the reader already sees; no new key |
| Sign-off block | include vs. minutes-only | **Include** — approval + release signatures (signer name, meaning, timestamp) |
| Branding | plain footer vs. controlled-copy band + verify QR | **Plain footer** — derived-view note + canonical version id + minutes source digest; **no QR** |

## 3. Scope / non-goals

**In scope:** one backend service (pure render fn), one new `GET` endpoint, the OpenAPI entry, one FE
download affordance (button + a tiny download handler), tests (api unit + integration, web component).

**Out of scope (named, not faked):**
- **No migration, no new permission key, no new enum** — head stays `0051`, catalog stays 100.
- **No caching / blob / evidence-record / WORM seal** — each download re-renders (deterministic, cheap).
- **No external share-links** (the S-pack-2 token machinery) — authenticated download only.
- **No embedded source artifacts** — the pack renders the minutes' compiled *summaries*
  (`source_ref` RAG data + decision rows), not the underlying audit-report/CAPA/scorecard PDFs.
- **No live action-tracking status** — the frozen `_minutes_output` deliberately omits `spawned_*`,
  so the pack shows the decisions *as filed*, not their downstream CAPA/DCR/task state (point-in-time).
- **No verify QR / controlled-copy band** — a plain footer states the canonical version + source digest.

## 4. Architecture

### 4.1 Backend — the render service

**New module `services/mgmt_review/pack.py`** (or `domain/mgmt_review/pack_render.py` for the pure
leaf + a thin service wrapper — implementer's call; keep the reportlab leaf pure and unit-testable):

```
async def build_minutes_pdf(session, mr: ManagementReview, doc: DocumentedInformation) -> bytes
```

Responsibilities (read-only — **no DB writes, no blob writes**):
1. **Resolve the released version.** `version = session.get(DocumentVersion, doc.current_effective_version_id)`.
   The caller (endpoint) has already 409'd if the pointer is None.
2. **Read the frozen minutes.** `minutes = version.metadata_snapshot["mgmt_review_minutes"]` →
   `{period_label, review_date, attendees[], inputs[], outputs[], compiled_at}`.
   (Defensive: if the key is absent — a legacy/edge version — 409 `pack_unavailable`.)
3. **Resolve display names.** Collect `owner_user_id`s from `outputs[]` and any `user_id`s in
   `attendees[]`; one `app_user` lookup → an `{id: display_name}` map (reuse the user-directory
   helper the MR/objectives serializers already use; null/unknown → the raw id or "—", never crash).
4. **Query the version's e-signatures.** `signature_event WHERE signed_object_type='document_version'
   AND signed_object_id = version.id AND meaning IN ('approval','release')`, ordered by `created_at`,
   left-joined to `app_user` for the signer name (signer may be NULL → "system"). The MR rides
   `document.approve`/`document.release` so both sign the same (now-Effective) version id.
5. **Render** the line-based PDF (reportlab `canvas`, `invariant=1` for byte-determinism). Reuse the
   evidence-pack `_text_pdf`/`_wrap` line idiom ([portfolio.py:63-100](apps/api/src/easysynq_api/services/packs/portfolio.py))
   — either factor a shared `pdf_text` primitive or inline a small self-contained copy (keep the
   MR pack decoupled from evidence-pack internals; prefer a tiny shared helper if it's clean).
6. Return the bytes.

**Determinism note:** the body carries **no live "generated at" timestamp** — it shows the frozen
`compiled_at` + the version's `effective_from`/`revision_label`, all stored. So a given released MR
always renders byte-identical (no `Date.now()`; safe under the no-clock workflow/test rules).

### 4.2 Backend — the endpoint

**`api/mgmt_review.py`**, a sub-path of `/{review_id}` (no literal-shadow issue):

```
GET /management-reviews/{review_id}/pack   → StreamingResponse(application/pdf)
  Depends(_mr_read)              # mgmtReview.read (the reader already holds it to see the page)
  404  cross-org / not-an-MR    (the existing _load_review)
  409  pack_unavailable          if doc.current_effective_version_id is None (not yet released)
       (and the defensive missing-snapshot-key case)
  200  application/pdf + Content-Disposition: attachment; filename="{identifier}-minutes.pdf"
```

The handler `_load_review`s (mr, doc), 409s if unreleased, calls `build_minutes_pdf`, and returns
`StreamingResponse(io.BytesIO(pdf), media_type="application/pdf", headers={Content-Disposition})`.

### 4.3 Contract

Add `GET /management-reviews/{review_id}/pack` to `packages/contracts/openapi.yaml`: a binary
`application/pdf` 200 response + the 404/409 problem responses. `/check-contracts` (redocly) must pass.

### 4.4 Frontend

**`features/management-review/`:**
- A small download handler (inline in the detail page or a `useMgmtReviewPack(id)` returning a
  `download()` fn): on click → `useApi().getBlob(`/api/v1/management-reviews/${id}/pack`)` →
  `URL.createObjectURL` → click a synthesized `<a download>` → `URL.revokeObjectURL`. The authed-binary
  pattern ([api.ts:65](apps/web/src/lib/api.ts), the visual-diff PNG precedent). Loading state +
  **calm error** (403 → quiet/disabled; 409 → "available once released"; other → calm inline message).
- **A "Download minutes pack (PDF)" button** on `ManagementReviewDetailPage.tsx`, shown when the MR is
  **released** (`detail.current_state === "Effective"` — release sets current_state Effective and it
  stays there through ActionsTracked/Closed, which are separate `close_state` values). Hidden otherwise.
- The MR detail page is a **full route** (not a drawer) → drivable by Chrome-MCP live smoke, unlike
  the `/dcrs` drawer wall.

## 5. The PDF layout (line-rendered, ordered)

1. **Header / cover:** "MANAGEMENT REVIEW — minutes (controlled record)"; identifier; title;
   `period_label`; `review_date`; `current_state`/`close_state`; the version `revision_label` +
   `effective_from`.
2. **Attendees:** the roster (`name` — `role`), resolving `user_id` → name where present.
3. **9.3.2 Review inputs:** one block per `inputs[]` row — `input_type`, `available` (Y/N), and a
   generic key/value render of `source_ref` (the compiled RAG/summary dict; render generically since
   the shape varies by input type — never crash on an unexpected shape). Order by `position`.
4. **9.3.3 Review outputs / decisions:** one block per `outputs[]` row — `output_type`, `description`,
   owner (resolved name), `due_date`.
5. **Sign-off:** the approval + release signatures — signer name, `meaning`, `created_at` (UTC ISO),
   `method`. "system" for a null signer (a Beat-activated future-dated release).
6. **Footer (every page):** "Derived printable view of the filed minutes — the canonical record is
   Management Review version `{version.id}` (Rev `{revision_label}`). Minutes source digest:
   `{version.source_blob_sha256}` — re-hash the `application/json` source blob (RFC 8785 JCS) to
   verify." (No QR; `source_blob_sha256` *is* the JCS-bytes SHA-256, so it's directly verifiable.)

## 6. Error handling, gating & invariants

- **Gate:** `mgmtReview.read` (no new key). Reading the MR page already requires it; the PDF exposes
  no data beyond the detail page.
- **Released-only:** 409 `pack_unavailable` when `current_effective_version_id is None`. The FE hides
  the button pre-release, but the backend is the boundary (defence in depth — and the
  missing-snapshot-key edge also 409s, never 500s).
- **Cross-org:** the existing `_load_review` 404 (org check on the base doc).
- **Read-only / no side effects** — pure render; honors the no-blob/no-cache decision. The endpoint
  never writes (so no WORM/append-only surface is touched; nothing for the blob-row-iff-bytes
  invariant to break).
- **Never crash on snapshot shape** — `source_ref`/`attendees` are free-form JSON; render defensively
  (generic key/value, missing keys → "—").
- **Determinism** — `invariant=1`, no live timestamp; a given released MR → identical bytes.

## 7. Testing

**API unit** (`tests/unit/`, run natively on this Windows box):
- `build_minutes_pdf` produces a non-empty `%PDF` byte string from a synthetic released MR (a
  `DocumentVersion` with a hand-built `metadata_snapshot["mgmt_review_minutes"]` + a couple of
  `signature_event`s). Assert it contains expected text (via `pypdf` extract) — identifier, an
  attendee, an output description, a signer name, the source-digest footer.
- Determinism: two renders of the same input are byte-identical.
- Defensive: a snapshot missing the `mgmt_review_minutes` key raises the 409-mapped error; an
  unexpected `source_ref` shape renders without raising; a null signer → "system".

**API integration** (`tests/integration/`, **CI-only on this Windows box** — write failing-first by
reasoning, let CI verify):
- Build a real MR through the services to **Effective** (create → compile-inputs → add outputs →
  submit → approve → release; author ≠ approver ≠ releaser SoD-2; approvers from each task's
  `candidate_pool`). `GET …/pack` → 200 `application/pdf`, body starts `%PDF`, `Content-Disposition`
  filename = `{identifier}-minutes.pdf`.
- `GET …/pack` on a **Draft** MR → 409 `pack_unavailable`.
- Cross-org → 404. A caller without `mgmtReview.read` → 403 (calm).
- ⚠ Carry the S-dcr-ui-4 CI flake lesson: the releaser needs `document.release` (grant
  `grant_lifecycle`, not just the `Approver` role) or `drive_to_effective`'s release 403s.

**Web** (`features/management-review/`, runs natively):
- The button **shows** when `current_state === "Effective"`, **hidden** for Draft/InReview/Approved.
- Click → `getBlob` called with `/api/v1/management-reviews/{id}/pack`; mock returns a Blob; assert
  `createObjectURL`/`revokeObjectURL` lifecycle + a synthesized download anchor (the visual-diff
  objectURL test precedent). 409/403 → calm message, no crash.
- ⚠ Web traps: `import { expect, it } from "vitest"`; jest-axe smoke on the page; MSW fixtures pinned
  via `satisfies MgmtReviewDetail`; a global `scrollIntoView` stub already exists.

**Gates:** `/check-api` + `/check-contracts` + `/check-web` (no migration → `/check-migrations` n/a).
Then `diff-critic` (no `migration-reviewer` — no migration). Live smoke via Chrome MCP (full route).

## 8. Files touched (estimate)

- **New:** `apps/api/src/easysynq_api/services/mgmt_review/pack.py` (+ maybe a `pdf_text` helper);
  api unit + integration tests; a web download handler + button + test.
- **Edit:** `api/mgmt_review.py` (the endpoint + export wiring in `services/mgmt_review/__init__.py`);
  `packages/contracts/openapi.yaml`; `ManagementReviewDetailPage.tsx` (+ `hooks.ts` if a hook is added);
  the page test.
- **No edit:** migrations, models, enums, the permission seed.

## 9. Risks / review-bait (pre-empt the diff-critic + Codex)

- **Reading live rows instead of the frozen snapshot** would be a correctness bug (the minutes could
  drift / outputs are append-only but inputs are Draft-mutable) — the design reads the **version
  snapshot** only. Pin this in a test (mutate a live `review_input` after release, assert the pack is
  unchanged).
- **Signature object id** — confirm the approval/release signatures attach to the **version id**
  (`signed_object_type='document_version'`), not the document id (verified: the default sink uses
  `signed_object_type="document_version"`, `signed_object_id`=version). The integration test asserts
  the signer name appears, which fails loudly if the id is wrong.
- **`source_blob_sha256` == the JCS digest** — the minutes blob is the bare rfc8785 bytes (no
  preamble), content-addressed, so its `sha256` is directly re-verifiable; the footer's verify
  instruction matches.
- **Determinism vs. a live timestamp** — no `Date.now()`/`generated_at` in the body.

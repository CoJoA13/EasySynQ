# NFR performance budgets

The binding performance budgets (doc 03 §7, P95 unless noted):

| Interaction | Budget (SLO) |
|---|---|
| Document metadata read | **≤ 300 ms** |
| Page interaction (interactive) | **≤ 1.5 s** |
| Search query | **≤ 800 ms** |
| Preview first-page render of a **cached** watermarked PDF | **≤ 2 s** |
| Office→PDF render of a 20-page doc | **≤ 15 s** (async, non-blocking) |

## What CI gates (the smoke test)
`tests/integration/test_nfr_smoke.py` measures **server-side** P95 (in-process ASGI) for the
metadata read, the document list (the S10 `clause_refs`-join — an N+1 guard), `/me`, and `/search`.
It asserts **generous regression ceilings (~5× the SLO)**, NOT the SLO itself: shared CI runners are
noisy, so the gate's job is to catch a **catastrophic** regression (an accidental N+1, a dropped
index), not to certify the production budget on a CI box. Read the test's printed P95 summary to
spot creeping regressions early.

## What CI cannot measure (manual dev-stack proof)
The **cached-PDF first-page ≤ 2 s** and **Office→PDF ≤ 15 s** budgets depend on Gotenberg (not run
in CI). Validate them once on a representative host:
```bash
# cached watermarked-PDF first page (the export stream of an Effective doc)
time curl -fsS -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/documents/<id>/export" -o /tmp/out.pdf      # target ≤ 2 s on a warm cache
```
For Office→PDF, upload a 20-page .docx and confirm the render completes async (the request returns
immediately; the rendition appears on the next reconcile) within ~15 s — it must never block the
interactive request path. Per-recipient watermark/stamp rendering is a real budget line (R34): it
re-overlays the PDF at download time and rides the same cached-rendition ≤ 2 s budget.

## Sizing context
Budgets assume the S/M profiles (doc 03 §7). On **S**, OpenSearch is intentionally absent (Postgres-
FTS only — a documented degraded mode); search relevance/faceting is reduced but the ≤ 800 ms budget
still applies to the FTS query.

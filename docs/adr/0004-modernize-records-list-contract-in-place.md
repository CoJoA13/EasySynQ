# 0004 — Modernize the Records list contract in place

**Date:** 2026-08-14

**Status:** Accepted

## Context

`GET /records` returns a bare array capped at the newest 100 candidates before the existing
`record.read` row filter. It has no identifier/title search, no continuation contract, and no
display-ready page model. The only web consumer is a bounded evidence picker; the product was only
partly deployed and was never completed as a supported production setup.

The first Evidence Operations slice needs one Records register that supports reliable lookup and
operational review without preserving a provisional interface at the cost of a duplicate list API.
The public response shape, authorization-aware page semantics, and compatibility posture therefore
need an explicit architectural decision.

## Decision

Modernize `GET /records` in place. Replace the bare array with a cursor-paginated `RecordPage`
envelope, add identifier/title search, retain the five existing structured filters, and return
display-ready record summaries. Migrate every known repository consumer in the same slice and remove
the obsolete bare-array types, fixtures, and assumptions. Do not add a legacy response mode or a
second register endpoint.

Pages use deterministic keyset order on `(captured_at DESC, id DESC)`. The API evaluates the
canonical `record.read` PDP over candidate batches until it has a full readable page plus evidence
of a following readable row, or exhausts the candidate set. It never substitutes a second SQL-only
authorization engine. Migration `0086` adds the composite ordering index; it changes no record data.

This decision is permitted by R65's pre-production compatibility posture. It does not relax schema
upgrade safety, data preservation, tenancy, deny-always-wins, WORM, audit, security, or site-data
boundaries.

## Consequences

The application has one coherent Records list contract, one authorization implementation, and no
permanent compatibility surface for an incomplete deployment. The CAPA picker and any other known
consumer must migrate atomically with OpenAPI and generated artifacts. An old client expecting a bare
array will break, deliberately.

Authorization correctness is preserved across process scope, predicates, correction fallback, and
explicit denies. The Python PDP scan can perform more database work for a deeply scoped caller than a
proven equivalent SQL projection; that deliberate trade-off is registered in
`docs/debt/20260814085951-record-authz-page-scan.md`.

The temporary breaking-change posture is registered in
`docs/debt/20260814085943-preproduction-api-compatibility.md`. It must not silently survive the first
supported production or external-client compatibility commitment.

## Alternatives

### Add a dedicated register read endpoint

A new endpoint could preserve `GET /records` for existing consumers while returning a richer page
model to the console. It avoids a breaking response but creates two list contracts whose filters,
authorization, and summaries can drift.

### Keep the array and publish continuation in response headers

The body could remain compatible while `q` and `cursor` are added and the next cursor is returned in
a header. This is awkward for generated clients, keeps an under-specified body, and pushes label
hydration into additional client requests.

### Keep the API unchanged and filter in the browser

The SPA could search the newest loaded records only. That cannot reliably find a known record or page
through an installation larger than the existing cap, so it fails the approved user outcome.

## Payoff trigger

Before the first supported production deployment or any external-client compatibility commitment,
replace R65's temporary posture with an explicit API versioning and deprecation policy. From that
point, do not make further in-place breaking response changes without following that policy.

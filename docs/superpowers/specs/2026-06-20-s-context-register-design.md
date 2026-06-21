# S-context-1 — Context Register (ISO 9001 clause 4.1) design note

> Spec-first, lighter than S-risk-spec: the architecture is **settled** by R49 (the register-as-Document
> pattern). This note records the design + the 4 owner decisions surfaced via AskUserQuestion before code.
> Authoritative decision: **R50** (`docs/decisions-register.md`). As-built schema: `docs/14 §6`.

## What this is

The clause 4.1 "Understanding the organization and its context" register — the **second register family**
after the now-complete clause-6.1 Risk & Opportunity register (R49). It reuses R49's register-as-Document
pattern **verbatim**: a 1:many `context_issue` satellite of a `kind=DOCUMENT` singleton **`CTX`** head
(auto-mapped to clause 4.1), advisory-lock head get-or-create, the rows are version content
(FSM-revision-edited → frozen at publish), and the head is reserved from every generic document mutation.

## The 4 owner decisions (AskUserQuestion, 2026-06-20)

1. **Content model — ENRICHED.** Beyond the contracted `14 §6` minimum (`classification`
   enum(`internal`,`external`) + `description`), the row carries the optional **SWOT** `category`
   enum(`strength`,`weakness`,`opportunity`,`threat`, NULLABLE), a `status` enum(`active`,`closed`)
   (a new issue is always `active`; retire by closing, never delete), and `last_reviewed_at`.
   `classification` is the ISO spine. The enum tuples are **golden-pinned + append-only** (mint a new
   value, never re-letter — so a frozen published row is never silently re-interpreted).
2. **Authz — ORG-LEVEL.** Clause 4.1 context is strategic/org-wide, so `context_issue` carries `org_id`
   but **no `process_id`** (deliberately unlike `risk_opportunity`). Rides the seeded `register.read` /
   `register.manage` @ **SYSTEM** — catalog stays **102**, **no new key, no new role grant** (the QMS
   Owner is the steward; a bound Process-Owner's PROCESS grant matches no context row).
3. **Interested Parties (4.2) — SEPARATE register** (its own `interested_party` head + table + a later
   `S-interested-parties-1` slice), per the `14 §6` two-table contract.
4. **Scope — CORE + LIFECYCLE together** (one slice; the risk family split it 1 → 1b).

## Key design points

- **No computed/graded axis.** SWOT + status are categorical user inputs, not a derived band — so, unlike
  risk, there is **no `criteria` block, no `resolve_criteria`, no derive-and-freeze**. `build_register`
  freezes the rows only; the live read serves the satellite rows as-is.
- **Org-level authz is all-or-nothing at SYSTEM.** `GET /context` is filter-not-403 (a no-grant caller
  → 200 + empty); `GET /context/{id}` enforces `register.read` @ SYSTEM; `POST`/`PATCH` + the steward acts
  enforce `register.manage` @ SYSTEM; release is `document.release` + SoD-2. No process scope, no path
  resolvers, no PATCH-reassign re-auth (there is no process-reassign TOCTOU) — but the row writes still
  lock the head `FOR UPDATE` (row→head) so a row edit cannot land after the publish freeze (the S-risk-1b
  P1 version-integrity discipline).
- **Reservation generalized.** R49's `reject_rsk_register_mutation` becomes
  `reject_managed_register_mutation` keyed by `_MANAGED_REGISTERS = {RSK, CTX}` (per-code message; RSK
  behavior byte-identical). The CTX head is reserved from metadata/distribution/links/clause-map/
  obsoletion (at the `lifecycle.obsolete` chokepoint) / DCR target / import — exactly as RSK.

## Lifecycle (mirrors S-risk-1b)

`start-revision` (T7) → edit satellite → `publish` (freeze rows via `checkin_context_register` +
`submit_review` T2/T9 + `instantiate_approval`, one txn) → approve via the generic `/tasks` DOCUMENT decide
leg → `release` (document.release + SoD-2 → shared SERIALIZABLE cutover) → Effective + read-only. CTX ∉
`LEADERSHIP_DOC_TYPES`, so the cutover leadership gate is a no-op.

## Migration `0060`

`context_issue` table + 3 enums (`context_classification`/`context_category`/`context_issue_status`) +
the `CTX` `document_type` seed + the additive `CONTEXT_ISSUE_UPDATED` event type. No role grant, no new
permission key. `status` is NOT NULL with no server_default (the service supplies `active` on insert —
greenfield, avoiding the server_default alembic-check trap).

## Named residuals (not faked)

- **S-context-2** — read consumers: `GET /context/summary` + the MR 9.3.2(b) context-change input (the
  `governing_register` helper is the seam).
- **S-context-fe** — the SPA (register page / SWOT view / `RegisterLifecyclePanel`).
- **S-interested-parties-1** — the clause 4.2 register (the contract omits `org_id`; add it when built).
- The carried-over server-computed `can_release`/`can_manage` capability on `GET /risks/register`
  (+ `/context/register`) — FE-facing, touches both registers; left for its own slice.

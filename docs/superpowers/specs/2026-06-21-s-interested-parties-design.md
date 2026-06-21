# S-interested-parties-1 — Interested Parties Register (ISO 9001 clause 4.2) design note

> Spec-first, lighter than S-risk-spec: the architecture is **settled** by R49/R50 (the
> register-as-Document pattern). This note records the design + the 4 owner decisions surfaced via
> AskUserQuestion before code. Authoritative decision: **R51** (`docs/decisions-register.md`).
> As-built schema: `docs/14 §6` (+ the `org_id` correction this slice lands).

## What this is

The clause 4.2 "Understanding the needs and expectations of interested parties" register — the
**third register family** (after the clause-6.1 Risk register R49 and the clause-4.1 Context register
R50) and the **second half of clause 4 "Context of the organization."** It reuses R50's
register-as-Document pattern **verbatim** (which itself clones R49): a 1:many `interested_party`
satellite of a `kind=DOCUMENT` singleton **`IPR`** head (auto-mapped to clause 4.2), advisory-lock
head get-or-create, the rows are version content (FSM-revision-edited → frozen at publish), and the
head is reserved from every generic document path. It is the *simpler* sibling of Risk — like Context:
org-level (no `process_id`), no graded axis (no `criteria`/`resolve`), only the rows freeze.

## The 4 owner decisions (AskUserQuestion, 2026-06-21)

1. **Content model — ENRICHED + INFLUENCE AXIS.** Beyond the contracted `14 §6` minimum (`party_name`
   + `needs_expectations`), the row carries `party_type` enum (`customer`, `regulator`, `supplier`,
   `employee`, `owner`, `community`, `partner` — the ISO-4.2 spine, NOT NULL), an optional `influence`
   enum (`low`, `medium`, `high`, NULLABLE), a `status` enum (`active`, `closed`; a new party is
   always `active`, retire by closing, never delete), and `last_reviewed_at`. The enum tuples are
   **golden-pinned + append-only** (mint a new value, never re-letter — a frozen published row is
   never silently re-interpreted). `influence` is a *categorical* axis, NOT a graded/computed one — so
   still **no `criteria`/`resolve_criteria`/derive-and-freeze**.
2. **`org_id` — ADD it.** `interested_party` (`14 §6`, the only register satellite that omitted it)
   carries `org_id` FK→`organization` ON DELETE RESTRICT — the `§1.1` org_id-everywhere convention;
   the doc-14 §6 editorial-gap correction R50 named. Recorded in R51 + the docs follow-up.
3. **Authz — ORG-LEVEL.** Clause 4.2 interested parties are strategic/org-wide, so `interested_party`
   carries `org_id` but **no `process_id`** (like `context_issue`, deliberately unlike
   `risk_opportunity`). Rides the seeded `register.read` / `register.manage` @ **SYSTEM** — catalog
   stays **102**, **no new key, no new role grant** (the QMS Owner is the steward; a bound
   Process-Owner's PROCESS grant matches no party row).
4. **Scope — CORE + LIFECYCLE this slice; the read consumers SPLIT to S-interested-parties-2.** Build
   core + lifecycle (head, satellite CRUD, lifecycle, register_caps, the reservation triad, migration
   0061) in S-interested-parties-1 (the clean S-context-1 clone). The `GET
   /interested-parties/summary` governing read + `governing_register` + `summarize_register` (with the
   `by_influence` bucket) AND the MR 9.3.2(b) consumer land in **S-interested-parties-2** (sourcing
   BOTH the 4.1 context AND 4.2 parties governing summaries, atomically once both governing helpers
   exist — mirrors the S-context-1 → S-context-2 split). **FE** = an own `/interested-parties` SPA
   (forward-looking → **S-interested-parties-fe**; recorded in R51, does not gate this slice).

## Key design points

- **No computed/graded axis.** party_type/influence/status are categorical user inputs, not a derived
  band — so, unlike risk, there is **no `criteria` block, no `resolve_criteria`, no
  derive-and-freeze**. `build_register` freezes the rows only; the live read serves the satellite rows
  as-is.
- **Org-level authz is all-or-nothing at SYSTEM.** `GET /interested-parties` is filter-not-403 (a
  no-grant caller → 200 + empty); `GET /interested-parties/{id}` enforces `register.read` @ SYSTEM;
  `POST`/`PATCH` + the steward acts enforce `register.manage` @ SYSTEM; release is `document.release`
  + SoD-2 over the multi-axis `_register_release_scope` (SYSTEM override in v1; ADMIN holds none). No
  process scope, no path resolvers, no PATCH-reassign re-auth (there is no process-reassign TOCTOU) —
  but the row writes still lock the head `FOR UPDATE` (row→head) so a row edit cannot land after the
  publish freeze (the S-context-1 P1 version-integrity discipline).
- **Server-computed caps.** `GET /interested-parties/register` carries `can_release`/`can_manage` via
  the shared register-agnostic `register_capabilities` (the S-context-fe pattern) — the steward
  console's faithful multi-axis release gate (a single-axis FE probe can't replicate the release
  scope). GET-only; the action routes stay lean.
- **The reservation TRIAD + import (the S-context-1 Codex P1/P2 lesson).** The `IPR` head is reachable
  via four generic paths — guard all four: the mutation chokepoint + the generic `POST /documents`
  create guard + the CREATE-DCR `_resolve_implement_version` guard (all keyed off a single
  `_MANAGED_REGISTERS["IPR"]` dict entry) PLUS the separate inline ingestion-import literal guard
  (`commit.py`, NOT keyed off the dict).
- **Canonical literals.** doc_type `IPR`; advisory-lock NS `7710102` (RSK `7710100`, CTX `7710101`);
  metadata-snapshot/governing key `interested_party_register` (a NEW key, never reuse
  `context_register`); clause `4.2`; event type `INTERESTED_PARTY_UPDATED`; table `interested_party`.

## Named residuals (not regressions)

- **S-interested-parties-2** — `GET /interested-parties/summary` + `governing_register` +
  `summarize_register` (`by_influence` bucket with `unspecified` for NULL) + the MR 9.3.2(b) consumer
  (un-gap `CONTEXT_CHANGES`, source both 4.1+4.2 governing summaries under one `register.read` gate,
  fail-closed). Until then `CONTEXT_CHANGES` stays a sourceless gap (R50 / S-context-2 D-2 punted it
  here).
- **S-interested-parties-fe** — the `/interested-parties` SPA (clone `features/context/`).

## R51 (draft → `docs/decisions-register.md`)

Interested Parties register-as-Document (clause 4.2; the THIRD register family). A per-org
`kind=DOCUMENT` `IPR` singleton owns 1:many `interested_party` rows that ARE the register version's
controlled content (enriched-lean: party_type spine + party_name + needs_expectations + optional
influence + status + last_reviewed_at; golden-pinned append-only enums; no graded axis). Org-level
(`org_id`, NO `process_id`); rides seeded `register.*` @ SYSTEM (catalog 102, no new key/role grant).
Reserved from the create/mutate/DCR-implement/import quad via `_MANAGED_REGISTERS["IPR"]` + the
ingestion guard. Full lifecycle (start-revision/publish/release; `checkin_interested_party_register`
clones `checkin_context_register`) + server-computed `can_release`/`can_manage`. The MR 9.3.2(b)
`CONTEXT_CHANGES` input becomes sourced (both 4.1+4.2 governing registers) in S-interested-parties-2.
Back-prop: doc-14 §6 `org_id` correction, doc-15 endpoints, doc-07/02 register surfaces.

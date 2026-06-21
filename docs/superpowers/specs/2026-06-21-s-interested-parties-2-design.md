# S-interested-parties-2 — Interested Parties register read consumers (clause 4.2) design note

> Spec-lighter: the architecture is **fully settled** — this is the S-context-2 summary clone + the
> S-risk-2 MR-input seam, extended to two halves. This note records the design + the owner decisions
> surfaced via AskUserQuestion before code. No new R-number — **R51** (`docs/decisions-register.md`)
> already covers the family; this slice ships its named-deferred read consumers and notes the MR
> 9.3.2(b) sourcing is now wired.

## What this is

The two read consumers R51 / S-interested-parties-1 deliberately deferred:

1. **`GET /interested-parties/summary`** — the GOVERNING (Effective) frozen-snapshot summary tile, a
   byte-mirror of S-context-2's `/context/summary`: `services/interested_parties/queries.governing_register`
   (the `IPR` head's current Effective `metadata_snapshot.interested_party_register` rows, or `None`)
   → the pure `domain/interested_parties/summary.summarize_register` →
   `{published, total, by_party_type, by_influence, by_status, active, never_reviewed}`. Gated
   `register.read` @ SYSTEM (org-level, 403-on-deny — clause 4.2 has no process scope), mounted before
   `/interested-parties/{party_id}` (the str-convertor shadow).

2. **The Management-Review 9.3.2(b) input** — clause 9.3.2(b) is "changes in external/internal issues
   **AND** interested parties", so `CONTEXT_CHANGES` is finally un-gapped (the R50 / S-context-2
   deferral) and wired to source **BOTH** the 4.1 Context register **and** the 4.2 Interested-Parties
   register governing summaries. This is the first time *either* clause-4 register reaches the MR.

NO migration (head stays `0061`), NO new permission key (rides `register.read`, catalog 102), NO new
`ReviewInputType` enum member (the canonical MR input set stays **12**).

## The owner decisions (AskUserQuestion, 2026-06-21)

1. **MR 9.3.2(b) envelope — NESTED SINGLE ROW.** ONE `CONTEXT_CHANGES` input whose
   `source_ref.summary = {context, interested_parties}` — each half its pure `summarize_register`
   dict, or `null` when that register is unpublished; `available:true` if **either** is published, a
   gap only when **both** are. One `register.read` gate covers both halves (both ride it @ SYSTEM).
   This distinguishes 4.1-only / 4.2-only / both / neither and preserves each pure projection's JSON
   leaves for the WORM minutes.

   > **Migration-conflict resolution.** The owner first chose *two separate MR rows*. Because
   > `ReviewInputType` is a **Postgres enum** (`review_input_type`; migration 0050 CREATEs it from the
   > live `REVIEW_INPUT_TYPE_VALUES`, and `compile._INPUT_ORDER = tuple(ReviewInputType)` materializes
   > every member as a canonical row), a second 9.3.2(b) row would require a new enum value →
   > `ALTER TYPE … ADD VALUE` → **migration 0062** + a **13th** canonical input — both contradicting
   > the handoff's binding "NO migration (head 0061) / the canonical 12-input set". Surfaced as an
   > explicit informed-consent question; the owner **reverted to the nested single-row, no-migration
   > shape**.

2. **Summary shape — ENRICHED + FRESHNESS.** `{published, total, by_party_type (the 7-value ISO 4.2
   spine), by_influence (low/medium/high + `unspecified` for a NULL influence — the `uncategorized`
   analogue), by_status (active/closed), active (= by_status.active), never_reviewed (rows with no
   `last_reviewed_at`, clock-free)}` — byte-mirroring the S-context-2 summary, swapping the content
   axes. Pure, every leaf a JSON int (rfc8785-safe).

3. **Scope — ONE SLICE.** The `/summary` endpoint + `governing_register` + `summarize_register` + the
   MR consumer ship together (both small read consumers; S-context-2 folded its summary into one
   slice, the MR seam reuses the S-risk-2 branch).

## Binding constraints carried

- Read-of-record = the GOVERNING (Effective) frozen snapshot, NEVER the live working satellite (an
  UnderRevision steward edit is invisible until the next publish/release; the MR minutes WORM-freeze
  the summary — the S-risk-2/S-context-2 posture). Proven by the `summ2 == summ` byte-equality test
  (a live ADD during UnderRevision leaves the summary unchanged) + the MR `interested_parties`-half
  invariance across two compiles.
- Org-level: every gate is SYSTEM-scope; `/summary` is a 403-on-deny enforce, not a per-row filter.
- The MR consumer reads the `_frozen_row` dicts from `governing_register`, never the ORM rows.

## As-built

- **Source:** `services/interested_parties/queries.py::governing_register` (+ un-deferred in the
  package `__init__.__all__`); `domain/interested_parties/summary.py::summarize_register` (`_UNSPECIFIED`
  bucket for NULL influence); `api/interested_parties.py::interested_party_summary_endpoint` (mounted
  before `/{party_id}`); `packages/contracts/openapi.yaml` (`/interested-parties/summary` +
  `InterestedPartyRegisterSummary`).
- **MR seam:** `services/mgmt_review/compile.py` — `CONTEXT_CHANGES` dropped from `_SOURCELESS_GAPS`,
  a new `_build_row` branch (one `register.read` gate; reads both `governing_register`s; the nested
  envelope), aliased imports (`context_governing_register` / `summarize_context` /
  `interested_parties_governing_register` / `summarize_parties` — the F401 BODY-FIRST trap; risk stays
  bare); `db/models/_mgmt_review_enums.py` comment updated.
- **Tests:** unit `test_interested_party_summary.py` (the projection + golden bucket sets) +
  `test_interested_party_routes.py::test_summary_resolves_before_party_id`; integration
  `test_interested_party_summary_endpoint.py` (read-of-record byte-equality, `published ==
  has_governing`, 403-on-deny) + `test_interested_party_lifecycle.py::test_mr_input_b_sources_governing_parties_snapshot`
  + `test_mgmt_review.py` type-set move (`CONTEXT_CHANGES` sourceless → sourced-but-gap; the
  `len(inputs) == 12` pins unchanged).

## Named residual (not faked)

- **S-interested-parties-fe** — the `/interested-parties` SPA (clone `features/context/`) + a
  `useInterestedPartySummary` Home tile. The `docs(s-interested-parties-2)` follow-up back-props
  `docs/13 §9.3.2(b)` (now sourced from 4.1 + 4.2), `docs/15 §8.10d` (+`/summary`), and the
  decisions-register note that `CONTEXT_CHANGES` is now sourced.

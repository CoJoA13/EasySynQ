# Web-track working artifacts (`specs/` + `plans/`)

> **Per-slice working documents, generated *before* implementing each web-UI slice — read the one matching
> your current slice, not eagerly.** A `specs/` file is the brainstormed **design** (approved by the owner before
> the plan); the matching `plans/` file is the bite-sized **implementation plan** (TDD task list) executed from it.
> They are **not updated after a slice ships** — the authoritative shipped narrative lives in
> [`../slice-history.md`](../slice-history.md), and the binding decisions in [`../decisions-register.md`](../decisions-register.md).
> Filenames are `YYYY-MM-DD-[web-track-]<slice>-…` (the date = the doc's creation date, so an epic's spec can
> pre-date its sub-slice plans).

| Slice | Design spec | Implementation plan | Status |
|---|---|---|---|
| **S-web-1** — app shell + token port + thin Library | `specs/2026-06-06-web-track-s-web-1-design.md` | `plans/2026-06-06-s-web-1.md` | ✅ shipped |
| **S-web-2** — faceted Library + read-only detail drawer | `specs/2026-06-07-web-track-s-web-2-design.md` | `plans/2026-06-07-s-web-2.md` | ✅ shipped |
| **S-web-3** — Document Authoring | `specs/2026-06-07-web-track-s-web-3-design.md` | `plans/2026-06-07-s-web-3.md` | ✅ shipped |
| **S-web-4** — Document detail page + text/metadata redline | `specs/2026-06-08-web-track-s-web-4-design.md` | `plans/2026-06-08-s-web-4.md` | ✅ shipped |
| **S-web-4b** — worker-async visual page-image diff | `specs/2026-06-08-web-track-s-web-4b-design.md` | `plans/2026-06-08-s-web-4b.md` | ✅ shipped |
| **S-web-5** — Review & Approve (closes UJ-3) | `specs/2026-06-08-web-track-s-web-5-review-and-approve-design.md` | `plans/2026-06-08-web-track-s-web-5-review-and-approve.md` | ✅ shipped |
| **S-web-6** — Global Search + Compliance Checklist | `specs/2026-06-08-web-track-s-web-6-search-and-compliance-design.md` | `plans/2026-06-08-s-web-6-search-and-compliance.md` | ✅ shipped |
| **S-ing-4b** — Ingestion Review UI (closes UJ-2) | `specs/2026-06-08-web-track-s-ing-4b-ingestion-review-design.md` | `plans/2026-06-08-web-track-s-ing-4b-ingestion-review.md` | ✅ shipped |
| **S-web-7** — Nonconformity & CAPA front door (epic) | `specs/2026-06-08-web-track-s-web-7-nc-capa-design.md` (shared design for 7a–7d) | — (per-sub-slice plans below) | epic — 7a/7b/7c/7d ✅ |
| **S-web-7a** — CAPA read spine (board + drawer) | (in the S-web-7 epic spec) | `plans/2026-06-08-web-track-s-web-7a-capa-board.md` | ✅ shipped |
| **S-web-7b** — CAPA lifecycle writes | `specs/2026-06-09-web-track-s-web-7b-capa-lifecycle-writes-design.md` | `plans/2026-06-09-web-track-s-web-7b-capa-lifecycle-writes.md` | ✅ shipped |
| **S-web-7c** — Complaint & NCR intake | `specs/2026-06-09-web-track-s-web-7c-complaint-ncr-intake-design.md` | `plans/2026-06-09-s-web-7c-complaint-ncr-intake.md` | ✅ shipped |
| **S-web-7d** — Audits & findings | `specs/2026-06-09-web-track-s-web-7d-audits-findings-design.md` | `plans/2026-06-09-s-web-7d-audits-findings.md` | ✅ shipped (#105) |

> **Backend slices (S0–S11, the v1 families) were not built with this spec→plan flow** — their narrative is in
> `../slice-history.md` + the squash-merge commits. This folder is the web-UI track's working archive only.

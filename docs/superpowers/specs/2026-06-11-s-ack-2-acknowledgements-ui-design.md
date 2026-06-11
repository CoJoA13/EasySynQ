# S-ack-2 — Acknowledgements UI — Design

> The trailing web slice of the S-ack family (the family backend is S-ack-1, merged as PR #113 /
> mig `0048`, main `65995d4`). **Front-end only** — no migration, no permission key, no
> endpoint, no contract change (the S-web-7d / S-web-8 precedent). Elaborates §9 of the family design
> (`docs/superpowers/specs/2026-06-10-s-ack-acknowledgements-design.md`); honours R42 + R43.

## §0 — Owner decisions (settled in brainstorm 2026-06-11)

1. **Bulk-ack lives in a dedicated DOC_ACK-filtered inbox view** (`/tasks?type=DOC_ACK`) with row
   multi-select + an "Acknowledge N selected" action looping the per-task decision POST. The general
   `/tasks` inbox stays single-task click-through. (doc 10 §8.2 sanctions the looped POST as the bulk action.)
2. **The single-task attestation is prominent copy + one click** — a *dedicated* `AttestationCard`
   (not a `DecisionCard` outcome): the act is `acknowledge`-only, writes **no signature** (R43 — an ack
   is append-only evidence, never a `signature_event`), so there is no radio and no sign checkbox. One
   button = the attestation; consistent with one-click bulk-ack.
3. **The doc detail page is refactored to a real Mantine `Tabs`** (Overview / History / Approvals /
   Where-used / **Acks**), replacing today's cards-in-a-grid. The Acknowledged metric tile joins the
   persistent tile row above the tabs.
4. **The doc-level Audit tab is OUT of the initial release** — the mockup's tab bar lists it, but the
   doc-level audit surface and its admin-audit-log sibling are both parked. Recorded in §11; not built.

Because the attestation path is a separate `AttestationCard` + a separate `useAcknowledgeTask` mutation,
the shared `DecisionCard` and the `DecisionSubjectType` union stay **byte-untouched** — zero regression
risk to the DOCUMENT / CAPA / PERIODIC_REVIEW decision legs (the "build a new module, keep the old path
byte-identical" engineering pattern).

## §1 — Why / what

S-ack-1 shipped the obligation engine (distribution config, the MAJOR-only carry-forward boundary, the
sweep, the `DOC_ACK` decide leg, and the read APIs) but no UI. S-ack-2 gives the four human surfaces, all
fed by **endpoints that already exist** (§2):

- **The per-task DOC_ACK leg** — Sam's "I have read & understood" attestation in the task focus page.
- **The dedicated DOC_ACK inbox + bulk-ack** — the bell's destination; multi-select looping the POST.
- **The doc page Acks tab + Acknowledged tile** — Mara's coverage view + the named chase matrix + the
  distribution editor; readers see only the counts/ring (the honest S-web-4 omission, restored).
- **The TopBar ack bell** — the S-web-1 stub, wired to the caller's open-DOC_ACK count + the inbox link.

**Remind is omitted-not-faked** — the mockup shows a "Remind 6 ▾" button; R43 defers Remind to the
notifications family (a Remind that delivers nothing is a fake). No Remind affordance is built anywhere.

## §2 — Pinned response shapes (copy these into the MSW fixtures; never the mockup)

The #1 false-PASS rule: every fixture is the **real S-ack-1 serializer**, `satisfies` the TS type under
strict `tsc`. Shapes verified against the code at the cited locations.

### GET `/documents/{id}/distribution` — `documents.py::_distribution_payload` (1217) · gate `document.read`
```jsonc
{
  "acknowledgement_required": true,                 // bool
  "entries": [                                       // DistributionEntry[]
    { "id": "<uuid>", "target_type": "user",         // "user"|"org_role"|"process"|"folder"
      "target_id": "<uuid>", "ack_required": true, "created_at": "<ISO>" }
  ],
  "coverage": { "required": 47, "acknowledged": 41, "pending": 6, "overdue": 2 }  // object | null
}
```
- `coverage` is **`null`** when the doc has no Effective version (`current_effective_version_id is None`),
  for both flag states (`queries.coverage_counts` 189–200).
- `coverage` is **`{required:0,acknowledged:0,pending:0,overdue:0}`** when `acknowledgement_required=false`
  but an Effective version exists (honest zeros, not null) (queries 194–197).
- `entries` is `[]` when none configured; `target_type` carries all four kinds but only `user`/`org_role`
  are ever creatable (process/folder 422 at write).

### POST `/documents/{id}/distribution` — `DistributionUpdate` (documents.py:115) · gate `document.distribute`
Request: `{ "acknowledgement_required": bool|null, "add_entries": [{ "target_type": str, "target_id": "<uuid>", "ack_required": bool /*=true*/ }] }`
Response: same payload as GET. Errors: **422** `validation_error` (unknown target_type) · **422**
`target_kind_deferred` (process/folder) · **404** `not_found` (target user/role not in org) · **409**
`conflict` (duplicate `UNIQUE(document_id,target_type,target_id)`). A no-op body (no entries, flag null)
returns the current payload unchanged. **No PATCH on an entry** — change = DELETE + re-add.

### DELETE `/documents/{id}/distribution/{entry_id}` — (documents.py:1331) · gate `document.distribute` → **204** (no body)

### GET `/documents/{id}/acknowledgements` — `queries.coverage_matrix` (229) · gate `document.distribute`
```jsonc
[
  { "user_id": "<uuid>", "display_name": "Sam Patel",     // display_name: string | null
    "status": "acknowledged",                              // "acknowledged"|"pending"|"overdue"
    "acknowledged_at": "<ISO>",                            // null unless acknowledged
    "acknowledged_revision_label": "Rev C",                // null unless acknowledged
    "due_at": null }                                       // ISO | null (null when acknowledged or task pinned < boundary)
]
```
Returns `[]` when flag off, no Effective version, or empty audience (queries 235–243).

### GET `/tasks/{id}` — `workflow.py::_task` (56) · self-scoped (404-collapse)
DOC_ACK task carries `type: "DOC_ACK"`, `subject_type: "DOC_ACK"`, `subject_id: <document_id>`,
`state: "PENDING"|"DONE"|"SKIPPED"`, plus `id,instance_id,stage_key,assignee_user_id,candidate_pool,
action_expected,due_at`. The list `GET /tasks?assignee=me&state=PENDING&type=DOC_ACK` returns the same
rows **without** `subject_type`/`subject_id` (those are detail-only — workflow.py:70).

### POST `/tasks/{id}/decision` (DOC_ACK) — `services/ack/decide.py` · dispatched at workflow.py:242
Request: `{ "outcome": "acknowledge" }` (the only accepted value; 422 otherwise). Header `Idempotency-Key`.
Success → the engine result **+** `document_id`, `document_version_id`, `acknowledgement_id`. Errors
(map on `e.code`, not message): **409** `ack_obligation_lapsed` (flag off / no Effective / not in live
audience) · **409** `ack_superseded` (task pinned below the last-MAJOR boundary) · **409** `conflict`
(already acknowledged — `UNIQUE(user_id,document_version_id)`) · **422** `validation_error` (bad outcome).

### Target-name resolution (for the editor + entries list)
- `user` targets → `GET /directory/users` (`useUserDirectory`, **auth-only** — works for everyone).
- `org_role` targets → `GET /roles` (`role.read`; **QMS Owner holds it**, `0004_seed_authz.py:183`).
  Both pickers are reachable by the `document.distribute` holder and by the demo admin.

## §3 — The doc detail page (`features/document/DocumentDetailPage.tsx`)

### 3.1 Tabs refactor
Convert the cards-in-grid to a Mantine `Tabs`, **URL-synced** via `?tab=` (`useSearchParams`;
deep-linkable so the tile and bell can target Acks; default `overview`). Persistent **above** the tabs:
`ArtifactHeader`, `AuthorActions`, the metric-tile `SimpleGrid` (now 5 tiles incl. Acknowledged).

| Tab | Panel content (existing components) |
|-----|-------------------------------------|
| Overview | `RenditionCard` + `ControlMetadata` (+ the `ReviewPeriodModal` trigger, `manage_metadata`-gated) |
| History | `HistoryTab` + `VersionCompare` |
| Approvals | `ApprovalsTab` |
| Where-used | `WhereUsedTab` |
| **Acks** | `AcknowledgementsTab` (new, §3.3) |

Each tab passes `active={activeTab === "<key>"}` to its panel component (the existing `active` prop gates
the query `enabled`) → lazy per-tab fetch. The `Tab.Panel`s mount but their queries stay idle until active.

### 3.2 The Acknowledged tile (`features/document/AckCoverageRing.tsx` + a tile in the grid)
Rides `GET /documents/{id}/distribution` (`document.read`). A shared `AckCoverageRing` (`RingProgress`,
success-green) renders the percent; the tile shows `{acknowledged}/{required}` + "N pending". States:
- `coverage === null` → value `—`, sub "Not yet effective" (no Effective version).
- `coverage` zeros + `acknowledgement_required=false` → value `—`, sub "Not distributed for acknowledgement".
- otherwise → `41/47` value, ring at `acknowledged/required`, sub "6 pending".
On a `document.read` 403 (a scope-hidden doc never reaches the page anyway) the tile degrades to `—`.

### 3.3 The Acks tab (`features/document/AcknowledgementsTab.tsx`)
Two zones, per-key gated via `usePermissions({ level: "DOC", id }).can(...)`:
- **Coverage (any `document.read` reader)** — the `AckCoverageRing` + counts (required / acknowledged /
  pending / overdue), from the distribution GET. This is all a plain reader (Sam) sees here.
- **Chase + manage (only `document.distribute`)** — the **named matrix** (`GET /acknowledgements`): a
  table of `display_name` + a status badge (acknowledged/pending/overdue) + acked-rev label + due date,
  with an **avatar stack of pending** users (`Avatar.Group`, initials, `+N` overflow); **and** the
  `DistributionEditor` (§3.4). A `document.distribute` 403 on the matrix degrades to a calm "You can view
  coverage but not the named acknowledgement matrix" note (no crash) — `retry:false` + `forbidden` flag.

**No Remind button.** No "Last reminded" line (both R43-deferred).

### 3.4 The distribution editor (`features/document/DistributionEditor.tsx`, `document.distribute`-gated)
- The **doc-level `acknowledgement_required` flag** — a `Switch`; POST `{acknowledgement_required: bool}`.
- **Add a recipient** — target-type segmented (`user` | `org_role`), a target picker (user directory /
  roles list), a per-entry `ack_required` `Checkbox` (default on); POST `{add_entries:[{...}]}`. Only
  user/org_role offered (process/folder never shown — they 422). 404 (target not in org) / 409 (duplicate)
  surface calmly.
- **The entries list** — each row: resolved name + kind badge + ack-required flag + a delete button
  (DELETE → 204). Names resolve via the directory / roles maps; an unresolved id shows the raw target.
- All mutations invalidate `["distribution", id]`, `["acknowledgements", id]`, `["document", id]`.

## §4 — The per-task DOC_ACK leg (`features/review/`)

### 4.1 `ReviewApprovePage` 4th branch
Add `const isDocAck = task?.subject_type === "DOC_ACK";`, extend the instance-disable guard
(`!isCapa && !isPeriodic && !isDocAck`). Branch (mirrors the PERIODIC_REVIEW leg):
```
Title: "Document acknowledgement"
Left  (md 7): <DocAckContext documentId={task.subject_id!} />   // best-effort; calm-403 never blocks
Right (md 5): decidable ? <AttestationCard taskId={task.id} documentId={task.subject_id!} /> : decidedAlert
```

### 4.2 `DocAckContext.tsx` (mirror `PeriodicReviewContext`)
Best-effort `useDocument(documentId)`: on success show identifier · title · current state · governing
revision + effective date + a link to the doc page. **On 403 → a calm "Document details aren't visible
to you" alert; the AttestationCard still renders** (the obligation stands regardless of doc-read). Uses
`retry:false` so an expected deny doesn't re-hammer (the S-web-8 lesson).

### 4.3 `AttestationCard.tsx` (new — NOT a DecisionCard)
- Heading "I have read & understood"; body *"By acknowledging, you confirm you have read and understood
  {identifier} {revision_label}."* (identifier/rev best-effort from `useDocument`; falls back to "this
  document" on 403).
- A single primary button **"I have read & understood"** → `useAcknowledgeTask().mutateAsync({ taskId,
  documentId })` (one per-mount `Idempotency-Key`), then `navigate("/tasks")`.
- No radio, no comment, no signature checkbox.
- Error copy mapped on `e.code`: `ack_obligation_lapsed` → "This document no longer requires your
  acknowledgement — it may be under revision or obsoleted." · `ack_superseded` → "A newer major revision
  was released — acknowledge the current version instead." · `conflict` → "You've already acknowledged
  this." · else `e.message`. All in a calm closable `Alert`.

### 4.4 `useAcknowledgeTask` + `useBulkAcknowledge` (`features/review/ackHooks.ts`)
```ts
useAcknowledgeTask() // POST /tasks/{id}/decision {outcome:"acknowledge"} + Idempotency-Key
  onSuccess({taskId, documentId}): invalidate ["task",taskId], ["tasks"], ["document",documentId],
    ["documents"], ["ack-count"]   // ack-count = the bell query (§6)
useBulkAcknowledge() // Promise.allSettled over selected taskIds, one Idempotency-Key each;
  returns { ok: string[], failed: {taskId, code}[] }; invalidates the same keys once at the end.
```
`DecisionSubjectType` and `useDecideTask` are NOT modified. `Task.subject_type` is already `string`, so
the branch typechecks; add `"DOC_ACK"` to the `TaskType` union (lib/types.ts) for the inbox filter/render.

## §5 — The dedicated DOC_ACK inbox + bulk-ack (`features/review/`)

`TasksInbox` reads `?type` / `?state` from `useSearchParams`. When `type === "DOC_ACK"` it renders
`AckInbox` (a sibling component); otherwise the existing single-task table (byte-equivalent behaviour —
the general inbox is unchanged besides honouring the URL params it previously ignored).

`AckInbox.tsx`:
- Title "Acknowledgements"; loads `useTasks({ state: "PENDING", type: "DOC_ACK" })`.
- A table with a leading **checkbox** column + select-all; each row = best-effort doc identifier/title
  (cached `useDocument(subject_id)`, calm-403 → "Document"), due date, and a row link to
  `/tasks/{id}` (the AttestationCard page) and/or a per-row "Acknowledge".
- Header action **"Acknowledge N selected"** → `useBulkAcknowledge`; on completion a calm summary
  ("9 acknowledged · 1 superseded — refresh") and a list refetch. Partial failures never throw.
- Empty state "No documents awaiting your acknowledgement."

Per-row `useDocument` is acceptable (a user's open acks are few; each is query-cached). The general
inbox's no-per-row-doc-resolution note still holds for the mixed-type queue.

## §6 — The TopBar ack bell (`app/shell/TopBar.tsx` + `app/shell/useAckCount.ts`)

A small `useAckCount()` in **app/shell** (not importing `features/review`, to keep the layer direction
clean — the `usePermissions` precedent) does `GET /api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK`
under `queryKey ["ack-count"]` and returns `.length`. The existing disabled Indicator becomes:
```
<Indicator label={count} disabled={count === 0} size={16}>
  <ActionIcon component={Link} to="/tasks?type=DOC_ACK&state=PENDING" aria-label="Acknowledgements">🔔</ActionIcon>
</Indicator>
```
`aria-label` stays "Acknowledgements" (distinct from the untouched "Tasks" sibling — the duplicate-label
trap). Zero count → disabled badge, still navigable. The Tasks stub is left exactly as-is.

## §7 — Types + fixtures + MSW

- **lib/types.ts** (additive): `DistributionEntry`, `DistributionPayload` (`{acknowledgement_required,
  entries,coverage}`), `Coverage` (`{required,acknowledged,pending,overdue}`), `AckMatrixRow`,
  `DistributionUpdateBody`, `AckDecisionResult` (extends the engine result with the three ack fields);
  add `"DOC_ACK"` to `TaskType`. Do **not** add `"DOC_ACK"` to `DecisionSubjectType` (it would force
  exhaustive `DecisionCard` records — the attestation path is separate).
- **test/fixtures.ts + test/handlers.ts**: a flag-on doc with coverage `1/1/0/0` and a fuller `47/41/6/2`
  fixture; a DOC_ACK task fixture (`subject_type:"DOC_ACK"`); the matrix; the decision success + the
  three 409 codes; null-coverage + flag-off-zeros + `[]`-matrix cases. Each `satisfies` its type.

## §8 — Error handling, gating, accessibility

- **Calm-403 everywhere**: `ApiError.status===403` + `retry:false` → a dimmed no-access panel, never a red
  crash. Per-key calm-403 tests pin a **production-defaults `QueryClient`** (not the test wrapper's
  `retry:false`, which masks the real retry behaviour — the S-web-8 lesson). Never set `retry: undefined`
  (it clobbers `QueryClient` defaults — the S-web-8 trap): spread the option conditionally.
- **409s** mapped by `e.code` to plain-language copy (above). Bulk partial-failure surfaced per-task.
- **Affordance gating** is per-key at the doc scope; never render a write button the caller can't exercise.
- **a11y**: distinct `aria-label`s (bell vs Tasks; per-row vs legend badges — scope `within(...)` if
  repeated); no `dangerouslySetInnerHTML`; the global `scrollIntoView` stub stays for any `Select`/`Combobox`.

## §9 — Testing & gates

- **Subagent-driven TDD per task** (failing test → implement → per-task quality review).
- `/check-web` is the gate: eslint + strict `tsc --noEmit` (`noUncheckedIndexedAccess`) + build + the full
  vitest suite (run with `--pool=forks ...singleFork=true` for a clean signal). Web baseline **551**;
  target ~**600+**.
- `diff-critic` on the branch diff before the PR (false-PASS hunting; the fixture-shape + identity-map +
  calm-403 traps).
- **Pre-merge live smoke** via Chrome MCP (localhost only, client-side nav, text-first verification).
  Resolve which of the two demo `app_user` rows the live Keycloak `demo` subject JIT-maps to (kcadm), and
  grant overrides **there**. `SOP-PUR-002` is live-seeded flag-on with coverage `1/1/0/0` — ready-made
  state for the tile/ring/matrix; the "Demo (Admin)" row (`e3964e7a…`) already carries
  `document.distribute` + `document.acknowledge` SYSTEM overrides from the S-ack-1 smoke.

## §10 — Docs in-PR + the slice-history chore

- **No `openapi.yaml` change** (no new/changed endpoint).
- **Chore (folded into this PR):** write the missing **S-ack-1** narrative entry in
  `docs/slice-history.md` (source: the PR #113 body + R42/R43) and bump the file's migration-head line to
  `0048`. The S-ack-1 entry was never added when the backend merged.
- Add the S-ack-2 entry to `docs/slice-history.md` and a CLAUDE.md "Recent learnings" line on merge.

## §11 — Non-goals (stay deferred; R43 is binding)

- **The doc-level Audit tab** — outside the initial release (the admin audit-log screen is parked).
- **Remind** + "Last reminded" + reminder history → notifications family.
- The doc 13 **§6.3 Distribution & Acknowledgement report** (provenance header, exports) → v1.x.
- **process / folder** distribution targets → owner-assignment track (the API 422s today).
- The **org-wide PDCA rollup** endpoint / dashboard → the dashboard slice (S-ack-2 + objectives unblock it).
- **Bulk re-acknowledge** (admin), the **every-release re-ack** org config flag, the **delegation
  carve-out**, **ack retention / GDPR** posture → all R43-recorded, all out.
- No migration, no permission key, no endpoint, no contract change.

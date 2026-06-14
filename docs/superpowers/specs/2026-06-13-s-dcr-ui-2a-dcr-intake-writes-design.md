# S-dcr-ui-2a — DCR intake & early-state writes (front-end-only)

> **Status:** approved design (2026-06-13). Front-end-only (no migration / key / endpoint / contract); gate
> `/check-web` only. First write slice of the **S-dcr-ui-2 epic** (the lifecycle-writes follow-up to the
> S-dcr-ui-1 read spine, #123 `916ce1e`). ui-2b (the lifecycle cockpit + approval leg + the
> `capabilities.implement` backend touch) is named in §9 and gets its **own** spec.

## 1. Thesis & scope

The S-dcr-ui-1 read spine gave the previously UI-less DCR backend a `/dcrs` register + a `?dcr=<id>`
read-only drawer. **ui-2a adds the *create + early-edit* affordances** so a DCR can be raised from all three
already-built backend seams and edited/cancelled while early in its lifecycle. Every endpoint already exists
and is **single-gated** on a `changeRequest.*` key that `usePermissions().can(...)` answers correctly — so
this slice touches **no migration, no permission key, no endpoint, no contract**, exactly like the read spine.

**In scope (ui-2a):**

1. **Raise — 3 seams:** the standalone `/dcrs` "Raise DCR"; the **CAPA-drawer** Raise-DCR; the
   **MR-output** Raise-DCR (this is the S-mr-1/-2/-3 *"MR→DCR FE"* named deferral — the highest-value piece).
2. **Edit** a DCR while `Open` (`PATCH /dcrs/{id}`).
3. **Cancel** a DCR while `{Open, Assessed, Routed}` (`POST /dcrs/{id}/cancel`).

**Out of scope → ui-2b** (separate spec): assess / route / implement / close; the `/tasks` DCR-approval
candidate-pool leg; the `capabilities.implement` detail enrichment. **→ ui-3:** the page-image visual diff.
See §9 for the full named-deferral list.

## 2. Backend ground truth (verified against code — pin the FE to these, not narrative)

All facts below were confirmed by a six-reader source sweep over `api/dcr.py`, `services/dcr/service.py`,
`domain/dcr/fsm.py`, the contract, and the docs (2026-06-13). **The narrative resume note is corroborated
except where noted.**

### 2.1 The five ui-2a write endpoints

| # | Endpoint | Gate (key) | Request body (Pydantic) | Success | Key errors |
|---|---|---|---|---|---|
| 1 | `POST /dcrs` | `changeRequest.create` (in-handler `enforce`; scope from target doc, SYSTEM for CREATE) | `DcrCreate` | **201** `Dcr` | 422 `create_has_target` / `target_required` / `not_a_document`; 404; 403 |
| 2 | `PATCH /dcrs/{id}` | `changeRequest.assess` (dep `_dcr_assess`) | `DcrPatch` (all optional, `None`=unchanged) | 200 `Dcr` | **409 `dcr_not_editable`** (≠ Open); 404; 403 |
| 3 | `POST /dcrs/{id}/cancel` | `changeRequest.close` (dep `_dcr_close`) | `DcrCancel` `{comment?}` | 200 `Dcr` | **409 `dcr_not_cancellable`** (state ∉ {Open,Assessed,Routed}); 404; 403 |
| 4 | `POST /capas/{capa_id}/raise-dcr` | `changeRequest.create` (in-handler) | `DcrFromCapa` + `Idempotency-Key` header | **201 new / 200 replay** `Dcr` | 422 target family; 404; 409 `capa_terminal`; 403 |
| 5 | `POST /management-reviews/{rid}/outputs/{oid}/raise-dcr` | `changeRequest.create` (in-handler) | `OutputDcrCreate` + `Idempotency-Key` header | **201 / 200 replay** `Dcr` | 422 target family; 404; 409 `output_not_actionable` / `review_not_tracking`; 403 |

> ⚠ `POST /dcrs` (#1) takes **no `Idempotency-Key`** and is 201-only. Only the two **spawn** endpoints
> (#4, #5) are the 201-new/200-replay idempotent shape.

### 2.2 Request body shapes (verbatim field lists)

```python
# api/dcr.py:71-79
class DcrCreate(BaseModel):
    change_type: DcrChangeType                          # REVISE | CREATE | RETIRE  (required)
    change_significance: ChangeSignificance             # MAJOR | MINOR             (required)
    reason_class: DcrReasonClass                        # 9 members                 (required)
    reason_text: str = Field(min_length=1, max_length=4000)                       # (required)
    target_document_id: uuid.UUID | None = None         # CREATE ⟺ null
    source_link_type: DcrSourceLinkType | None = None   # standalone leaves null
    source_link_id: uuid.UUID | None = None             # standalone leaves null
    proposed_effective_from: datetime.datetime | None = None

# api/dcr.py:82-86 — PATCH; every field None=unchanged (CANNOT clear a field)
class DcrPatch(BaseModel):
    reason_text: str | None = Field(default=None, min_length=1, max_length=4000)
    reason_class: DcrReasonClass | None = None
    change_significance: ChangeSignificance | None = None
    proposed_effective_from: datetime.datetime | None = None

# api/dcr.py:89-90
class DcrCancel(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)

# api/dcr.py:106-112 — CAPA spawn
class DcrFromCapa(BaseModel):
    change_type: DcrChangeType
    change_significance: ChangeSignificance
    reason_text: str = Field(min_length=1, max_length=4000)
    target_document_id: uuid.UUID | None = None         # required for REVISE/RETIRE
    reason_class: DcrReasonClass = DcrReasonClass.capa   # DEFAULTED to capa
    proposed_effective_from: datetime.datetime | None = None

# api/mgmt_review.py:120-125 — MR-output spawn (INLINE body in the contract; no reason_class field —
# the service FORCES reason_class=mgmt_review)
class OutputDcrCreate(BaseModel):
    change_type: DcrChangeType
    change_significance: ChangeSignificance
    reason_text: str = Field(min_length=1, max_length=4000)
    target_document_id: uuid.UUID | None = None
    proposed_effective_from: datetime.datetime | None = None
```

### 2.3 The CREATE⟺no-target biconditional (`services/dcr/service.py:141-169`)

- `change_type == CREATE` **and** `target_document_id` set → **422 `create_has_target`**.
- `change_type ∈ {REVISE, RETIRE}` **and** `target_document_id` null → **422 `target_required`**.
- target not found / cross-org → **404**; target `kind != DOCUMENT` (a Record) → **422 `not_a_document`**.

> The FE **enforces this client-side** so the first two 422s are unreachable from the UI: the target picker
> is **hidden** for `CREATE` and **shown + required** for REVISE/RETIRE (submit disabled until set). The
> server message is still surfaced verbatim as a calm backstop.

### 2.4 Enums (the FE already declares all of these — pin to `lib/types.ts:1284-1305`)

- `DcrChangeType = "REVISE" | "CREATE" | "RETIRE"` (declaration order REVISE-first).
- `ChangeSignificance = "MAJOR" | "MINOR"` (the **reused vault enum**, not DCR-specific).
- `DcrReasonClass` — 9 members (`regulatory, audit_finding, capa, process_improvement, error_correction,
  periodic_review, customer_requirement, mgmt_review, other`).
- `DcrSourceLinkType = "capa" | "finding" | "mgmt_review" | "risk"`.
- `DcrState` — 9 (`Open, Assessed, Routed, InApproval, Approved, Implemented, Closed, Cancelled, Rejected`).

### 2.5 FSM edges that bind ui-2a affordances (`domain/dcr/fsm.py:32-46`)

- **Edit** is `Open`-only (server 409 otherwise).
- **Cancel** is reachable **only from `{Open, Assessed, Routed}`** — there is **no `Approved→Cancelled`**
  edge. So the Cancel affordance hides once `state` is past `Routed`.

### 2.6 No reciprocal spawn latch (verified by grep, load-bearing for F3)

`Complaint.spawned_capa_id` and `ReviewOutput.spawned_capa_id` exist, but **nothing carries
`spawned_dcr_id`**. The CAPA→DCR and MR→DCR spawns are **1:N idempotent** with the link living only on the
*DCR* side (`source_link_type` + `source_link_id`, polymorphic, no FK). Therefore:

- A successful spawn **deep-links on success** to `/dcrs?dcr=<newId>` (no persistent "View DCR" on the
  source row — that would need a backend "spawned DCRs" enrichment, **declined** per the F3 decision).
- A spawn row shows **Raise only** (not the CAPA-style Raise/View two-state). A reload could raise a second
  DCR — **allowed by design** (1:N); the per-mount `Idempotency-Key` only dedups within a single modal mount.

## 3. Architecture & component inventory

All new files under `apps/web/src/features/dcr/` unless noted. Mirrors the CAPA write precedents
(`features/capa/mutations.ts`, `RaiseCapaModal`, `SpawnCapaModal`, `AdvancePanel`) and the S-dcr-ui-1 idioms.

### 3.1 `features/dcr/mutations.ts` (new)

Five hooks, **none optimistic** (the server is the source of truth — `features/capa/mutations.ts:18-19`
invariant). A shared `useDcrInvalidator()` re-reads the server after every write:

```ts
function useDcrInvalidator(id?: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["dcrs"] });
    if (id) {
      void qc.invalidateQueries({ queryKey: ["dcr", id] });
      void qc.invalidateQueries({ queryKey: ["dcr-impact", id] });
    }
  };
}
```

| Hook | Call | invalidates | Notes |
|---|---|---|---|
| `useRaiseDcr()` | `send<Dcr>("POST", "/api/v1/dcrs", body)` | `["dcrs"]` | `onSuccess`; returns the new `Dcr`; caller opens its drawer |
| `usePatchDcr(id)` | `send<Dcr>("PATCH", `/api/v1/dcrs/${id}`, body)` | `["dcrs"]`, `["dcr",id]`, `["dcr-impact",id]` | `DcrPatch`; `onSettled` (race-tolerant; see §6) |
| `useCancelDcr(id)` | `send<Dcr>("POST", `/api/v1/dcrs/${id}/cancel`, body)` | same triple | `DcrCancel`; `onSettled` (race-tolerant; see §6) |
| `useRaiseDcrFromCapa(capaId)` | `send<Dcr>("POST", `/api/v1/capas/${capaId}/raise-dcr`, body, idemHeader)` | `["dcrs"]` | per-mount `Idempotency-Key` |
| `useRaiseDcrFromMrOutput(reviewId, outputId)` | `send<Dcr>("POST", `/api/v1/management-reviews/${reviewId}/outputs/${outputId}/raise-dcr`, body, idemHeader)` | `["dcrs"]` | per-mount `Idempotency-Key` |

`api.send<Dcr>` returns the parsed body identically for **201 and 200** — there is **no status-code
branching** in the spawn success path (the `useSpawnCapa` precedent). `Idempotency-Key` is generated **once
per mount** (`crypto.randomUUID()` in a `useRef`/`useState` initializer — the `DecisionCard` precedent) and
passed as the 4th `send` arg `{ "Idempotency-Key": key }`.

### 3.2 `features/dcr/DcrRaiseFields.tsx` (new — shared presentational core)

A controlled (values + `onChange`) field cluster shared by all three raise modals:

- `change_type` — Mantine `SegmentedControl` (REVISE / CREATE / RETIRE; labels from `labels.ts`).
- **Conditional target picker** — a `useDocuments`-backed `Select` (searchable; option label
  `${identifier} — ${title}`, value = `document.id`). **Hidden when `change_type === "CREATE"`; shown +
  required for REVISE/RETIRE.** Switching to CREATE clears any selected target (so the body never carries a
  CREATE-with-target). Backed by `useDocuments(filters, page)` from `features/library/useDocuments.ts`
  (returns `{data, page}`); v1 uses a reasonable first page + client-side substring filter on
  identifier/title (the exact filter wiring is a plan detail; the picker only ever lists `kind=DOCUMENT`, so
  `not_a_document` is unreachable from the UI).
- `change_significance` — `SegmentedControl` (MAJOR / MINOR).
- `reason_text` — `Textarea` (required, 1–4000, trim-validated).
- `proposed_effective_from` — optional `DateInput` (R8: a local-midnight date → ISO; null allowed).

A small derived `isValid` (reason non-empty **and** (CREATE **or** target set)) drives the submit button's
`disabled`. `reason_class` is **not** in this core — only the standalone modal exposes it (see §3.3).

### 3.3 `features/dcr/RaiseDcrModal.tsx` (new — standalone create)

`{ opened?, onClose, onCreated }`. Renders `DcrRaiseFields` + a `reason_class` `Select` (9 members from
`labels.ts`). Submit → `useRaiseDcr` → on success `onCreated(dcr.id)` then `onClose()`. Surfaces
`ApiError.message` verbatim on 422/404 (calm `Alert`). Conditionally mounted by the parent
(`{raising && <RaiseDcrModal .../>}`) so close unmounts + resets typed state (the S-web-7d idiom).
The parent (`DcrsRegisterPage`) wires `onCreated={(id) => setSelected(id)}` → the new DCR's drawer opens.

### 3.4 `features/dcr/SpawnDcrModal.tsx` (new — parameterized CAPA + MR-output spawn)

`{ title, mutation, onClose }` where `mutation` is a pre-bound `useRaiseDcrFromCapa(capaId)` or
`useRaiseDcrFromMrOutput(reviewId, outputId)`. Renders `DcrRaiseFields` **without** a `reason_class` field
(CAPA defaults `capa`, MR forces `mgmt_review`). On success → `navigate('/dcrs?dcr=' + dcr.id)` (deep-link;
the `RaiseMrCapaModal`/`SpawnCapaModal` precedent). Conditionally mounted by each source.

### 3.5 `features/dcr/EditDcrModal.tsx` (new — PATCH while Open)

`{ dcr, onClose }`. Pre-seeded from the current `dcr` (`reason_text`, `reason_class`, `change_significance`,
`proposed_effective_from`). On submit, sends **only changed** fields (`None`=unchanged; cannot clear
`proposed_effective_from`). `usePatchDcr` → invalidate → `onClose()`. 409 `dcr_not_editable` (a racing
advance) surfaced calmly + the invalidate refreshes the drawer to the real state. Conditionally mounted.
Gate at the call site: `can("changeRequest.assess") && dcr.state === "Open"`.

### 3.6 Edits to existing surfaces

- **`DcrsRegisterPage.tsx`** — a header "Raise DCR" `Button`, gated `can("changeRequest.create")`, toggling
  a `raising` state that conditionally mounts `RaiseDcrModal`; `onCreated` → `setSelected(newId)` (the
  existing `?dcr=` drawer seam opens the new DCR).
- **`DcrDrawer.tsx`** — a new **actions area** (below the badges) rendering, per state + per key:
  **Edit** button (`can("changeRequest.assess") && state==="Open"` → mounts `EditDcrModal`) and **Cancel**
  button (`can("changeRequest.close") && state ∈ {Open,Assessed,Routed}` → a small confirm with an optional
  comment → `useCancelDcr`). Uses `usePermissions(scope)` where `scope` resolves the DCR's own scope
  (PROCESS via the target doc when available, else SYSTEM — the `AdvancePanel` precedent; v1 SYSTEM fallback
  is acceptable). *ui-2b grows this actions area into the full assess/route/implement/close `AdvancePanel`.*
- **`features/capa/CapaDrawer.tsx`** — a "Raise DCR" `Button`, gated `can("changeRequest.create")`, mounting
  `SpawnDcrModal` bound to `useRaiseDcrFromCapa(capa.id)`. (No View-DCR affordance — §2.6.)
- **`features/management-review/ReviewOutputsSection.tsx`** — on **ACTION** output rows, a "Raise DCR"
  `Button` gated `tracking && can("changeRequest.create")` (mirrors the existing `canRaiseCapa` two-state
  block, but **Raise-only** — no `spawned_dcr_id` to drive a View link), mounting `SpawnDcrModal` bound to
  `useRaiseDcrFromMrOutput(review.id, output.id)`.

## 4. Data flow

```
Raise (standalone):  DcrsRegisterPage → RaiseDcrModal → useRaiseDcr → POST /dcrs → 201 Dcr
                     → invalidate ["dcrs"] → onCreated(id) → setSelected(id) → drawer opens on the new DCR
Raise (CAPA):        CapaDrawer → SpawnDcrModal → useRaiseDcrFromCapa(idem) → 201/200 Dcr
                     → invalidate ["dcrs"] → navigate /dcrs?dcr=<id>
Raise (MR-output):   ReviewOutputsSection(ACTION) → SpawnDcrModal → useRaiseDcrFromMrOutput(idem) → 201/200
                     → invalidate ["dcrs"] → navigate /dcrs?dcr=<id>
Edit:                DcrDrawer(Edit, Open) → EditDcrModal → usePatchDcr → PATCH → 200 Dcr
                     → invalidate ["dcr",id]+["dcrs"]+["dcr-impact",id] → drawer refreshes
Cancel:              DcrDrawer(Cancel, {Open,Assessed,Routed}) → useCancelDcr → POST /cancel → 200 Dcr
                     → invalidate (onSettled) → drawer refreshes to Cancelled
```

## 5. Affordance gating (per-key, per-state)

| Affordance | Permission key | State guard | Extra |
|---|---|---|---|
| Raise (standalone / CAPA) | `changeRequest.create` | — | — |
| Raise (MR-output) | `changeRequest.create` | — | **+ `tracking` window** (mirrors `canRaiseCapa`) |
| Edit | `changeRequest.assess` | `state === "Open"` | pre-seeded modal |
| Cancel | `changeRequest.close` | `state ∈ {Open, Assessed, Routed}` | optional comment |

`usePermissions(scope).can(key)` (v1 SYSTEM fallback). A write button the caller can't exercise is
**hidden** (not shown-then-403 — these are single-gated keys `can()` answers correctly; the only
double-gated write, implement, is ui-2b). A read the caller lacks already degrades calmly via the read
spine's `forbidden` flag.

## 6. Error handling (calm, server-truth)

- **409 `dcr_not_editable` / `dcr_not_cancellable`** — a concurrent advance moved the DCR out of the legal
  state. Surface the server `message` calmly **and** invalidate so the drawer refreshes to the real state
  (the `useNcrDisposition` `onSettled` precedent — hence Cancel/Edit invalidate on settle, not only on
  success).
- **422 `create_has_target` / `target_required` / `not_a_document`** — prevented by the client CREATE⟺target
  logic + the documents-only picker, but surfaced verbatim as a backstop.
- **409 `capa_terminal` / `output_not_actionable` / `review_not_tracking`** — the source isn't in a
  spawnable state; surface the message verbatim (the Raise button is already tracking-gated for MR, so these
  are edge cases).
- **Spawn 201 vs 200** — handled **identically**; no client branching.
- All errors render as a Mantine `Alert color="red"` with `e instanceof ApiError ? e.message : <fallback>`
  (the `errText` idiom). No optimistic cache writes anywhere.

## 7. Testing strategy (`/check-web` only)

- Every component test: `import { expect, it } from "vitest"` (the jest-dom × tsc trap).
- **MSW handlers** for all five writes, added to `test/msw/handlers.ts`, **pinned to the real serializers**
  (`Dcr` for #1-#4-#5 / `Dcr` for #2-#3; the spawn handlers return 201 then 200 on replay) via fixtures the
  strict `tsc` enforces; respect **static-before-`:id`** registration order.
- **CREATE⟺no-target field-toggle** test: target picker hidden for CREATE, shown + required (submit disabled
  until set) for REVISE/RETIRE.
- **Conditional-mount reset** test: a modal closed mid-typing reopens pristine.
- **Deep-link-on-success** tests: standalone Raise opens the new DCR's drawer (`?dcr=` set); a CAPA/MR spawn
  `navigate`s to `/dcrs?dcr=<id>` (a `LocationProbe` or `navigate` spy).
- **Calm-error** tests: 409 `dcr_not_editable` / `dcr_not_cancellable` render the server message + the row
  refreshes; a 422 backstop renders the message.
- **Gating** tests: each affordance hidden without its key / out of its state window.
- **jest-axe smoke** on the modal-bearing page(s) (`DcrsRegisterPage`, and the CapaDrawer / MR section
  hosting the spawn buttons) — heading-order + label checks; the **Mantine-v7 duplicate-`aria-label`**
  `Select` trap → `getAllByLabelText(...)[0]`.
- Run the **full `/check-web`** (eslint + strict `tsc --noEmit` + build + the whole vitest suite) before the
  PR — the per-file run is blind to the jest-dom×tsc trap and `noUncheckedIndexedAccess` nits.

## 8. Verification & live smoke

- Local gate: `/check-web` (the FE runs fully natively on this Windows box).
- diff-critic on the branch diff pre-PR.
- **Live smoke (Chrome MCP):** rebuild the web image (`up -d --build web`); grant `changeRequest.create` +
  `.assess` + `.close` (+ the existing `changeRequest.read`/`document.read`) **SYSTEM overrides** on the
  live demo `app_user` (org **AHT**, the grant-all-org-users heredoc); pre-create a target Document (or
  reuse one) so the REVISE target picker has an option; exercise all three raise seams + edit + cancel; the
  owner does the Keycloak login. *(implement/approval smoke is a ui-2b concern — it needs a second SoD
  principal + a live candidate-pool approver.)*

## 9. Deferred — named, not faked

**→ S-dcr-ui-2b** (own spec; backend + FE): the assess / route / implement / close lifecycle cockpit
(`AdvancePanel`/stepper switching on `dcr.state`); the **detail-only `capabilities.implement`** enrichment
on `_dcr` (the S-mr-3 `_mr_capabilities` precedent — avoids the implement double-gate show-then-403;
+`/check-api` +`/check-contracts`); the **RETIRE `force_retire` + `override_justification`** escalation on a
409 `obsoletion_blocked`; the **close 409 `dcr_effectivity_pending`** submit-and-show (no client gate — the
CAPA `CloseAction` precedent); the **`/tasks` DCR-approval leg** (`task.subject_type === "DCR"` →
`DcrApprovalContext` + a signing `DecisionCard`, outcomes approve/reject/changes_requested,
`meaning=approval`, **candidate-pool authz not a key** — extend `DecisionSubjectType`, add the
invalidation leg for `["dcr",id]`/`["dcrs"]`); **CREATE-implement** (needs `version_id→document_id`
resolution that doesn't exist client-side); the **doc-15 §8.7 + OpenAPI prose-drift** fix
(`reason_for_change`/nested `source_link` → the flat `reason_class`+`reason_text` / `source_link_type`+
`source_link_id`; the stale `DcrSourceLinkType` "mgmt_review reserved" description) — rides ui-2b's contract
touch.

**→ S-dcr-ui-3:** the page-image visual diff + text/metadata redline against `resulting_version_id`
(REVISE-only; reuse `features/document/VisualDiffViewer`/`useVisualDiff`).

**Not faked (no backend touch this epic):** a persistent "View DCR" on CAPA/MR-output source rows (no
`spawned_dcr_id` latch + 1:N — the F3 decision); a document-page "Raise change request" entry point (not one
of the three named seams; a future nicety with the target pre-filled).

## 10. Risks & mitigations

- **The target picker** is the one non-trivial new UI. Mitigation: reuse `useDocuments`; v1 a searchable
  Select over a first page + client-side filter; the picker only lists `kind=DOCUMENT` so `not_a_document`
  is unreachable. (If the org has many documents, pagination/server-search is a v1.x refinement — noted.)
- **Fabricated MSW fixtures** (the #1 web false-PASS). Mitigation: every fixture `satisfies` the real
  `lib/types.ts` type; the spawn handlers mirror the real 201/200 idempotent shape.
- **`?dcr=` deep-link races** the freshly-invalidated `["dcrs"]`. Mitigation: the drawer reads `useDcr(id)`
  (its own `["dcr",id]` query), independent of the list; the register seam already handles a deep-link on
  mount/param-change (S-dcr-ui-1).
- **Cancel/Edit on a stale state.** Mitigation: invalidate-on-settle so the drawer self-heals to the real
  state behind the calm 409.

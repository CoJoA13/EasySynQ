# S-web-7 — Nonconformity & CAPA front door (epic design)

> **Status:** approved (owner, 2026-06-08). **Track:** web-UI. **Surfaces:** the Audits/Findings/CAPA
> v1 family (R39), already built + contracted on the backend. **Closes:** the ACT-phase / Clause 10.2
> operational loop in the SPA (mockup `#screen-capa`).
> **Decomposition:** one unified design, built as **4 dependency-ordered PRs** (7a–7d). Each PR gets its
> own implementation plan when we reach it; this doc is the shared design.

## 1. Why / what

EasySynQ already owns the entire nonconformity → CAPA backend (R39): declarative severity-routed
approval, severity-aware SoD-4, the S-capa-3 Verify→RootCause effectiveness loop, the M4 real-evidence
close gate, and the audit **block-until-corrected** close gate. None of it has a UI. This epic surfaces
the whole **nonconformity front door** in the SPA:

- the **CAPA board** (kanban by lifecycle state) + an in-context **detail drawer** (the closed-loop
  thread + the close-gate stepper), per the owner-approved mockup `#screen-capa`;
- the **CAPA lifecycle writes** (raise → containment → root-cause → action-plan → implement → verify →
  close, with the loop), leaning on shipped surfaces for the heavyweight integration points;
- **complaint** and **NCR** intake (the lightweight front doors that feed CAPAs);
- the **audit** module + **findings** (NC auto-creates a CAPA; the audit can't close while a live NC is
  uncorrected).

This is **front-end only except one thin read-enrichment** (§4): the backend `Capa` row carries no
human label, so a usable title-led board needs `title` added to the serializer.

## 2. The decomposition — 4 PRs

| PR | Name | Scope | Backend? |
|----|------|-------|----------|
| **7a** | CAPA read spine | Kanban board (cols = `close_state`) + Board/List toggle + summary tiles + read-only drawer (closed-loop timeline from `stages[]` + close-gate stepper) + nav entry | **+ thin read-enrichment** (§4) |
| **7b** | CAPA lifecycle writes | The 6 stage forms in the drawer (raise · containment · root-cause · action-plan · implement · **signed** verify · close); `usePermissions`-gated; SoD-4 calm-409; M4 close gate; Verify→RootCause loop; approval **shown** in drawer, **decided in `/tasks`**; light "link existing record as evidence" | front-end only |
| **7c** | Complaint & NCR intake | Complaints (list · create · one-click **spawn-CAPA**, idempotent) + NCRs (list · create · ISO 8.7 **disposition**, one-shot). Self-contained — no audit dependency | front-end only |
| **7d** | Audits & findings | Audit module (programs/plans/audits list+create+lifecycle) + findings (create NC→**auto-CAPA**, correction/retype) + **block-until-corrected** close gate | front-end only |

**Order rationale:** 7a establishes the domain in the SPA (types, fixtures pinned to real shapes, nav,
the read spine); 7b adds the writes onto that spine; 7c (complaints/NCRs) is self-contained and spawns
CAPAs (visible on the 7a board); 7d (audits) is the largest and most independent, and its findings also
auto-create CAPAs visible on the board.

## 3. Verified backend surface (the load-bearing facts)

All citations are `apps/api/src/easysynq_api/...` unless noted. Every MSW fixture in this epic is pinned
to **these** shapes, verified against the serializer/route code — never the mockup or a guess (the #1
lesson from S-web-6 / S-ing-4b: a fabricated fixture hides a wrong-shape bug that ships reading 0/undefined).

### 3.1 Reads

- **`GET /capas`** (`api/capa.py:273`) → **flat `{"data": [...]}`** — **no pagination envelope, no
  server-side filters**. The board fetches the whole org list and groups/filters **client-side** (the
  list is org-scoped + bounded → kanban-friendly, no virtualization). Gate `capa.read` (`_capa_read`,
  `capa.py:230`).
- **`GET /capas/{id}`** (`api/capa.py:282`) → the `Capa` row **plus `stages: CapaStage[]`**; 404 if not
  found / cross-org. Gate `capa.read`.
- **`Capa` row serializer** (`_capa`, `capa.py:137`): `id · identifier (CAPA-NNN | null) · source · severity
  · process_id (nullable) · close_state · cycle_marker · origin_finding_id (NULL in v1)`. **No title, no
  description, no due-date** on the row. → §4 enriches this.
- **`CapaStage` serializer** (`_stage`, `capa.py:126`): `id · stage · content_block (free-form JSON) ·
  cycle_marker · created_by (user id) · created_at`. `content_block` is **free-form per stage** (no fixed
  schema in v1) → render **generically** (key/value), never typed fields.
- **`GET /users`** (`api/users.py:132`) exposes `display_name` → resolve a stage's `created_by` to a name;
  **degrade to the raw id** if the lookup isn't permitted (don't hard-depend on it).
- **`GET /me/permissions?scope_level=&scope_id=`** (`api/auth.py:48`) → the caller's effective grants;
  the SPA already wraps it as `usePermissions().can(key)` (`app/shell/usePermissions.ts`).

### 3.2 The lifecycle (FSM) — `domain/capa/fsm.py`

```
Raised → {Containment, Rejected}
Containment → {RootCause, Rejected}
RootCause → {ActionPlan, Rejected}
ActionPlan → {Implement, Rejected}
Implement → {Verify, Rejected}
Verify → {Closed, RootCause}     # not_effective loops back to RootCause, cycle_marker++
Closed → {}   Rejected → {}      # terminal
```

### 3.3 Writes (for 7b–7d; request shapes from `packages/contracts/openapi.yaml`)

- `POST /capas` ← `CapaRaise {title*, severity*, source?, process_id?, problem?}` (5623). **`title` is
  stored on the record** (`services/capa/service.py:321/377`); the Raised stage block is
  `{problem, source, severity}` (`:381`) — so neither title nor problem is on the `Capa` row today.
- `POST /capas/{id}/containment` ← `ContainmentCreate {content_block*}` (gate `capa.update`).
- `POST /capas/{id}/root-cause` ← `StageBlockCreate {content_block*}` (gate `capa.record_rca`; unsigned).
- `POST /capas/{id}/action-plan` ← `StageBlockCreate {content_block* (action_items[])}` (gate
  `capa.plan_action`) → returns `Capa + approval_instance {id, current_state, definition_version}`;
  **`close_state` stays RootCause until the approving `POST /tasks/{id}/decision`** (`capa.py:331-344`).
  `current_state == NEEDS_ATTENTION` when no approver is assigned (re-propose after assigning).
- `POST /capas/{id}/implement` ← `StageBlockCreate {content_block*}` (gate `capa.capture_effectiveness`).
- `POST /capas/{id}/verify` ← `CapaVerifyCreate {decision* (effective|not_effective), content_block*}`
  (gate `capa.verify`; **SIGNED** → `signature_event(meaning=verify)`). **Severity-aware SoD-4** (verifier
  ≠ implementer) is enforced server-side after the gate, never bypassed by SYSTEM → `409 sod_self_verify`.
- `POST /capas/{id}/close` (gate `capa.close`): `effective` + root_cause + ≥1 action-with-evidence +
  effectiveness-evidence → `Closed`; `not_effective` → loop to RootCause (cycle++); `effective` but
  missing evidence → `409 capa_close_incomplete`.
- `POST /records/{id}/evidence-links` ← `EvidenceLinkCreate {target_type, target_id, link_reason?}`
  (`api/records.py:88`); `target_type` ∈ `clause|process|document|finding|capa_stage`. The Implement +
  Verify stages need linked evidence to pass the M4 close gate.
- **Complaints** (`capa.py:405+`): `POST /complaints` ← `ComplaintCreate` (gate `record.create`);
  `GET /complaints`/`{id}` (gate `record.read`); `POST /complaints/{id}/spawn-capa` ← `SpawnCapa` (gate
  `capa.create`; **idempotent** via the `spawned_capa_id` latch — 201 new / 200 replay). Serializer
  `_complaint` (`capa.py:155`): `id · identifier · customer · received_at · channel · description ·
  severity? · spawned_capa_id?`.
- **NCRs** (`capa.py:470+`): `POST /ncrs` ← `NcrCreate` (gate `ncr.create`); `GET /ncrs`/`{id}` (gate
  `ncr.read`); `PATCH /ncrs/{id}/disposition` ← `NcrDispositionBody` (gate `ncr.record_correction`;
  **one-shot** — 409 if already disposed). Serializer `_ncr` (`capa.py:168`): `id · identifier (NCR-NNN)
  · source · description · severity · process_id? · disposition? · disposition_authorized_by? ·
  disposition_notes? · disposed_at? · created_at`.
- **Audits/findings** (`api/audits.py`): programs/plans/audits CRUD + lifecycle transitions
  (`/plan /conduct /draft-findings /report /begin-closing /close`); findings
  (`POST /audits/{id}/findings` ← `FindingCreate`, NC auto-creates a linked CAPA; `POST
  /findings/{id}/correction` ← `FindingCorrection`). `POST /audits/{id}/close` → `409 audit_close_blocked`
  while any live NC finding lacks a Closed CAPA (R39).

### 3.4 Permission keys + who holds them (`migrations/versions/0004_seed_authz.py`)

All `capa.*` / `finding.*` / `ncr.*` keys are **non-system, PROCESS-scoped** (except `capa.verify` /
`capa.close`, which are sig-hook keys). **The System-Administrator bundle (`_SYSTEM_KEYS`, the `demo`
admin) holds NONE of them** — this is the **S-web-6 compliance case** (calm-403), *not* the S-ing-4b
import case. Holders: `capa.read` → QMS-Owner + Process-Owner + Internal-Auditor; the write keys →
Process-Owner (create/update/rca/plan/implement); `capa.verify`/`capa.close` → QMS-Owner. So:

- **The board 403s for `demo`** → a calm no-access panel. Live smoke: grant `demo` SYSTEM overrides of
  the relevant keys (the integration-test pattern; org short_code `AHT`), or use a persona.
- A **full Critical/Major close** needs **≥2 distinct users** (SoD-4 verifier ≠ implementer is
  non-overridable). A Minor CAPA with `allow_capa_self_verify` on can be driven by one user. (Smoke detail
  for 7b, not a build blocker; MSW tests simulate responses.)

## 4. The one backend change (in 7a) — thin read-enrichment

Add to the `_capa` serializer (`api/capa.py:137`):

- **`title`** — from the record (`documented_information.title`; already written at raise, `service.py:321`).
  **list + detail.**
- **`created_at`** — the CAPA record's creation time (for "age" / ordering). **list + detail.**
- **`raised_by`** — the Raised-stage `created_by` (the raiser). **detail only** (computed from the
  already-loaded `stages[0]`; the list avoids a per-row subquery — the board card shows no avatar).

Implementation is trivial: `list_capas` (`repository.py:105`) **already joins `DocumentedInformation`** and
selects `identifier` from it, so `title` + `created_at` are a zero-cost add to that same `select`; the
serializer takes them as args. `get_capa` already loads `stages`, so detail computes `raised_by` from
`stages[0].created_by` with no query change. **No migration** (all columns already exist). Add the fields
to the `Capa` schema in `openapi.yaml` (`/check-contracts` gates it). No new permission key, no new endpoint.

> Note: `list_capas` filters only on `org_id` (`repository.py:109`) — it returns **all** states, so the
> board's Closed column populates and the by-source tile sees terminal CAPAs.

> Rationale: without `title`, board cards read `CAPA-2026-031 · Major · Audit · RootCause` — no human
> label, because the title lives on the record and the row serializer omits it. This is the difference
> between a usable board and a code-only one. It is the only non-front-end work in the epic.

## 5. Cross-cutting decisions (every PR)

1. **Fixtures pinned to real shapes.** Mirror the §3 serializers exactly: `{data: Capa[]}` envelope; the
   spare row (+ the §4 fields); the detail's nested free-form `stages[].content_block`; `close_state` ∈
   the 8-state FSM; complaints/NCRs/findings per their serializers. Verify each against `apps/api`, not
   the mockup.
2. **Gating** = `usePermissions().can("capa.…")` at the CAPA's **PROCESS scope**
   (`?scope_level=PROCESS&scope_id=<process_id>`; SYSTEM fallback when `process_id` null). Don't render a
   write affordance the caller can't exercise (the recurring Codex catch).
3. **SoD-4 / close gate are server-only truths.** The Verify button renders on `capa.verify`, but
   `409 sod_self_verify`, `409 capa_close_incomplete` (M4), `409 audit_close_blocked` (R39) are surfaced
   **calmly** (clear inline message), never a crash.
4. **Calm-403 / no-access** (compliance-checklist precedent): the read hook surfaces a `forbidden` flag →
   a calm no-access panel; nav entry stays discoverable, the page explains the missing key.
5. **Honest tiles / affordances only.** Open-count + by-source are computable → keep. Overdue /
   avg-cycle-time / clause-ref-on-card / Owner+Age filters have no backing data → **dropped** (no faked
   affordances — the PDCA-dashboard lesson). No button that 403s/422s.
6. **Verify is signed but needs no signing UI** — step-up is a v1 no-op (`auth.py:71`, S-web-5 release
   precedent); the `signature_event` is written server-side.
7. **Free-form `content_block` rendered generically** — key/value, XSS-safe (no
   `dangerouslySetInnerHTML`), never typed fields (the schema is free-form in v1).

## 6. PR 7a — CAPA read spine (the first build)

### 6.1 Backend
The §4 enrichment (`title` + `created_at` on the list + detail `_capa`; `raised_by` on detail only) + the
`Capa` schema field-add in `openapi.yaml`. Gated locally by `/check-api` (ruff/format/mypy — the api
**test** suites are Linux-CI-only on this box) + `/check-contracts`.

### 6.2 Front-end
- **Nav + route** — a "Nonconformity & CAPA" entry (ACT / Improvement group); page reachable; **calm
  no-access panel on 403** (so `demo` sees a clear "you don't hold `capa.read`" panel, not a blank).
- **Board** — `useCapas()` → `{data}`, grouped **client-side** by `close_state` into **6 columns**:
  **Open** (Raised) · **Correction** (Containment) · **Root Cause** (RootCause) · **Action** (ActionPlan
  + Implement) · **Verify** (Verify) · **Closed** (Closed). **Rejected** rows fold into a muted
  "Closed / Rejected" tail (not dropped). Card = `identifier · title · severity badge · source tag ·
  close_state`. **Board / List** segmented toggle (List = a table of the same rows).
- **Summary tiles** — **Open count** (non-terminal) + **by-source** breakdown, computed from the list.
  (Overdue / avg-cycle-time dropped.)
- **Filters** — client-side: **source · severity · state**. (No Owner/Age — unbacked.)
- **Drawer (read-only)** — card click → `useCapa(id)` → `stages[]`:
  - **Closed-loop timeline** — stages in order; each: stage label · `created_at` · actor
    (`created_by`→`display_name`, degrade to id) · the free-form `content_block` rendered generically.
    `cycle_marker > 0` renders the Verify→RootCause loop honestly (repeated RootCause/Verify at higher
    cycles).
  - **Close-gate stepper** — 3 steps (Root cause documented · Corrective action defined · Effectiveness
    evidence), derived from stage presence; **informational in 7a** (evidence-completeness wiring lands in
    7b with the close action).
  - Header meta: severity · source · state · process · cycle.
- **Hooks/types** — `useCapas()` (list), `useCapa(id)` (detail); `Capa` / `CapaStage` types in
  `lib/types.ts` matching §3.1 + §4. Optional `useUsers()` map for name resolution (degrade-gracefully).

### 6.3 Tests
vitest + MSW + jest-axe. Fixtures pinned to §3.1 + §4 shapes. Cover: grouping into columns (incl. the
ActionPlan+Implement merge and the Rejected tail), client-side filters, the calm-403 panel, the drawer
timeline (incl. a `cycle_marker > 0` loop), generic `content_block` render (incl. an XSS-y value rendered
as text), by-source tile math. Run the full `/check-web` (eslint + strict tsc + build + the whole vitest
suite) before the PR — the full run catches cross-file drift the per-file runs miss.

## 7. Out of scope (this epic)
- The PDCA dashboard (its acknowledgement/objective engines don't exist → tiles would be faked).
- Any new permission key / lifecycle change / migration beyond §4's serializer enrichment.
- Drawer-native approval decisions or drawer-native evidence **upload** (7b leans on `/tasks` + linking an
  *existing* record; net-new evidence capture stays in the records surface).
- Export of CAPA/audit data (the `GET /audit-events/export` async-job pattern is deferred backend-side).

## 8. Risks / watch-items
- **Wrong-shape fixture** (the recurring false-PASS): mitigated by §5.1 — pin to §3, verify vs `apps/api`.
- **PROCESS-scoped gating**: `usePermissions` must query at the CAPA's process scope, not SYSTEM, or a
  genuinely process-scoped grant mis-gates (the S-pack-1 R28 lesson, SPA side).
- **`created_by` name resolution** may 403 for non-admins → must degrade to id, not error.
- **`origin_finding_id` is NULL in v1** → the board can't link a card back to its finding/complaint from
  the CAPA side; source is a tag only (the finding→CAPA link is `auto_capa_id` on the finding, surfaced in
  7d from the audit side).

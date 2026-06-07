# EasySynQ Web-UI Track — S-web-3 Design (Document Authoring)

> **Status:** Draft for owner review · **Date:** 2026-06-07 · **Owner:** CoJoA13
> **Work-stream:** the web-UI track, slice 3. Builds on **S-web-1** (PR #83 — app shell + token port) and
> **S-web-2** (PR #86 — faceted Library + read-only tabbed drawer + the minimal read endpoints +
> `{data,page}` envelope). Shares the SPA architecture in
> `docs/superpowers/specs/2026-06-06-web-track-s-web-1-design.md` (§3 routing / provider tree / api client) and
> reuses the S-web-2 drawer + `features/document/` components. This is a **full-stack** slice: two migration-free
> backend additions (an affordance endpoint + a per-document capability block) + the front-end authoring journey.

## 1. Context & the locked owner decisions

S-web-2 shipped a read-only Library + drawer and deferred **every write affordance** and `/me/permissions` to "the
write slice." This is that slice — the first **write** surface in the operational app. A grounded six-agent
exploration (the `s-web-3-exploration` workflow, 2026-06-07; every fact below is cited `file:line`) mapped the
authoring backend, the vault/SoD enforcement, the approval/tasks boundary, the doc-11/mockup UX, the apps/web
conventions, and the affordance-gating options. Three scoping decisions were then locked with the owner:

- **D-A — Scope = the author's half: create → submit-review.** S-web-3 ships create, check-out, upload/check-in,
  clause-mapping, and **submit-for-review**, and renders the resulting `InReview` ("Awaiting review") state. The
  **reviewer inbox, the approve / request-changes decision, and release** are deferred to **S-web-5 (Review &
  Approve)**. This is forced by **SoD-1** (`document.edit` vs `document.approve`, `SAME_VERSION`, **HARD_DENY,
  non-overridable** — `migrations/versions/0009_seed_workflow_sod.py:38-46`): the author of a version *cannot*
  approve it, and `GET /tasks` self-scopes the author **out** of their own review (`services/workflow/repository.py:208-235`),
  so "approve" is structurally a *different user's* journey. submit-review is the clean FSM seam — the last action
  the author owns.
- **D-B — Affordance gating (DP-6) = add `GET /me/permissions` + per-document `capabilities`.** Both are
  **migration-free** and add **no new permission key** (R5/R38 untouched — they report the caller's *own* grants, so
  there is no resource to gate, the `GET /me` precedent). `/me/permissions` answers coarse SYSTEM-scope affordances
  (the Library "New Document" entry); the per-document `capabilities` block on `GET /documents/{id}` answers the
  precise per-button question soundly (ABAC + version-relative SoD); optimistic-403 is the documented fallback for
  the dynamic edges (lock state, SoD self-release). This touches the `api`, `contracts`, and `integration` CI jobs.
- **D-C — UX shape = a New-Document wizard + capability-gated "Author actions" in the existing drawer.** A guided
  Mantine `Stepper` "New Document" wizard launched from the Library, plus an "Author actions" section added to the
  shipped S-web-2 `DocumentDrawer` for continuing/​submitting an existing Draft (and starting a revision on an
  Effective doc). The **standalone Document detail *page* is now S-web-4** (re-sequenced — see §10), so S-web-3 does
  **not** pre-build it; it reuses S-web-2's drawer + `ArtifactHeader` instead.

## 2. What the API actually serves today (the grounding facts)

The authoring backend is **complete**; S-web-3 is almost entirely a front-end slice over it, plus two small read
additions for DP-6. Every endpoint and its gate (`apps/api/src/easysynq_api/api/documents.py`):

- **The author chain is a fixed 7-call sequence**, each with its own gate (note: *three different keys* across one
  upload flow):
  - `POST /documents` (`documents.py:574-597`) — gate **`document.create`** (imperative, scope = DOC_CLASS from the
    document-type's `document_level`). Body `DocumentCreate{title, document_type_id, area_code?, folder_path?,
    classification="Internal"}` (`documents.py:86-91`). Creates a **Draft with no version**; identifier is
    vault-allocated `{TYPE}-{AREA}-{SEQ}` (`services/vault/service.py:111-160`). A 2nd singleton (Quality
    Policy/Scope) → **409 `singleton_exists`**.
  - `POST /documents/{id}/checkout` (`:1045-1054`) — gate **`document.checkout`**. Acquires the Redis exclusive lock
    (8h TTL, `LOCK_TTL_SECONDS=28800` — `services/vault/locks.py:18`); held-by-another → **409 `lock_conflict`** with
    the holder. Returns a `WorkingDraft{…, lock_ttl_seconds}` (`documents.py:215-223`).
  - `POST /documents/{id}/versions:init-upload` (`:1057-1065`, **note the colon**) — gate **`document.checkout`**.
    Body `InitUpload{sha256, content_type}`. Returns `{dedup:true, object_key, upload_url:null}` (bytes already
    vaulted — **skip the PUT**) **or** `{dedup:false, object_key:<sha>, upload_url:<presigned PUT>}`
    (`services/vault/service.py:198-215`). The presigned PUT targets the **plain `staging` bucket**, rewritten to the
    browser-reachable `s3_public_endpoint` (`services/vault/storage.py:72-100`).
  - **PUT the bytes → the presigned MinIO URL** (raw, cross-origin, *no* bearer — the S3 signature is the auth). The
    API never proxies bytes (D1) and **trusts the client `sha256`** as the content-addressed key (no server re-hash).
  - `POST /documents/{id}/checkin` (`:1068-1087`) — gate **`document.edit`**. Body `CheckIn{sha256, change_reason,
    change_significance, mime_type?}`. **INV-3**: empty `change_reason` → **422** (`field=change_reason`);
    `change_significance ∉ {MAJOR, MINOR}` → **422** (`services/vault/service.py:237-253`); not the lock-holder →
    **409 `lock_conflict`**; staged object missing → **422 "Uploaded object not found …"** (never PUT / wrong sha,
    `:269-275`). Identical-to-latest bytes → `change_detected:false`, **no new version**. Returns the new
    `DocumentVersion` (state Draft) + `change_detected`.
  - `POST /documents/{id}/clause-mappings` (`:727-780`) — gate **`document.manage_metadata`**. Body
    `ClauseMappingCreate{clause_id, is_requirement_level?}`. Framework mismatch → 422; duplicate → 409. `clause_id`s
    come from `GET /clauses` (gate `clauseMap.read`).
  - `POST /documents/{id}/submit-review` (`:1116-1132`) — gate **`document.submit`**. **422 `validation_error`
    (`field=clause_mappings`) if the doc has 0 clause mappings** (`services/vault/lifecycle.py:188-200`). On success →
    `current_state=InReview`; **instantiates the `document_approval` workflow + an APPROVE task** in the same commit
    (`documents.py:1129`, `services/workflow/service.py:42-86`). Illegal-from-state → **409 `invalid_state_transition`**.
- **Revision (for an Effective doc):** `POST /documents/{id}/start-revision` (`:1156-1164`) — gate **`document.edit`**
  (no `document.revise` key exists) → `UnderRevision` + a fresh working draft seeded from the Effective version
  (which keeps governing); then the same upload→checkin→map→submit (T9). Lock held → 409.
- **The download "working copy":** `GET /documents/{id}/versions/{vid}/download` (gate **`document.read_draft`**,
  `:1448-1461`) returns a **presigned GET** to a specific version's source blob — the existing browser-direct
  pattern (`OverviewTab.tsx:34-46` → `window.open`).
- **Affordance data does *not* exist yet:** `GET /me` is identity-only (`api/auth.py:29-42`); the only
  effective-permissions endpoint is **admin-gated** `GET /users/{id}/effective-permissions` (`require("user.read")`,
  `api/authz.py:457-488`); `GET /documents/{id}` (`:600-609`) returns no capability flags. **This is the gap D-B fills.**
- **Latch / setup / auth:** every `/api/v1/*` QMS route is **423 `setup_incomplete`** until setup finalizes, and
  gated routes also emit **401 `unauthenticated`/`token_invalid`** and **403 `permission_denied`** (inactive). The
  SPA must handle these (S-web-1/2 precedent).
- **Lifecycle is named action sub-resources, never `PATCH status=`** (`docs/15-api-design.md:32`).

### The role/grant reality that shapes the demo (call-out, not a code change)
- **The Author role lacks `document.manage_metadata`** (only Process Owner holds it — `0004_seed_authz.py:201`),
  yet submit-review **requires** a clause mapping. So a pure "Author" cannot pass the submit gate with seeded roles.
- **`document.release`/`document.obsolete` are held by *no* seeded role** (S-web-5/later ride SYSTEM overrides).
- **The demo user is a System Administrator with *zero* `document.*` keys** (`0004_seed_authz.py:308`) → it cannot
  author and sees an empty Library. **⚠ The brief's `grant-role … "QMS Owner"` grants reads only — QMS Owner holds
  no authoring write keys.** To smoke-test authoring, the demo user needs the authoring keys (`document.create,
  checkout, edit, manage_metadata, submit, read, read_draft`) via **SYSTEM overrides** (the "ride a SYSTEM override"
  pattern; SYSTEM scope matches every resource) — the exact operator command is pinned in the plan's live-smoke task.
  For S-web-3 (author's half) **one** content-granted user suffices (no SoD second user needed until S-web-5).

## 3. Scope of S-web-3

**In:**
- **Backend (no migration, no new key):** `GET /api/v1/me/permissions` (§4.1) + a per-document `capabilities` block
  on `GET /documents/{id}` (§4.2) + the `openapi.yaml` updates.
- **Front-end (§5–§6):** the affordance layer (`usePermissions` + `can()` + the `capabilities` consumer); the
  **New-Document wizard** (create → upload first version → map clauses → submit); the **"Author actions"** section in
  the existing drawer (check-out, download working copy, upload new version, map/unmap clauses, submit-for-review,
  and start-revision on an Effective doc); the shared `CheckInPanel` (doc-11 §5.4) + `ClauseMapper`; the
  client-side **SHA-256 + presigned-PUT** infra (`lib/hash.ts`, `lib/upload.ts`); the lock banner + heartbeat;
  full test + a11y coverage.

**Out (deferred, with reasons in §9):** the reviewer task **inbox** (`GET /tasks`), the **approve / request-changes**
decision UI, the **redline two-pane**, and **release** → **S-web-5**; the **standalone Document detail page** (version
timeline page, watermark preview, metric tiles, Approvals/Acks/Audit tabs) → **S-web-4**; a document→workflow-instance
"who is reviewing" lookup (no endpoint exists) → S-web-5; the **DCR-orchestrated** revise path (the mockup's "New
revision (DCR)") — S-web-3 uses the plain check-out/start-revision path (doc-11 §5.4) — later; `next_review_due`
(drift family); a "discard draft" (T8 is deferred backend-side — see §9); custom request headers / `Idempotency-Key`
in `api.ts` (only needed by the S-web-5 decision call).

## 4. Backend — two migration-free additions (no new permission key)

Both are documented in `packages/contracts/openapi.yaml` in-PR and tested. **No migration — head stays `0044`.**

### 4.1 `GET /api/v1/me/permissions` (new) — the self-scoped affordance endpoint
Reports the **caller's own** effective grants, resolved at a scope. **Gate: authentication only**
(`Depends(get_current_user)`, the `GET /me` precedent — reporting your own grants gates nothing). Query params
`scope_level` (`SYSTEM`|`DOC_CLASS`|`PROCESS`|`FOLDER`|`ARTIFACT`, **default `SYSTEM`**) + `scope_id` (the selector
for a non-SYSTEM level). Response (reusing the existing `EffectivePermissions` schema for contract uniformity):
```jsonc
{ "scope": { "level": "SYSTEM", "selector": null },
  "permissions": [ { "key": "document.create", "effect": "ALLOW", "source": "override:system" }, … ] }
```
Implementation = the **exact body of `effective_permissions`** (`api/authz.py:457-488`) with `target = caller` and
the `user.read` gate dropped. To avoid drift, extract the loop into a shared service helper
(`services/authz/…compute_effective_permissions(session, user, org_id, scope_level, scope_id)`) and call it from
**both** the admin endpoint and `/me/permissions`. It runs `gather_grants` once per candidate key from
`granted_permission_keys` (`services/authz/repository.py:137-165`) → `authorize(...)` at the requested scope
(O(keys) queries for one user — fine). Lives in `api/auth.py` (next to `/me`). **Not** latch-exempt (it is not needed
pre-setup). No new permission key, no migration.

*Why both default-SYSTEM and a scope param:* the SYSTEM-scope answer drives coarse affordances (and the demo's
SYSTEM-override author). The optional `scope_level=DOC_CLASS&scope_id=<level>` lets the wizard ask "may I create
*this type*?" honestly at the moment a document-type is chosen (DOC_CLASS-scoped `document.create`,
`documents.py:583-585`). **Known v1 limitation (documented):** the Library "New Document" *entry* button is gated on
the coarse SYSTEM-scope `document.create` answer, so a purely DOC_CLASS-scoped Author with no SYSTEM grant would not
see the entry (under-claim). Acceptable for the v1 demo (SYSTEM override); revisited when role-scoped authoring
lands. The R34 "ship the simple correct realization first" posture.

### 4.2 `capabilities` block on `GET /api/v1/documents/{id}` (detail-only) — the per-button gate
Extend the **detail** handler (`get_document_endpoint`, `documents.py:600-609`) only — **never** the list (it would
be O(rows×keys) authz queries). Compute a `capabilities` map by running `authorize()` per authoring key against the
document's **real** `ResourceContext` (the existing `_document_scope_by_id` — artifact_id + folder_path +
document_level + lifecycle_state, `documents.py:316-329`):
```jsonc
"capabilities": {
  "checkout": true,          // document.checkout
  "edit": true,              // document.edit  (check-in + start-revision)
  "manage_metadata": false,  // document.manage_metadata  (clause mapping)
  "submit": true,            // document.submit
  "release": false,          // document.release  — SoD-enriched, see below
  "obsolete": false,         // document.obsolete (sig_hook)
  "read_draft": true         // document.read_draft (history/diff/working-copy download)
}
```
`release` is computed against the **latest Approved version** with the SoD overlay
(`enrich_release_sod_scope`, `services/vault/release_scope.py:30-78`) so the **author-can't-release** SoD is
reflected (no Approved version → `release:false`); the others use the plain document scope. `obsolete`/`release`
pass `sig_hook=True` (inert in v1 — step-up is satisfied). The map is the *authz* answer only; the UI **combines it
with lifecycle state + lock state** for the final affordance (e.g. "Check out" vs "Locked by X — Request unlock";
"Start revision" only when `state==Effective && edit`). `document.create` is **not** here (it is coarse/DOC_CLASS —
§4.1). Reuses the same `gather_grants`+`authorize` already used everywhere; **no new key, no migration**. Add an
optional `capabilities` field to the `Document` schema in `openapi.yaml` (present only on the detail response).

## 5. Front-end — the affordance layer + the New-Document wizard

### 5.1 The affordance layer (DP-6)
- **`usePermissions()`** (shell-scoped, `app/shell/`) — a TanStack Query over `GET /me/permissions` (default SYSTEM),
  cached at app load; exposes `can(key): boolean` (ALLOW present). Used for **coarse** gating only (the Library "New
  Document" entry, future admin nav). A second, parameterized call (`scope_level=DOC_CLASS&scope_id`) backs the
  wizard's per-type create check.
- **Per-document buttons** read the `capabilities` block from `useDocument(id)` (the drawer already fetches
  `GET /documents/{id}`; add `capabilities` to the `Document` type, optional). The button is rendered **only** when
  the capability is true **and** the lifecycle state permits **and** (for checkout/checkin) the lock state allows.
- **Optimistic-403 fallback** for the dynamic edges the flags can't precompute (lock held by another, a concurrent
  FSM race, SoD self-release): let the action 403/409 and reuse the S-web-2 `ApiError.status/.code` pattern
  (`lib/api.ts:7-15,44`), branching on `code` (`permission_denied` / `sod_violation` / `lock_conflict` /
  `invalid_state_transition` / `validation_error`) for the message. **Never** a silent dead button.
- **Caching/staleness:** grants resolve from the DB per request; `usePermissions` is boot-cached and goes stale only
  if an admin re-grants mid-session (re-login refreshes — acceptable v1, stated in the contract).

### 5.2 The New-Document wizard (`features/authoring/NewDocumentWizard.tsx`)
Launched from a **"New Document"** button in the Library header (shown when `can("document.create")`), as a focused
Mantine `<Stepper>` (the SetupWizard precedent, `SetupWizard.tsx:197`) — deep-linkable step via a URL param. Four
steps, each driving the API; calm, one decision-cluster per step (doc-11 §4.4 / DP-2 / DP-3):

1. **Metadata** → `POST /documents`. Fields: `title` (required), `document_type_id` (required — the cached
   `useDocumentTypes` picker; on select, re-check `document.create` at that DOC_CLASS), `classification` (default
   Internal), `area_code` (**optional, client-validated** short uppercase token — it is unconstrained server-side and
   lands in the identifier/filenames; blank → server "GEN"), `folder_path` (optional). On success the Draft exists;
   advance with its id. Handle **409 `singleton_exists`** inline.
2. **Upload first version** → `checkout` → `init-upload` (sha256 computed in-browser) → (PUT to MinIO unless
   `dedup`) → `checkin`. Reuses the shared **`CheckInPanel`** (§5.4 below). For a brand-new doc the change
   significance defaults to **MAJOR** with a required reason (e.g. "Initial version").
3. **Map clauses** → the shared **`ClauseMapper`** (`POST /documents/{id}/clause-mappings`, ≥1 required to advance —
   mirrors the server's 422 gate so the user never hits it).
4. **Review & submit** → a read-only summary (DP-9 confirm) → `POST /documents/{id}/submit-review`. On success →
   `current_state=InReview`; route to the Library with the new doc's drawer open showing the **"Awaiting review"**
   state. Surface a 422 (no clause) inline as a safety net.

**Honest caveat (documented in-UI + §9):** abandoning the wizard after step 1 leaves an empty **Draft with no
version** (legal, but it cannot be submitted). There is **no "discard draft"** in v1 (T8 is a deferred backend
transition — `domain/vault/lifecycle.py:11-12`); the wizard only creates on the explicit step-1 action and the
copy makes clear the document is created at step 1.

## 6. Front-end — "Author actions" in the existing drawer + the shared pieces

### 6.1 "Author actions" in the `DocumentDrawer` (`features/authoring/AuthorActions.tsx`)
Add a capability-gated, state-aware **"Author actions"** section to the shipped S-web-2 drawer (`?detail=<id>`), above
the read-only tabs. It orchestrates the *continue/submit/revise* paths for an existing document (the wizard owns
*new*). Rendered per `capabilities` + `current_state` + lock:
- **Draft / UnderRevision:** Check out (if free) → the **`CheckInPanel`** (download working copy + upload a new
  version) + the **`ClauseMapper`** + **Submit for review** (enabled once ≥1 clause). The lock banner shows holder +
  TTL; if held by another → "Locked by X — Request unlock" (`break-lock`, DP-9 confirm).
- **Effective:** **Start revision** (`start-revision` → UnderRevision, then the Draft path).
- **InReview / Approved / Superseded / Obsolete:** **no author action** — a calm state indicator ("Awaiting review"
  for InReview). Approve/release are S-web-5.

This is the *only* write surface added to the drawer; the Overview/History/Where-used tabs stay read-only. The drawer
keeps reusing `ArtifactHeader`/`StateBadge` (DP-5/DP-7) unchanged.

### 6.2 `CheckInPanel` (`features/authoring/CheckInPanel.tsx`) — doc-11 §5.4
The reusable check-out → edit → check-in card (used by wizard step 2 **and** the drawer):
- **Lock banner** (DP-6): "Checked out by you · expires in HH:MM (extend)" with a **heartbeat** on a timer
  (`POST …/heartbeat`, every few minutes — well under 8h) and a TTL countdown; held-by-another →
  "Locked by X — Request unlock" (`break-lock`, confirm).
- **Step 1 — Get the working file** (revisions only): "Download working copy" → presigned GET of the working
  draft's `source_version_id` (`GET …/versions/{vid}/download`, `read_draft`) → `window.open` (the `OverviewTab`
  precedent). A brand-new doc has no source version → upload fresh.
- **Step 2 — Upload + check in:** a keyboard-accessible **file drop / browse** → compute SHA-256 (`lib/hash.ts`) →
  `init-upload` → (PUT via `lib/upload.ts` unless `dedup`) → a required **Change Reason** textarea + a **MAJOR/MINOR**
  segmented control (R-significance) → **Check in as Draft Rev N**. Upload progress; render runs async post-check-in
  (quiet "Generating preview…" — never blocks; doc-11 §5.4). Surfaces the INV-3 422s + the lock-conflict 409 inline.

### 6.3 `ClauseMapper` (`features/authoring/ClauseMapper.tsx`)
A clause multi-select over `GET /clauses` (PDCA-banded, `★` marker — reuse the S-web-2 `ClauseTree` visual idiom)
that **adds/removes** mappings via `POST`/`DELETE /documents/{id}/clause-mappings`, shows the current set as chips,
enforces ≥1 before submit, and surfaces framework-mismatch (422) / duplicate (409) inline.

### 6.4 New infra (`lib/`)
- **`lib/hash.ts`** — `sha256Hex(file: Blob): Promise<string>` via `crypto.subtle.digest("SHA-256", …)` → hex
  (secure-context, satisfied on `localhost`).
- **`lib/upload.ts`** — `putToPresigned(url, file, contentType): Promise<void>` — a **raw** `fetch(url, {method:"PUT",
  body:file, headers:{ "Content-Type": contentType }})` with **no** bearer (the S3 signature is the auth; an extra
  `Authorization` breaks it). Same `content_type` as `init-upload`. **Does not** go through `useApi`.
- **Mutation hooks** (`features/authoring/`) using **`useApi().send`** (token implicit — the admin `useMutation`
  precedent, `UsersAdmin.tsx`, but on the `useApi` client): `useCreateDocument`, `useCheckout`, `useHeartbeat`,
  `useBreakLock`, `useInitUpload`, `useCheckin`, `useMapClause`/`useUnmapClause`, `useSubmitReview`,
  `useStartRevision`. Each `onSuccess` invalidates the right keys — `["document", id]`, `["document-versions", id]`,
  `["where-used", id]`, and the list `["documents"]` (the existing key prefix, `useDocuments.ts:28`). Dependency
  direction stays one-way: **`features/authoring/` → `features/document/`** (no cycle; the S-web-1/2 discipline).

## 7. Testing & accessibility (the binding gates)

- **Front-end (stack-free):** vitest + @testing-library/react + **MSW** + **jest-axe** (the S-web-1/2 idiom —
  `renderWithProviders`, co-located `<Name>.test.tsx`, `onUnhandledRequest:"error"`). New MSW handlers + fixtures
  for `GET /me/permissions`, `POST /documents` (+ 409 singleton), `checkout` (+ 409 lock_conflict),
  `versions:init-upload` (both `dedup:true`/`false`), the **presigned PUT** (`http.put("https://minio.test/*", …200)`
  — **assert the body carries the file bytes and NO `Authorization` header**), `checkin` (+ 422 empty-reason / +
  bad-significance), `clause-mappings` (+ 409 dup / 422 framework), `submit-review` (+ 422 no-clause),
  `start-revision`, and `GET /documents/{id}` **with `capabilities`**. Cover: the wizard 4 steps (each mutation gates
  the next; loading/disabled states; the ≥1-clause gate; the error `<Alert>`s); the drawer "Author actions" gating
  (buttons appear/vanish by `capabilities` + state + lock; the locked-by-other path; start-revision on Effective);
  the SHA-256/upload helpers. **jest-axe `toHaveNoViolations` on each wizard step and the drawer-with-actions is a
  release gate** (WCAG 2.2 AA — keyboard file-browse fallback, required-field announce, live-region lock/TTL,
  status-never-color-only). `crypto.subtle.digest` in jsdom: use Node webcrypto if present, else stub in `setup.ts`
  (the `matchMedia`/`ResizeObserver` precedent) — verified in T-build.
- **Backend:** `api`/`integration` tests for **`/me/permissions`** (401 unauth; an **ordinary, non-admin** user gets
  their own keys; `scope_level`/`scope_id` works; the rich shape; **not** `user.read`-gated) and the **`capabilities`
  block** (a **scoped** Author: `checkout/edit/submit/read_draft` true, **`manage_metadata` false**, `release` false;
  a Process Owner: `manage_metadata` true; the **version author**: `release` false even with a release grant — SoD;
  a distinct release-granted user: `release` true). Assertions **run-scoped / delta-based** (shared integration DB —
  the S-ing-4 lesson); a **scoped** (non-SYSTEM-override) user is mandatory (the admin-sees-all path would mask a bug).
- **CI:** all five jobs green — `contracts` (openapi.yaml: `/me/permissions` + the `capabilities` field), `api`,
  `integration`, `web` (eslint/tsc/build/test), `migrations` (no-op — head stays `0044`).

## 8. Data flow & errors

```
AuthProvider ─token─▶ useApi() ─▶ React Query hooks ─▶ wizard / drawer Author actions
GET /me/permissions               → usePermissions().can(key)  (coarse: "New Document" entry)
GET /documents/{id}.capabilities  → per-button gate (ABAC + SoD-correct)
POST /documents → checkout → init-upload → [PUT raw→MinIO] → checkin → clause-mappings → submit-review
                                  (each mutation invalidates ["document",id]/["document-versions",id]/["documents"])
```
- **Write errors** branch on RFC 9457 `code`: `singleton_exists`(409), `lock_conflict`(409), `validation_error`(422 —
  empty reason / bad significance / no clause), `invalid_state_transition`(409), `sod_violation`/`permission_denied`/
  `step_up_required`(403), `worm_required`(423), `setup_incomplete`(423), `unauthenticated`/`token_invalid`(401).
  Each surfaces as an inline, dismissable `<Alert>` (never `.message`-string matching).
- **The presigned PUT** is the one call outside `useApi` (raw cross-origin, no bearer); a MinIO/CORS failure surfaces
  as a network error on the upload step with a retry.

## 9. Out of scope — and why (honesty over mockup-completeness)

- **Approve / request-changes + the reviewer inbox + release + the redline two-pane** → **S-web-5** (D-A). SoD-1 makes
  these a *different user's* journey; `GET /tasks` self-scopes the author out; release is incoherent without approve.
- **Standalone Document detail *page*** (version-timeline page, watermarked preview, metric tiles, Approvals/Acks/
  Audit tabs) → **S-web-4** (re-sequenced). S-web-3 reuses the S-web-2 drawer + `ArtifactHeader` instead.
- **"Who is reviewing / approval chain" for the author** → no endpoint maps a document → its active workflow instance
  (`submit-review` returns only the document; `GET /workflow-instances/{id}` needs an id the author never receives —
  `services/workflow/repository.py:95-118` is unexposed). The drawer shows only the doc's own `InReview` state. A
  discovery endpoint lands with S-web-5.
- **DCR-orchestrated revise** (the mockup's "New revision (DCR)") → S-web-3 uses the plain `start-revision`/check-out
  path (doc-11 §5.4); the governed doc-05 DCR UI is later.
- **"Discard draft" / cancel-review** → T8 (discard) and T5 (rescind-approval) are deferred backend transitions
  (`domain/vault/lifecycle.py:11-12`); the only backward edge is the reviewer's `request_changes` (S-web-5).
- **`Idempotency-Key` / custom headers in `api.ts`** → only the S-web-5 decision call needs it; not added here.
- **`next_review_due` / "Days to review"** → drift family (not populated).
- **Scheduled `effective_from`** → captured at *approve* time via the tasks decision (S-web-5), not on this surface.

## 10. Decisions log

- **D-A** Scope = the author's half (create → check-out → upload/check-in → clause-map → **submit-review**); render
  the `InReview`/"Awaiting review" state. Approve/request-changes, the reviewer inbox, the redline, and release →
  **S-web-5**. Forced by SoD-1 (HARD_DENY, non-overridable) + the self-scoped `GET /tasks`.
- **D-B** Affordance gating = **`GET /me/permissions`** (auth-only, SYSTEM default + optional scope params; reuses the
  `effective_permissions` loop, `target=caller`, no `user.read` gate) **+ a per-document `capabilities` block** on
  `GET /documents/{id}` (detail-only, ABAC + version-relative SoD), with **optimistic-403** as the dynamic-edge
  fallback. **No migration, no new permission key** (R5/R38 untouched — own-data). The Library "New Document" entry
  rides the coarse SYSTEM-scope answer (documented under-claim for purely DOC_CLASS-scoped authors).
- **D-C** UX = a **New-Document wizard** (Mantine Stepper: metadata → upload → clauses → submit) + a capability-gated
  **"Author actions"** section in the **existing S-web-2 drawer** (continue/submit/start-revision). The standalone
  Document **page** is **S-web-4** (not built here).
- **Re-sequence (supersedes the S-web-2 decision log's numbering):** **S-web-3 = Document Authoring** (this) ·
  **S-web-4 = read-only Document detail page** · **S-web-5 = Review & Approve (closes UJ-3)**.
- The presigned **PUT** is a raw cross-origin `fetch` outside `useApi` (no bearer); client-side **SHA-256** is new
  `lib/` infra; **MinIO CORS** for a browser PUT is a runtime prerequisite verified in the live smoke (§ plan).
- The **demo precondition**: grant the demo System Administrator the authoring keys via **SYSTEM overrides** (the
  brief's `grant-role "QMS Owner"` is reads-only and insufficient); one content user suffices for the author's half.
- New code lives in **`features/authoring/`** (depends on `features/document/`, no cycle); shared `CheckInPanel` +
  `ClauseMapper` are used by both the wizard and the drawer.

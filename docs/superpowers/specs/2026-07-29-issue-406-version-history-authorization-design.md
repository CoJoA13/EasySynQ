# Issue #406 — version-history authorization matrix

> Owner-approved design for completing `document.read_obsolete` on version-history surfaces.
> Backend + OpenAPI/docs + integration tests. No migration, new permission key, endpoint, response
> field, or frontend behavior branch.

## 1. Problem

The live Document metadata boundary is already fixed by R57: every
`documented_information(kind=DOCUMENT)` row requires `document.read`, independent of headline
lifecycle state. The immutable version-history family does not yet honor the specialized content
keys consistently:

- every list, detail, download, text-diff, and visual-diff route currently requires
  `document.read_draft`;
- `document.read_obsolete` is documented and seeded but has no complete runtime consumer;
- an ordinary `document.read` holder cannot read even an Effective version through the version
  routes; and
- the current dependency resolves only the mutable Document headline state, not each immutable
  version's own state.

This both over-denies released content and fails to express the intended retired-content boundary.

## 2. Decision — one state-to-key matrix

Every immutable version-history read surface first requires `document.read` against the live Document's canonical
metadata scope. A version then adds the following requirement:

| Immutable `version_state` | Additional permission |
|---|---|
| `Effective` | none |
| `Draft` | `document.read_draft` |
| `InReview` | `document.read_draft` |
| `Approved` | `document.read_draft` |
| `Superseded` | `document.read_obsolete` |
| `Obsolete` | `document.read_obsolete` |

The specialized keys never substitute for `document.read`. A caller holding both specialized keys
but not the metadata key receives the detail-style `403 permission_denied` at the base boundary.

No version state is inferred from the Document headline. An Effective governing version remains
Effective for this policy while the live Document headline is `UnderRevision`.

## 3. Collection policy — authorized subset

`GET /documents/{id}/versions` returns newest-first rows from the authorized subset:

- a base reader sees Effective rows;
- adding `document.read_draft` adds Draft/InReview/Approved rows;
- adding `document.read_obsolete` adds Superseded/Obsolete rows; and
- holding all three keys exposes the complete retained history.

The route refuses the whole collection only when the base `document.read` gate denies. Missing a
specialized key filters the corresponding rows rather than turning a mixed collection into a 403.
This preserves released history for ordinary readers without leaking draft or retired metadata,
hashes, change reasons, or bytes.

## 4. Single-version policy

`GET /documents/{id}/versions/{version_id}` and its `/download` child enforce the base metadata gate
and the selected row's state-specific key. The download is authorized before the blob is loaded or
a presigned URL is generated.

Cross-document or missing version identifiers retain the existing 404 behavior after the caller
has passed the live Document boundary.

## 5. Diff policy — authorize both sides

Every text or visual comparison authorizes both immutable sides before reading, extracting,
rendering, caching, polling, or streaming comparison bytes:

- Effective + Draft needs `document.read_draft`;
- Effective + Superseded needs `document.read_obsolete`;
- Draft + Superseded needs both specialized keys; and
- a self-comparison evaluates its one version only once.

This applies uniformly to:

- `GET .../{version_id}/diff`;
- `POST .../{version_id}/visual-diff`;
- `GET .../{version_id}/visual-diff`; and
- `GET .../{version_id}/visual-diff/page/{page}`.

The visual-diff worker may still build an idempotent shared cache after an authorized request.
Every API retrieval re-authorizes both sides, so later revocation prevents access to cached bytes.

## 6. Canonical scope and request context

The state selector is centralized in one authorization module. Specialized decisions start with
the complete canonical Document `ResourceContext` and replace only version-relative fields:

- `lifecycle_state` becomes the immutable `version_state`;
- `version_id` becomes the selected version id; and
- `author_user_id` becomes that version's immutable author.

The Document ARTIFACT id, folder, document class, kind, process ids, framework, and every future
canonical field remain intact. Request-time decisions also preserve the live source IP, clock, and
actor, so ARTIFACT/PROCESS/FOLDER/DOC_CLASS scopes, lifecycle predicates, `ip_allow`,
`valid_from`/`valid_until`, and deny-always-wins behavior remain effective.

Detail/download/diff checks use the audited PEP. Collection row filtering mirrors the Library:
the base gate is audited once, while per-row specialized visibility uses the pure PDP to avoid an
authorization-audit row for every visible or hidden history row.

## 7. Compatibility

- No schema or data migration.
- No permission-catalog or seeded-role change.
- No endpoint, request, or response-schema change.
- Existing callers with `document.read` + `document.read_draft` retain draft-review workflows and
  gain Effective rows; they no longer receive retired rows unless they also hold
  `document.read_obsolete`.
- Existing Internal Auditor/QMS roles that hold `document.read_obsolete` gain the intended retired
  history when they also hold `document.read`.
- The SPA already renders whatever authorized subset the version list returns and calm-degrades
  403s, so no new frontend branch or capability field is required. Comments and operator-facing
  contract prose must stop claiming a universal `document.read_draft` gate.

## 8. Verification

Docker-backed integration coverage must prove:

- all six immutable version states across list, detail, and download;
- `document.read` remains mandatory and specialized keys cannot substitute;
- newest-first list filtering for base-only, draft, obsolete, and full readers;
- an ARTIFACT-scoped `document.read_draft` ALLOW with matching lifecycle and `ip_allow`;
- a broad obsolete ALLOW narrowed by a lifecycle-scoped ARTIFACT DENY;
- mixed-state text and visual diffs require every side's key; and
- authorized visual requests/polls reach their existing Pending/404 availability behavior.

The affected legacy diff, visual-diff, vault, metadata-policy, contract, and frontend suites must
remain green, followed by the normal API static/unit gate and repository diff checks.

## 9. Documentation

Add R59 to `docs/decisions-register.md`; align docs 07 and 15 plus the hand-maintained OpenAPI;
close the #406 residual in `docs/slice-history.md`; confirm no stale remediation-tracker entry
exists; and record the reusable version-resource authorization pattern in
`.claude/rules/engineering-patterns.md`.

## 10. Non-goals

- No change to R57 Library, Document detail, Controlled Document Register, or general-search
  metadata authorization.
- No new version state, permission key, grant, role, migration, or public response field.
- No change to version retention, WORM immutability, controlled-copy export/print, or visual-diff
  worker/cache semantics.
- No expansion of exact-string `ip_allow` into CIDR or proxy-header trust.
- No implementation of the dormant `concrete_type` selector (#345).

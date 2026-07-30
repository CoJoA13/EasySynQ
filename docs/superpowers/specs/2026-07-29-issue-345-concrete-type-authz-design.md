# Issue #345 — canonical DOC_CLASS concrete-type selector

> Owner-approved design for defining and populating the dormant `concrete_type` authorization
> selector. Backend + OpenAPI/docs + unit/integration tests. No migration, new permission key,
> endpoint, request field, response field, or frontend change.

## 1. Problem

The PDP already supports a DOC_CLASS selector containing `concrete_type`, but document
`ResourceContext` producers leave the value unset. A grant narrowed by that selector therefore
cannot match a real document. In the security-sensitive case, a broad ALLOW plus a matching
concrete-type DENY incorrectly allows because the DENY is silently dropped.

Issue #333 centralized most document scope construction and deliberately left the source decision
to #345. The remaining work is to define one canonical source and carry it through every
row-backed, projected, and pre-create document gate.

## 2. Decision — `DocumentType.code` is the canonical value

`ResourceContext.concrete_type` for `kind=DOCUMENT` is the exact, case-sensitive
`document_type.code`, such as `POL`, `SOP`, `WI`, `FRM`, `OBJ`, `MR`, `RSK`, `CTX`, or `IPR`.

The code is already the stable, organization-scoped catalog discriminator and the `{TYPE}` token
used by identifiers. The display `name` is mutable presentation text and is not an authorization
identifier. A satellite-derived value would fragment the document taxonomy, while a new
`documented_information` column would duplicate the authoritative catalog relationship.

This decision is recorded as R60.

## 3. Canonical completion

The pure `resource_from_doc` helper receives the resolved `DocumentType | None`, then derives both
DOC_CLASS attributes together:

- `document_level = document_type.document_level.value`
- `concrete_type = document_type.code`

Deriving the pair in one function prevents call sites from supplying a code and level from
different catalog rows. The asynchronous canonical builder loads the catalog row once, and all
row-backed call sites that already load or batch-load document types pass the same object.

A missing document retains the existing artifact-only degraded context. A legacy/corrupt document
without a resolvable type retains `None` for both attributes and therefore cannot accidentally
match a narrower DOC_CLASS grant.

## 4. Projected and pre-create gates

Two surfaces cannot call the row-backed helper directly:

- Search and suggestion candidates already join `document_type`; their projection adds
  `dt.code AS concrete_type` next to `dt.document_level`. An eventual OpenSearch implementation
  must denormalize both authorization attributes in the same candidate contract.
- `POST /documents` authorizes before a Document row exists; it uses the already-loaded catalog
  row to set `concrete_type` on both the base create scope and each per-process metadata scope.

These values are the same values the stored row carries immediately after creation, so matching
ALLOWs and DENYs evaluate consistently before and after persistence.

## 5. Authorization behavior and compatibility

The existing DOC_CLASS match remains conjunctive:

- `document_level` is always required;
- `kind`, when present, must match; and
- `concrete_type`, when present, must match the exact catalog code.

No current grant using only `document_level` or `kind` changes behavior. A grant that already
contains `concrete_type` becomes live as documented. Deny-always-wins therefore applies to a
matching concrete-type DENY on every document gate.

There is no migration, data backfill, permission-catalog change, role change, endpoint change, or
response-schema change. The source is resolved from the existing `document_type_id` foreign key.

## 6. Verification

Mutation-distinguishing tests must prove:

- the canonical builder emits the exact `DocumentType.code`;
- a SYSTEM ALLOW plus matching DOC_CLASS concrete-type DENY returns DENY;
- replacing only `ResourceContext.concrete_type` with `None` flips that decision to ALLOW, proving
  the test exercises the newly completed field;
- the async database-backed builder resolves the seeded catalog code;
- a real Document detail gate enforces the matching DENY while a different concrete type remains
  readable;
- the pre-create gate rejects creation under a matching concrete-type DENY; and
- search and suggestion row filters hide the matching type without hiding a different type.

Focused coverage is followed by API static/unit checks, affected integration tests, contract lint,
and a complete diff inspection.

## 7. Documentation

Add R60 to `docs/decisions-register.md`; replace the stale `type` selector wording in doc 07 with
`concrete_type`; clarify in doc 14 that it is resolved rather than separately stored; document the
canonical selector example in OpenAPI; close #345 in slice history; and record the paired
catalog-resolution pattern in the engineering rules and repository orientation.

## 8. Non-goals

- No free-form document-type authorization names or case folding.
- No new Document Type mutation surface.
- No `requirement_source`, `pdca_phase`, or `include_subprocesses` implementation.
- No change to non-document Record scopes or record satellite types.
- No expansion of effective-permission query parameters beyond the issue's document-gate scope.

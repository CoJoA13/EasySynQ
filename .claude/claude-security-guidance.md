# EasySynQ security rules

<!--
  This file is read by the `security-guidance@claude-plugins-official` plugin and
  injected into security review prompts. Cap (across user/project/local files combined):
  8 KB, tail-truncated. Generic OWASP coverage is built into the plugin — the rules
  below should be CODEBASE-SPECIFIC things the model can't infer (custom helpers,
  read-replica/primary splits, internal allowlist wrappers, etc.).
-->

## Database access & roles

- app/worker/beat connect as easysynq_app (non-owner); never as owner easysynq
- Alembic + backup/restore run as OWNER via DATABASE_URL_SYNC only
- audit_event/signature_event are INSERT/SELECT-only for the app role; never UPDATE/DELETE
- Use ORM / text() bound params; never f-string or .format() user input into SQL
- Chain-linker writes go through the easysynq_linker DSN, not the app role

## Authorization & SoD

- Request handlers gate via the PEP require('domain.action'); never inline ad-hoc checks
- Workers/compilers authorize via gather_grants+authorize DIRECT, never pep.evaluate/require
- Deny-by-default, deny-always-wins (RBAC+ABAC); never grant on absent ABAC context
- ADMIN sits outside the QMS: System Administrator holds no document.* keys
- A read the caller lacks must 403 / filter calmly, never widen scope to fill it

## Audit & WORM integrity

- Never UPDATE/DELETE capa_stage or dcr_stage_event (the DB REVOKEs both)
- Preserve the audit_event hash chain; never backfill, reorder, or recompute rows
- Keep blob-row-iff-bytes: deleting object bytes must drop the blob + evidence_blob links
- Never add a RESTRICT FK to a blob a disposed record can reach; reach it via evidence_blob
- MinIO object-lock WORM is load-bearing; never disable lock or shorten retention

## Tokens & public routes

- Public no-auth routes (/verify, share-links) must be added to _LATCH_EXEMPT_EXACT exactly
- Never log the raw verify/share token; log a digest only
- Domain-separate every Ed25519 use (distinct preamble + token length); reuse no preamble
- Fail closed at mint if the signing key isn't durably persisted
- Set Referrer-Policy: no-referrer; stream revocable content, never hand out a presigned URL

## Output rendering & SSRF

- Render ts_headline/snippets as Mantine <Mark> text nodes; never dangerouslySetInnerHTML
- Treat any server content_block as text; never inject typed/raw HTML
- Rendering is worker-only (the API holds a no-op sink); never render/rasterize in-request
- Presign against the browser-facing host; never rewrite the host after signing (SigV4)
- If a user-supplied URL ever feeds httpx, wrap it in an SSRF allowlist first

## Open questions

Items the analysis flagged but couldn't confirm. Convert into real rules above, or delete.

- Verify these rules don't duplicate `CLAUDE.md` (## Critical rules — NEVER violate) before relying on them.
- SSRF: all outbound HTTP today (Keycloak JWKS, Tika `TIKA_URL`, Gotenberg `GOTENBERG_URL`, readiness) targets fixed env-config hosts — no user-influenced URL reaches an outbound call. Keep the allowlist-wrapper rule advisory unless a user-supplied URL is ever passed to httpx.
- Deserialization: only `yaml.safe_load` found (no `pickle.load` / `yaml.load()` / `torch.load`); the plugin's built-in layer-1 covers generic cases, so no project rule was emitted — re-check if an unsafe loader is added.
- Scope: confirm against the installed plugin README whether the layer-3 agentic commit reviewer reads this file; the 2.0.6 README suggests these rules bind the Stop-hook LLM diff review.

<!-- HAND_EDIT_BELOW_THIS_LINE: anything after this comment is preserved across regenerations. Sections above are owned by docu-optimizer scaffold security (generated on 2026-06-15). -->

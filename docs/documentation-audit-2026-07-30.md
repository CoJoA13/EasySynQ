# Documentation Accuracy Audit — 2026-07-30

## Outcome

The task-oriented documentation now distinguishes current implementation from intended
architecture. At the time of this audit, GitHub reported **zero open issues** in
`CoJoA13/EasySynQ`; that does not erase the deliberate residuals recorded in
[`slice-history.md`](slice-history.md).

The audit produced:

- an [Installation Guide](manuals/installation-guide.md);
- a [User Manual](manuals/user-manual.md);
- an [Administrator & IT Manual](manuals/administrator-it-manual.md);
- corrections to current-facing setup, deployment, maintenance, and developer instructions; and
- visible implementation-status notes in the architecture and onboarding design documents.

## Evidence checked

The audit cross-checked documentation against:

- web routing and navigation in `apps/web/src/App.tsx`, `LeftRail.tsx`, and `TopBar.tsx`;
- the six-screen setup flow in `apps/web/src/SetupWizard.tsx`;
- current Users, Roles, Processes, and Config administration components;
- JIT identity resolution and the direct host break-glass role path;
- the S/M Compose overlays, production edge, `.env.example`, and `scripts/install.sh`;
- `/healthz` and `/readyz` implementation in `apps/api/src/easysynq_api/readiness.py`;
- host commands dispatched by `scripts/easysynq`;
- OpenAPI generation in `scripts/gen-contracts.sh`;
- shipped-slice and residual records in [`slice-history.md`](slice-history.md); and
- the authoritative [`decisions-register.md`](decisions-register.md), whose then-current coverage ended at decision R60.

## Corrections made

| Area | Inaccurate or ambiguous statement | Correction |
|---|---|---|
| Deployment profiles | S/M/L were described as shipped. | Only S and M ship; L is reserved and no `compose.l.yml` exists. |
| Search | M was described as a full OpenSearch stack and `/readyz` as probing OpenSearch. | Both S and M use PostgreSQL FTS. OpenSearch is reserved; readiness probes PostgreSQL, Redis, MinIO, Keycloak, and Alembic. |
| Observability | A bundled observability overlay was described as available. | Current operations use health endpoints, Compose health, logs, and configured job alarms; no Prometheus/Grafana/Loki overlay ships. |
| Setup | The canonical ten-step design was presented as the current browser wizard. | The UI has six blocking screens; deferrable administration/import work occurs after finalize. |
| Identity | The design implied in-app Keycloak account creation and email/CSV onboarding. | Create the identity in Keycloak first, then bind its OIDC `sub` in Administration → Users. |
| Roles | Seeded bundles were described as editable in the current UI. | The Roles tab is read-only; assignments and system-scoped overrides are managed from Users. |
| Developer start | The documented demo login was available immediately after `just up s`. | `just demo-user` is required, and fresh setup must still be completed. |
| Developer environment | Copying `.env.example` retained production OIDC placeholders, and the runbook used the demo identity before creating it. | The quick start now names the three localhost OIDC values and creates the demo identity before activation. |
| Contract workflow | The contract was described as lint-only, and a fresh generator run lacked output directories and emitted enum defaults that failed `mypy`. | The generator now creates both output trees, emits typed enum-member defaults, lints/bundles OpenAPI, generates Pydantic models plus TypeScript schema types, and maintains the checked hash lock. |
| Restore | Restore instructions required an undefined reindex. | Shipped PostgreSQL FTS needs no separate reindex; rebuild the filesystem mirror. |
| Audit witness | The runbook used unsupported `easysynq audit verify-offhost`. | It now invokes the implemented audit CLI in a one-off worker container. |
| Runbook index | Hyper-V appliance installation was omitted. | The appliance runbook is now indexed. |
| Decision range | The overview stopped at R46. | It now points to the register's then-current final decision. |
| Lifecycle terminology | Mirror guidance mixed “Released” with the canonical `Effective` state. | Current-facing mirror guidance now consistently says Effective versions only. |
| Break-glass audit | The direct `grant-role` CLI was described as audited. | The admin manual now states that it bypasses the API audit path and requires an independent change/incident record. |
| Break-glass scope | Developer guidance required `--org`, but the host wrapper discarded that option. | The wrapper now forwards `--org` and optional `--bound-scope` arguments to the underlying CLI, and the manual requires the exact org short code. |
| Host command path | Runbooks used a bare `easysynq` command even though online installs keep the helper in the repository. | Current runbooks now use `./scripts/easysynq` from the repository root. |
| Browser coverage | Current-status text described every shipped family as web-UI complete/end-to-end. | Records/Retention and Evidence Packs are now identified as shipped API/worker capabilities without dedicated SPA management routes. |
| UX design | Target wireframes and OpenSearch behavior could be read as current route coverage. | The design-system front door now points to the route-based User Manual and names current search/browser gaps. |
| Effectivity | The revision design claimed a read could perform a lazy scheduled cutover and that a superseded predecessor became Obsolete. | Current behavior is a five-minute Beat/explicit-trigger cutover; the predecessor becomes Superseded, and reads do not mutate lifecycle state. |
| Version immutability | Design prose said version rows never update, even though lifecycle state/effectivity fields transition. | Immutability is now scoped accurately to content and frozen metadata; controlled lifecycle fields may change only through lifecycle services. |
| Effective-version invariant | Some prose described the partial unique index as guaranteeing one Effective row at every instant. | The database guarantees **at most one**; an active released document has one, while a pre-release or Obsolete document can have none. |
| Import dedup/search | Import design prose described OpenSearch as the active near-duplicate and final-index service. | Shipped import uses in-process MinHash; extracted text is transient for classification/dedup, and current global search remains metadata-only. |
| API catalog | Target search, audit, and admin routes were easy to read as mounted endpoints. | Shipped rows now show exact current query parameters/storage and unmounted saved-search, generic-job/health/export, and audit-export routes are labeled future/deferred. |

## Deliberate residuals are not closed issues

The absence of open GitHub issues is a queue state, not a claim that all future hardening is
complete. The durable residual ledger remains [`slice-history.md`](slice-history.md). Important
current examples include:

- revision-chain reconstruction during import is refused rather than fabricated;
- T5 approval rescheduling/rescission and T8 revision-draft discard are not mounted;
- some PartiallyCommitted import retry and progress-heartbeat edge cases remain;
- only the newest off-host audit checkpoint is independently verified;
- audit-checkpoint signing-key history across rotation is not modeled;
- CAPA multi-approver reject/changes-requested behavior has a known wedge/coverage gap; and
- `easysynq upgrade` has no migration `lock_timeout`, so the maintenance-window writer-stop procedure
  remains required for audit-table index migrations.

The manuals link to the ledger instead of copying a complete residual list that would drift.

## Maintenance rule

When implementation changes:

1. update the relevant manual and runbook in the same pull request;
2. keep target architecture labeled as target when no deployable artifact exists;
3. derive route names, buttons, commands, service counts, and health dependencies from code;
4. never convert a deliberate residual into “complete” merely because no GitHub issue is open; and
5. re-run the local link and deployment-document checks described in the repository workflow.

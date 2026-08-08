# EasySynQ Codex Takeover Design

> **Status:** Programme 0 approved by the owner on 2026-08-08; its PostgreSQL MCP security-disable
> amendment was approved the same day; Programmes 1–6 remain proposed and no production behavior is
> authorized by this document
> **Date:** 2026-08-08
> **Baseline:** `main` at `c15541f` (squash merge of PR #447)
> **Visual command center:** [EasySynQ Takeover Command Center](https://www.figma.com/design/sdtIklWupCvzQvJOxTbf9z)
> **Authority:** existing security, audit-remediation, recovery, and release designs keep their current
> authority. This document coordinates those programmes and defines repository ownership; it does not
> silently supersede a previously approved slice contract.

---

## 1. Decision

Codex will take over EasySynQ through a sequence of small, reviewable programmes rather than a rewrite.
The existing architecture, visual language, audit history, and quality-management vocabulary remain the
product foundation. The takeover first makes the repository easy to operate from a fresh Fedora
workstation, then improves failure handling and testability, and only then expands release, recovery,
and demonstration capabilities.

The first implementation slice is **`S-codex-fedora-foundation`**. It is deliberately limited to agent
ownership, developer setup, and truthful diagnostics. It changes no application behavior, database
schema, deployment topology, or security guarantee.

## 2. Why this document exists

The repository is substantial and already carries many approved designs, plans, and audit records. A
full takeover without a coordinating contract would create three risks:

1. cleanup work could erase useful historical or Claude-specific automation;
2. broad UI, recovery, and release edits could overlap the active audit-remediation dependency chain;
3. “fresh install” documentation could claim readiness that has not been exercised on Fedora.

This document establishes one programme map, explicit boundaries, evidence gates, and a definition of
done. Each risk-bearing slice still receives its own owner-reviewed design and task-level TDD plan before
production code changes.

## 3. Current baseline

### 3.1 Repository and workstation

- `main` and `origin/main` both point to `c15541f`; the worktree was clean before this proposal was
  created.
- PR #447 is present locally as the current head.
- CI requires Node 22 and the API requires uv-managed Python `>=3.12,<3.13`, but the repository lacks a
  tracked Node version pin and a Fedora path that reliably selects both supported runtimes while
  preserving the system Python.
- The developer setup must handle every Docker access state explicitly: stopped daemon, missing socket,
  permission failure, and group membership configured but not active.
- The Fedora path must also verify `just`, pre-commit, and PostgreSQL 16 client tools rather than assuming
  they are present.
- The repository has an Ubuntu **production-host** bootstrap and partial Fedora Atomic developer notes,
  but no standard Fedora Workstation developer bootstrap.

### 3.2 Verified evidence

- `bash scripts/tests/test-ci-hardening.sh`: 65 passed, 0 failed.
- `bash scripts/check-no-site-data.sh`: clean.
- Active shell scripts pass syntax checks.
- This design adds no new full API/web-suite evidence. Full-stack readiness remains an explicit unverified
  boundary until the Fedora proof installs dependencies and establishes Docker access.

### 3.3 Product-design evidence

The repository mockup covers dashboard, document library, document detail, review and approval, CAPA,
audit, first-run setup, and ingestion. The takeover audit additionally captured a mobile dashboard view.
Together they demonstrate a strong QMS-specific visual system. They do not prove live application
accessibility, responsive behavior, or runtime error handling.

The design tokens in `apps/web/src/theme/tokens.css` remain the visual source of truth. New UI work must
reuse its surface, text, border, accent, feedback, spacing, radius, elevation, and typography semantics.

## 4. Operating principles

1. **Preserve product identity.** Improve the current design system; do not replace it with a generic
   dashboard aesthetic.
2. **Fail closed and explain the failure.** Authentication, setup-state, recovery, and release errors must
   never route users into a plausible but false state.
3. **One trust boundary per slice.** Backup, release, identity, and container-hardening work stay aligned
   with the existing audit-remediation sequence.
4. **Test before implementation.** Every behavior change starts with a failing focused proof and ends with
   fresh affected-suite evidence.
5. **No cleanup theatre.** Large files are split only when a behavior slice benefits; historical evidence
   is indexed or archived before deletion.
6. **Offline truthfulness.** Air-gapped installation and disaster recovery are complete only when they
   succeed with upstream and source-system access denied.
7. **No-phone-home by default.** New hosted plugins, telemetry, and SaaS dependencies require an explicit
   owner decision and deployment-policy update.

## 5. Programme map

| Order | Programme | Outcome | Dependency / authority |
|---:|---|---|---|
| 0 | **Repository ownership and Fedora foundation** | A fresh Fedora contributor and Codex can discover, bootstrap, diagnose, and verify the repository without relying on Claude-only context. | First slice; no application behavior. |
| 1 | **Frontend resilience and accessibility** | Startup failures, setup-state failures, missing routes, mutation failures, keyboard interactions, and narrow layouts are explicit and testable. | Begins after programme 0. Individual UX behaviors require their own approved designs. |
| 2 | **Demo scenario and browser journeys** | A guarded, idempotent QMS story can seed, reset, and drive deterministic end-to-end tests and demos. | Depends on stable bootstrap and frontend error contracts. |
| 3A | **Release-prerequisite runtime hardening** | Secret validation, image users, static serving, volume/SELinux ownership, and health semantics are stable before artifacts are signed. | Continues existing container/security slices; blocks programme 3B. |
| 3B | **Release and air-gap integrity** | Release artifacts contain the exact app and dependency images, are digest-bound, and install without a network. | Depends on 3A and existing release/security authority; separate design required. |
| 4 | **Self-contained disaster recovery** | A backup generation carries every required database, realm, config, checkpoint, object-data, and exact offline-release leg and can restore into an isolated target with source access denied. | Continues the approved audit-remediation/recovery sequence and consumes a verified 3B artifact digest. |
| 5 | **Repository information architecture** | Current decisions, open residuals, historical plans, and agent guidance are discoverable without deleting evidence. | Starts narrowly in programme 0; bulk archive moves follow link/reference analysis. |
| 6 | **Later operational hardening** | Additional least-privilege and local-first observability improvements land without invalidating the signed runtime contract. | Follows the prerequisite matrix in 3A; separate trust-boundary designs. |

Programmes 3A, 3B, 4, and 6 may be designed in parallel with programmes 1 and 2, but production edits that
share Compose, storage, migrations, authentication, or CI surfaces remain serialized.

## 6. Programme 0 — `S-codex-fedora-foundation`

### 6.1 Objective

Make repository ownership vendor-neutral and make the documented Fedora Workstation setup match the
actual checks a new contributor must pass.

### 6.2 Design

#### Root agent contract

Add a tracked root `AGENTS.md` as the authoritative, tool-neutral **contributor-workflow** and coding-agent
guide. Product authority remains in `docs/decisions-register.md` and approved slice designs; current
execution state moves to `docs/current-status.md` and open residuals move to `docs/open-residuals.md`
rather than becoming `AGENTS.md` content. The guide will cover repository layout, authoritative
design/plan locations, commands, test expectations, security boundaries, generated files, migration
rules, documentation truth, and
change-handoff conventions.

It must be concise enough to stay current. Detailed workflow instructions remain in existing runbooks and
are linked rather than duplicated.

#### Claude compatibility

Retain `.claude/` because its hooks and commands are active repository assets. Slimming `CLAUDE.md` is an
atomic migration, not a text deletion:

- move `Current status` and the machine-consumed test baseline to `docs/current-status.md`;
- update `.claude/commands/finish-slice.md`, `.claude/hooks/test-baseline.sh`, and
  `.claude/hooks/register-range-guard.sh` in the same slice;
- update current inbound authority references in `docs/00-overview.md`, `docs/16-roadmap.md`,
  `docs/18-mvp-implementation-plan.md`, and `docs/runbooks/fresh-linux-setup.md`; generate and review a
  complete live-reference manifest so this initial list cannot be treated as exhaustive; and
- prove hook behavior through executable contract tests rather than checking only that paths exist.

Every mutable fact—migration head, test baseline, CI matrix, decision-register range, permission count,
and current slice—has exactly one canonical home. All live consumers are updated atomically, including
root/docs references and `.claude` agents, commands, and hooks discovered by the manifest. The
`docs/slice-history.md` header becomes historical/link-only rather than a second mutable head. A grep-based
gate rejects any remaining live claim that `CLAUDE.md` owns current execution authority.

After those consumers move, retain a short Claude-specific compatibility note that points to `AGENTS.md`
and documents only genuinely Claude-specific behavior. No `.claude` command or hook is removed until an
equivalent vendor-neutral path exists and has been verified.

#### Agent database safety

The original `.mcp.json` advertised a read-only PostgreSQL connection while using the migration owner and
a floating `npx` fetch. Programme 0 evaluated the approved exact package
`@modelcontextprotocol/server-postgres@0.6.2`; the required npm audit found high-severity
`GHSA-w48q-cv73-mx4w` through `@modelcontextprotocol/sdk` with no compatible fix. The owner therefore
approved the fail-closed branch on 2026-08-08: `.mcp.json` exposes no PostgreSQL server, the vulnerable
package/lock/launcher are not committed, and no orphan database role is provisioned. A maintained,
locked, audit-clean implementation and its dedicated least-privilege dev role are deferred under
`RES-POSTGRES-MCP-REPLACEMENT`. No agent database integration may use production or owner credentials.

#### Fedora bootstrap

Add a Fedora Workstation **developer** bootstrap path beside `docs/runbooks/fresh-linux-setup.md`; leave the
Ubuntu production-host bootstrap contract unchanged. The developer bootstrap will:

- detect Fedora and reject unsupported distributions with a clear message;
- support a non-mutating check/dry-run mode;
- enumerate required packages and commands before privilege elevation;
- install or verify Git, curl, OpenSSL, a tracked Node 22 pin, uv-managed Python 3.12, Docker
  Engine/Compose, `just`, pre-commit, PostgreSQL 16 client tools, and other repository prerequisites using
  Fedora-native package boundaries while preserving the system Python;
- treat Docker Engine as the supported runtime; Podman compatibility is unsupported unless a later full
  integration proof explicitly qualifies it;
- detect SELinux enforcing mode and validate bind-mount labelling without disabling SELinux globally;
- document firewalld implications without mutating firewall policy automatically;
- explain Docker group/session and daemon-start requirements without hiding privilege changes;
- avoid installing project dependencies from floating one-line internet scripts when a distribution or
  repository-pinned route exists; and
- finish by running a bounded repository doctor command rather than claiming the full stack works.

The bootstrap script must not enable services, alter firewall policy, or add the user to privileged groups
without an explicit interactive step and visible explanation.

#### Disposable Fedora acceptance media

The Fedora VM proof uses two independently checksummed Fedora 44 media artifacts. A Fedora 44
Everything netinstall ISO boots the real Anaconda/Kickstart environment. A Fedora 44 Workstation Live
ISO is attached read-only and its `LiveOS/squashfs.img` is the Kickstart `liveimg` payload. This is the
supported unattended path for proving the installed Workstation payload; a Workstation Live ISO is not
treated as a `virt-install --location` installation tree, and no cloud image or Server payload may be
substituted. The installed guest must still prove `VARIANT_ID=workstation`, `VERSION_ID=44`, `x86_64`,
and SELinux `Enforcing` before any repository acceptance command runs.

#### Repository doctor

Add one dependency-light read-only entry point at `./scripts/doctor.sh`, with `just doctor` as a convenience
alias rather than the only entry point. It will report:

- operating system, architecture, SELinux mode, and supported-platform verdict;
- required tool presence and supported-version verdicts;
- Docker CLI, Compose plugin, daemon/socket reachability, socket permissions, and group/session guidance;
- whether API, web, and contract dependencies are installed;
- required local configuration files and placeholder-secret detection without printing secrets;
- occupied project ports; and
- the exact next command for each failed prerequisite.

The doctor must distinguish **missing**, **installed but unavailable**, **unsupported version**, and
**unverified**. It exposes `contributor`, `test`, and `stack` profiles, because a missing daemon blocks a
stack proof but not documentation work. Stable machine-readable reason identifiers accompany human
guidance, and the selected profile alone controls the exit verdict. Docker installed with unavailable
daemon access is not reported as missing or healthy.

### 6.3 Acceptance criteria

1. `AGENTS.md` is the single authoritative cross-agent contributor guide while the decision register,
   approved designs, neutral current-status document, and open-residual ledger retain their named roles.
2. A complete reviewed live-reference manifest migrates every `CLAUDE.md` current-authority consumer
   atomically; each mutable fact has one canonical home; no-live-current-authority and duplicate-fact
   gates pass; and hook/command behavior passes executable contract tests before the file is slimmed.
3. `.mcp.json` exposes no PostgreSQL connector; no floating launcher or vulnerable PostgreSQL MCP package
   is tracked. Re-enablement requires the closure proof in `RES-POSTGRES-MCP-REPLACEMENT`.
4. Fedora detection and dry-run behavior are fixture-tested without changing the host.
5. A disposable clean Fedora Workstation VM/CI proof boots Anaconda from an independently checksummed
   Fedora 44 Everything netinstall ISO, installs the independently checksummed Workstation Live
   `LiveOS/squashfs.img` payload, verifies the supported toolchain, survives a second idempotent run,
   obtains Docker/testcontainers access after the documented session transition, and runs setup, fast
   API/web/contract checks, and Compose configuration.
6. The Fedora proof runs with SELinux enforcing; bind mounts work through explicit compatible labels or
   pre-labelled directories rather than global disablement.
7. Doctor output is deterministic under command stubs, uses stable reason identifiers, and fails only for
   prerequisites blocking the selected `contributor`, `test`, or `stack` profile.
8. Doctor output never contains secret values and fixture proofs cover every named Docker access category.
9. Ubuntu links and source-level regression tests remain intact; live Ubuntu validity is unchanged and is
   not re-proven by this Fedora slice.
10. Shell syntax, focused bootstrap/doctor/hook tests, current-document link and traceability checks, and
    `git diff --check` pass.

### 6.4 Non-goals

- Starting Docker or changing group membership automatically.
- Installing application dependencies on an operator workstation during fixture/unit tests; the isolated
  Fedora VM proof may install them as part of its explicit acceptance contract.
- Reworking production Compose, containers, production configuration, or application behavior. A bounded
  Fedora VM/CI smoke job is in scope.
- Deleting `.claude`, historical plans, or audit evidence.
- Declaring full-stack readiness before API, web, contract, and browser gates run successfully.

## 7. Programme 1 — frontend resilience and accessibility

This programme is split into independently testable UX slices in this order:

1. **Startup and authentication boundary.** `AuthProvider` always reaches a visible success or failure
   state. OIDC manager and user-load failures produce an actionable sign-in error with retry, never an
   infinite unlabeled spinner.
2. **Setup-state boundary.** Failure to fetch setup state renders a fail-closed operational error. It must
   never infer `UNINITIALIZED` and route an existing deployment into the setup wizard.
3. **Application error and route boundary.** Add a root error boundary, a useful not-found page, and route
   recovery actions. Wildcard routes do not silently redirect to the dashboard.
4. **Mutation feedback.** Notification and other user-initiated mutations surface failure in context and
   preserve retryable intent.
5. **URL-state correctness.** Chrome/navigation state reacts when query parameters select a real product
   view, such as `/tasks?type=DOC_ACK` or a detail/drawer identity. Filter, sort, and pagination parameters
   remain shareable and back/forward-correct without stealing focus on every edit.
6. **Keyboard and semantic interaction.** Clickable table rows become native links/buttons or expose
   complete keyboard semantics, focus visibility, and accessible names.
7. **Responsive data views.** Every data-heavy route has an intentional narrow-screen strategy: semantic
   cards, column prioritisation, or a labelled horizontal scroll container.
8. **Browser failure proof.** Add the Playwright harness and request-intercepted authentication bootstrap,
   setup-state failure, 404/error recovery, keyboard, and responsive smoke proofs. Seeded real-stack
   document review/approval and CAPA journeys belong to Programme 2.

Each slice reuses existing tokens and components, adds focused tests first, and includes screenshot-based
visual comparison where the captured mockup provides a matching state.

### 7.1 Accessibility, responsive, and visual acceptance

- WCAG 2.2 AA is the target. Core journeys are keyboard-completable with visible focus, modal/drawer focus
  restoration, native interactive semantics, meaningful landmarks/headings, and named loading/error live
  regions.
- View-selecting navigation moves focus/announcement intentionally; ordinary filter and sort edits update
  results without resetting the user.
- Browser axe scans cover loaded, empty, forbidden, error, modal, and drawer states. Manual checks cover
  forced colors, reduced motion, contrast, 200% zoom/reflow, and screen-reader spot checks.
- At 320 CSS px there is no document-level horizontal scrolling. A labelled data-region scroll container
  is acceptable only when row identity and primary actions remain available.
- Dashboard, library, document detail, review, CAPA, setup, error/empty, and mobile states have an approved
  light/dark baseline matrix. Existing primitives and tokens are reused before new variants; no palette,
  shadow, or spacing literal is added without token review. Intentional visual differences require owner
  review; accessibility wins over pixel matching.

### 7.2 Browser operating contract

The dedicated slice chooses the exact paths but must provide repository-owned npm and `just` entry points,
a required CI gate, Chromium coverage, and an explicit decision on Firefox/WebKit. The minimum viewport
matrix is 320×800, 768×1024, and 1440×900. Tests fix locale, timezone, clock, animation policy, and colour
scheme; isolate state per test; use bounded workers; retain trace/screenshot/video artifacts on failure;
and never depend on an external SaaS identity provider.

### 7.3 Programme acceptance criteria

1. Auth config, manager creation, user-load, and callback failures reach a named retryable error and never
   leave an indefinite loader.
2. Setup-state failure never renders `SetupWizard` or issues a setup mutation; retry can recover.
3. Uncaught route/render failures preserve a recoverable application shell.
4. Unknown URLs remain visible and render a useful 404 with safe recovery actions.
5. Failed mutations announce the error and preserve user intent. Retry is enabled only when failure is
   proven pre-commit or the named server mutation accepts an idempotency key that makes ambiguous retries
   safe for regulated writes.
6. Query-selected views, filter behavior, keyboard semantics, focus handling, WCAG checks, and the viewport
   matrix satisfy §§7.1–7.2.
7. Request-intercepted browser failure proofs pass without Programme 2 seed data.

## 8. Programme 2 — guarded demo scenario and browser journeys

Add an idempotent, cross-store demo seeder with an equally explicit reset path. The scenario contains:

- one organisation and process map;
- three personas with distinct review, approval, and CAPA responsibilities;
- a controlled document with multiple revisions and one pending approval;
- an audit finding connected to CAPA and a document-change request;
- an objective with trend data; and
- deterministic local Keycloak users, role/scope assignment, and separation of duties for full-stack CI;
  request-intercepted failure tests may use a test-build-only login fixture that is absent from production
  artifacts and cannot be enabled by runtime configuration;
- workflow tasks, source blobs and renditions in object storage; and
- notifications with a fixed clock/timezone anchor that exercise read/unread and failure feedback.

Expose the workflow as `just demo-seed` and `just demo-reset`. Safety is based on an immutable demo
organisation namespace and seed-ownership marker, an allowlist of database/realm/bucket identifiers,
absence of release/production markers, bounded target enumeration, transaction/advisory locking, and
explicit confirmation or a CI-only flag. It never infers safety from a hostname or profile alone and
refuses to continue if unrelated tenant data would be touched. Reset preserves migrations, ISO/reference
seeds, non-demo identities, and non-demo object bytes. Re-running the seed converges to the same logical
dataset rather than duplicating entities. Browser tests use stable external identifiers, not database row
numbers.

Because PostgreSQL, Keycloak, and object storage cannot share one transaction, seed/reset uses a durable
operation ledger with explicit phases, ownership markers, and resumable compensation. Tests inject failure
after each store leg and prove the next invocation converges without orphaned identities, rows, or blobs.

The existing ISO-clause seed and identity/persona helpers remain inputs; rich MSW fixtures should be
reused or promoted where their data is already realistic instead of inventing a second vocabulary.

The canonical story is: an author submits a controlled-document revision; an independent reviewer and
approver complete their tasks; an acknowledgement is issued; an auditor records a finding and raises a
CAPA; an approved DCR links the corrective revision. The detailed seed design fixes the expected state at
every transition.

### 8.1 Programme acceptance criteria

1. The design declares exact post-seed entity counts, relationships, stable external IDs, role/SoD rules,
   object keys, and workflow states across PostgreSQL, Keycloak/login fixtures, and object storage.
2. A second seed produces no duplicates or divergent state.
3. Reset dry-run enumerates only seed-owned targets; reset refuses every unmarked or mixed-tenant target.
4. Reset removes demo-owned data only and preserves every named non-demo category above.
5. Failure injected after every cross-store phase resumes or compensates to the declared converged state.
6. The complete persona story passes twice from a clean reset with fixed locale/timezone/clock settings.
7. Seeded full-stack review, approval, acknowledgement, CAPA, and DCR browser journeys are the Programme 2
   CI gate; Programme 1 continues to own intercepted failure-path coverage.

## 9. Programme 3 — release-prerequisite hardening and air-gap integrity

### 9.1 Programme 3A — prerequisite runtime contract

Before signing a release, freeze the per-service contract for secret validation, runtime UID/GID, root
exceptions, writable paths/volumes/tmpfs, capabilities, no-new-privileges, seccomp, read-only rootfs,
resource/log limits, secret ownership, volume-upgrade compatibility, Fedora SELinux labelling, static web
serving, liveness, readiness, and degraded-state semantics. Include worker heartbeat, Beat freshness,
renderer/Tika degradation, disk pressure, backup age, audit witness, and certificate expiry without
forcing every degraded dependency into `/readyz`.

Existing named-volume ownership must upgrade safely. A release cannot be signed until this matrix has
container-level proofs for every service and any root exception is documented and minimal.

### 9.2 Programme 3B — release and air-gap artifact

The release design must close the gap between a source checkout and a self-sufficient artifact:

- build the shared EasySynQ application image used by migrate/API/worker/Beat, the web image, and the
  repository's optimized Keycloak image in CI;
- identify every app and infrastructure image by immutable digest;
- include the built application images in the air-gap bundle;
- generate a release Compose manifest with immutable digests, no target-side `build:` operations, and
  `pull_policy: never` or an equivalent offline guarantee;
- bind the artifact manifest, Compose, configuration templates, migration head, application version,
  source commit, platform/architecture, SBOM, provenance, and every image digest together and
  cryptographically sign that manifest/bundle;
- block signing on every unaccepted high/critical dependency or image finding; any exception is
  owner-approved, reasoned, time-bounded, and included in the signed manifest;
- import and verify images without registry access;
- run installation from the bundle with upstream network denied; and
- boot a smoke-tested stack from the installed images rather than rebuilding on the target; and
- prove both a fresh installation and upgrade from the exact predecessor artifact/digest and migration
  head named by the release design with registry/network access denied. If this is the first formally
  supported release, the owner must approve an explicit one-time fresh-install-only exception.

The current tag-pinned-but-not-digest-pinned `images.lock`, `EASYSYNQ_RELEASE=1` convention, and
`vite preview` serving path remain findings to resolve in the dedicated designs. This programme must not
bypass the existing dependency-security and CI gates.

### 9.3 Programme acceptance criteria

1. The 3A service matrix and container proofs pass before 3B artifact signing.
2. A signed manifest resolves every **release-controlled** runtime image, Compose file, template, script,
   and metadata input by immutable identity. Operator-generated hostnames, secrets, destinations, and
   local-CA choices remain outside the publisher signature and instead pass schema validation, file-mode,
   placeholder-rejection, and local audit/hash checks.
3. The offline target performs no build, pull, registry, package-manager, or external IdP operation.
4. Fresh-install and supported-upgrade drills boot the expected migration head and pass the release smoke
   contract with network denied.
5. Tampered release-controlled images, Compose, templates, scripts, metadata, or manifest signatures are
   rejected before installation; invalid operator instance configuration is rejected by its separate
   local contract.

## 10. Programme 4 — self-contained disaster recovery

Recovery remains governed by the existing audit-remediation sequence. The takeover definition of done is
strict: a generation is not disaster-recovery capable until it includes or can immutably resolve every
required object byte as well as database, realm, configuration, checkpoint, and key metadata.

The design must establish one sealed consistency boundary across PostgreSQL, Keycloak schema, immutable
object versions, configuration, checkpoint, and concurrent writes. It maintains a durable generation
inventory and retention/pruning policy, proves decryption-key escrow under separate custody rather than
embedding private keys in the backup, and binds the generation to the exact signed offline release bundle
needed after total host loss.

The final proof must:

1. select one sealed generation;
2. deny access to the source database, source MinIO buckets, and upstream registries;
3. restore into fresh role-preserving targets;
4. reconcile failed and abandoned targets through durable inventory;
5. resume or safely restart after partial copy/crash and prove idempotent retry;
6. perform the supported cutover procedure and exercise cutover-failure rollback; and
7. boot the recovered stack and verify authentication, authorization, WORM/object-lock state, scheduled
   worker/Beat behavior, representative controlled documents/records/renditions/audit evidence, and a new
   post-recovery backup.

Before Programme 4 implementation planning, the owner approves numeric RPO and RTO targets plus the
workload size, object count/bytes, and host profile used for proof. The drill records actual RPO/RTO against
that profile and validates the stack remains closed while intermediate targets are inconsistent.

Until that proof exists, runbooks must describe current backups as source-store-dependent operational
backups rather than complete disaster recovery.

### 10.1 Programme acceptance criteria

1. Every generation has a sealed identity, complete leg verdict, exact release-bundle binding, retention
   disposition, and separately-custodied key reference.
2. Source-denied restore, crash/resume, abandoned-target reconciliation, failed-cutover rollback, and
   idempotent retry proofs pass.
3. The recovered stack passes the authentication, authorization, object-lock, scheduler, content, audit,
   and post-recovery-backup checks above within the approved RPO/RTO.

## 11. Programme 5 — repository information architecture

### 11.1 Immediate, safe changes

- Add `AGENTS.md` and slim `CLAUDE.md` as part of programme 0.
- Add `docs/open-residuals.md` as the sole current, owner-visible residual ledger. Give entries stable IDs,
  move only genuinely open work, update every current inbound reference atomically, and leave a
  tombstone/index in `docs/slice-history.md`. A repository check prevents the same residual from being
  current in both files.
- Update stale decision-range references from R1–R63 to the actual registered range where supported by
  the decision register.
- Add an index that separates current specs/plans from historical execution evidence.

### 11.2 Review-required archive moves

The 158 plan/spec artifacts plus `docs/superpowers/README.md`, and the 2,239-line
`docs/slice-history.md`, are evidence, not bulk deletion candidates. Before moving any file:

1. classify it as current authority, superseded authority, implementation evidence, or disposable visual
   scratch;
2. identify inbound repository links;
3. preserve Git history and stable links where practical; and
4. review a manifest of exact move/delete targets.

Completed one-off review documents and redundant mockup fragments may be archived or deleted only from
that reviewed manifest. No wildcard cleanup is permitted.

### 11.3 Large-file policy

`apps/web/src/test/msw/handlers.ts`, `apps/web/src/lib/types.ts`, and
`apps/api/src/easysynq_api/api/documents.py` are refactor candidates, not standalone cleanup projects.
Split them along feature or ownership boundaries only when an active behavior slice supplies regression
tests and a measurable maintenance benefit.

## 12. Programme 6 — later operational hardening

After the signed-runtime prerequisites in Programme 3A, continue the existing least-privilege and
operational work through dedicated slices:

- further split application, migration, backup, object-store, and identity authorities where 3A does not
  already settle the release-critical boundary;
- define liveness, readiness, dependency health, and operator-visible degraded states; and
- keep optional observability compatible with the no-phone-home deployment policy.

Hosted Sentry, PostHog, Supabase, Vercel, or similar plugins are not introduced by default. A self-hosted
or explicitly approved deployment may select them in a later owner decision.

Every later slice updates the 3A service matrix and proves that the signed runtime remains compatible or
intentionally triggers a new release. Optional local-first monitoring must cover the degraded signals
named in §9.1 with bounded storage, redaction, and no customer/QMS content egress.

## 13. Plugin and tool policy

- **GitHub:** an optional integration may accelerate PR, issue, and review context; `git`/`gh` and tracked
  repository state remain the reproducible fallback.
- **Figma / Superdesign:** optional evidence boards and visual alternatives are supplementary. Approved
  repository specs, tokens, and checked-in screenshots remain authoritative and usable without a desktop
  integration.
- **Codex Security:** recommended as the one additional plugin aligned with the threat model, but its
  installation and scopes require a separate owner-approved security decision.
- **Sentry:** consider only after the telemetry/no-phone-home policy, redaction model, and hosting boundary
  are approved.
- **Hosted data/runtime plugins:** do not connect production data or deployment control without a separate
  security and architecture decision.

Any plugin decision records least-privilege scopes, permitted data classes, egress/retention, revocation,
credential ownership, and a local/CLI fallback. Customer content and site-specific deployment data are
excluded unless a later approved policy says otherwise.

## 14. Delivery model

Every implementation slice follows this sequence:

1. owner-approved design with scope, non-goals, risks, and falsifiers;
2. task-level implementation plan naming exact files, tests, and commands;
3. for executable behavior, a failing focused test observed against the current baseline; for
   documentation or repository-organization work, a falsifying link, traceability, lint, or before/after
   evidence check;
4. minimal implementation and focused green proof;
5. full affected API, web, contract, migration, shell, documentation, and browser gates as applicable;
6. independent adversarial review; and
7. owner-visible handoff naming residual risk and the next slice.

One PR owns one bounded behavior or trust boundary. Documentation and tests that define that boundary land
in the same PR. A slice is not complete merely because its focused test passes.

## 15. Takeover definition of done

The takeover is mature when:

- a fresh Fedora workstation can bootstrap and diagnose the repository from tracked instructions;
- agent guidance is vendor-neutral and current;
- the full local test/build/browser matrix is reproducible;
- core startup, setup, route, mutation, keyboard, and responsive failure paths are explicit;
- the guarded demo scenario can exercise the main QMS story deterministically;
- release and air-gap artifacts install and boot without network access;
- self-contained recovery succeeds with source systems denied;
- production runtime authorities and health states are explicit and least-privilege; and
- current decisions and open residuals are easier to find than historical evidence.

## 16. Owner review gate

Approval of this design authorizes only the task-level plan for `S-codex-fedora-foundation`. It does not
authorize bulk deletion, privilege changes on the workstation, production UI changes, release publication,
or recovery implementation. Each later programme retains its own review gate.

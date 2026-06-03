# EasySynQ — Project Context

> Read this first. It orients a new session. The **authoritative** detail lives in `docs/` —
> start with `docs/00-overview.md` (front door) and `docs/decisions-register.md` (the binding decisions).

## What this is

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. Its
core idea is to *invert authority* so document drift becomes an **enforced invariant** rather than a
discipline problem: a managed **controlled vault** (PostgreSQL + MinIO WORM) owns the master copy of
every controlled document and record, and the on-disk filesystem is only a **read-only, organized
mirror** regenerated from Released versions. It is built to prevent document drift, track revision
changes, manage documented evidence/records, and keep an organization audit-ready by default. The
UI/UX flows the way ISO 9001 flows (clause spine / process map / PDCA) and must stay calm, modern,
and progressively disclosed — never overwhelming.

## Current status (as of 2026-06-03)

**Spec complete + MVP build underway** (foundation-first, against the approved plan). The design is locked;
we are now writing code.

- **Specification** in `docs/` (00–17 + `decisions-register.md`) — complete, adversarially audited, reconciled
  (Register R1–R37 back-propagated). The Register is authoritative.
- **Approved implementation plan:** `docs/18-mvp-implementation-plan.md` — repo/tooling, Compose dev stack, the
  Alembic schema from doc 14, the FastAPI/OpenAPI surface from doc 15, and **11 ordered vertical slices S0–S11**,
  each mapped to the six MVP acceptance proofs. §1 records the canon corrections an adversarial pass forced
  (two state enums `version_state`/`current_state`; `audit_event` identity-gap is the tamper signal — **no `seq`
  col**; `framework_id` only on `documented_information`/`clause`/`clause_mapping`/`scope`; doc-07 permission keys
  verbatim; doc-15 flat action sub-resources + approval via `POST /tasks/{id}/decision`).
- **HTML UI mockup** at `mockup/easysynq-mockup.html` (owner-approved).

**Code lives on GitHub:** https://github.com/CoJoA13/EasySynQ (`main`, protected — PR + green CI required;
admin-bypass on for the solo owner). **Shipped so far (each merged via PR, all CI green, validated on the real
Docker stack):**

**S0–S7d — foundation + the mirror/rendering epic** ✅ (one line each; the full per-slice "non-obvious decisions" live in the squash-merge commits + the project memory `easysynq-project.md`):

- **S0** walking skeleton · **S1** AuthN (Keycloak OIDC/PKCE, JWT↔JWKS, `app_user` JIT, `GET /me`) · **S2** AuthZ (deny-wins PDP/PEP, the closed doc-07 96-key catalog + 8 seeded roles, the R35 two-tier grant guard).
- **S3** Vault (check-out → presigned CAS upload → immutable check-in; MinIO WORM + Redis lock; atomic `{TYPE}-{AREA}-{SEQ}` numbering) · **S4** Lifecycle **[AC#1]** (the doc FSM + the atomic SERIALIZABLE single-Effective cutover + the INV-1 partial-unique index) · **S5** Approval + SoD (`POST /tasks/{id}/decision` one-txn + append-only `signature_event` + the deny-wins SoD-1/2/3 gate).
- **S6** Audit **[AC#6]** (append-only, monthly-partitioned, hash-chained `audit_event` behind DB **role separation** [non-owner `easysynq_app`] + the decoupled chain-linker + frozen `canonical_serialize` + the off-host checkpoint anchor) · **S7** Mirror **[AC#2]** (RO Effective-only filesystem mirror, atomic symlink-repoint swap, mounted `:ro`) + **S7b/c/d** (watermarked-PDF rendering via Gotenberg + a deterministic reportlab/pypdf §11.3 band · Ed25519 verify-token + QR + public `GET /verify` · the per-request export/print stamp).

- **S8a — Setup spine (latch + bootstrap-of-trust + first admin + org profile + finalize)** ✅ — PR #16. The first-run
  foundation (doc 08) that stands a fresh install up **self-service + latch-protected**, without the `grant-role` CLI.
  An owner-approved **decomposition** of S8 (G-B WORM-verify, G-C/AC#5 backup+restore-drill CLI, G-D auth-config, wizard
  steps 6-9, the client-side router, in-app Keycloak provisioning + MFA all deferred to **S8b/S8c**). **The 423 latch**
  is an ASGI middleware in `create_app()`: `/api/v1/*` → 423 `setup_incomplete` while `setup_state != OPERATIONAL`, with
  **boundary-anchored** exemptions (the `/setup` tree + exact `/auth/config`, `/me`, `/verify`, `/openapi.json`, `/docs`
  — a `startswith`-collision review fix) so a future sibling route can't be silently un-latched; no cache (per-request
  indexed PK lookup — isolation-safe; the conftest defaults the shared test DB to OPERATIONAL so non-setup tests aren't
  latched). **Bootstrap-of-trust:** `easysynq setup mint-bootstrap` (a new `cli/setup.py`) stores a 256-bit single-use,
  TTL'd, **salted-SHA256** secret on `system_config`; the **public** `POST /setup/bootstrap` (Keycloak-authenticated but
  **outside the PEP** — the secret, not a grant, authorizes it) verifies it constant-time + grants the caller the seeded
  System Administrator role → breaks the deny-by-default chicken-and-egg. Best-effort Redis rate-limit (5/15min, degrades
  if Redis is down). `grant-role` stays **break-glass**. **Endpoints** (`api/setup.py`): `GET /setup/state` (public, SPA
  routing), `GET /setup` (auth), `POST /setup/bootstrap`, `PATCH /setup/org-profile` + `POST /setup/finalize`
  (`config.update`). An **extensible gate registry** (`services/setup/service.py GATES`): S8a checks **G-A** (admin) +
  **G-E** (org `short_code != 'DEFAULT'`); finalize flips the one-way `UNINITIALIZED→IN_SETUP→OPERATIONAL` + emits
  `SETUP_FINALIZED` (its `after` carries the full `{gate: bool}` snapshot — a `sorted(dict)`-drops-bools review fix).
  Setup `audit_event` rows (object types `config`/`user`) commit atomically; `canonical_serialize` v1 untouched.
  **Migration `0012`**: `ALTER TYPE event_type ADD VALUE` for the 4 setup events (the `0011` pattern; no-op downgrade) +
  Python `EventType` members; `organization.timezone` (R8); the bootstrap columns; and it **seeds the never-before-created
  `system_config` row** — `OPERATIONAL` iff a `role_assignment` already exists (so upgrading a **running** install isn't
  bricked by the new latch), else `UNINITIALIZED`; downgrade deletes the seeded row (the org FK would block `0002`).
  **Web (minimal, no router):** `App` branches on `/setup/state` — a Mantine `<Stepper>` wizard (sign-in + bootstrap
  secret → org-profile form → finalize) vs the normal shell; a bearer-fetch helper; **no new deps**. Adversarially
  reviewed (5 lenses → 15-agent verify); the `0012` OPERATIONAL-upgrade branch verified on a throwaway PG. Proofs:
  secret mint/verify + EventType (unit); latch-423-until-operational, bootstrap-grants-admin+audits, wrong/replay/
  expired/no-secret rejected, rate-limit-lockout, org-profile authz+validation, finalize-gates→OPERATIONAL+latch-lifts,
  exemption-boundary, grant-role break-glass (integration). 131 unit + 84 integration.

- **S8b — Setup gate G-B (WORM-verify) + `storage_config`** ✅ — PR #18. An owner-approved **split** of S8b (the
  backup/restore CLI + the AC#5 restore-into-scratch drill + gate G-C are **S8b2** — disjoint risk profiles). Lands
  gate **G-B** (doc 08 §7): **`storage.worm_probe`** PUTs a tiny probe to the object-locked `documents` bucket →
  confirms a future `retain-until` → attempts to delete **that version** with no bypass and expects a **denial** (the
  honest §7.2 proof — deletes the *version*, not a delete marker; a non-versioned/non-locked bucket → no VersionId →
  **not verified**, so no false-PASS). Short boto3 timeouts + a guarded `put` so a missing/unreachable bucket is a clean
  422, not a 500/hang (review fix). **`POST /api/v1/setup/verify-storage`** (gate `storage.manage`, latch-exempt) → PASS
  upserts `storage_config` (`worm_verified_at` + the `object_lock_mode` choice) + emits `WORM_VERIFIED` + commits
  (serialized on the `system_config` singleton lock — a review fix for the same-org check-then-insert race); FAIL → 422
  `worm_not_enforced` (signal stays null → no gate false-pass). **D-7:** the object-lock mode is **recorded** (default
  GOVERNANCE); COMPLIANCE is not plumbed (a hardened v1.x opt-in). `Gate("G-B", _gate_worm_verified)` appended to the
  registry — finalize now requires **G-A + G-E + G-B** (live re-check, zero finalize-code change). **`0013`**: `ALTER
  TYPE event_type ADD VALUE 'WORM_VERIFIED'` (+ Python member) + a **minimal** `storage_config` (id PK, org_id unique,
  `worm_verified_at`, `object_lock_mode`; no seed — null reads as G-B-unsatisfied; no brick risk — upgraded OPERATIONAL
  installs never re-finalize; doc-14's backup/bucket/mirror columns land in S8b2). **Web:** a "Storage" `<Stepper>` step
  (GOVERNANCE/COMPLIANCE mode + a Verify button) between Organization and Finalize. Adversarially reviewed (4 lenses →
  verify) — the probe lens hunted the dangerous **false-PASS** direction and all three findings independently confirmed
  there is none; folded the concurrency lock + the guarded-probe-put/timeout + a re-run/UPDATE-in-place test. Proofs:
  probe verifies the locked bucket + correctly reports plain `staging` as non-WORM, verify-storage sets G-B +
  `WORM_VERIFIED` audit + requires `storage.manage` (403 else), finalize-blocked-on-G-B-then-passes, re-run UPDATEs in
  place. 131 unit + 89 integration.

- **S8b2 — Setup gate G-C (backup/restore-into-scratch drill) + durable backup [AC#5]** ✅ — PR #20. The last blocking
  setup gate + a named MVP acceptance proof: finalize is **blocked until a real backup→restore-into-scratch drill
  PASSES** the integrity triad (**blob SHA-256 re-hash · per-table row-count parity · `document_version→blob` FK
  check**); "configured but unverified" does **not** satisfy G-C (doc 08 §8, doc 18 §7). **Owner forks:** real
  `pg_dump`→`pg_restore` (a faithful artifact round-trip, NOT a logical copy — the thing G-C exists to catch) ·
  restore into a scratch **DATABASE** (pg_restore's natural unit; doc 08 §8.2's "temporary PG schema" **reconciled**
  as "an isolated namespace", noted in `drill.py` + back-propagated to doc 08 §8.2) · **durable archive + drill** (a
  real `easysynq backup` + nightly Beat, alongside the gating drill). `services/backup/`: `dsn` (SQLAlchemy URL→libpq
  env, password via env not argv), `archive` (`pg_dump -Fc`/`pg_restore` subprocess + tar + `.sha256` pack/verify),
  `drill` (scratch-DB createdb→`pg_restore`→teardown; blob copy into the **non-WORM** `restore-scratch` bucket under a
  per-drill prefix; the triad on the **restored** copy; **race-free row-count parity** via `pg_export_snapshot()` +
  `pg_dump --snapshot`; composable steps + an `after_restore` fault seam; **never raises** — a missing binary/crash is
  an honest FAIL, never a 500), `service` (async orchestration: `LOCK_RESTORE_DRILL`=7710004, persist
  `last_restore_test_result`, emit `RESTORE_TEST_*` + commit). Runs as the **OWNER** role (`sync_dsn`) — the
  `easysynq_app` role can neither `pg_dump` the whole DB nor `CREATE DATABASE`. `Gate("G-C", _gate_restore_test_passed)`
  appended to `GATES` (keys on `result=='PASS'`, not just `_at`) → finalize now needs **G-A+G-E+G-B+G-C**, zero
  finalize-code change; the off-host audit anchor stays a **soft gate** (surfaced in `GET /setup` via S6's
  `tamper_evidence_attested`; never blocks; R13). **`0014`**: `ALTER TYPE event_type ADD VALUE` ×3
  (`BACKUP_CONFIGURED`/`RESTORE_TEST_PASSED`/`RESTORE_TEST_FAILED`, the 0012/0013 pattern; Python `EventType` members
  added too) + `backup_policy` (doc 14 §2 columns; retention as **counts** 7/4/6; `wal_pitr_enabled` a recorded
  forward-seam — `configure-backup` **rejects `true`** as D-6 scope). Endpoints (latch-exempt): `POST
  /setup/configure-backup` (`backup.configure` + live destination writability check) + `POST /setup/run-restore-test`
  (`restore.run`, enqueues the worker task — 202). `tasks/backup.py` + the nightly `easysynq.backup.run` Beat job +
  `cli/backup.py` (`run`/`restore-test`) wired into `scripts/easysynq`. **Dockerfile**: `postgresql-client-16` via the
  PGDG repo (matches `postgres:16`; build-time only → air-gapped *installs* unaffected). Compose: a `backup` volume on
  the worker; minio-init + the integration conftest add the plain `restore-scratch` bucket (R37 — object-lock can't be
  retro-added, never restore into the WORM vault bucket). **Web:** a "Backup" `<Stepper>` step (configure + run-restore-
  test with poll-to-green) + the not-tamper-evident soft-gate warning. Adversarially reviewed (5 lenses → 24 raw → 14
  verified; the false-PASS lens found no way for the drill to PASS without a real restore): folded the **headline
  coverage gap** — the blob-dependent legs (re-hash + FK) were only vacuously exercised at 0-blob IN_SETUP, so added two
  deterministic OPERATIONAL-state tests over a real Effective document (`test_drill_passes_over_real_blobs` asserts
  `details.blobs ≥ 1`; `test_drill_fails_on_corrupted_restored_blob` proves the re-hash leg catches a corrupted restored
  blob) + the WAL/PITR-reject test + the §8.2 deviation/skip docstrings. Also fixed a real bug review surfaced via the
  full-suite (with prior tests' blobs): teardown's multi-delete `delete_objects` → MinIO `MissingContentMD5` → switched
  to per-object `delete_object`. Proofs: `test_setup_finalize_requires_restore_pass` **[AC#5]** + negative
  drill→FAIL→finalize-blocked, configure/run authz (403), destination/cron/wal-pitr validation, durable archive,
  scratch teardown (no orphan DB/objects), real-blob PASS + corrupted-blob FAIL; archive-checksum + dsn + EventType
  unit. **139 unit + 101 integration** (the real-drill path validated locally with `postgresql-client-16` + in CI).

- **S8c — Setup gate G-D (auth-config + non-bootstrap login proof) + minimal client router** ✅ — PR #22. The **last
  blocking** setup gate (doc 08 §9): finalize is blocked until an auth method is selected and a **non-bootstrap login is
  proven** — "never strand the org on a misconfigured IdP". Once G-D lands the latch fully lifts (G-A+G-E+G-B+G-C+G-D →
  OPERATIONAL). **Owner forks:** scope = G-D gate + minimal router (defer wizard steps 6–9 + Keycloak admin-API
  provisioning + MFA *enforcement* → S8d) · proof = mode-routed live check + persisted attestation (mirrors G-B
  `worm_probe` / G-C drill) · MFA = **logged acknowledgement only** (the `acr`/`/auth/step-up` enforcement seam stays a
  no-op, Part-11 reserved per D3). **The proof, faithful to the Keycloak-brokered architecture:** the `configure-auth`
  caller's **valid non-bootstrap JWT** (JWKS-validated by `get_current_user`; the bootstrap path authorizes via the
  *secret* OUTSIDE the PEP) **+ a live OIDC-issuer discovery reachability probe** (`services/setup/auth_check.py` —
  httpx, short timeout, **never raises**, type-guards a malformed IdP body → a clean 422 not a 500; mockable like
  `worm_probe`). A failed probe → **422 `auth_unavailable`** + `AUTH_TEST_LOGIN_FAILED`, signal stays null (no
  false-PASS). Upstream federation (LDAP/OIDC/SAML) is **Keycloak's** job (deferred); `auth_method` is recorded metadata;
  local break-glass login is never disabled → the org can't be locked out. `Gate("G-D", _gate_auth_configured)` appended
  to `GATES` (keys on `system_config.auth_test_login_ok is True`, not just `_at`) — zero finalize-code change.
  `configure_auth` singleton-locks the `system_config` row + emits `AUTH_CONFIGURED` + `AUTH_TEST_LOGIN_OK` (mfa-ack +
  break-glass ride in the audit `after`). **`0015`**: `ALTER TYPE event_type ADD VALUE` ×3
  (`AUTH_CONFIGURED`/`AUTH_TEST_LOGIN_OK`/`AUTH_TEST_LOGIN_FAILED`, the 0011–0014 pattern; Python `EventType` members
  added too) + 3 **nullable** `system_config` auth columns (no seed → null = G-D-unsatisfied; no brick). `POST
  /setup/configure-auth` (`config.update`, latch-exempt). The `acr`/step-up seam is **untouched** (D3). **Web:** added
  `react-router-dom` (`<BrowserRouter>` wraps `App`; routes `/setup` · `/` shell · `/admin` stub — `useAuth` stays at the
  root so the OIDC `/?code&state` callback is processed before routing) + an **"Authentication"** wizard `<Stepper.Step>`
  (method + MFA-ack + Verify) between Backup and Finalize. Adversarially reviewed (4 lenses → verify; the false-PASS /
  lock-out lens confirmed neither risk exists) — folded the one real finding: the OIDC probe parsed the JSON body outside
  the try/except (a malformed IdP → 500, not 422), now type-guarded + 2 unit tests. **CI runs no Keycloak** (JWKS stubbed)
  — the probe is monkeypatched and the minted token is the non-bootstrap login proof; the live round-trip stays a manual
  dev-stack proof. Proofs: `test_setup_finalize_requires_auth_proven` **[G-D]** + a negative (unreachable IdP → 422,
  finalize blocked, `AUTH_TEST_LOGIN_FAILED`) + configure-auth authz (403) + bad-method (422); the probe parsing
  (mismatch/missing-jwks/non-200/network-error/non-dict-body/non-string-issuer all FAIL) + `EventType` unit. **148 unit +
  108 integration**.

- **S8d — Users & Roles admin + user lifecycle (invite / enable-disable)** ✅ — PR #24. The first **post-finalize,
  non-blocking** admin surface (doc 08 §10/§11) — makes the seeded roles + per-user grants manageable in-app (the
  Avery→Mara hand-off), replacing the `grant-role` break-glass CLI as the only path. **Mostly web + reuse:** S2 already
  shipped the entire authz-admin API incl. the WRITE paths (`POST/DELETE /users/{id}/roles` with the R35 two-tier guard,
  `POST/DELETE /users/{id}/overrides`), all audited + epoch-bumped — **no new permission keys, no new authz concepts, no
  schema columns** (`UserStatus` already has `INVITED`; `app_user.mfa_enrolled` already exists). **Owner forks:** scope =
  Users & Roles admin + user lifecycle (invite/enable-disable); defer the doc-08 §10.4 self-grant friction → v1
  (self-grants are already `OVERRIDE_ADD`-audited + two-tier-guarded), MFA = display `mfa_enrolled` only (enforcement
  Part-11/D3), Keycloak provisioning = rely on JIT (in-app admin-API is v1), custom-role authoring + scope/process-map
  (S9) + import (v1) deferred. **`api/users.py`** (new router): `GET /users` (roster + role names, `user.read`) +
  `GET /users/{id}`; `POST /users` = **invite** (pre-create an `INVITED` `app_user` bound to an operator-supplied
  Keycloak subject, `user.create`, `USER_CREATED` audit, 409 on dup); `PATCH /users/{id}` = **enable/disable**
  (ACTIVE|DISABLED, `user.deactivate`, `USER_STATUS_CHANGED` audit) with a **last-admin lock-out guard** (409
  `last_admin`, doc 08 §9.1 — refuses disabling the sole active System Administrator). All org-scoped + pre-commit-audited.
  **`auth/dependencies.py`**: `get_current_user` reconciles an `INVITED` row → `ACTIVE` on first genuine login (JIT match
  on `keycloak_subject`; never resurrects an inactive account). **`0016`**: `ALTER TYPE event_type ADD VALUE` ×2
  (`USER_CREATED`/`USER_STATUS_CHANGED`, the 0011–0015 pattern; Python `EventType` members too); **no columns**. **Web:**
  `react-router` nested routes under `/admin` → `AdminShell` (Users/Roles tabs + `Outlet`) + `UsersAdmin` (Mantine Table
  roster + invite `Modal` + enable/disable + a per-user `Drawer`: assign/revoke seeded roles + add/remove SYSTEM-scoped
  overrides via the reused S2 endpoints, surfacing `two_tier_violation`/`last_admin`) + `RolesAdmin` (read-only seeded
  roles + grants); `lib/api` gains `DELETE` + 204 handling + `useMutation`. Adversarially reviewed (3 lenses incl. an
  authz-bypass/lock-out hunter → per-finding verify; **0 confirmed of 1** — no false-PASS/lock-out/escalation path).
  `openapi.yaml` deliberately not updated (matches S8b2/S8c; the `contracts` CI is redocly-lint only, the web client isn't
  generated). Proofs: roster authz (403), invite + `USER_CREATED` + 409-dup + 403, the `INVITED→ACTIVE` reconciliation,
  disable-blocks-then-reenable + `USER_STATUS_CHANGED` + 403, the last-admin guard (409), assign-seeded-role-visible-in-
  roster end-to-end; the 2 `EventType` members unit. **149 unit + 111 integration**.

- **S9 — Clause IA + `clause_mapping`** ✅ — PR #27. The ISO 9001:2015 clause spine + the M:N document↔clause mapping + the
  lifecycle submit gate. **Owner-scoped to clause IA only** (process IA — `process`/`process_edge`/`process_link` +
  endpoints — deferred to a follow-on/S8e to avoid the not-yet-built `org_role`/`supplier` FK targets; the mirror
  §10.3 tree deferred to **S9b**; clause-mapping writes gated on the existing `document.manage_metadata`). **`0017`**
  creates the read-only `clause` table (self-nested `parent_id`, `pdca_phase` enum, `is_mandatory_star`,
  INSERT-by-seed-only — no `clause.edit` key) + the audited `clause_mapping` join (`org_id`+`framework_id` per C5;
  `UNIQUE(documented_information_id, clause_id)`; the `documented_information_id` FK **named explicitly** — the
  convention default is 64 chars > PG's 63 limit) + `ALTER TYPE event_type ADD VALUE 'CLAUSE_MAPPED'/'CLAUSE_UNMAPPED'`
  (the 0011-0016 additive pattern; reuse `object_type=document` — the closed `AuditObjectType` gains no member).
  **`0018`** seeds the **83-clause** ISO 9001:2015 catalog (the **20 ★ mandatory** rows = doc 02 §2.1 / **R30**, incl.
  **8.5.6**; PDCA per §3.2 with clause 7 split PLAN/DO) from a **reviewable, unit-tested** data module
  (`db/seeds/iso9001_clauses.py`) — **drafted + adversarially verified against doc 02** (a draft → 3-skeptic-lens →
  reconcile workflow; two corrections: 7.5 is not a §2.1 ★ row, 5.1.1's official title is "General"); parent_id resolved
  in a second pass. **The headline:** the S4 `# S9:` seam at `lifecycle.py` is filled — `submit_review` now **422
  `validation_error`** when a document has **zero** clause mappings (counted on the DOCUMENT, so a revision T9 inherits
  its mappings; fail-closed before any mutation). New `GET /clauses` (`clauseMap.read`, SYSTEM — doc 15 §8.4's `clause.read`
  shorthand reconciled to the real seeded key) returns the spine flat + ordered by a numeric `string_to_array(number,'.')::int[]`
  sort; flat sub-resources **`POST`/`GET`/`DELETE /documents/{id}/clause-mappings`** (`document.manage_metadata`/`document.read`)
  with a multi-standard framework-match guard (422), dup-map 409 (+ `IntegrityError` race backstop), and in-txn
  `CLAUSE_MAPPED`/`CLAUSE_UNMAPPED` audit (the `users._emit_user_event` pattern). **No new permission keys, no catalog
  change, no web** (S9 is the API/data foundation). Shared test helper `_map_clause` (iso9001-scoped clause pick) wired
  into `drive_to_approved` + 4 direct-submit sites so the gate doesn't break existing flows. Adversarially reviewed
  (4 lenses → per-finding verify; 10 confirmed of 22, all folded) — incl. a **HIGH**: `0018` originally resolved the
  framework via a `short_code='DEFAULT'` org join, but a finalized install renames `short_code` away (the G-E gate), so
  `0018` (the first seed migration to run during an **upgrade of an already-finalized install**) would `NoResultFound` →
  `alembic upgrade` fail (CI can't catch it — a fresh DB still has `DEFAULT`); fixed to resolve by the stable
  `code='iso9001:2015'` + `scalar_one_or_none` skip (proven on a throwaway PG by renaming the org, then upgrading). Plus
  the submit/unmap **TOCTOU** (unmap now `FOR UPDATE`-locks the doc row) + 4 test gaps (the T9 gate, the concurrent-dup
  `IntegrityError` race, the audit-payload content, the default-`False` path). Proofs:
  `test_submit_requires_clause_mapping` [S9 headline] (0-maps→422, then map→T2 200) + `test_t9_revision_submit_requires_
  clause_mapping` (the T9 gate + revision-inherits-mappings) + GET-clauses-spine (hierarchy + ★ + PDCA) + clauseMap.read
  403 + map/unmap-audited round-trip (before/after payloads) + dup-409 + concurrent-dup-race (201/409) +
  cross-framework-422 + map-needs-manage_metadata-403 (integration); the frozen catalog (83/20★/8.5.6/PDCA/tree) +
  `EventType` members (unit).
  **156 unit + 120 integration** (the only locally-red tests are the 5 pre-existing `pg_dump`-absent backup-drill tests —
  environmental, green on CI's `ubuntu-latest`).

- **S9b — Clause-aligned mirror tree (doc 04 §10.3)** ✅ — PR #31. Rebuilds the flat S7 mirror into the
  PLAN/DO/CHECK/ACT → top-level-clause tree now that `clause_mapping` exists (fills the `# S9:` seam at `mirror.py:21`).
  **Owner forks:** scope = **mirror tree only** (process IA + the by-process secondary index deferred to **S9c** — the
  `process`/`edge`/`link` + `org_role`/`supplier` FK targets don't exist, `process.create` is held by no seeded role, and
  process rows only come from the deferred S8e wizard); placement = **symlink into every mapped clause** (real bytes
  **once** under the *numerically*-lowest mapped clause, a **relative** symlink from every other mapped clause folder —
  §10.3/§10.4 "without duplicating bytes"). **Phase rides on the mapped clause's own `pdca_phase`** (`documented_information`
  has **no** `pdca_phase` column — doc 04 §6.1 says it should; S3 never added it), so the clause-7 split lands 7.2 →
  `PLAN/07-Support` and 7.5 → `DO/07-Support`; the `{NN}-Word` folder = the top-level ancestor's number (0-padded) + the
  first word of its title (reproduces the §10.3 example exactly). A zero-mapping Effective doc (only reachable as a pre-S9
  **upgrade** artifact — the submit gate forbids it) lands in `_unmapped/`. **Pure `services/vault/mirror.py` + tests — no
  migration/schema/`event_type`/web/endpoint change (head stays `0018`):** `ClauseRef` + the pure `_placement_dirs`
  (numeric-not-lexical primary [9 before 10], `(phase, top_number)` dedup, canonical PLAN<DO<CHECK<ACT `other_dirs` order)
  + `fetch_clause_refs`/`fetch_top_words` (one batch query each; top words **(framework_id, top_number)-keyed** so a future
  standard's "8" can't collide with ISO's) + `_write_symlink` (relative target, asserted within `build_root` — no host-path
  leak — manifest `{path, symlink_to}` entry, no `sha256`); `metadata.json`/`INDEX.md` gain the numeric-sorted mapped-clause
  list (byte-deterministic → the §10.4 idempotency invariant holds); `MirrorSyncResult` gains a `symlinks` count.
  `atomic_swap`/render-cache/the `:ro` mount contract/the AC#2 whole-tree-rebuild are untouched (internal symlinks are
  relative → survive the swap). **Single-org invariant (D1)** documented (the mirror stays org-agnostic → no cross-org
  `{ident}_{rev}` collision). **Fresh-dir-only:** a path can flip dir↔symlink between builds, so production always builds
  into a fresh `.builds/<uuid>` + swaps (a unit test pins that reuse-after-remap raises). Adversarially reviewed (5 lenses
  → per-finding verify; **1 of 5 confirmed + folded** — a test-assertion tightening; placement/symlink-safety/query/spec
  lenses found nothing). Proofs: `_placement_dirs` (single · dedup · clause-7 split · numeric primary · canonical phase
  order · `_unmapped` · zero-pad) + build-tree real-bytes-under-primary + cross-clause relative symlink (resolves +
  contained) + manifest symlink entries + the fresh-dir-only reuse-raises contract (unit); clause-placement +
  multi-clause-symlink (bytes-once) + clause-7-two-phase + `_unmapped`-fallback (simulated via direct mapping-row delete) +
  **symlink-survives-swap** end-to-end (integration); `test_render`/`test_verify` helpers updated for the nested layout.
  **169 unit + 28 mirror/render/verify integration green** (the 5 `pg_dump`-absent backup tests stay environmental, green
  on CI).

- **S9c — Process IA backend (graph + authoring + process-links)** ✅ — PR #32. The ISO 9001 Clause 4.4 process
  dimension as the API/data foundation (the S9 clause-backend → S9b clause-mirror split, applied to processes).
  **Owner forks:** **backend only** (the by-process secondary mirror index → **S9d**, a separate filesystem surface that
  reuses S9b's `_write_symlink` over `process_link`); **minimal real `org_role` + `supplier`** tables (RACI / outsourcing
  FK targets, **empty-but-present** per D-3 — no `/org-roles`/`/suppliers` authoring; `org_role` is RACI-not-authz, doc 02
  §3.4) with the `process` FKs **nullable**. **`0019`**: `org_role` + `supplier` + `process` (self-nested `SEED`/`ACTIVE`
  node; `pdca_phase` **reuses** the 0017 enum) + `process_edge` (`CHECK` no-self-loop + `UNIQUE` pair) + the audited
  `process_link` M:N join (FK named explicitly — the 63-char limit, clause_mapping precedent) + `ALTER TYPE
  audit_object_type ADD VALUE 'process'` + 7 `PROCESS_*` `event_type` values (the 0011-0017 additive pattern; Python
  members too). **No seed migration, no `storage_config`/mirror change, no web; `alembic check` clean** (all 5 models
  registered, names matched; verified up↔down↔check on a throwaway PG16). **`api/processes.py`**: `GET /processes(/{id})
  (/map)` (`process.read`, **default SYSTEM scope** — the `GET /clauses` shape) + `POST`/`PATCH /processes` + `POST`/
  `DELETE /processes/{id}/edges` (`process.create` SYSTEM / `process.manage` + `_process_scope`); the **SEED→ACTIVE
  one-way ratchet** (`ACTIVE→SEED` → 409 `invalid_state_transition`; null-on-required → 422 not a 500); self-loop/dup 409
  (+ `IntegrityError` backstops); `_emit_process_event` (`object_type=process`). **Process-links** `POST`/`GET`/`DELETE
  /documents/{id}/process-links` in `api/documents.py` clone the clause-mappings shape (gate `document.manage_metadata`;
  `PROCESS_LINKED`/`UNLINKED` **reuse `object_type=document`** keyed to the doc — the S9 precedent). **Authz reality (the
  decisive point):** `process.create`/`assign_owner` are **seeded but held by no role** (override-until-UI, the
  `document.export` precedent); the seeded `process.read/manage` grants are PROCESS-scoped with an unsubstituted
  `:assignment_process` placeholder that matches **no concrete process** → S9c authoring rides on **SYSTEM overrides**,
  and a negative test documents the deferral (concrete per-process authoring lands with owner-assignment). `org_role` is
  never wired to the PDP/PEP. **No web; openapi not regenerated** (the S8b2-S9b precedent). Adversarially **designed** (a
  Plan-agent pressure-test caught the PROCESS-scope dead-end, the 64-char FK, the audit-object_type choice, the
  `alembic check` discipline up front) and **reviewed** (5 lenses → per-finding verify; **7 of 9 confirmed + folded** —
  the real bug was the PATCH-null 500→422; rest defense-in-depth/tests; 2 refuted soundly). Deferred as
  consistent-with-clause-precedent / systemic / moot-under-D1: repo by-id helpers fetch-then-handler-validate (the
  `get_clause` pattern); a DB-level `process_link` org-consistency constraint (`clause_mapping` has the same shape).
  Proofs: 4 unit (enum values + new audit members) + 19 integration (create/dup/concurrent-race/validations · the
  SEED→ACTIVE machine + null-422 + active-edit · edges self-loop/dup/missing/delete · reads+map+403 · the **PROCESS-scope
  deferral 403 + concrete-binding 200** · process-link map/unmap/dup/422/403 + **cross-org-422** [seeds a throwaway 2nd
  org, cleaned up in `finally` so `test_setup`'s `Organization.scalar_one` stays valid]). **177 unit + the
  process/clause/documents integration green** (the 5 `pg_dump`-absent backup tests stay environmental, green on CI).

- **S9d — By-process secondary mirror index (doc 04 §10.3)** ✅ — PR #33. Now that S9c landed `process_link`, the mirror
  gains the doc 04 §10.3 "secondary index by Process": a parallel **`current/by-process/{name}/`** tree of **relative
  symlinks** into the same real clause-tree doc folders (bytes never duplicated), so a human can browse the disk by the
  process a doc serves. **Completes the mirror epic for the IA's process dimension** (closes the S6/S7 by-process seam).
  **Owner fork: always-on hybrid, NO `mirror_layout` column** — the index is cheap + there's no v1 UI to toggle it, so the
  doc-14 `storage_config.mirror_layout` column lands later with its config surface. **Pure `services/vault/mirror.py` +
  tests — no migration/schema/API/web (head stays `0019`):** reuses S9b's `_write_symlink` via `ProcessRef` +
  `fetch_process_links` (the `fetch_clause_refs` twin, one batch query) + `_placement_process_dirs`
  (`sorted({"by-process/{_safe(name)}"})`, deduped-by-safe-name) + a `build_tree` loop symlinking `doc_dir` from each
  process folder (works whether `doc_dir` is under a clause or `_unmapped/`); `metadata.json` gains a name-sorted
  `processes` array; the `MirrorSyncResult.symlinks` count already covers them. Adversarially reviewed (5 lenses →
  per-finding verify; **0 of 3 confirmed** — placement/symlink/query/idempotency/spec lenses found nothing; the 3 refuted
  were test-nits). Proofs: 8 unit (`_placement_process_dirs` single/multi/empty/safe-name-dedup + build-tree by-process
  symlink resolves + clause-and-process share one real folder + manifest/metadata + unmapped-doc-with-process-link) + 2
  integration (by-process symlink resolves + metadata; multi-process two symlinks + bytes-once, via a direct
  `Process`+`ProcessLink` seed on an Effective doc). **181 unit + the mirror/render/verify integration green** (the 5
  `pg_dump`-absent backup tests stay environmental, green on CI).

- **OpenAPI contract catch-up (S9–S9c)** ✅ — PR #35. A bounded, no-code chore: the hand-maintained
  `packages/contracts/openapi.yaml` (caught up only through S8d/#26) now documents the shipped S9/S9c
  surface — `GET /clauses`, the `/documents/{id}/clause-mappings` + `/process-links` sub-resources, and
  `/processes(/{id})(/map)` + `/processes/{id}/edges` — **2 tags (`clauses`/`processes`), 14 ops across 10
  path items, 11 component schemas**, with field names/types/nullability/enums (`PdcaPhase`/`ProcessState`)
  + every status (201/200/204/403/404/409/422) + machine error code transcribed **verbatim** from the
  models + handler serializers (no new `Problem.code` value needed — `framework_mismatch` is a free-form
  `errors[].code`). Pure-additive; **redocly lint green** (the `contracts` CI is redocly-lint only — no
  codegen, the API server/web client are not generated from this file). Adversarial **4-lens fidelity
  review** (clauses · process-graph · process-links · style/lint/completeness → per-finding verify) — **0
  findings**. S9b/S9d are pure-mirror (no endpoints). The contract is now caught up **through S9c**, so the
  stale per-slice "openapi deliberately not updated" notes (S8b2–S9c) are superseded — going forward,
  document new endpoints in the same PR as they ship.

- **S10 — search/reporting backend: the Compliance Checklist + Postgres-FTS search + clause_refs/filter on
  `GET /documents`** ✅ — PR #38. The doc-13 search & reporting layer, **owner-scoped (AskUserQuestion) to the
  reporting/FTS-search backend; NO web** (the Admin Audit-Log *screen* stays with the deferred web track). Forks:
  scope = reporting/FTS-search · coverage = **Mapped+Effective** · list = **minimal add** (no envelope) · checklist authz =
  **also grant Internal Auditor**. **(1) Compliance Checklist** — `GET /reports/compliance-checklist` (gate
  `report.compliance_checklist.read`, SYSTEM — **already seeded in 0004**, QMS-Owner-only) returns the **20 ★ mandatory
  clauses** (`clause.is_mandatory_star`, doc 02 §2.1 / R30 incl. 8.5.6) with per-clause **COVERED** (≥1 mapped doc has
  an Effective version) / **PARTIAL** (mapped, none Effective) / **GAP** + a rollup; one grouped query (`clause` LEFT
  JOIN `clause_mapping` ON clause_id+org_id LEFT JOIN `documented_information`, `count(distinct di.id)` + a
  `FILTER(effective)`); framework resolved by stable code; **PG-only** (doc 13 §1.2; never the index);
  `services/reports/checklist.py` + a pure `coverage_status`. **(2) Search** — `GET /search` + `GET /search/suggest`
  behind an engine-agnostic **`Indexer` Protocol** (`services/search/indexer.py` `PostgresFtsIndexer`; `get_indexer()`
  the seam; **OpenSearch is the v1 drop-in**, R34). A **functional GIN index** via `op.execute` (`0020` — not a
  generated column, so no `Computed`-comparison drift; `migrations/env.py._include_object` **excludes**
  `ix_documented_information_search_tsv` from autogenerate — *this Alembic version DOES reflect expression indexes, so it
  must be excluded; verified on a throwaway PG16*). **Effective documents only** (doc 13's "Effective only" default — a
  folded review finding: searching all states leaked Draft/Obsolete titles to a `document.read`-only caller, since
  `read_draft`/`read_obsolete` are distinct keys search never consults). **Filter-not-403**: candidate hits are
  post-filtered by per-row `authorize(document.read)` → a `hidden_by_scope` footer ("N hidden by your access scope").
  **(3) `GET /documents`** — `clause_refs` (batch-joined via `repository.clause_numbers_for_docs`, no N+1) in the list +
  single serializers; the doc-15 bracketed `filter[field][op]` grammar parsed from `request.query_params` (allow-list:
  `clause_refs[has]` exact clause-number, **framework-constrained** [folded D3 defense-in-depth] + `current_state` /
  `document_type` / `owner_user_id` / `classification` `eq`); unknown field/op → **400 `unknown_filter`**, bad
  enum/uuid → 422; kept the bare `list[dict]` + `limit` (minimal add — no `{data,page,_links}` envelope/cursor).
  **(4) Authz** — `0021` **backfills `report.compliance_checklist.read` onto the Internal Auditor role for every org**
  (resolve role by stable name + permission by key; `on_conflict_do_nothing`; downgrade deletes exactly that grant by
  role-name so QMS Owner's `0004` grant is untouched — the **first authz backfill after 0004**). **PROOF:** the audit
  read API exposes **no write verbs** (route-inventory unit test over `api.audit.router`; doc 18 §7 S10 DoD; co-proves
  **AC#6**). **No new permission keys.** openapi.yaml caught up **in-PR** (tags `search`/`reports` + the 3 paths +
  `clause_refs`/filters + `SearchResults`/`Suggestions`/`ComplianceChecklist` schemas; redocly green). Adversarially
  reviewed (5 lenses → per-finding verify; **2 of 2 confirmed + folded** — the framework-constrained clause filter and
  the Effective-only search restriction). **186 unit + the S10 integration suite green** (the 5 `pg_dump`-absent
  backup tests stay environmental, green on CI).

**Next slice: S11 — backup/restore-CLI hardening + the exit slice** (operator-grade *live* WORM-aware restore + cutover,
PITR↔blob-snapshot alignment, retention *pruning*, archive envelope encryption, S3 destination, `easysynq
restore`/`upgrade`, the NFR/security/runbook pass). The gate registry + latch extend by just appending gates.
**Deferred (S8e / v1 / Part-11):** the doc-14 `storage_config.mirror_layout` toggle (with its config UI);
**owner-assignment** (`org_role_assignment` + concrete PROCESS-scope grants → real Process-Owner authoring) +
`/org-roles`/`/suppliers` authoring (v1); the **web** Compliance-Checklist + Admin Audit-Log screens + clause-spine nav +
mapping UI + process-map UI; the rest of doc-13 search/reporting (faceted facet-rail, saved searches, dashboards, the
canonical reports, evidence packs, find-where-used, content-plane/body-text FTS, the `{data,page,_links}` cursor envelope,
subtree clause rollup, the checklist's "overdue review"/"linked evidence" legs [need `next_review_due`/records], R31
scope-conditional coverage); wizard Step 8 (scope/process-map seed → SEED nodes) + Step 9 (import → the v1 ingestion
epic); custom-role create/update/delete + bulk-CSV invite + the effective-permissions explorer (v1); in-app Keycloak
admin-API provisioning (v1); MFA *enforcement* + `acr`/step-up (Part-11, D3); the §10.4 self-grant friction +
`ADMIN_SELF_GRANTED_QMS_CAP` event (v1). **Deferred (S11 / v1.x, D-6 / R37):** PITR/WAL, retention *pruning*, Keycloak
realm export, archive envelope encryption, S3-destination, `easysynq restore`/`upgrade`. S6/S7 seams still open (Keycloak
auth-event SPI, `/audit-events/export`). Pre-existing hardening noted: `area_code` is unconstrained `Text` at the S3
create boundary.

## Building the MVP (dev workflow)

- **Branch + PR flow:** `main` is protected. Do slice work on a `feat/sN-*` branch → open a PR → green CI →
  squash-merge. CI jobs: `contracts` (redocly), `api` (ruff/mypy-strict/unit), `migrations` (alembic up↔down +
  `alembic check`), `web` (eslint/tsc/build), `integration` (pytest -m integration via testcontainers). All five
  are required checks.
- **Toolchain (this machine):** `uv` + a managed **Python 3.12** at `~/.local/bin/uv` (system `python3` is 3.14;
  `pip` needs `--break-system-packages`). Node 22 + npm. Docker v29.x. Lockfiles committed (`uv.lock`,
  `package-lock.json`); CI uses `uv sync --frozen` / `npm ci`.
  - **Docker socket:** the user is in the `docker` group, so a fresh login session (e.g. after a reboot) should
    use Docker directly. If a shell still gets "permission denied", re-run `sudo chmod 666 /var/run/docker.sock`
    (personal, non-shared device).
- **Local loops** (fast; no commit needed to iterate):
  - API: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
    (unit always; `-m integration` needs Docker for testcontainers).
  - Web: `cd apps/web && npm run lint && npm run typecheck && npm run build`.
- **Run the stack:** `just up s` (or `docker compose -f infra/compose/compose.yml -f infra/compose/compose.s.yml
  up -d --build`). Open **http://localhost**. Stop with `just down`. A gitignored `.env` holds dev secrets +
  `OIDC_ISSUER=http://localhost/realms/easysynq`. OpenSearch + gotenberg are intentionally not run in MVP dev
  (R34 / not needed until S7).
- **⚠ S6 `.env` role separation (do this before bringing the stack up for S6+):** `0010` adds DB role separation, so
  the gitignored `.env` must now point the app at the **non-owner** role (else the running stack still connects as the
  owner and the append-only grant is a no-op — though CI proves AC#6a regardless). Set
  `DATABASE_URL=postgresql+psycopg://easysynq_app:<APP_DB_PASSWORD>@postgres:5432/easysynq`, keep
  `DATABASE_URL_SYNC` on the **owner** `easysynq` (alembic CREATEs the roles), and add
  `AUDIT_LINKER_DATABASE_URL` (the `easysynq_linker` DSN) + `APP_DB_PASSWORD`/`LINKER_DB_PASSWORD` (matching the
  DSNs) + `S3_BUCKET_AUDIT_CHECKPOINTS`/`AUDIT_SINK_ACCESS_KEY`/`AUDIT_SINK_SECRET_KEY` — see `.env.example`. Then
  `just up s --build` (the `migrate` service runs `0010` as the owner → creates `easysynq_app`/`easysynq_linker`
  before `api`/`worker`/`beat` start as the app role). `minio-init.sh` provisions the `audit-checkpoints` bucket +
  the scoped `audit-sink` user. The `worker`/`beat` containers now run real tasks (the S6 chain-linker/verify/
  checkpoint/roll-partitions Beat jobs + the **S7 mirror reconcile**).
- **S7/S7b/S7c/S7d mirror + rendering + verify + export/print (operator):** the `worker` writes the read-only mirror to
  the `mirror` volume **rw**; `api` mounts it **`:ro`** — the whole R11 contract for the single-host MVP (Caddy must NOT
  `file_server` it; the in-app view route stays the presigned-MinIO `GET /documents/{id}/download`, while **S7d**'s
  `GET /documents/{id}/export` (gate `document.export`) + `GET /documents/{id}/print` (gate `document.print_controlled`)
  **stream** a fresh per-request stamped PDF from the api — `document.export` is granted to no seeded role, so grant it
  via override/custom role until S8's role UI).
  On a network share, validate `root_squash`/UID mapping (runbook caveat). The mirror is **regenerable, never
  backup-critical**, rebuilt on every release/obsolete (post-commit) + a nightly Beat reconcile. Browse it at
  `${MIRROR_PATH}/current/` — **S9b** organizes it as the doc 04 §10.3 **`{PLAN|DO|CHECK|ACT}/{NN-Name}/`** clause tree
  (a doc lives once under its numerically-lowest mapped clause + a relative symlink from every other mapped clause folder;
  a zero-mapping upgrade artifact lands in `_unmapped/`). Plain `sync` rebuilds the whole tree, so the flat→tree
  migration needs no `rebuild` (which only forces re-render). The files are **watermarked controlled-copy PDFs** (S7b:
  gotenberg `renderer` is live; office→PDF + the §11.3 band + a verify QR) with each footer carrying a signed verify token. **S7c `.env` additions (already in
  `.env.example`):** `VERIFY_TOKEN_SIGNING_KEY_PATH=/run/secrets/verify_token_key` + `PUBLIC_BASE_URL=http://localhost`;
  the verify key is **shared api↔worker via the `secrets` volume** (worker mints, api verifies). The public verify page
  is `GET /api/v1/verify?t=…` → CURRENT/SUPERSEDED/UNKNOWN. **After upgrading an existing stack** (so S7b/S7c renditions
  carry the new template/QR), force a full re-render: `docker compose … exec worker python -m easysynq_api.cli.mirror
  rebuild` (clears `rendition_blob_sha256` + re-renders; plain `sync` keeps the cache). The `worker`/`beat` now run the
  S6 audit jobs + the S7 mirror reconcile, and the `renderer` (gotenberg:8.33) must be up for real rendering (a
  renderer outage degrades to `render_status:"pending"` and self-heals on the next reconcile).
- **Dev login:** `demo` / `Demo-Password-1` (created at runtime in Keycloak, **not committed**; realm policy
  requires ≥12-char passwords). After a Keycloak container reset, recreate with `kcadm.sh` (`create users -r
  easysynq -s username=demo -s enabled=true` then `set-password`).
- **First-run setup (S8a) — the primary path now:** a fresh install boots `UNINITIALIZED`, so the **whole `/api/v1/*`
  QMS surface is 423 `setup_incomplete`** until setup finalizes (the latch). Stand it up self-service: (1) operator runs
  **`easysynq setup mint-bootstrap`** (prints a one-time secret); (2) open **`/setup`** in the browser, sign in via
  Keycloak, paste the secret → you become the first **System Administrator** (`setup_state → IN_SETUP`); (3) the wizard
  sets the org profile (legal name / short code / timezone); (3.5 — **S8b**) **Verify storage** (the WORM probe, G-B);
  (3.6 — **S8b2**) **Backup**: set a backup destination, then **Run backup + restore-test drill** — finalize is blocked
  until it PASSES (G-C / AC#5); (3.7 — **S8c**) **Authentication**: pick a login method + ack MFA, then **Verify
  authentication** (G-D — a non-bootstrap login proof + an OIDC-issuer reachability probe); (4) **Finalize** flips
  `→ OPERATIONAL` and the latch lifts (all five gates G-A…G-E now satisfied). After an **upgrade of a
  running install**, `0012` seeds `OPERATIONAL` automatically (a `role_assignment` already exists) — no wizard, no
  lock-out. **NB the operator must point the app at the non-owner DB role for the latch UPDATE to work** (same `.env`
  role-separation as S6).
- **⚠ S8b2 backup/restore drill (operator):** the drill + `pg_dump` run as the **OWNER** role, so the **worker** must
  see `DATABASE_URL_SYNC` (the owner `easysynq` DSN — the same one Alembic uses; already set for S6) in addition to the
  non-owner `DATABASE_URL`. New `.env`/compose: `BACKUP_PATH` (default destination, a mounted `backup` volume on the
  worker) + `S3_BUCKET_RESTORE_SCRATCH=restore-scratch` (a plain non-WORM scratch bucket minio-init provisions). The
  worker image now carries `postgresql-client-16`. Operator CLI (host-side): `easysynq backup run` (write a durable
  archive now) and `easysynq backup restore-test` (run the gating drill; exits non-zero on FAIL) — both dispatch to the
  worker container. The nightly `easysynq.backup.run` Beat job writes durable archives (pg_dump + a MinIO blob
  manifest); the operator-grade **live** WORM-aware restore stays S11.
- **Users & Roles admin (S8d) — the primary in-app path now:** once OPERATIONAL, sign in as a System Administrator
  and open **`/admin/users`** to invite users (paste their Keycloak `sub` — create the Keycloak account out-of-band
  first; they go `INVITED`→`ACTIVE` on first login), assign/revoke the seeded roles, add/remove per-user overrides
  (the R35 two-tier guard applies), and enable/disable accounts (the last active admin can't be disabled). `/admin/roles`
  is a read-only view of the seeded bundles. (Custom-role authoring, bulk-CSV invite, and in-app Keycloak provisioning
  are v1.)
- **Clause IA + mapping (S9) — no UI yet (API/data only):** a fresh/upgraded install now carries the read-only
  ISO 9001:2015 clause spine (seeded by `0018`; **no operator action**). `GET /api/v1/clauses` lists it (gate
  `clauseMap.read`, held by QMS Owner + Internal Auditor — grant it via override for others until the clause-nav UI
  lands). A document must be mapped to **≥1 clause before `submit-review`** (else **422**) — map via
  `POST /api/v1/documents/{id}/clause-mappings {clause_id}` (gate `document.manage_metadata`, held by the lifecycle
  actors), unmap via `DELETE …/clause-mappings/{clause_id}`. Both audited (`CLAUSE_MAPPED`/`CLAUSE_UNMAPPED`). The
  clause-spine nav + mapping UI are deferred (web).
- **Process IA (S9c) — API/data only, no UI:** `GET /api/v1/processes(/{id})(/map)` read the Clause 4.4 process graph
  (gate `process.read`, held at SYSTEM by QMS Owner + Internal Auditor). Authoring — `POST`/`PATCH /processes` (confirm
  `SEED→ACTIVE`), `POST`/`DELETE /processes/{id}/edges`, and `POST`/`DELETE /documents/{id}/process-links` — is gated on
  `process.create`/`process.manage` (the first **held by no seeded role** → grant via override until the role UI, like
  `document.export`) and `document.manage_metadata` for links. `org_role`/`supplier` tables exist but have no authoring
  endpoint yet (owner-assignment + supplier population are deferred). **S9d** then mirrors the links: a process-linked
  Effective doc shows up under `${MIRROR_PATH}/current/by-process/{ProcessName}/` (relative symlinks into the clause tree;
  plain `mirror sync` builds it).
- **Search + Compliance Checklist (S10) — API/data only, no UI:** the org-wide **Compliance Checklist** is
  `GET /api/v1/reports/compliance-checklist` (gate `report.compliance_checklist.read`, now held by **QMS Owner +
  Internal Auditor** after `0021`) — the 20 ★ mandatory clauses with per-clause **COVERED/PARTIAL/GAP** coverage + a
  rollup, computed from PostgreSQL. **Search** is `GET /api/v1/search?q=…` + `GET /api/v1/search/suggest?q=…`
  (authenticated; **filter-not-403** — results post-filtered by `document.read`, with a `hidden_by_scope` count; **over
  Effective documents only**, doc 13's "Effective only" default). Postgres-FTS behind the `Indexer` seam — **OpenSearch
  stays omitted in MVP dev** (R34); `/readyz` must not probe it. `GET /api/v1/documents` now carries `clause_refs` and
  accepts the doc-15 bracketed filters (`filter[clause_refs][has]=8.4`, `filter[current_state][eq]=…`, etc.; unknown →
  400 `unknown_filter`). The web Checklist dashboard + Admin Audit-Log screen + the rest of doc-13 (facets, saved
  searches, dashboards, reports, evidence packs) are deferred.
- **Authz break-glass (`grant-role`):** still available to assign a seeded role directly, bypassing the wizard +
  PEP — `easysynq grant-role <keycloak-subject> ["Role Name"]` (default "System Administrator"; idempotent;
  JIT-creates the `app_user`; runs `easysynq_api.cli.grant_role` as the DB owner). Use it to recover a botched
  bootstrap or to seed the first admin before the UI is reachable.
- **No Docker?** Every slice is still buildable + unit-testable on the uv/3.12 loop; CI runs the stack-dependent
  proofs.

## The four LOCKED foundational decisions (never contradict)

| # | Decision |
|---|---|
| **D1** | **Self-hosted web app.** On the org's own server; browser access; data never leaves their infra; admin-controlled backups; single-organization per install; no phone-home. |
| **D2** | **Managed controlled vault** is the source of truth (PostgreSQL + object storage). Filesystem = read-only mirror, regenerated from Released versions only. Authority flows vault → mirror, never the reverse. |
| **D3** | **ISO 9001:2015 foundation**, *architected* (not built) to extend cleanly to 21 CFR Part 11 e-signatures and multi-standard frameworks (ISO 13485/14001/45001/IATF). Reserved hooks exist (`signature_event`, `framework_id`, M:N clause mapping) — do not implement them in v1, do not remove them. |
| **D4** | **Stack:** React/TS + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 (API) · PostgreSQL 16 + MinIO + OpenSearch + Redis · Celery workers · Keycloak (auth) · Gotenberg/LibreOffice (rendering) · Caddy (TLS) · Docker Compose (single host; S/M/L profiles). |

**Permission philosophy (locked):** hybrid **RBAC + ABAC** — granular `domain.action` permissions,
bundled into org-defined roles, scopable to system/process/folder/document, with per-user overrides
and explicit deny. **Deny-by-default; deny-always-wins.** ADMIN sits *outside* the QMS with full
system permissions. Per a stakeholder decision, the **Quality Manager may hold `permission.grant`
scoped to content domains within QMS scope**; system permissions (user/storage/backup/restore/config/
import) stay admin-only.

## Other stakeholder decisions made this session

- **Import default = current-version-only** (older copies archived as provenance); revision-chain
  reconstruction is opt-in per family; Document-vs-Record *kind* is always human-confirmed.
- **Tamper-evidence requires a mandatory off-host / append-only audit-checkpoint anchor.**
- The full reconcile+harden pass was completed (see `docs/decisions-register.md`).

## Document map (`docs/`)

`decisions-register.md` is **AUTHORITATIVE** — it resolves R1–R37 and **supersedes any conflicting
text** in the section docs. If two docs disagree, the Register wins; otherwise the more specific
section governs (00 §7 explains authority precedence).

- `00-overview.md` — front door: summary, locked decisions, TOC, cross-cutting map, persona×feature matrix
- `01` vision/personas/glossary · `02` ISO domain model & information architecture · `03` architecture & stack
- `04` document control & vault · `05` revision & drift · `06` records & evidence · `07` authorization model
- `08` setup & onboarding · `09` ingestion engine · `10` workflows & notifications · `11` UI/UX design system
- `12` security & audit · `13` search & reporting · **`14` data model (ERD)** · **`15` API design**
- `16` roadmap (MVP → v1 → v1.x → Future) · `17` gaps & open-questions (with per-finding resolution status)

## Conventions used throughout the spec

- **Document lifecycle = 7 canonical states:** `Draft → InReview → Approved → Effective →
  UnderRevision → Superseded → Obsolete` (the 5-state form is a simplified UI view).
- Permission keys are `domain.action` (canonical catalog in `docs/07`; data-model seed in `docs/14 §3.1`).
- 8 canonical personas: Avery (Admin), Mara (Quality Manager), Diego (Process Owner), Priya (Author),
  Ken (Approver), Ingrid (Internal Auditor), Sam (Employee), Olsen (External Auditor).
- `signature_event.meaning` enum (v1): `review, approval, release, obsolete, verify, disposition,
  import_baseline, review_confirmed`; `authored`/`responsibility` reserved for the Part-11 phase.

## Working preferences

- **Spec/plan before code.** Produce and get approval on a plan before implementing.
- The owner used **`/effort ultracode`** (multi-agent Workflow orchestration) for the heavy
  spec/mockup work; `/effort` is per-session, so re-enable it if you want that approach again.
- When a genuinely strategic decision is the owner's to make, ask rather than silently pick.
- Persistent memory: `~/.claude/projects/-home-cojoa13-Documents-EasySynQ/memory/` (MEMORY.md index).

## How to view the mockup

`mockup/easysynq-mockup.html` — open in a browser (e.g. `xdg-open mockup/easysynq-mockup.html`).
This laptop has **no headless browser**, so PNG screenshots can't be auto-generated here; install one
(e.g. `chromium-browser`) if static images are wanted.

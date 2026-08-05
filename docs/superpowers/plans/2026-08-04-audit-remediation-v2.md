# Audit remediation programme v2 — evidence-corrected, implementation-ready

> **Status:** planning pass complete; the decision-independent implementation batch is recorded in §9.
> The remaining programme still requires its named decisions and later implementation slices.
> **Supersedes:** `2026-08-04-audit-remediation.md` (v1, preserved as the historical validation trail;
> only its raw-evidence boundary note was clarified for publication).
> **Revision brief:** `2026-08-04-audit-remediation-codex-handoff.md`.
> **Source audit provenance:** local-only `audit-results/2026-08-03/AUDIT.md` (revision `376ec1e`). Raw
> authenticated/machine evidence is intentionally ignored and absent from Git under R61; the three
> committed documents are the sanitized synthesis and review trail, not a replacement for that evidence.
>
> v1 validated the audit correctly and then sequenced it badly. This revision accepts every
> non-negotiable correction in the handoff, and adds the corrections that the evidence work turned up
> against the handoff itself.

---

## 0. What changed from v1, and why

The handoff's disposition was **REVISE**, on seven counts. All seven are upheld — each was re-verified
against source before being accepted, not taken on authority:

| # | Correction | Verified how |
| --- | --- | --- |
| 1 | **M-01 was absent from the programme.** | `rg -c "M-01"` over v1 → no match. Restored as a named co-owner of the recovery slice. |
| 2 | **NEW-04…07 are contradicted by tests that run in CI.** | The cited files exist and contain 13 workflow tests, real owner-role chain tamper, real MinIO mirror quarantine, real R27 dual-control. **Refuted as phrased.** |
| 3 | **The Celery premise is wrong.** | `task_reject_on_worker_lost=None` ⇒ `WorkerLostError` is *acked*. Hard-kill redelivery is not routine. Measured: 25 beat entries, 14 task modules, 35 registered names. |
| 4 | **NEW-03 overstated.** | The realm ships brute-force protection, a 12-char floor, PKCE, temporary-password first login, and inherits working Keycloak defaults. Split into 03a/03b/03c. |
| 5 | **`blob` omitted from S-db-grants.** | v1 named it in prose and dropped it from the slice. All five tables re-confirmed unrestricted. |
| 6 | **D-D asked a question already answered.** | `services/workflow/service.py:203-206` already binds `signed_object_id` **and** `content_digest`. |
| 7 | **H-10 attached at the wrong boundary.** | `DcrCreate.proposed_effective_from: datetime` accepts any instant and the service persists it unchanged. A JS helper fixes one client. |

Plus the structural defects: duplicate primary ownership (M-27 ×2, M-32 ×2, L-09 ×3), and Wave 4 as a
sizing table rather than a plan. Both fixed below.

### 0.1 Corrections that run the other way

The evidence work turned up three points where the handoff is itself imprecise. Recording them so the
ledger is not built on them:

- **Three of five cited ranges are wrong at the edges.** `test_workflow_engine_domain.py:122-153`
  contains only the four quorum tests — *not* distinct approvers, cycles, replay or concurrency (those
  are at lines 78/105 and integration-only). `test_workflow_engine.py:228-519` cuts a test mid-body.
  `drill.py:331-421` does **not** contain the single-current-key limitation; that is `restore.py:257-260`.
  The conclusions survive; the citations should not be copied forward verbatim.
- **The citation for the audit chain *undersells* it.** The strongest test in the subsystem —
  `test_checkpoint_signature_catches_consistent_chain_rewrite` (`test_audit.py:554`), where the DB owner
  rewrites a row *and recomputes `row_hash`* so the walk alone passes and only the Ed25519 checkpoint
  catches it — sits outside the cited range.
- **Two "verification gaps" are product gaps, not test gaps.** `AppUser.session_invalidated_at` has
  **zero writers** in `src/` (M-03 is unreachable dead code, not untested code), and the backup archive
  contains no object bytes (C-01). Precisely: **a test cannot *close* either gap — but a failing test can
  certainly *demonstrate* it**, and this plan proposes exactly such a RED proof for both (§3,
  `S-recovery-generation` and `S-session-revocation`). The distinction matters because "untestable" would
  wrongly excuse them from the RED-against-HEAD rule; they are subject to it.

### 0.2 The planning fact that changes how M-01 must be proven

`test_durable_backup_without_key_omits_sensitive_legs` (`tests/integration/test_restore.py`)
asserts
`encrypted is False`, `archive.endswith(".tar")`, `legs["realm_export"] == "absent"`,
`legs["config_snapshot"] == "absent"` — and still treats the archive as a success.
**The suite pins M-01's fail-open behaviour as intended.** (Precisely: the test pins the *successful
return* plus the archive/leg shape; it does not itself assert `verified is True`. That value comes from
production's `verify_archive` at `drill.py:415-422`. The pinned behaviour is the degraded-but-returned
archive, which is the part remediation must change.)

Be precise about *which part* is wrong, because the test's stated rationale is sound: omitting the
secret-bearing legs from a plaintext archive is genuinely better than writing them in cleartext
(doc 12 §6.2), and that half must survive. The defect is narrower — **a degraded archive is returned as
a success**, so the operator is told they have a backup that cannot recover identity or configuration.

So M-01's remediation must **change a passing test**, not add a failing one. The handoff's blanket
"require a test observed RED against HEAD" does not apply cleanly here, and pretending otherwise would
produce a manufactured proof. The honest acceptance is: this test is inverted to assert that the backup
is *refused* when no key is configured, with the cleartext-omission rationale preserved in its docstring,
and the inversion mutation-verified against the old behaviour. Called out per-slice wherever it recurs.

---

## 1. Deliverable 1 — the complete finding ledger

**Mechanical assertion:** all 57 original IDs (C-01, H-01…H-12, M-01…M-33, L-01…L-11) appear exactly
once as a primary owner. Umbrella rows (NEW-01, NEW-03) are marked *superseded* and own nothing.

Legend — **Status:** `C` confirmed · `P` partial · `R` refuted-as-phrased · `K` known residual.
**RB** = release-blocking.

Ledger claims describe the audited `376ec1e` baseline. Sections 8–9 record the subset implemented on
this branch; in particular, NEW-09's hidden-lockfile condition no longer exists in the publication diff.

### 1.1 Integrity and recovery

| ID | Corrected claim | St | Sev | RB | Existing CI evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **C-01** | The archive carries `db.dump` + manifest + optional legs, never object bytes; restore/drill copy from the **live** buckets (`drill.py:228-235`), so no drill can prove host/vault-loss recovery. | C | **Crit** | ✅ | `test_backup.py` (15) + `test_restore.py` (9) — real `pg_dump`/`pg_restore`, real MinIO, nothing mocked; corrupted-blob and corrupted-chain both caught | No test blocks source-vault access before restoring — structurally impossible today | D-B | `S-recovery-generation` |
| **C-01b** | The standing target lands objects flat at `{restore_id}/{sha}` while restored `blob` rows keep `(bucket, object_key)`; ~12 read paths resolve the stored literal. | C | **Crit** | ✅ | none | Cutover cannot serve a byte; runbook step 4 specifies neither prefix strip nor the 3-bucket re-split | D-B | `S-restore-target` |
| **C-01c** | `_rehash_scratch_blobs` verifies the **scratch namespace it just wrote** (`Key=f"{prefix}{sha}"`), never the `(bucket, object_key)` pair the restored database actually names. So the G-C gate certifies "the copied bytes are intact" while the runbook reads it as "the restored system resolves". ⚠ **Not** a tautology — see delta. | C | High | ✅ | `test_restore.py:168-194` corrupts a copied scratch object post-copy and the triad correctly returns `FAIL` — byte corruption **is** caught | Namespace **resolvability** after cutover is never asserted; a restored `blob.object_key` that no longer resolves passes the triad | — | `S-restore-target` |
| **M-01** | Realm/config/checkpoint legs fail open; no key ⇒ **plaintext** archive with the identity legs omitted, reported `verified`; `decrypt_archive` parses `key_ref` then ignores it, so rotation strands every prior archive. | P | High | ✅ | **a passing test pins this as intended** (§0.2) | Mandatory-leg concept does not exist in the code; zero rotation coverage | D-B | `S-backup-legs` |
| **NEW-01** | *(umbrella — superseded by 01a–01e)* | — | — | — | — | — | — | — |
| **NEW-01a** | `worm_lock_period` is declared, validated ≥ duration, serialized — and never reaches storage. `rg "put_object_retention\|ObjectLockRetainUntilDate"` returns only the R27 bypass. Every object gets the flat 30-day bucket default. | C | High | ✅ | `test_records_disposition.py` (31) proves physical deletion against real MinIO with real object lock | **No test asserts a per-object retention date to a value**; `_max_worm_retain_until`'s MAX is unfalsifiable (every object has the same default) | D-A | `S-worm-retention` |
| **NEW-01b** | Document **source bytes** have no retention policy at all — `document_type.default_retention_policy_id` governs records produced under the type, not the version. D2's "vault is the source of truth" has a 30-day storage half-life. | C | High | ✅ | none | No policy model reaches `document_version` | D-A | `S-worm-retention` |
| **NEW-01c** | `minio-init.sh:29-30` hardcodes `GOVERNANCE`; `storage_config.object_lock_mode` is read only at `disposition.py:573` to **refuse** R27. Selecting COMPLIANCE disables the erasure hatch while the bucket stays GOVERNANCE — strictly worse than either mode. | C | Med | — | COMPLIANCE refusal is tested — but as a **DB flag**, never against a real COMPLIANCE bucket | Mode coherence is never checked | D-A | `S-worm-retention` |
| **NEW-01d** | `update_policy` ratchets `duration`/`disposition_action`/`review_required` but **not** `worm_lock_period`, which may also be explicitly nulled. | C | Low | — | `test_retention_policies.py` (9) covers the three ratcheted fields | Latent today; a lock-shortening path the moment 01a lands | — | `S-worm-retention` |
| **NEW-01e** | The G-B setup probe asserts the bucket default exists and a no-bypass delete is denied. It cannot detect that per-object retention is never set, and probes `documents` only — `records` is never probed. | C | Med | — | `test_setup.py` G-B | Gate proves less than the runbook claims | — | `S-worm-retention` |
| **H-03** | `presign_put` writes client bytes to key `{claimed_sha}`; `_finalize_sync` does `head_object` then `copy_object`. Nothing re-hashes, before or after promotion. `storage.py:12-14` says so verbatim. | C | High | ✅ | every integration fixture is an **honest client** — the case is never tested | **No test uploads mismatched bytes and asserts rejection.** Same on the records path. Same-size wrong bytes defeat the only check (`ContentLength`) | D-C | `S-upload-identity` |
| **H-02** | `0010` grants the app role full DML on all current *and future* tables. 13 tables carry targeted REVOKEs; `blob`, `document_version`, `record`, `evidence_blob`, `import_decision` carry none. | C | **High** | ✅ | `test_audit.py:141` proves the app role *is* blocked on `audit_event`/`signature_event` (real SQLSTATE 42501) | No equivalent proof for the five content tables | — | `S-db-grants` |
| **L-01** | Audit partition creation races between Gunicorn workers; a live start produced a `DuplicateTable`-class error. | C | Low | — | `test_partitions.py` (2), `test_partition_runway.py` (1) | No concurrent-creation test | — | `S-db-grants` |
| **NEW-02** | `run_upgrade`'s stage-1 catch is `BackupError` only; the `verified` flag is never read, so it migrates over a checksum-failed archive; and the **outer `try:` has only a `finally:`, no `except`** — `_alembic_head`, destination lookup, `_emit`, `commit` and the health check all escape the `"""Never raises"""` contract. The exact guard exists at `service.py:191-209` and was never propagated. | C | High | ✅ | one test, which stubs everything green and asserts `result == "OK"` | Every failure branch; single-flight; orphaned-`STARTED` reconciliation | D-B | `S-upgrade-safety` |
| **L-03** | Orphan windows around post-object/pre-commit and post-commit task failure. | C | Low | — | partial (reaper paths) | Staging buckets have no lifecycle expiry | — | `S-orphan-reconcile` |
| **K-lock** | `easysynq upgrade` has no `lock_timeout`/`statement_timeout`; a migration can convoy the write path. | K | Med | — | none — CI round-trips an empty single-connection DB | Pre-existing residual (`slice-history.md:107-123`), folded in here rather than left dangling | — | `S-upgrade-safety` |

### 1.2 Release trust

| ID | Corrected claim | St | Sev | RB | Existing CI evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **H-08** | Every `images.lock` ref is a mutable tag; `test_images_lock_pinned.py:50-53` skips unless `EASYSYNQ_RELEASE=1`, which no workflow sets. The four built images have **no stable identity anywhere in the repo** — no `image:` key on any of the six built services. | C | **High** | ✅ | `compose-images-lock` verifies membership only | Nothing addresses the built images at all | D-E | `S-build-identity` |
| **H-04** | `airgap-bundle.sh:16` reads only `images.lock` (9 upstream refs). `install.sh:186` runs `up -d --build`, and the API Dockerfile fetches the PGDG key over HTTPS while the web Dockerfile runs `npm ci`. **An offline host cannot build these at all** — the advertised air-gap path has never worked. | C | **High** | ✅ | none | No offline smoke test exists | D-E | `S-offline-install` |
| **H-06** | `easysynq upgrade` runs `cli.upgrade` inside the **already-installed** worker. It cannot apply a new migration — the migration files live in the running image. Separately, `scripts/easysynq:7` uses base `compose.yml` alone and every `run --rm` omits `--no-deps`. | C | High | ✅ | none | Deployment is unproven end to end | D-E | `S-deploy-rollback` |
| **H-01** | One `.env` reaches migrate/api/worker/beat; no `USER` in either Dockerfile; both images run root. One web-facing compromise yields DB owner + MinIO root + Keycloak admin + backup key + checkpoint signing key. | C | High | ✅ | **none — H-01 is untested.** `test_deploy_configuration.py:44-56` is *adjacent* only: it string-matches five audit-sink variable **names** in Compose and asserts nothing about secret isolation, credential absence, or runtime user | Everything: per-service isolation, non-root runtime, effective container identity | D-F | `S-container-identity` |
| **M-31** | Web is a single-stage Node image served by `vite preview`, shipping dev deps and npm tooling. | C | Med | — | none | — | — | `S-container-identity` |
| **M-04** | First-use Ed25519 share-key generation races between concurrent creators. | C | Low | — | `test_verify_token.py` (5); the ephemeral-key fallback is `# pragma: no cover` | Race untested | — | `S-container-identity` |
| **M-30** | Actions on mutable tags; implicit workflow permissions; persisted checkout credentials; floating Redocly/pip-audit/hatchling/PGDG inputs; `uv sync` without `--frozen`. | C | Med | — | none | — | — | `S-build-identity` |
| **H-07** | Three `exit-code: "0"` Trivy steps and ignored `pip-audit`/`npm audit` status. `cryptography 48.0.0` and `starlette 1.2.1` carry fixed High advisories; `uv lock --dry-run` resolves `50.0.0`/`1.3.1` cleanly. | C | Med | — | `security` job runs but is **warn-only and not a required check** | Reachability re-verified: no `request.form`/`Form`/`File`/`UploadFile`; no RSC imports | D-G | `S-image-ratchet` |
| **H-05** | Docs describe LUKS, SSE-S3 and envelope controls; Compose enables none. `APP_MASTER_KEK` has no runtime consumer. | C | Med | — | none | — | D-F2 | `S-doc-truth` |
| **M-32** | No release SBOM, OCI signature, provenance attestation, signed release, third-party notices, or root `LICENSE`/`SECURITY.md`. | C | Med | — | none | — | D-E2 | `S-governance-artifacts` |
| **M-33** | Docs contradict source on branch protection, CI job count, register range, the removed clause rail, account creation, checkpoint cadence, toolchain, and version. 59/202 `docs/superpowers` links broken. | C | Med | — | `check-no-site-data.sh` in the `contracts` job (R61 only) | No link check; no doc-truth gate | D-H | `S-doc-truth` |

### 1.3 Authentication

| ID | Corrected claim | St | Sev | RB | Existing CI evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **NEW-03** | *(umbrella — superseded by 03a–03c)* | — | — | — | — | — | — | — |
| **NEW-03a** | The realm **does** ship brute-force protection, a 12-char floor, PKCE, no direct grants, and inherits working defaults (300 s / 1800 s / 36000 s, live-read). Confirmed drift: no refresh-token rotation, no password history, no breached-password list, production keeps `localhost/*` wildcards + `webOrigins: ["+"]`, and auth/admin event logging is off. **Inheriting a default is itself the defect** — unversioned, undetectable, and never re-asserted (import is first-boot-only). | C | High | ✅ | **no test asserts any realm value**; `test_temp_password.py` validates a hand-maintained re-implementation | Everything above; drift is unobservable | D-I | `S-auth-baseline` |
| **NEW-03b** | `app_user.mfa_enrolled` is rendered at `UsersAdmin.tsx:118` and has **no writer that ever sets it true** — the admin surface reports "no MFA" for every user, including one who has enrolled TOTP. No realm-wide enrollment policy. | C | Med | — | none | A wrong claim rendered in-product | D-I | `S-mfa-enrollment` |
| **NEW-03c** | `POST /auth/step-up` returns a hard-coded `enforced: false`; eight signature sites write the **literal** `auth_context={"acr":"SESSION"}`, which is hashed into the append-only chain. A constant encoded identically to an observation. | K | Info | — | none | Deferred by D3 (Part 11 architected, not built) — recorded so the deferral is not later mistaken for an implementation | — | *(deferred; documented in `S-auth-baseline`)* |
| **M-03** | Session watermark requires `iat`, which `tokens.py:54` does not require. **`session_invalidated_at` has zero writers in `src/`** — the revocation branch is unreachable dead code, not merely untested. No Keycloak session-revocation call exists anywhere. | C | Med | — | `test_users_admin.py` proves a DISABLED user's token is rejected — via `AppUser.status`, not the watermark | The entire feature | — | `S-session-revocation` |

### 1.4 User-facing correctness

| ID | Corrected claim | St | Sev | RB | Existing evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **H-09** | `ReviewApprovePage.tsx` renders `VersionCompare` as its only content context, and that returns `null` below two versions. **Scope correction:** `GET /documents/{id}/versions/{vid}/download` already exists and is gated — this is UI wiring, not a missing capability. **Second correction:** the signature already binds `signed_object_id` + `content_digest`. | C | High | ✅ | none for the approval surface | Content is unreachable in the task flow; no fail-closed behaviour while loading/forbidden/changed | D-D | `S-approval-content` |
| **L-07** | Async presign then `window.open` is popup-blockable with no recovery. **Promoted out of Wave 4:** the approval gate depends on this action. | C | Low | ✅ | none | — | — | `S-approval-content` |
| **M-22** | The release invalidator omits the distribution query, so an Effective document reads "Not yet effective" until reload. **Split from approval** per the handoff. | C | Low | — | none | An unawaited invalidation can still race the document refetch | — | `S-release-consistency` |
| **H-10** | `DcrRaiseFields.tsx:32-35` sends `${date}T00:00:00+00:00`, **and** `DcrCreate.proposed_effective_from: datetime` accepts any instant which the service persists unchanged. The authoritative boundary is the API. | C | **High** | ✅ | none | Every non-UTC org stores governed calendar dates at the wrong instant | — | `S-calendar-contract` |
| **M-14** | Governed-date rendering is inconsistent across routes despite R56/Batch 15 unifying the backend. | C | Med | — | partial | — | — | `S-calendar-contract` |
| **H-11** | `auth.tsx:71-97` has no catch/finally; `App.tsx:85-90` shows only a loader; `operational` is computed only from **successful** setup data, so a 503 routes an operational deployment to `/setup`. | C | Med | — | `auth.test.tsx` (11) covers renewal + a 7-case open-redirect guard | No failure/terminal/retry states | — | `S-auth-shell` |
| **M-08** | Only `addUserLoaded` is subscribed; no `silentRenewError`/`accessTokenExpired`/`userSignedOut`. | C | Med | — | none | — | — | `S-auth-shell` |
| **L-11** | No error boundary (`rg ErrorBoundary\|componentDidCatch\|errorElement` → 0) and no not-found route. | P | Low | — | none | react-router is in declarative mode, so `errorElement` is genuinely unavailable | — | `S-auth-shell` |
| **H-12** | Close predicates cover in-flight only; a route change, history navigation, reload or tab close destroys an applied credential. **Severity down:** `POST /users/{id}/temporary-password` reissues, so the cost is an admin round-trip, not a lockout. | C | Med | — | none | Browser guards cannot solve a process crash — must be stated, not implied | — | `S-secret-capture` |
| **L-08** | Clipboard failure is silent. | C | Low | — | none | — | — | `S-secret-capture` |
| **M-11** | Affordances probe at SYSTEM scope while the server enforces at resolved ABAC scope. **Correction:** a test walking `require()` misses dynamic `enforce()`, resolver identity, composite authority and SoD state. | C | Med | — | none | Needs a server-owned action manifest or persona contract tests | D-J | `S-capability-truth` |
| **M-21** | Ordinary shell/deep-link mounts generate repeated forbidden dashboard queries; the persona journey added 70 durable `ACCESS_DENIED` rows to an append-only chain. | C | Med | — | none | — | D-J | `S-capability-truth` |
| **M-10** | Supporting-data query errors render as legitimate empty data. **Split out** of the authorization slice. | C | Med | — | none | — | — | `S-query-errors` |
| **M-12** | CAPA/DCR/Improvement drawers do not clear selection on history change. **Split out.** | C | Low | — | none | — | — | `S-url-selection` |
| **M-07** | Only legal name is hydrated from persisted setup state. | C | Med | — | **`SetupWizard.tsx` has no test file at all** | — | — | `S-setup-resume` |
| **M-09** | Restore-test polling stops or fails to resume across error/reload. **Correction:** needs a persisted job ID/status contract; snapshot hydration cannot fix it. | C | Low | — | none | — | — | `S-restore-job-recovery` |
| **M-13** | Background refetches re-run hydration and overwrite open-form edits. | C | Low | — | none | — | — | `S-dirty-form` |
| **L-10** | Check-in feedback is weak; raw `InReview`; first release says it supersedes a current version. **Three separate boundaries**, split into `S-checkin-feedback` (primary owner) + `S-lifecycle-copy` + `S-release-copy` (remainder references, not co-owners). | C | Low | — | none | — | — | `S-checkin-feedback` |

### 1.5 Edge, protocol, accessibility

| ID | Corrected claim | St | Sev | RB | Existing evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **M-15** | Caddy CSP is `img-src 'self' data:` while `VisualDiffViewer.tsx:54` mints a `blob:` URL — **the visual diff renders nothing behind the shipped edge**. **Severity: Medium** per the handoff (urgency ≠ severity), and **not release-blocking**: visual diff is a convenience surface, not a control. It is a *ship-early* fix because it is one token, not because it gates a release. | C | Med | — | `test_caddy_headers.py` scans the committed Caddyfile | No browser-level CSP coverage; jsdom cannot see CSP | — | `S-edge-headers` |
| **M-24** | Token-bearing responses lack `no-store`; the site-wide `Referrer-Policy` overwrites the intended `no-referrer`. | C | Med | — | static header tests only | Final-edge headers unproven | — | `S-edge-headers` |
| **M-05** | `FORWARDED_ALLOW_IPS` unset ⇒ gunicorn trusts loopback only, so ~18 `request.client.host` reads see Caddy. `ip_allow` grants and R58's replayed `source_ip` compare against the proxy. | C | Med | — | none | — | — | `S-proxy-trust` |
| **L-06** | Dev edge is a catch-all `:80`; spoofed Host reaches redirects; exact `/api` falls to Vite → 502. **Partial:** KC_HOSTNAME is pinned and redirect URIs are constrained, so the dangerous sink is closed. | P | Low | — | none | Routing nit is real; Host injection is inert | — | `S-proxy-trust` |
| **M-23** | Unauthenticated Swagger UI loads executable JS/CSS from jsDelivr with no CSP/frame denial — against D1 (no phone-home) and broken air-gapped. | C | Med | — | none | — | — | `S-api-docs` |
| **M-25** | No edge rate or body limits; public `/readyz` fans one request across five dependencies. **Correction:** the stock Caddy image has no rate-limit module. | C | Med | — | none | `request_body` is stock; `rate_limit` needs a custom build | D-K | `S-abuse-liveness` |
| **M-26** | Readiness omits web/worker/beat/renderer/Tika; several dependencies rely on start ordering. | C | Med | — | `readiness.py` probes 5 deps | Worker/beat liveness unobservable | — | `S-abuse-liveness` |
| **L-04** | Caller request IDs echoed verbatim; invalid UUIDs become null audit correlation; closed-latch 423 has no request ID. | C | Low | — | none | — | — | `S-api-protocol` |
| **L-05** | `problems.py:145-157` maps every Starlette exception except 404 to `internal_error` and drops `exc.headers`, losing `Allow` and blocking `WWW-Authenticate`. | C | Low | — | none | — | — | `S-api-protocol` |
| **M-18** | 342 violating nodes, 28/28 routes, both themes, against a stated WCAG 2.2 AA bar. **Severity: High.** | C | **High** | ✅ | 1,458 web tests incl. jest-axe — **which cannot compute browser colour contrast** | Needs production-browser checks | D-L | `S-contrast` |
| **M-19** | Tabs-as-navigation generate `aria-controls` to missing panels on 11 routes. | C | Med | — | jest-axe (misses this) | — | — | `S-a11y-semantics` |
| **M-20** | `aria-label` on a generic div; 17 routes without `h1`; keyboard handlers on non-actionable `tr`; empty table header; duplicate banner landmarks. | C | Med | — | jest-axe (partial) | — | — | `S-a11y-semantics` |
| **M-16** | The 390 px nav drawer stays open after selecting a route. | C | Med | — | none | — | — | `S-responsive` |
| **M-17** | Admin Users is 626 px in a 390 px viewport; the 320 px top bar overlaps. | C | Med | — | none | — | — | `S-responsive` |

### 1.6 Verification deltas, contracts, and depth

| ID | Corrected claim | St | Sev | RB | Existing CI evidence | Remaining delta | Own | Slice |
| --- | --- | :-: | :-: | :-: | --- | --- | :-: | --- |
| **NEW-04** | *"Audit chain never verified/tamper-tested."* **REFUTED.** Real owner-role tamper, real MinIO checkpoint push/read-back/staleness/ghost, and a consistent-chain-rewrite test the checkpoint alone catches. No false-PASS-by-stubbing anywhere. | R | — | — | `test_audit.py` (7) + 45 unit tests | *(see NEW-04a)* | — | — |
| **NEW-04a** | Narrowed: no test **deletes or reorders** a chained row, so `verify.py`'s reorder/deletion branch is unreached; the D-8 write/read sink credentials are never distinct in any run; checkpoint objects carry no asserted object-lock retention; `audit_chain_lag_alarm_seconds` and all four `tasks/audit.py` wrappers are untested; no checkpoint-**history** coverage. | C | Med | — | — | as stated | — | `S-audit-tail-policy` |
| **NEW-05** | *"Mirror/D2 never exercised."* **REFUTED.** Real tamper→quarantine→rescan-CLEAN, real whole-tree replacement incl. stray removal, Effective-only proven by asserting *absence*. | R | — | — | `test_mirror.py` (18) + `test_mirror_scan.py` (15) + 110 unit tests | *(see NEW-05a)* | — | — |
| **NEW-05a** | Narrowed: **no test anywhere runs a real Gotenberg/LibreOffice conversion** — every path is PDF-passthrough or `MockTransport`, and CI provisions no Gotenberg. **The QR payload is never decoded** — both tests assert only that *some* image XObject exists, so a QR encoding a wrong URL or stale token passes. | C | Med | — | — | as stated | — | `S-renderer-proof` |
| **NEW-06** | *"R27 never exercised."* **REFUTED.** Dual control, real physical deletion against real object-locked MinIO with the blob-row-iff-bytes invariant, real advisory-lock blocking proven via `pg_locks`, real reaper recovery. | R | — | — | `test_records_disposition.py` (31) | folded into NEW-01a | — | — |
| **NEW-06a** | Narrowed: "crash recovery" kills no process — the DB state is hand-constructed. COMPLIANCE is a DB flag never exercised against a real COMPLIANCE bucket. | P | Low | — | — | as stated | — | `S-celery-contract` |
| **NEW-07** | *"Workflow negative paths untested."* **REFUTED.** 15 integration tests: quorum, distinct-approver 409, early-fail, fail-closed at instantiate, router + cycle guard, idempotent replay, and a genuine two-session race. Zero monkeypatching. | R | — | — | `test_workflow_engine.py` (15) + 39 unit tests | *(see NEW-07a)* | — | — |
| **NEW-07a** | Narrowed: no **empty** candidate pool test (self-documented as "rides code review"); `_route()`'s own fail-closed check untested; `_resolve_pool`'s `context_users` union untested; no `populate_existing` two-session proof for `Task`/`WorkflowInstance`. | C | Low | — | — | as stated | — | `S-workflow-edges` |
| **NEW-08** | Corrected: `task_acks_late=True` alone does **not** make hard-kill redelivery routine (`task_reject_on_worker_lost=None` ⇒ `WorkerLostError` is acked). The real defect is **no declared per-task delivery contract**: some failures strand work, connection loss can duplicate. Measured: 25 beat entries, 14 modules, 35 names. | C | Med | — | 11 registration files (~34 tests) — **name-in-dict pins, not behaviour**; sweep idempotency well covered | **No test calls `.delay`, kills a worker, or loses the broker**; 12 of 14 modules' `asyncio.run`+fresh-engine plumbing never executes | D-M | `S-celery-contract` |
| **M-28** | 19 operations lack documented 4XX; `gen-contracts.sh --check` checks a **lock hash, not generated authority**; no CI job runs it; the contract-response assertion accepts any non-2xx (204 of 283 returned non-2xx). | C | Med | — | `contract-responses` (283 ops) | Intended-status assertions absent | D-N | `S-contract-authority` |
| **M-29** | Two integration tests skip when the shared DB is contaminated. **Correction:** do not isolate the DB — that breaks the single-org invariant. Self-provide the precondition. | C | Med | — | — | — | — | `S-test-isolation` |
| **COV-TABLE** | Corrected: **CI measures no coverage at all.** The 53%/22%/19% figures come from a local gitignored `.coverage` dated 2026-08-04. The low module numbers are unit-only and those modules are integration-covered — except `upgrade.py`, whose 0% concealed NEW-02. | P | Low | — | none | No coverage config, no threshold, no combined figure | D-O | `S-coverage-policy` |
| **M-02** | Presigned uploads have no size/checksum ceiling; some fetches unbounded; several paths buffer whole objects. **Split from H-03** per the handoff. | C | Med | — | none | — | — | `S-transfer-limits` |
| **M-06** | Downloads go through presigned URLs, so the app records authorization but cannot prove delivery. | C | Med | — | none | Design decision before it is a patch — proxying contradicts D1 | D-P | `S-download-accountability` |
| **L-02** | The share-link download counter is an unlocked ORM read-modify-write, incremented before byte fetch. | P | Low | — | none | Lost update is real; escalation paths closed | — | `S-download-accountability` |
| **L-09** | Two independent full-file materialisations (`hash.ts:5`, `upload.ts:8`); no client size guard. | P | Low | — | none | The proposed Blob assertion passes over the broken code — must be rewritten | — | `S-web-transfer` |
| **M-27** | CI does not build/scan the exact web image, run install/air-gap smoke, exercise the m-profile, lint shell scripts, or run browser E2E/a11y. **Single owner** (was duplicated). | C | Med | — | 9 jobs / 12 checks | — | — | `S-ci-gaps` |
| **NEW-09** | `.gitattributes:9-10` sets `uv.lock -diff` and `package-lock.json -diff`, so the two files carrying the entire dependency surface produce **no reviewable diff** — in `git diff`, `git show`, or any PR UI honouring gitattributes. A transitive change, intended or not, is invisible at review time. Compounds H-07: advisories do not block *and* the change is unreadable. Found while verifying the `cryptography`/`starlette` bump — the claim "only two packages moved" required `git diff --text` to prove; overriding `core.attributesfile` does not disable the repository's `.gitattributes`. **The right tool is already used three lines above in the same file:** `linguist-generated=true` collapses a diff in GitHub's UI but keeps it expandable and keeps `git diff` working; `-diff` removes it entirely. | C | Med | — | none | — | — | `S-image-ratchet` (same supply-chain review surface) |

**Count check:** C-01, H-01…H-12, M-01…M-33, L-01…L-11 = 57 primary rows, each appearing once.
Sub-IDs (C-01b/c, NEW-01a–e, NEW-03a–c, NEW-04a, NEW-05a, NEW-06a, NEW-07a) and `K-lock`, `COV-TABLE`
are additional traceable rows; the two umbrellas own nothing.

---

## 2. High-risk designs — decision-ready summaries

Full analysis is retained in the working record; these are the decisions.

### DR-1 · WORM ownership and retention → **decision D-A**

**Threat boundary, stated so severity is honest.** GOVERNANCE object lock protects against the
application itself (a bug, a compromised `api`/`worker`, an authenticated user), accidental `mc rm`, and
ransomware using the app's S3 credentials. It does **not** protect against a MinIO-root principal. Under
H-01's shipped topology — api/worker/beat all receive the full `.env` and run as root — those are the
**same principal**. So NEW-01 is **High, not Critical**, as a security finding; it is release-blocking as
a *control-effectiveness and truthfulness* finding, because four separate documented guarantees are
inert. **NEW-01a and H-01 should ship as a pair**; the credential split buys more real immutability than
the retention work does.

- **D-A1 — GOVERNANCE vs COMPLIANCE.** Recommend **GOVERNANCE only in v1**, with COMPLIANCE removed
  from the product surface until it is coherent. COMPLIANCE does not make erasure harder — it makes R27
  *impossible for every principal including root*, so a mis-imported PII record becomes a decade-long
  unremediable breach. Either way, NEW-01c must be fixed in the same slice: apply the recorded mode, or
  remove the column and its wizard control. A recorded-but-unapplied mode that silently disables R27 is
  unacceptable in both directions.
- **D-A2 — anchor.** `retention_basis_date + worm_lock_period`, with a mandatory floor
  `max(current_object_retain_until, computed)`. The floor is load-bearing: `event:*` bases have no anchor
  at capture (use `captured_at` provisionally and extend later), and a backdated basis would otherwise
  compute a past date that S3 rejects, silently leaving the bucket default.
- **D-A3 — where it lives.** **Not** in `finalize_worm`. Two provable reasons: it receives no owner (policy
  resolution happens in `capture_record`), and `_attach_evidence` calls it **only when `blob is None`**, so
  a second record attaching identical bytes makes zero storage calls and inherits the first owner's lock.
  Retention is a property of the *(object, owner-set)* edge. New `services/vault/object_retention.py`
  with `ensure_object_retention(session, bucket, object_key)` — idempotent, monotone, called under the
  existing `lock_physical_object`, **before** the owner row commits (an over-locked orphan is harmless;
  a committed owner with no lock is a silent hole).
- **Simplification that makes it tractable:** cross-domain blob reuse is already refused symmetrically in
  three places, so the owner set is single-domain by bucket. "Max across every live owner" collapses to
  one indexed query.
- **The test that proves the design** (not just the parameter): capture record A under `P3Y`, then record
  B with **byte-identical** evidence under `P10Y`; assert the object carries `P10Y`. Mutation-verify by
  moving the call inside the `if blob is None:` branch. A `finalize_worm`-only implementation cannot pass it.

### DR-2 · The atomic recovery set → **decision D-B**

One **generation** with mandatory legs — database, object bytes, realm, config, checkpoint, manifest,
digests, key ID — visible as complete only after every leg verifies. **There are no optional legs**; that
is M-01's fix stated as a property.

- ⚠ **CORRECTED after Codex checkpoint B — the original premise was wrong.** The first draft argued "no
  quiesce is needed because objects are content-addressed and immutable, so a snapshot-visible row's
  object can only be *deleted*, never changed." **That is false at the logical-key boundary the backup
  actually uses.** `_finalize_sync` (`storage.py:140-155`) heads the *source* and then `copy_object`s to
  the target with **no check that the target key already exists**; on an object-locked (therefore
  versioned) bucket that creates a **new version at the same key**. And `_capture_and_dump`
  (`drill.py:114`) records only `sha256, size_bytes, bucket, object_key` — **no `VersionId`** — while
  `_copy_blobs` copies whatever the *current* version is. So the bytes a snapshot-visible row resolves to
  can change without the row being touched.
  **And the interaction with H-03 is what makes it bite:** because nothing verifies uploaded bytes
  against the claimed SHA, two promotions of "sha X" can carry genuinely *different* bytes. Content
  addressing is an assumption the code does not currently enforce, so it cannot be leaned on here.
  **Consequences, all now binding on the design:** every generation entry must bind the
  **`object_version_id`** visible in the DB snapshot and restore *that exact version* — which is why
  DR-1's new `blob.object_version_id` column is a shared dependency, not a DR-1-local detail; and
  **`S-upload-identity` (H-03) must land before the recovery generation can rely on content addressing
  at all.** Guard the remaining deletion race with the org-scoped shared/exclusive advisory lock already
  shipped for pack-build-vs-R27 (Issue #361): the build takes shared, DESTROY/R27 take exclusive. Nothing
  user-facing blocks — capture, check-in and reads never take this lock.
- **D-B1 — storage shape.** Recommend a **shared content-addressed store** plus a **monthly sealed
  self-contained generation** for off-site carriage. Self-contained-per-generation is ×7 at
  `retention_daily=7` and unaffordable; content addressing makes the CAS near-free. Pruning becomes
  reference-counted GC, reusing the last-owner check `disposition.py` already implements.
- **D-B2 — R27 interaction.** Recommend **build and verify a replacement generation that excludes the
  erased material, then complete invalidation/deletion of the affected generations**. If replacement
  fails, the destructive invalidation does not complete and the operation reports that the legal action
  is still pending; this fail-safe ordering is the B-6 correction below. R27's derivative-copy addendum
  already extends erasure to sealed Evidence Packs, and a backup generation is the same class of
  derivative. Generation retention is bounded below by the erasure cadence, and pruning stops being
  v1.x and becomes mandatory.
- **Key rotation:** key ID = `sha256(derive_key(secret))[:16]`, recorded in the envelope and manifest;
  `BACKUP_ENCRYPTION_KEY_PREVIOUS` for decrypt-only history; an unknown key ID reports *"encrypted under
  key ⟨id⟩, which is not configured"* rather than today's conflation of rotation with tampering.
- **The acceptance test C-01 cannot pass today:** restore into a stack with **no source-vault access** —
  S3 credentials that cannot read the source buckets. Today's `_copy_blobs` fails `AccessDenied` there,
  which is exactly the RED needed. A test that merely uses a different bucket is a live-vault copy in
  disguise.
- **Re-point the triad's blob leg at `(blob.bucket, blob.object_key)` as stored.** That single change
  converts C-01c's tautology into a real proof and is worth landing before the object leg exists.

### DR-3 · Release and rollback contract → **decision D-E**

H-04/H-06/H-08 are one omission: **the four built images have no stable identity anywhere in the repo.**

- **The mechanical fact the design must absorb:** `docker save`/`load` **discards repository digests**, so
  `image: repo@sha256:…` is unresolvable offline and Compose will try to pull. The **image config digest**
  (the image ID) *does* survive and is exactly the bytes that run. So the manifest records both, bound
  together on the connected release host where both are known and signed together.
- **D-E1.** Recommend a signed release manifest + `compose.release.yml` (no `build:`, images by variable) +
  config-digest verification of running containers. Reject a bundled `registry:2` (adds a tenth service
  and a new failure mode on the most constrained host).
- **Compatibility window is zero by design** — `readiness._check_alembic` requires exact head equality in
  both directions. Therefore the migration must run from the **target** image
  (`run --rm --no-deps migrate` under the release overlay); that one line is the mechanical core of H-06.
- **`alembic downgrade` is not a rollback mechanism in this product** and the runbook must say so: enum
  extensions have no-op downgrades, populated downgrades can abort on constraint ordering (the `0082`
  lesson), and downgrades drop data the newer release wrote. The manifest carries a computed
  `rollback_class` — `image_only` (complete and lossless) or `restore_only` (data written after the
  migration committed is lost), told to the operator **before** the upgrade runs.
- **Verification is host-side.** `api`/`worker` have no Docker socket and must not get one; the host
  computes the verdict and hands it to the container as data.
- **Owner decision D-E2 — signing key custody.** A release-signing key would be the **first vendor-held
  secret** in a product where every key is per-install. If the owner prefers not to hold one, the honest
  fallback is unsigned manifests plus out-of-band hash comparison — weaker, and stated rather than faked.
- **The appliance is not offline-installable today** (`easysynq-provision.sh:151` runs `up -d --build`).
  Three copies of the overlay list exist; extract one and gate parity mechanically.

### DR-4 · Authentication baseline and migration → **decision D-I**

- **Why an inherited default is the defect:** not versioned (no diff to review when Keycloak changes a
  default), not detectable (nothing reads effective realm settings; anyone with the admin credential —
  readable from the API container per H-01 — can set a 30-day session and no gate objects), and not
  re-asserted (`--import-realm` is first-boot-only, so drift is never corrected).
- **D-I1.** Recommend a **single `BASELINE` dict** in `services/keycloak_baseline.py` from which
  `realm-export.json` is **generated** and pinned by a unit test, plus an idempotent Admin-API reconciler
  (`plan` / `apply --confirm` / `rollback`) and a daily drift Beat raising the existing operator alarm.
  Export-only fixes no existing install; reconciler-only leaves fresh installs un-hardened at first boot.
  This is the same class of drift the repo already fights with migration seeds (the `0010` "source from
  the ORM, not a retyped list" precedent).
- **Reuse `services/keycloak_admin.py`** — it already does the read-modify-write-*full-representation*
  discipline a realm reconciliation needs. Not `keycloak_provisioning.py` (request-scoped, user-shaped).
- **The highest-risk single change is redirect-URI narrowing**; get it wrong and nobody can log in.
  Mandatory mitigations: refuse unless the computed set contains the `SITE_ADDRESS`-derived origin;
  **post-write readback with automatic restore**; `plan` is the default and names every URI it will remove.
- **MFA recovery inherits R64 rule 5.** Clearing someone's second factor is the same authority class as
  resetting their password, so it requires **system tier with no inspection of the target** — the same
  reasoning that made target-inspection wrong for credential reset. Break-glass is a console primitive
  (`easysynq-mfa-reset`), which grants no new authority because console access is already
  root-equivalent, and it must be audited.
- **The lockout invariant:** refuse to enable realm-wide required MFA unless **two** system-tier accounts
  are already enrolled — reusing `admin_guard.py`'s "never leave zero reachable admins" shape. Without
  it, one toggle can lock an organisation out of its own QMS.
- `passwordBlacklist` is **coupled to DR-3** (the list must be in the image). Until then `docs/12 §2.3`
  must say *not implemented* rather than claim it.

---

### 2.5 Surviving failure scenarios from Codex checkpoint B

Fourteen scenarios survived the adversarial pass. Per the handoff, each must become an **acceptance
criterion** or an **explicit owner-accepted residual**. Dispositions:

| # | Scenario | Design | Disposition |
| --- | --- | --- | --- |
| B-1 | Concurrent dedup creates multiple object versions; `ensure_object_retention(session, bucket, object_key)` takes no version argument | DR-1 | **AC** — signature gains the version; prove concurrent promotion retains the authoritative version and handles orphans |
| B-2 | Process death after promotion but before the owner commit; retry creates a new version carrying only the bucket default | DR-1 | **AC** — kill at that boundary, retry, assert every surviving version is retained or GC'd |
| B-3 | Policy extension races owner-edge creation (`update_policy` does not lock pinned owners) | DR-1 | **AC** — interleave capture with extension; the committed owner must get the maximum final policy |
| B-4 | Snapshot identifies a logical key, not an immutable version | DR-2 | **AC** — bind `object_version_id`; see the correction above |
| B-5 | Concurrent R27 vs generation build is described but has no race acceptance | DR-2 | **AC** — pause a build after its snapshot, approve R27, prove one of the two orderings holds |
| B-6 | R27 can leave **zero valid generations** (invalidate-then-force, where the forced generation fails) | DR-2 | **AC** — fail-safe ordering: do not complete destructive invalidation until a replacement generation verifies. This is a genuine design correction, not a test gap |
| B-7 | Process death exposes or strands partial generations (archive bytes written before the sidecar; no atomic rename) | DR-2 | **AC** — kill at every leg/manifest/ledger transition; partial generations must never be selectable and must be reclaimable |
| B-8 | Death after target migration but before container replacement has no retry contract | DR-3 | **AC** — kill after the migration commit and after each replacement; prove convergence or declared rollback |
| B-9 | `restore_only` rollback has no executable criterion | DR-3 | **AC** — failed B health gate → restore the exact pre-upgrade generation → restart A, with operator disclosure |
| B-10 | Offline image identity unverified against the installed Docker (daemon socket inaccessible to the reviewer) | DR-3 | **AC** — assert the post-load tag resolves to the signed config digest *before* Compose starts |
| B-11 | Redirect readback can succeed while browser login is still broken (Caddy, `KC_HOSTNAME`, issuer, SPA callback are independently configured) | DR-4 | **AC** — a real authorization-code round trip before rollback state is discarded. Readback alone is insufficient |
| B-12 | Active-session impact is unclassified per baseline field | DR-4 | **AC** — classify every field immediate / new-session / relogin-required and verify access + refresh tokens accordingly |
| B-13 | Death during multi-field reconciliation leaves a partial baseline with no usable rollback | DR-4 | **AC** — persisted operation identity + snapshot; kill after each write; idempotent retry |
| B-14 | MFA reset retry/audit semantics unspecified (death after credential deletion, before the audit commit) | DR-4 | **AC** — one terminal result, no duplicate or false business effect |

**B-6 is the most consequential**: it changes the R27→backup ordering from "invalidate, delete, then
force a fresh generation" to "**verify a replacement first, then complete the invalidation**". Without
that inversion a legally-mandated erasure could leave an install with no recoverable backup at all.

None of the fourteen was accepted as a residual — every one is cheap to state as acceptance, and each
describes a way the design fails rather than a cost the owner would knowingly take.

## 3. Deliverable 2 — corrected slices

Each slice states: **goal · findings · authority boundary · files · deps/decisions · falsifying proof ·
commands · cases · migration/rollback · doc truth · gate effect + residual risk.**

Per the handoff, a **RED-against-HEAD test is required for a current executable defect**; inventory,
design and documentation slices instead require a mechanical check, stated explicitly.

### Stage 1 — programme correction and baseline inventory *(no production edits)*

**`S1-inventory`** — this document, the coverage inventory, and DR-1…DR-4.
*Findings:* NEW-04, NEW-05, NEW-06, NEW-07 (all refuted-as-phrased and closed here).
*Boundary:* documentation only. *Proof:* mechanical — the ledger's one-owner-per-ID assertion, and the
cited test names verified to exist. *Rollback:* n/a. *Doc truth:* v1 is retained as the validation trail
and this document supersedes it. *Gate:* none. *Residual:* the narrowed deltas (NEW-04a/05a/06a/07a) are
carried forward as real rows, not dropped.

### Stage 2 — integrity and recovery

**`S-worm-retention`** *(NEW-01a, 01b, 01c, 01d, 01e)* — **the largest single gap between claim and behaviour.**
*Boundary:* object-lock authority on the vault buckets.
*Files:* new `services/vault/object_retention.py`; `domain/records/retention.py` (`required_retain_until`);
`services/records/service.py::_attach_evidence`; the seven `services/vault/service.py` promotions;
`services/ingestion/commit.py`; `services/records/disposition.py` (hold clear before purge);
`services/records/retention_policies.py` (the 01d ratchet); `infra/compose/minio/minio-init.sh`;
`cli/blob.py`; migration `0086`.
*Deps:* **D-A**; pairs with `S-container-identity` (H-01).
*Proof (RED against HEAD):* the dedup test in DR-1 — record A `P3Y`, record B identical bytes `P10Y`,
assert `P10Y`; mutation-verified by moving the call into the `is None` branch.
*Commands:* `/check-api`, `/check-migrations`, `-m integration` for records + disposition.
*Cases:* dedup, extension propagation, `event:*` provisional anchor, `PERMANENT`, legal hold with a second
held owner, R27 still succeeds over a locked+held object, backfill idempotence, mode coherence.
*Migration:* `0086` adds `blob.object_version_id`, `retention_asserted_at/until`, an index on
`document_version.source_blob_sha256`, `system_config.document_worm_period`, and an additive `event_type`.
No data backfill in the migration — a fresh CI DB has zero `blob` rows, so the backfill lives in the CLI.
*Rollback:* privilege/column additive; the applied locks are **not** reversible (monotone by design) —
state this in the register entry.
*Doc truth:* `docs/06:48`, `docs/12:506`, `docs/14:519`, `docs/06:298` become true in this merge.
*Gate:* release-blocking. *Residual:* MinIO root still deletes; delete markers are detected, not prevented.

**`S-backup-legs`** *(M-01)* — mandatory legs, fail-closed on no key, key IDs + history.
*Boundary:* backup generation completeness. *Files:* `services/backup/{drill,crypto,service}.py`, `config.py`.
*Proof:* **inverted test** — `test_durable_backup_without_key_omits_sensitive_legs` currently pins the
defect (§0.2); it is rewritten to assert refusal and mutation-verified against the old behaviour.
*Cases:* each leg forced to fail; rotated key decrypts; unknown key ID gives the specific message.
*Doc truth:* the runbook stops describing a partial archive as a backup. *Gate:* release-blocking.

**`S-restore-target`** *(C-01b, C-01c)* — the restore-target triple, cutover guards, and the real triad.
*Boundary:* restored-namespace resolvability. *Files:* `services/backup/{restore,drill}.py`, runbook.
*Proof (RED against HEAD):* **rewrite one restored `blob.object_key` to a value that does not resolve,
leaving the copied bytes intact, and assert the triad FAILs.** It passes today — the triad reads the
scratch namespace it wrote, so an unresolvable stored key is invisible to it. ⚠ Do **not** frame this as
"the triad cannot fail": `test_restore.py:168-194` already proves byte corruption *is* caught, and a
proof aimed at corruption would pass over the real defect. The gap is namespace **resolvability**.
*Gate:* release-blocking. *Note:* deliverable independent of, and cheaper than, the object leg.

**`S-recovery-generation`** *(C-01)* — the object-bytes leg, CAS, RW lock, capacity pre-flight, pruning GC.
*Boundary:* recovery-set durability. *Deps:* **D-B**; consumes `S-backup-legs` + `S-restore-target`.
*Proof (RED against HEAD):* restore with S3 credentials that cannot read the source buckets — today
`_copy_blobs` fails `AccessDenied`. Then boot the app on the restored DB + fresh buckets and download a
document version, a record evidence blob and a pack ZIP (three buckets, three read paths).
*Migration:* `backup_generation` ledger. *Gate:* release-blocking, and **G-C must be re-pointed at a
generation that passes this test** — until then the gate certifies the wrong thing.
*Residual:* storage multiplies; the shared CAS is shared fate; still no PITR (R37 half-answered).

**`S-upload-identity`** *(H-03)* — verify the staging object **before** promotion.
*Boundary:* content identity at the WORM boundary.
*Deps:* **D-C**. *Proof (RED against HEAD):* declare `sha256(good)`, PUT `evil`, check in, assert refusal —
today this returns 201 and WORM-locks bytes under a false identity.
*Cases:* documents + records + ingestion; omitted/altered checksum header; **same-size wrong bytes**;
multipart; dedup race with divergent content; rejection audit; staging cleanup; storage failure.
*Note:* do **not** re-hash only after promotion — wrong bytes would already be locked.
*Gate:* release-blocking.

**`S-db-grants`** *(H-02, L-01)* — privilege-only migration `0087`.
*Boundary:* database write authority. *Files:* one migration + `test_db_grants.py`.
*Proof (RED against HEAD):* as the real `easysynq_app` role, `UPDATE`/`DELETE` on each of `blob`,
`document_version`, `record`, `evidence_blob`, `import_decision` must raise SQLSTATE `42501` — mirroring
the existing `audit_event` proof at `test_audit.py:141`, which is the template.
*Design:* column-scoped `GRANT UPDATE (…)` following `0010:229`; **`blob` included** (only `verified_at`
/ `verify_failed_at` are mutable); do **not** preserve blanket DELETE for R27 — put destructive
blob/evidence operations behind an authority-bound function following the `pending_blob_purge` precedent.
*Migration:* privilege-only ⇒ no `alembic check` drift (the `0072` precedent — cite it in the docstring).
*Gate:* release-blocking. *Residual:* the owner role is unaffected by design.

**`S-upgrade-safety`** *(NEW-02, K-lock)* — make `"""Never raises"""` true.
*Boundary:* upgrade orchestration on the worker.
*Proof (RED against HEAD):* stub `build_durable_backup` to raise `OSError("No space left on device")`
(assert FAILED + `stage: pre_backup` + an `UPGRADE_FAILED` row + alembic never ran), **and** to return
`{"verified": False}` (assert abort before alembic). ⚠ A `BackupError` stub — the obvious test — passes
against HEAD and proves nothing.
*Design:* one shared typed backup-result validator rather than duplicated guards; an outer `except`;
single-flight + operation identity + orphaned-`STARTED` reconciliation; `lock_timeout` on the alembic
connection (closing K-lock, which changes every migration's failure mode from *wait* to *abort* — an
owner-visible behaviour change).
*Gate:* release-blocking, **and production upgrade eligibility depends on `S-recovery-generation`
passing.** Do not advertise the pre-backup as a safety net before then.

### Stage 3 — release trust

**`S-build-identity`** *(H-08, M-30)* — `scripts/release-build.sh`, `compose.release.yml`, the signed
manifest, the `$`-skip in `compose-images-lock`, and a CI job running with `EASYSYNQ_RELEASE=1`.
*Proof:* mechanical — with `EASYSYNQ_RELEASE=1`, CI is red if any upstream ref floats or any built image
lacks a `config_digest`; two builds of one `git_sha` differ only in `built_at` and digests.
*Deps:* **D-E**, **D-E2** (signing custody). *Gate:* release-blocking. *Note:* the running stack is
unchanged by this slice, so it merges freely.

**`S-offline-install`** *(H-04)* — bundle the built images; `install.sh --release`; `--no-build --pull never`.
*Proof (RED against HEAD):* a CI job that builds the bundle then, in a **network-disabled** environment
with an empty image cache, runs the installer end to end and reaches `/readyz`. **This is the only test
that can falsify H-04**; without it the claim is re-asserted rather than proven.
*Doc truth:* `install-airgapped.md:20-22` and `airgap-bundle.sh:3-6` are rewritten **in this slice** to
describe what now happens — no later wave reverses an intentional contradiction.
*Gate:* release-blocking while air-gapped install is a supported profile.

**`S-deploy-rollback`** *(H-06)* — host-side orchestration; migrate from the **target** image; four-leg
gate (identity, schema, dependencies, function); `--no-deps` on every helper.
*Proof (RED against HEAD):* `easysynq backup run` must stop recreating `minio` (today it does).
Then: upgrade A→B ends with every container's `.Image` equal to B's manifest; a deliberately altered
`config_digest` turns the gate red (mutation-verified).
*Migration:* `RELEASE_*` event types. *Gate:* release-blocking.

**`S-container-identity`** *(H-01, M-31, M-04)* — **⇧ moved into Stage 2** (see §4); listed here only for
its release-trust adjacency. Capability inventory first, then non-root.
*Deps:* **D-F**; **ships atomically with `S-worm-retention`** — a GOVERNANCE lock means nothing while the
principal that could bypass it is the same principal running the web tier.
*Files:* `infra/compose/compose.yml` (per-role env anchors or Docker `secrets:`), `config.py` (`*_FILE`
support), `apps/api/Dockerfile` + `apps/web/Dockerfile` (`USER`, fixed UID/GID; web → multi-stage static),
`services/vault/verify_token.py` (M-04's `O_CREAT|O_EXCL` publish), and the `infra/appliance/` twins.
*Design note:* per-service env files are **not** a capability design. Inventory API, worker, maintenance,
purge, backup/restore, Keycloak-admin, DB-owner and S3 governance-bypass authority; give `api`/`worker` an
S3 principal **without** `s3:BypassGovernanceRetention`, holding the bypass credential only on the R27 path.
*Proof (RED against HEAD):* three, each currently failing — (i) a unit test asserting both built images
declare a non-root `USER` (neither does); (ii) a test asserting the `api` service's environment does
**not** contain the DB-owner DSN, MinIO root, Keycloak admin, backup key or checkpoint signing key (all
five are present today via the shared `.env`); (iii) M-04: two concurrent first-use key generations must
converge on one persisted key — today it is last-writer-wins.
*Commands:* `/check-api`, `docker compose config`, a container start-up smoke on the live stack.
*Cases:* fixed UID/GID, populated-volume ownership preflight and migration, appliance parity, rollback,
and proof that mirror/backup/signing-key paths stay writable **without** falling back to an ephemeral key.
*Migration:* none. *Rollback:* image + compose revert; ⚠ **volume ownership changes are not automatically
reversible** — the preflight must record prior ownership so a rollback can restore it.
*Doc truth:* docs/12 §7's credential-separation description becomes true; the appliance runbook's
first-boot steps change. *Gate:* release-blocking.
*Residual:* a root-equivalent host operator is unaffected by any of this — D1 assumes a trusted admin.

**`S-image-ratchet`** *(H-07, NEW-09)* — take `cryptography 50.0.0` + `starlette 1.3.1` now; replace
lockfile `-diff` attributes with `linguist-generated=true` so dependency changes remain reviewable; then
make scans blocking with an expiring baseline. *Deps:* **D-G** applies to the blocking-scan policy, not
the reviewability fix. *Proof:* `uv lock --check`, a normal `git diff` that exposes lockfile text without
`--text`, and existing gates that exercise Ed25519, AES-GCM, JWT and the share/verify token paths.

**`S-doc-truth`** *(H-05, M-33)* — narrow every overclaim; add the link check to the `contracts` job.
*Deps:* **D-F2**, **D-H**. *Proof:* mechanical — a link checker that is RED on the 59 broken links today.
⚠ Every docs sweep collides with the R61 backstop's 4-segment-clause-literal rule.
*Note:* per-slice doc truth is the rule; this slice owns only what no other slice touches.

### Stage 4 — authentication and user-facing correctness

**`S-auth-baseline`** *(NEW-03a; documents NEW-03c's deferral)* — `BASELINE` + generated export + pin test +
reconciler + drift Beat. *Deps:* **D-I**; **blocks `S-auth-shell`**; and its `passwordBlacklist` leg is
**blocked by `S-offline-install`** (Stage 3) because the word list must be baked into the Keycloak image —
until that lands, this slice must write *"breached-password screening: not implemented"* in `docs/12 §2.3`
rather than set the policy (setting it without the file makes every password change fail). *Proof (RED against HEAD):* the anti-inheritance unit test —
every field named in `docs/12 §2.2/§2.3/§2.5` must be **explicitly present** in `BASELINE`; and
`test_realm_export_matches_baseline` mutation-verified by editing one field.
*Cases:* rotation proven live (a refresh token reused twice → rejected); redirect reconciliation leaves
exactly the computed set; forced readback failure triggers automatic restore. *Gate:* release-blocking.

**`S-mfa-enrollment`** *(NEW-03b)* — populate `mfa_enrolled` from Keycloak credentials; the optional
required action; `USER_MFA_RESET` (migration); the console break-glass; the two-enrolled-admins precondition.
*Proof (RED against HEAD):* `mfa_enrolled` is `false` for a TOTP-enrolled user today.
*Note:* do **not** derive enrollment from the token's `amr` — a remembered session presents no factor and
the column would flicker. *Register:* the lockout invariant is **R66-candidate**; ask the owner.

**`S-auth-shell`** *(H-11, M-08, L-11)* — explicit loading / retryable failure / terminal failure /
unauthenticated / authenticated / expired / reauthentication, with preserved deep links and dirty-form
behaviour. **Deps: `S-auth-baseline` first** — the shell's expired/renewal states must be built against
the token and session lifetimes the baseline pins, or they encode today's inherited defaults.
*Proof (RED against HEAD):* force the auth-config fetch to fail and assert a `main` landmark with a retry
control — today the page is a permanent blank spinner. Second: make `/setup/state` return 503 and assert
the first-run wizard does **not** render. ⚠ Specify the observation point precisely or this one is
indeterminate: assert on the **settled** tree (`waitFor` the query to reach `isError`), not on an
arbitrary tick, and assert the *absence* of the wizard heading rather than the presence of an error.

**`S-session-revocation`** *(M-03)* — **this is a product gap, not a test gap**: the column has no writer.
Require `iat`; write and audit the watermark on credential issue and on deactivation; revoke Keycloak
sessions/refresh capability; define fail-closed behaviour when Keycloak is unavailable.
*Proof (RED against HEAD):* mint a token, reset the password, assert the pre-reset token now 401s.
*Note:* ride the existing `user.deactivate` key — do not open the catalog.

**`S-approval-content`** *(H-09, L-07)* — do not render an **enabled** decision control until the candidate
version and bytes are identified and openable.
*Deps:* **D-D** (narrowed: raw download vs preview; explicit acknowledgement; fail-closed while loading /
forbidden / missing / non-previewable / changed). Digest binding is **already implemented** and is not a
question — add a regression asserting both existing bindings instead.
*Proof (RED against HEAD):* the approve control is enabled today with no content request in flight.
*Cases:* loading, 403, 404, 500, missing bytes, changed candidate between load and submit, non-previewable
MIME, popup-blocked. Use a synchronously opened tab or an ordinary authorized link after presigning.
*Gate:* release-blocking, **and its accessibility acceptance lives here**, not in Stage 5.

**`S-release-consistency`** *(M-22)* — *(split from approval.)* *Boundary:* post-release cache coherence.
*Files:* `features/authoring/hooks.ts::useInvalidateDocument` (add the distribution + acknowledgement
query keys) and the release confirmation component. *Deps:* none. *Owner decisions:* none.
*Proof (RED against HEAD):* a **deterministic** race arrangement, not a timing hope — MSW holds the
distribution response open behind a manually-resolved promise; release the document; assert that at no
render does the tree contain both the `Effective` badge and the "Not yet effective" acknowledgement text;
then resolve. ⚠ Default refetch-on-mount makes a naive test green regardless — the held-response
arrangement is what makes it falsifying. Mutation-verify by removing the added invalidation.
*Commands:* `/check-web`. *Cases:* release, immediate re-render, reload, and a failed distribution fetch.
*Migration:* none. *Rollback:* pure UI. *Doc truth:* none. *Gate:* not release-blocking.
*Residual:* an unawaited invalidation is still fire-and-forget; if the assertion proves flaky, the
correct escalation is to await it in the mutation's `onSuccess` — name that as the fallback.

**`S-calendar-contract`** *(H-10, M-14)* — **at the API boundary.** Require date-only calendar intent and
resolve server-side in the org's validated IANA timezone; if wire compatibility needs the old datetime
field, normalize server-side, reject ambiguous instants, and publish a deprecation path. Display
formatting stays separate. *Proof (RED against HEAD):* a Chicago org creating a DCR for a given date
stores the wrong instant today. *Cases:* create, CAPA spawn, edit, save, read round trips × Chicago
winter/summer, a positive-offset zone, date boundaries, a midnight-transition zone, unavailable-timezone
state. *Gate:* release-blocking. *Contract:* OpenAPI change + `gen-contracts.sh` + commit the lock.

**`S-secret-capture`** *(H-12, L-08)* — *Boundary:* the show-once credential's acknowledgement lifecycle.
*Files:* `admin/ShowOncePassword.tsx` (acknowledgement state, `beforeunload`, announced clipboard result),
`admin/CreateUserModal.tsx` and `admin/UsersAdmin.tsx` / `ManageUser` (close predicates widened from
"in flight" to "in flight **or** unacknowledged"), plus a router-level navigation blocker.
*Deps:* none. *Owner decisions:* whether to design a server-side escrow/reveal token (otherwise crash
loss is accepted). *Proof (RED against HEAD):* issue a temporary password, then navigate via an in-app
link; assert the navigation is blocked and the secret is still on screen — today the route changes and
the credential is destroyed. Second proof: make `navigator.clipboard` reject and assert a visible,
announced failure — today it fails silently. *Commands:* `/check-web` + the Stage-5 browser harness for
focus/announcement. *Cases:* X, backdrop, Escape, in-app navigation, back/forward, reload, tab close,
Done, listener cleanup on unmount. *Migration:* none. *Rollback:* pure UI. *Doc truth:* the admin manual's
user-creation step must state that the password is shown once and how to reissue.
*Gate:* not release-blocking (the auditable reissue path prevents lockout).
*Residual:* **a browser guard cannot survive a process crash or a force-quit.** Either accept that
explicitly in the register or build the escrow; do not let the guards imply otherwise. Accessibility
acceptance for this flow lives here, not in Stage 5.

**`S-capability-truth`** *(M-11, M-21)* — a **server-owned action manifest** (operation, action identity,
permission, evaluator/scope, composite checks) or persona contract tests. A `require()`-walking test is
insufficient: it misses dynamic `enforce()`, resolver identity, composite authority, auth-only routes and
ABAC/SoD state. *Deps:* **D-J**. *Proof (RED against HEAD) — pinned so it is decidable:* as the seeded **Sam (Employee)**
persona, who holds no dashboard-summary capability, sign in and load `/` then deep-link to `/library`;
count `audit_event` rows with `event_type = ACCESS_DENIED` for that user before and after; assert the
delta is **0**. Today the audited persona journey produced 70 such rows, so this fails. Pin the persona,
the two routes and the publication state in the test fixture — an unpinned version of this assertion is
indeterminate. *Commands:* `-m integration`. *Note:* preserve per-field
available/forbidden/error/not-published semantics in any dashboard aggregate. Catalog stays at 102 keys.

**`S-query-errors`** *(M-10)* — give the supporting-data hooks the three-state contract the primary hooks
already have. *Boundary:* SPA query error semantics. *Files:* `app/shell/{useDocumentTypes,useUserDirectory,useClauses}.ts`,
`features/objectives/hooks.ts::useProcesses`, and each consuming picker.
*Proof (RED against HEAD):* MSW returns 500 for `GET /document-types`; assert the picker renders a scoped
error and the dependent submit is **disabled** — today it renders as an empty list with the action live.
*Commands:* `/check-web`. *Cases:* 403 vs 500 vs empty; each of the four hooks. *Migration:* none.
*Rollback:* pure UI. *Doc truth:* none required. *Gate:* not release-blocking. *Residual:* none.

**`S-url-selection`** *(M-12)* — derive drawer selection from the URL. *Boundary:* SPA routing state.
*Files:* `features/{capa,dcr,improvement}` register pages, following `RisksRegisterPage.tsx:108-118`.
*Proof (RED against HEAD):* open a CAPA drawer, press Back, assert the drawer closes — today the local
state survives. *Commands:* `/check-web`. *Cases:* back, forward, deep link, replace. *Migration:* none.
*Rollback:* pure UI. *Doc truth:* none. *Gate:* no. *Residual:* none.

**`S-setup-resume`** *(M-07)* — per-field non-clobbering hydration from persisted setup state.
*Boundary:* setup wizard state. *Files:* `SetupWizard.tsx` + **its first test file** (it has none today).
*Proof (RED against HEAD):* persist org profile, reload at step 3, assert short code / timezone / backup
destination / auth method are restored — today only legal name is. *Commands:* `/check-web`.
*Deps:* **`S-dirty-form` must land first** — this slice consumes its dirty-guard so hydration cannot
clobber an in-progress edit; building hydration first would reintroduce M-13 on a new surface.
*Cases:* resume after each of the six gates; hydration must not clobber an in-progress edit.
⚠ Pin every MSW fixture to the real `GET /setup` serializer — a hand-typed shape is the repo's documented
#1 false-PASS. *Migration:* none. *Doc truth:* none. *Gate:* no.
*Residual:* fields the server never persists cannot be restored — enumerate them in the slice.

**`S-dirty-form`** *(M-13)* — guard hydration on dirtiness, not data identity. *Boundary:* form state.
*Files:* `ConfigAdmin.tsx`, `WorkingCalendarEditor.tsx`, `NotificationSettingsPage.tsx` (each already
computes its own dirty signal). *Proof (RED against HEAD):* type an edit, trigger a background refetch,
assert the edit survives — today it is overwritten. *Commands:* `/check-web`. *Cases:* dirty vs clean,
refetch vs remount, conflict on save. *Migration:* none. *Doc truth:* none. *Gate:* no.
*Residual:* a genuine server-side conflict still needs a conflict UI — out of scope, named.

**L-10 splits into three** — checkpoint C was right that one slice covering check-in feedback, lifecycle
copy and release-confirmation wording is three boundaries, not one. Each is independently testable and
independently revertible; they share only the finding ID, and L-10's primary owner is `S-checkin-feedback`
(the other two are explicit remainder references, not co-owners).

**`S-checkin-feedback`** *(L-10, primary)* — *Boundary:* check-in result reporting. *Files:*
`features/authoring/CheckInPanel.tsx`. *Proof (RED against HEAD):* after a successful check-in, assert the
retained filename, revision label and content digest are on screen — today the panel resets and shows none
of them. *Commands:* `/check-web`. *Migration:* none. *Doc truth:* the operator manual's check-in step.
*Gate:* no. *Residual:* none.

**`S-lifecycle-copy`** *(L-10, remainder)* — *Boundary:* one shared state-label mapping. *Files:* a new
`documentStateLabel` helper + every call site rendering a raw state. *Proof (RED against HEAD):* assert no
rendered surface contains the raw string `InReview` — today several do. *Commands:* `/check-web`.
*Note:* `S-approval-content` consumes this helper, so it lands first within Stage 4.
*Migration:* none. *Doc truth:* none. *Gate:* no. *Residual:* none.

**`S-release-copy`** *(L-10, remainder)* — *Boundary:* release-confirmation wording. *Files:* the release
confirmation component. *Proof (RED against HEAD):* for a document with **no** prior Effective version,
assert the confirmation does not claim it supersedes a current version — today it does.
*Commands:* `/check-web`. *Migration:* none. *Doc truth:* the operator manual's release step.
*Gate:* no. *Residual:* none.

**`S-restore-job-recovery`** *(M-09)* — a **persisted job ID/status contract**, not snapshot hydration.
*Boundary:* restore-test job lifecycle (server-owned). *Files:* `services/backup/service.py` (expose job
id + status), the OpenAPI contract, and the SPA restore panel.
*Proof (RED against HEAD):* start a restore test, reload the page, assert the panel shows `running` and
resumes polling — today polling is derived from a terminal-result guess and does not resume.
*Commands:* `/check-api`, `/check-contracts`, `/check-web`. *Cases:* queued / running / skipped / failed /
passed × reload, error, and a second concurrent request. *Migration:* possibly a status column — decide in
the slice; if so it serializes against the Stage-2 migrations. *Contract:* OpenAPI + `gen-contracts.sh` +
commit the lock. *Doc truth:* the backup runbook's restore-test step. *Gate:* no.
*Residual:* a job lost to a worker restart still needs the reaper — name it.

The v1 `S-edge-trust` splits **four ways** — these are four different authority boundaries, and each
carries its own fields. A shared proof across them would have re-created the conflation the split exists
to remove.

**`S-edge-headers`** *(M-15, M-24)* — *Boundary:* response-header and CSP policy at the edge.
*Files:* `infra/compose/caddy/Caddyfile` (CSP `img-src`; move `Referrer-Policy` off the site-wide block
into each handle), `apps/api/src/easysynq_api/api/{verify,pack_share}.py` (`Cache-Control: no-store`),
`apps/api/tests/unit/test_caddy_headers.py`. *Deps:* none. *Owner decisions:* none.
*Proof (RED against HEAD):* two, both against a **running edge**, because jsdom cannot see CSP —
(i) load the visual-diff page and assert the rendered `<img>` has `naturalWidth > 0` (today the blob URL
is blocked and it is 0); (ii) `curl -sI` the three token-bearing paths and assert `Cache-Control: no-store`
**and** a final `Referrer-Policy: no-referrer` (today the site-wide `strict-origin-when-cross-origin`
wins). ⚠ Asserting the substring `no-referrer` anywhere in the response passes over the defect — assert
the *effective final* header value. *Commands:* `just up s`, `curl -sI`, plus `/check-api` for the static
Caddyfile pin. *Cases:* valid + denied share branches; verify page; download; SPA route.
*Migration:* none. *Rollback:* Caddyfile revert; no data effect. *Doc truth:* docs/12's header table.
*Gate:* not release-blocking. *Residual:* the static Caddyfile test cannot prove the final header —
the browser/curl check is the only real proof, so it must be a required check, not a manual step.

**`S-proxy-trust`** *(M-05, L-06)* — *Boundary:* who the API believes the client is.
*Files:* `Caddyfile` (`header_up X-Forwarded-For {remote_host}` on every proxy block; `@api` matcher to
include exact `/api`), `apps/api/Dockerfile` / compose env (`FORWARDED_ALLOW_IPS` pinned to the internal
subnet), and the two hand-rolled parsers (`api/pack_share.py:47-51`, `services/ack/decide.py:57-62`)
consolidated onto one helper. *Deps:* none. *Owner decisions:* none.
*Proof (RED against HEAD):* an integration test that issues a request through the edge with a known
client address and asserts an `ip_allow`-scoped grant **denies** a non-matching client and **permits** a
matching one — today both see Caddy's address, so the grant either always matches or never does.
*Commands:* `-m integration`, plus `curl` for the exact-`/api` 502. *Cases:* trusted vs untrusted
forwarded chain; spoofed XFF; audit attribution; R58's replayed `source_ip`. *Migration:* none.
*Rollback:* config-only. *Doc truth:* docs/07's ip_allow description. *Gate:* not release-blocking.
*Residual:* L-06's Host leg is inert (KC_HOSTNAME pinned, redirect URIs constrained) — fixing the
routing nit only; say so rather than implying a Host-injection fix.

**`S-api-docs`** *(M-23)* — *Boundary:* what the API's public docs surface loads and from where.
*Files:* `apps/api/src/easysynq_api/main.py:113-121` (custom `get_swagger_ui_html` with self-hosted
assets), the vendored `swagger-ui-dist` bytes, and the Caddy CSP block for that route.
*Deps:* none. *Owner decisions:* whether to serve docs at all outside development.
*Proof (RED against HEAD):* fetch `/api/v1/docs` with egress blocked and assert the page renders with no
external request — today it pulls three assets from `cdn.jsdelivr.net` and `fastapi.tiangolo.com`, which
also breaks air-gapped by construction. *Commands:* `-m integration` + a network-disabled fetch.
*Cases:* docs enabled/disabled; CSP present; frame denial. *Migration:* none. *Rollback:* revert route.
*Doc truth:* D1's no-phone-home claim becomes true for this surface. *Gate:* not release-blocking.
*Residual:* vendored assets must be refreshed on FastAPI upgrades — name the maintenance cost.

**`S-abuse-liveness`** *(M-25, M-26)* — *Boundary:* unauthenticated work amplification and subsystem
health reporting. *Files:* `Caddyfile` (`request_body { max_size }` — stock), `readiness.py` (split
cheap liveness from deep readiness), a worker/beat heartbeat (reuse the `0074` alarm table).
*Deps:* **D-K** — `rate_limit` is **not** in the stock Caddy image and needs a custom build; the body
ceiling does not. *Proof (RED against HEAD):* (i) POST a body above the intended ceiling and assert the
edge rejects it — today there is no ceiling; (ii) assert `/readyz` reports worker and beat liveness —
today it reports five dependencies and no worker/beat at all. *Commands:* `curl`, `-m integration`.
*Cases:* oversized body; slow body; readiness with a dead worker; readiness with a dead beat.
*Migration:* possibly a heartbeat column — see the migration ledger below. *Rollback:* config-only for
the ceiling; the heartbeat needs its migration reverted. *Doc truth:* docs/12's documented boundary
(currently describes limits that are not configured). *Gate:* not release-blocking.
*Residual:* without a custom Caddy build there is **no** edge rate limit — an explicit accepted risk.

**M-15's one-token CSP fix may land early**, ahead of the rest of `S-edge-headers`, but only once the
`naturalWidth` acceptance test above exists — otherwise nothing proves it worked.

**`S-api-protocol`** *(L-04, L-05)* — *Boundary:* HTTP protocol correctness at the problem-response layer.
*Files:* `problems.py:145-157` (forward `exc.headers`; map status→code by status rather than collapsing
everything to `internal_error`), `main.py:123-156` (validate/bound the inbound request ID, mint a server
UUID when invalid, retain the caller's value in a separate capped header, and move request-ID middleware
**outside** the setup latch). *Deps:* none. *Owner decisions:* none.
*Proof (RED against HEAD):* `OPTIONS /healthz` must return `405` **with an `Allow` header** and
`code: "method_not_allowed"` — today the header is dropped and the code is `internal_error`. Second:
a 423 from the closed latch must carry an `X-Request-Id` — today it has none because the latch returns
before the request-ID middleware. *Commands:* `/check-api`, `/check-contracts`.
*Cases:* 401 `WWW-Authenticate`; 404 unchanged; non-UUID caller ID; oversized caller ID; latched 423.
*Migration:* none. *Contract:* `Problem.code` gains members → OpenAPI + `gen-contracts.sh` + commit the
lock. *Rollback:* pure API-layer. *Doc truth:* docs/15's problem-code table.
*Gate:* not release-blocking. *Residual:* changing `Problem.code` values is a **contract-visible change**
for any consumer matching on `internal_error` — v1 has no external consumers, but state it.

### Stage 5 — accessibility and responsive release gate

**No product release occurs before this gate passes.** Approval, authentication and show-once-secret
accessibility acceptance live in their **owning Stage-4 slices**, so the gate is not the first time those
flows are checked.

**`S-contrast`** *(M-18)* — root-caused: mapping `--mantine-color-dimmed` to `--es-text-2` in both schemes
retires the dominant share without touching 329 call sites; then stop shipping Mantine's stock indigo as
primary and map the `--mantine-color-body/text/default*` family onto the `--es` surfaces.
*Deps:* **D-L**. *Proof:* **jest-axe cannot compute browser colour contrast** — this needs a
production-browser check across routes, themes, lifecycle states, 320/390 widths, zoom/reflow, keyboard
order, focus restoration and portal content. Building that harness is part of the slice.

**`S-a11y-semantics`** *(M-19, M-20)* — *Boundary:* ARIA/semantic correctness. *Files:* `CapaLayout.tsx`,
`AuditsLayout.tsx`, `DriftLayout.tsx`, `AdminShell.tsx` (tabs-as-nav → links with `aria-current`);
`ClauseTree.tsx` (`role="group"` on a real element); 17 route headers (`h1`); CAPA rows (real interactive
elements, not keyboard handlers on `tr`); `UsersAdmin.tsx` (empty header cell); the shared confirmation
modal (duplicate banner landmark). *Proof (RED against HEAD):* a jest-axe assertion on
`aria-valid-attr-value` for each of the four layouts, plus `getByRole("heading", {level:1})` per route —
both fail today on 11 and 17 routes respectively. *Commands:* `/check-web` + the Stage-5 browser harness.
*Cases:* keyboard order and focus restoration through each converted control. *Migration:* none.
*Rollback:* pure UI. *Doc truth:* none. *Gate:* release-blocking (Stage 5). *Residual:* automated axe is
a floor, not conformance — manual screen-reader passes stay out of scope and are named as such.

**`S-responsive`** *(M-16, M-17)* — *Boundary:* app-shell layout. *Files:* `AppShell.tsx` (close nav on
location change + return focus to the Burger), `TopBar.tsx` (reserve/collapse by measured width),
`UsersAdmin.tsx` / `ProcessesAdmin.tsx` / `RolesAdmin` (`Table.ScrollContainer`).
*Proof (RED against HEAD):* at 390 px, activate a nav link and assert the drawer is closed — today it
stays open; and assert `document.body.scrollWidth <= 390` on Admin Users — today it is 626 px.
⚠ Mantine's `Table.ScrollContainer` viewport carries no `tabIndex`, so a keyboard-reachability assertion
must be added rather than assumed. *Commands:* `/check-web` + the Stage-5 browser harness at 320/390.
*Migration:* none. *Doc truth:* none. *Gate:* release-blocking (Stage 5). *Residual:* none.

### Stage 6 — depth and governance deltas

Real slices, carrying the same required fields as every other stage. None is release-blocking unless the
owner reclassifies it; each states its own residual risk.

| Slice · findings | Authority boundary · files | Falsifying proof (RED against HEAD) | Commands · cases | Migration / rollback · doc truth · residual |
| --- | --- | --- | --- | --- |
| **`S-renderer-proof`** · NEW-05a | Rendition fidelity · CI compose (add Gotenberg), `services/vault/render_gotenberg.py`, `test_render.py`, `test_verify.py` | ⚠ **COVERAGE DELTA, not a RED proof.** A QR-decode test would **PASS** today: `mirror.py:594-600` builds the exact signed URL and `watermark.py:99` encodes it with `segno.make(url)`. The gap is that nothing *asserts* it, so a future regression is unguarded. Mechanical acceptance: the new assertion must be **mutation-verified** — corrupt the URL passed to `segno.make` and confirm RED. Real Office→PDF is a genuine delta (no test opens a socket today) | `-m integration`; cases: real Office→PDF, QR payload, banding text, non-renderable refusal | No migration; rollback = drop the CI service · runbook gains the renderer prerequisite · **residual:** CI now depends on a Gotenberg image |
| **`S-celery-contract`** · NEW-08, NEW-06a | Task delivery semantics · `tasks/app.py`, all 14 task modules | **Kill a real worker process** mid-task before and after commit. ⚠ Pin the target or this is indeterminate: use **`easysynq.packs.build`** (it already has a `FOR UPDATE` + terminal-pointer guard and a reaper, so the expected states are defined). Synchronise the kill with a `SIGSTOP`-then-`SIGKILL` at a fixed instrumentation point, not a sleep. Expected: pre-commit kill → zero PG side effects, redelivery rebuilds; post-commit kill → the guard early-returns, **no duplicate blob and no duplicate audit row**. No test kills a process today (`os.kill`/`SIGKILL` appear nowhere in `tests/`) | `-m integration`; cases: pre-commit kill, post-commit kill, broker loss, redelivery, poison ceiling | Config-only; rollback = revert options · `engineering-patterns.md` gains the per-task contract · **residual:** at-most-once tasks can still strand work by design — enumerate which |
| **`S-audit-tail-policy`** · NEW-04a | Audit chain completeness · `services/audit/{verify,linker,sink}.py`, `tasks/audit.py` | ⚠ **COVERAGE DELTA for the delete case, not a RED proof.** `verify.py:100` already reports `chain reorder/deletion`, and `verify.py:210` catches checkpoint-referenced deletion — a delete test would **PASS**. Mutation-verify the new assertion instead. The genuine RED items are narrower: the D-8 sink read/write credentials are **the same principal in every run** (`sink.py:59-60` falls back to `s3_access_key`), and no test asserts checkpoint-object retention | `-m integration`; cases: delete, reorder, distinct sink credentials, checkpoint-object retention, checkpoint history, lag alarm | Possibly an alarm-config column · docs/12 pending-tail policy · **residual:** checkpoint *lineage* stays an open residual (Merkle anchor is its own slice) |
| **`S-workflow-edges`** · NEW-07a | Workflow pool/route resolution · `services/workflow/engine.py`, `test_workflow_engine.py` | ⚠ **COVERAGE DELTA, not a RED proof.** `engine.py:192` explicitly returns false on `not pool` and `engine.py:280` converts that to `NEEDS_ATTENTION` — an empty-pool test would **PASS**. The code is correct and merely unguarded; the in-file comment "rides code review" describes the *test* gap, not a defect. Mutation-verify each new assertion (delete the `not pool` guard → RED). Same for `_route()` fail-closed and the `populate_existing` proof | `-m integration` + `-m unit`; cases: empty pool, `_route()` fail-closed, `context_users` union, `populate_existing` two-session proof | None · none · **residual:** multi-hop cycles (A→B→C→A) remain unconstructed — named, not closed |
| **`S-contract-authority`** · M-28 | Contract authority · `scripts/gen-contracts.sh`, `packages/contracts/`, `ci.yml` | ⚠ **A clean-HEAD regenerate/diff would PASS** — the lock currently matches (`445d…bfa3`) and `gen-contracts.sh:25` already implements the comparison; the gap is only that **no CI job invokes it**. Proof must therefore be a **deliberately drifted fixture**: mutate `openapi.yaml`, run the new CI step, require RED. The genuine RED item is separate: a contract-response assertion currently accepts **any** non-2xx (204 of 283 operations return non-2xx today), so seed valid inputs and require the intended 2xx | `/check-contracts`; cases: drifted lock, missing 4XX, wrong intended status | None · docs/15 · **residual:** Redocly still cannot detect a *wrong* documented status |
| **`S-coverage-policy`** · COV-TABLE, M-29 | Test measurement policy · `ci.yml`, `pyproject.toml`, `vite.config.ts` | Mechanical: coverage is reported in CI where none exists today. M-29: replace the two pollution-skips with self-provided preconditions — do **not** isolate the DB, which would break the single-org invariant | `/check-api`; cases: shard-order independence for both skipping tests | None · `engineering-patterns.md` records why unit-only figures are declined · **residual:** `fail_under` is deferred to **D-O**; measuring is not gating |
| **`S-download-accountability`** · M-06, L-02 | Delivery evidence · `services/packs/service.py`, download routes | Atomic server-side increment: two concurrent guest downloads must yield count +2 — today the unlocked ORM read-modify-write loses one | `-m integration`; cases: concurrency, failed delivery, revoke race | Possibly an event type · docs/06 distinguishes authorization from delivery · **residual:** **D-P** — proxying contradicts D1's presign rationale; log ingestion may be the answer |
| **`S-transfer-limits`** · M-02 | Upload/fetch resource ceiling · `config.py`, `storage.presign_put`, the two init-upload bodies, Caddy | Presign with a declared size and assert an oversized PUT is rejected by the object store — no ceiling exists today | `/check-api`, `/check-contracts`; cases: over-limit, per-kind cap, streaming, back-pressure | Contract change (additive required field) · docs/15 · **residual:** existing clients must send size — plan the deprecation |
| **`S-web-transfer`** · L-09 | Browser memory · `lib/upload.ts`, `lib/hash.ts`, `CheckInPanel.tsx` | ⚠ The obvious "fetch received a Blob" assertion **passes over the broken code**. The proof must distinguish **one** materialisation from **two** — assert `arrayBuffer` is called once, not twice | `/check-web`; cases: large file, hash+upload sequence, size hint shown pre-upload | None · none · **residual:** the hash pass still materialises unless streamed — named |
| **`S-orphan-reconcile`** · L-03 | Object lifecycle · `minio-init.sh`, reconciliation tasks | Assert an abandoned staging object is expired — nothing expires the two plain staging buckets today | `-m integration`; cases: post-object/pre-commit, post-commit task failure | None · runbook · **residual:** the cheap half (lifecycle expiry) closes one window; full reconciliation is larger |
| **`S-ci-gaps`** · M-27 | CI surface · `ci.yml` | Mechanical: each new job must fail against a deliberately broken input (m-profile syntax, shellcheck violation, web-image CVE) | n/a; cases: m-profile config, shellcheck, exact web image scan, browser E2E | None · docs/dev-workflow CI description · **residual:** browser E2E is the expensive item; scope it explicitly |
| **`S-governance-artifacts`** · M-32 | Release provenance · `infra/appliance/`, repo root | Mechanical: the appliance installer must **refuse** a hash-mismatched artifact — it does not verify one today | n/a; cases: tampered VHDX, tampered ISO | None · root `LICENSE`/`SECURITY.md`/`NOTICE` added · **residual:** SBOM/signing depends on **D-E2** |

---

## 4. Deliverable 3 — sequencing

The ledger's **release-blocking** field is authoritative across all six stages. Stage 5 is an
unconditional minimum gate; any Stage-6 item later classified release-blocking must also close.

```
Stage 1  programme correction + baseline inventory        ← this document (no production edits)
Stage 2  integrity and recovery
         ┌ S-worm-retention ⟷ S-container-identity ┐  ← ATOMIC PAIR, must merge together
         └──────────────────────────────────────────┘
         S-backup-legs ────┬─→ S-recovery-generation ──→ (gates production upgrade eligibility)
         S-restore-target ─┤
         S-upload-identity ┘   ← H-03 must land FIRST: without server-verified digests, content
                                 addressing is an unenforced assumption and the generation cannot
                                 rely on it (checkpoint B, DR-2 correction)
         S-db-grants · S-upgrade-safety
Stage 3  release trust
         S-build-identity ──→ S-offline-install ──→ S-deploy-rollback
         S-image-ratchet · S-doc-truth
Stage 4  authentication + user-facing correctness
         S-auth-baseline ──→ S-auth-shell            (baseline pins the lifetimes the shell encodes)
         S-auth-baseline ──→ S-mfa-enrollment
         S-auth-baseline ··→ [passwordBlacklist leg BLOCKED BY Stage 3 S-offline-install]
         S-lifecycle-copy ─→ S-approval-content      (approval consumes documentStateLabel)
         S-dirty-form ────→ S-setup-resume           (resume consumes the dirty guard)
         S-session-revocation · S-release-consistency · S-calendar-contract
         S-secret-capture · S-capability-truth
         S-query-errors · S-url-selection · S-checkin-feedback · S-release-copy
         S-restore-job-recovery
         S-edge-headers · S-proxy-trust · S-api-docs · S-abuse-liveness · S-api-protocol
Stage 5  accessibility and responsive release gate  ← no product release before this passes
         S-contrast · S-a11y-semantics · S-responsive
Stage 6  depth and governance deltas  (12 slices, table above)
```

**Hard dependencies, with reasons:**

- `S-recovery-generation` **precedes or ships atomically with** production upgrade eligibility. Until a
  self-contained restore/cutover proof passes, the pre-upgrade archive must not be described as a safety net.
- `S-build-identity` → `S-offline-install` → `S-deploy-rollback`: nothing can be bundled before it has a
  name, and nothing can be deployed before it can be bundled.
- `S-worm-retention` **ships atomically with** `S-container-identity` — both in Stage 2, merged together,
  not merely "adjacent". Object lock's threat boundary stops at MinIO root; while api/worker/beat hold
  root credentials the retention guarantee and the thing it guards against are the same principal, so a
  release carrying one without the other would be a claim without a control.
- `S-auth-baseline` precedes `S-auth-shell` — the handoff's point that baseline auth drift, if High,
  belongs before shell work, not in a late wave.
- `S-calendar-contract` fixes the write boundary before display; fixing rendering first makes more
  surfaces *look* right while still storing the wrong instant.
- `passwordBlacklist` (in `S-auth-baseline`) is gated on `S-offline-install` shipping a rebuilt Keycloak
  image; until then `docs/12 §2.3` says *not implemented*.

**Documentation truth is per-slice.** No slice writes a statement a later slice must reverse. Where v1
planned an intentional contradiction (`S-doc-truth` writing "offline not supported", `S-airgap` reversing
it), the correction is that `S-offline-install` rewrites those exact sentences **in its own merge**.

---

## 5. Owner decisions

### 5.0 DECIDED — 2026-08-04

| # | Decision | Consequence |
| --- | --- | --- |
| **D-A1** | **GOVERNANCE only; COMPLIANCE is dropped from the product surface.** | R27 legal erasure stays executable on every install. `S-worm-retention` must remove the wizard control and retire `storage_config.object_lock_mode` (or reduce it to a recorded-and-enforced GOVERNANCE constant), closing NEW-01c. The register entry must state plainly that object lock does **not** bind a MinIO-root principal. |
| **D-B1** | **Shared content-addressed store + one monthly sealed self-contained generation.** | Destination sizing ≈ one vault plus deltas, not ×7. Pruning becomes reference-counted GC reusing `disposition.py`'s last-owner check. The monthly sealed generation is the copy that leaves the building. Shared-CAS fate is an accepted residual, mitigated by the rotating verifier. |
| **D-B2** | *(implied by B-6, see §2.5)* **Verify a replacement generation before completing an R27 invalidation.** | Ordering inverted from the first draft; an erasure can no longer leave an install with zero recoverable backups. |
| **Offline boot** | **A fresh install must come up without reaching PyPI / npm / Debian.** | ⚠ **H-04 is reframed and stays High.** The finding is not "the air-gap bundle is incomplete" — it is **"no install path works without network, including the appliance."** `infra/appliance/provision/easysynq-provision.sh:151` runs `compose up -d --build`, so the Hyper-V appliance builds from source at first boot. Appliance offline-boot and air-gap therefore share ~80% of the work and ship as one family: stable image identity → images pre-loaded into the VHDX → an offline overlay (`pull_policy: never`, no `build:`) → a network-disabled smoke test. `docs/runbooks/install-airgapped.md:20-22` is a *true premise with a false conclusion* — the wheels and SPA genuinely are baked into the api/web layers; those layers were simply never added to the bundle. |

Remaining open: D-C, D-D, D-E, D-E2, D-F, D-F2, D-G, D-H, D-I, D-J, D-K, D-L, D-M, D-N, D-O, D-P.

### 5.1 Still open

| # | Decision | Blocks |
| --- | --- | --- |
| **D-C** | Digest enforcement: object-store `ChecksumSHA256` on the presigned PUT (needs the browser to send the header, may need a MinIO bump) vs server-side verify-before-promote | `S-upload-identity` |
| **D-D** | Approval: raw download vs preview; explicit reviewer acknowledgement; fail-closed while loading/forbidden/changed. *(Digest binding is already implemented — no longer a question.)* | `S-approval-content` |
| **D-E** | Does the digest reach the **running stack**? A manifest-only digest is not an owner-selectable alternative — either the running stack is verified or the claim is narrowed | Stage 3 |
| **D-E2** | Release-signing key custody — the first **vendor-held** secret in this product. Accept, or ship unsigned manifests with out-of-band hash comparison | `S-build-identity` |
| **D-F** | Container hardening scope: the full capability inventory, or non-root + env split only | `S-container-identity` |
| **D-F2** | At-rest: implement the advertised controls, or narrow the language to operator responsibility (recommended for a single-host D1 product) | `S-doc-truth` |
| **D-G** | Flip scans blocking now with an expiring baseline? ⚠ that baseline file is itself a list of the product's weaknesses | `S-image-ratchet` |
| **D-H** | Repair vs explicitly archive the 59 broken `docs/superpowers` links | `S-doc-truth` |
| **D-I** | Realm baseline: single-source `BASELINE` + generated export + reconciler + drift alarm (recommended). Plus the MFA lockout invariant (**R66 candidate**) | Stage 4 auth |
| **D-J** | Dashboard: one filter-not-403 `GET /dashboard/summary` (recommended; contract change, no new key) vs client-side gating (cheaper, but a SYSTEM-scope probe blinds a bound Process Owner) | `S-capability-truth` |
| **D-K** | Rate limiting requires a custom Caddy build — accept, or rely on app-level limits only | `S-abuse-liveness` |
| **D-L** | Adopt a production-browser accessibility harness as a required check | `S-contrast`, Stage 5 |
| **D-M** | Per-task delivery contract: which tasks are at-most-once vs at-least-once | `S-celery-contract` |
| **D-N** | Are generated contract artifacts authoritative? If not, delete the claim | `S-contract-authority` |
| **D-O** | Coverage: measure-only now, threshold later — and only on a combined figure | `S-coverage-policy` |
| **D-P** | Download accountability: proxy high-assurance downloads (contradicts D1's presign rationale) vs ingest object-access logs | `S-download-accountability` |

**Register candidates:** WORM retention + threat boundary (D-A) · recovery-set atomicity, amending R37
(D-B) · release identity and rollback semantics, **R65 candidate** (D-E) · the MFA lockout invariant,
**R66 candidate** (D-I). Each is the owner's call, not implementation prose.

**Permission catalog stays at 102 keys for the entire programme.** No finding requires a new key. Reaching
for one is a signal the fix has drifted (R38).

---

## 6. Execution rules

1. **Migration ledger.** Head is `0085`; read it with `cd apps/api && uv run alembic heads`, never `ls`.
   Alembic is a single tree, so these are **serialized in this order** and a slice that lands out of
   order must rebase its revision:

   | # | Slice | Contents | Certainty |
   | --- | --- | --- | --- |
   | `0086` | `S-worm-retention` | `blob.object_version_id`, `retention_asserted_at/until`; index on `document_version.source_blob_sha256`; `system_config.document_worm_period`; additive `event_type` | definite |
   | `0087` | `S-db-grants` | privilege-only REVOKE/column-scoped GRANT on the five tables (no `alembic check` drift — the `0072` precedent) | definite |
   | `0088` | `S-recovery-generation` | `backup_generation` ledger + additive `event_type` | definite |
   | `0089` | `S-deploy-rollback` | `RELEASE_STAGED/DEPLOYED/VERIFY_FAILED/ROLLED_BACK` event types | definite |
   | `0090` | `S-mfa-enrollment` | `USER_MFA_RESET` event type | definite |
   | `0091` | `S-restore-job-recovery` | restore-job status/id columns | **conditional** — decide in the slice; if it lands, renumber the two below |
   | `0092` | `S-abuse-liveness` | worker/beat heartbeat (or reuse `0074`'s alarm table — prefer reuse and drop this) | **conditional** |
   | `0093` | `S-audit-tail-policy` | alarm-config column | **conditional** |

   Enum extensions use `ALTER TYPE … ADD VALUE` with a no-op downgrade (the `0011` pattern) and must
   source their tuples from the ORM `*_VALUES`, not a retyped list (the `0010` precedent).
2. **A green new test is unproven until observed RED against HEAD.** The evidence work caught six
   proposed proofs that pass over broken code: L-09's Blob assertion, M-24's Referrer-Policy substring,
   NEW-02's `BackupError` stub, M-22's default refetch-on-mount, `S-restore-target`'s triad leg, and
   `S-db-grants` without `blob`. Where a defect is *pinned as intended* (M-01), the honest proof is an
   inverted test, mutation-verified — say so rather than manufacture a RED.
3. **Pin every MSW fixture to the real backend serializer.** Several slices write a surface's first test.
4. **Run the full `/check-web`.** A jest-dom matcher needs `import { expect, it } from "vitest"`; a
   per-file vitest run is green while `tsc` fails.
5. **Integration assertions stay delta-based and self-provisioning** — one shared DB, `.test_durations`
   shard composition that moves under you, and `audit_event` writes must land in a seeded monthly partition.
6. **`.github/workflows/ci.yml` is the most contended file**; `Caddyfile` + `test_caddy_headers.py` and
   `scripts/install.sh` (four slices, plus an appliance twin) are next. Keep them serial and mirror the
   appliance, or a change in one re-widens what the other narrowed.
7. **`main` is unprotected and the five "core" checks are convention.** Green ≠ done unless all 12 are.
8. **R61 on every docs sweep** — the backstop flags 4-segment clause literals as IPv4-shaped, and every
   example host must stay a placeholder.

---

## 7. Completion gate for this planning pass

| Requirement | State |
| --- | --- |
| Every original finding accounted for exactly once | ✅ 57 primary rows, duplicates removed, M-01 restored |
| NEW-03…NEW-08 use corrected claims and evidence | ✅ 03 split 3 ways; 04–07 refuted-as-phrased with narrowed deltas; 08 re-premised |
| M-01 explicit | ✅ own row, own slice, own inverted-test proof |
| Four risky-boundary designs decision-ready | ✅ DR-1…DR-4 with options, recommendations, acceptance, residual risk; D-A1/D-B1/D-B2 settled and the remaining owner decisions listed in §5.1 |
| Every slice has executable acceptance, dependency, rollback, documentation criteria | ✅ — *was falsely green in the first draft; six Stage-4 slices and all twelve Stage-6 slices were labels only. Filled in after Codex checkpoint A blocker E-2.* |
| Wave 4 replaced by real slices | ✅ 12 Stage-6 slices, each with boundary, files, falsifying proof, commands, cases, migration/rollback, doc truth, residual |
| Severity and release-blocking agree with sequencing | ✅ — *corrected after A/D-1 (M-15's "immediate release fix" wording contradicted its non-RB flag) and A/D-2 (the NEW-01a ⟷ H-01 pair spanned two stages).* |
| Codex checkpoints A–C returned no unresolved planning blockers | ✅ **all three run, every blocker reconciled at the end of that planning pass.** **A:** 5 blockers (B-1, D-1, D-2, E-1, E-2). **B:** 1 design claim factually wrong (DR-2's immutability premise) + 14 surviving scenarios, all promoted to acceptance criteria (§2.5). **C:** 4 blocker classes — four proposed RED proofs that would have **passed**, six label-slices, migration accounting, dependency mismatches. See §7.1. Checkpoint D implementation findings and fixes are recorded separately in §9. |
| During checkpoints A–C, no production source, audit evidence, operator data, or live state modified | ✅ |

---

### 7.1 What the Codex checkpoints changed — recorded, not smoothed over

Three review passes, ten blockers, one factually wrong design claim. Worth keeping visible, because the
pattern in them is the same pattern the audit found in the product.

**The most instructive: I wrote the RED-against-HEAD rule and then broke it four times.** Checkpoint C
found that `S-renderer-proof`'s QR decode, `S-audit-tail-policy`'s row deletion, `S-workflow-edges`'
empty pool, and `S-contract-authority`'s regenerate/diff would all have **passed** against HEAD —
`watermark.py:99` already encodes the real URL, `verify.py:100` already reports chain deletion,
`engine.py:192` already returns false on an empty pool, and `.contract.lock` currently matches. Each is a
genuine *coverage* gap where the code is already correct, and each was written up as if it were a defect.
They are now labelled coverage deltas with mutation-verification instead, which is the honest proof for
"correct but unguarded".

**Checkpoint B found a load-bearing premise false.** DR-2 argued no MinIO quiesce was needed because
content-addressed objects can only be deleted, never changed. `_finalize_sync` copies to the target key
with no existence check, on a versioned bucket — so a repeat promotion makes a new version at the same
key, and the snapshot records no `VersionId`. Combined with H-03 (bytes are never checked against the
claimed SHA), two promotions of one "sha" can carry different bytes. That reordered the programme:
`S-upload-identity` now precedes `S-recovery-generation`.

**Checkpoint A caught a false green in the completion table** — "every slice has executable acceptance"
was ticked while eighteen slices were labels. Checkpoint C then caught that the first fix was itself
partial: six more slices, including all four of the supposedly-split edge slices, were still sharing one
proof. Both are now filled in.

The generalisable lesson, and the reason it is recorded here rather than quietly fixed: **a plan that
asserts its own completeness is exactly as trustworthy as a green test suite that was never mutation-
verified.** The v1 plan claimed to account for every finding and had dropped M-01 entirely.

## 8. Immediate, decision-independent batch

The branch implements these changes without requiring any of the open decisions above:

1. **`.gitignore` the audit output.** Before this batch it was untracked but not ignored, so
   `git add -A` could stage screenshots of a live authenticated instance. It is now ignored; the
   `check-no-site-data.sh` backstop remains necessary because it cannot inspect PNG contents.
2. **`S-upgrade-safety`'s two edits** — copying a guard that already exists one module over, in the
   operator's most destructive command. (The `lock_timeout` half needs a decision; the two edits do not.)
3. **`S-image-ratchet`'s decision-independent parts** — `cryptography 50.0.0` + `starlette 1.3.1`,
   plus reviewable lockfile attributes. Blocking advisory policy still requires D-G.
4. **M-15's CSP token** — with a running-edge blob/external-origin browser probe; the full authenticated
   VisualDiffViewer route remains part of `S-edge-headers` rather than being claimed here.

## 9. Checkpoint D implementation closure

The decision-independent batch received three adversarial implementation reviews. The first found that
setup and engine disposal still sat outside the claimed operational-failure boundary; the follow-up
moved setup under the guard and made engine disposal best-effort. The second found the same defect one
scope inward: SQLAlchemy's session context manager can raise from `AsyncSession.close()` after a return
or while another exception is pending. That could turn a committed `UPGRADE_COMPLETED` result into a
contradictory `UPGRADE_FAILED`, or replace `CancelledError` with an ordinary orchestration failure. The
third found that the first cleanup tests did not prove close was attempted, and that an orchestration
failure after Alembic succeeded dropped the verified archive path the CLI needs for recovery guidance.

The closure explicitly manages and best-effort closes both the primary upgrade session and the fallback
failure-audit session. Ordinary close failures are logged without replacing the primary return or
pending exception; cancellation and process-exit signals still propagate. Three integration regression
families in `tests/integration/test_restore.py` cover five cleanup cases and assert exactly one close:

- `test_upgrade_session_close_failure_preserves_committed_success`
- `test_upgrade_session_close_failure_does_not_replace_base_exception` (`CancelledError`, `SystemExit`)
- `test_upgrade_failure_audit_close_failure_does_not_swallow_base_exception` (both signals)

The original three cleanup/cancellation cases were observed failing before the fix. Deleting either
explicit close call afterward makes the owning close-count assertion fail. A separate regression,
`test_upgrade_failure_after_migration_keeps_exact_recovery_pointer`, was observed failing before the
post-migration archive pointer was carried into both the structured result and terminal audit row.

The same pass also made the earlier proofs more exact: sessionmaker construction joins the setup-failure
matrix, the engine-disposal double proves disposal actually ran and disposes its real inner engine, each
run-scoped audit assertion retains every terminal failure before requiring exactly one, and the three
original failure tests no longer rewrite the shared backup-policy destination.

This closes the review blockers for the immediate batch; it does **not** mark the full
`S-upgrade-safety` slice complete. Single-flight execution, operation identity, orphaned-`STARTED`
reconciliation, migration lock/statement timeouts, the shared typed backup-result validator,
self-contained recovery eligibility, and the CLI's preliminary organization lookup remain owned by that
slice. A structured `audit_recorded: false` signal is also still absent when the best-effort terminal
audit cannot be written; the current contract deliberately returns the primary failure and logs the
audit under-claim. Cancellation while `build_durable_backup` or Alembic is already running in
`asyncio.to_thread` also remains unsafe: cancelling the awaiting task does not stop the side-effecting
thread. Operation identity, single-flight execution, and reconciliation must define that boundary.

Fresh integrated evidence on 2026-08-05:

- the cleanup/cancellation/exit regression families passed, their close-attempt assertions were
  mutation-checked, and the post-migration recovery-pointer regression was observed RED then GREEN;
- the complete restore/upgrade integration module passed 22 tests using PostgreSQL client 16.14;
- the full integration suite passed 1,015 tests with zero failures; its two shared-DB management-review
  skips remain the open M-29 test-isolation finding rather than being counted as coverage;
- the API unit gate passed 1,282 tests with one release-ceremony skip, Ruff and format checks were clean,
  and strict mypy reported no issues in 435 source files;
- `uv lock --check` passed; replacing lockfile `-diff` attributes with `linguist-generated=true` made
  the normal text diff reviewable and confirmed that only `cryptography` 48.0.0→50.0.0 and `starlette`
  1.2.1→1.3.1 moved in `uv.lock`;
- the exact committed Caddy configuration validated in `caddy:2`; Chrome loaded a faithful
  fetch→Blob→object-URL image at 2×3 natural pixels, while a hostname-distinct external image produced
  an enforced `img-src` violation and no upstream request. The full authenticated VisualDiffViewer
  route remains part of the broader `S-edge-headers` acceptance;
- `scripts/check-no-site-data.sh` passed. Raw authenticated screenshots and machine evidence remain
  ignored under `audit-results/` and are intentionally excluded from version control.

# Audit remediation programme — validating the 2026-08-03 Codex audit and fixing what it found

> **Historical status at creation:** proposal, awaiting owner approval; nothing had been implemented.
> **Source audit provenance:** local-only `audit-results/2026-08-03/AUDIT.md` at revision `376ec1e`
> (disposition "not release-ready"). Raw authenticated/machine evidence is intentionally ignored and
> absent from Git under R61; this committed document is the sanitized synthesis, not a repository link
> to that private evidence.
> **Validation method:** 42 agents — 16 cluster verifiers reading the actual source for every cited
> `file:line`, then an adversarial challenger per finding tasked with *overturning* the verifier's
> verdict, then a completeness critic hunting what the audit never looked at.

---

## 1. Validation verdict

**Every Codex finding is real.** 58 findings assessed (the 57 in the report plus the coverage table,
which the verifier promoted to a finding in its own right):

| Verdict | Count |
| --- | ---: |
| CONFIRMED | 51 |
| PARTIAL (claim true, consequence narrower than stated) | 7 |
| REFUTED | **0** |
| Already-known residual | 0 |

Not one finding survived as invalid, and the adversarial pass overturned **zero** verdicts. That is an
unusually clean audit — worth saying plainly, because the rest of this document is a long list of work.

### 1.1 Where severity moved

The audit's severity labels were re-derived independently rather than inherited. Thirteen moved down,
two moved up:

| Change | Findings |
| --- | --- |
| High → Medium | H-02, H-04, H-05, H-07, H-08, H-10, H-11, H-12 |
| Medium → Low | M-04, M-09, M-12, M-13, M-22 |
| **Medium → High** | **M-15**, **M-18** |

The downgrades are not disagreements about the facts — they are disagreements about blast radius. Three
examples:

- **H-12 (show-once credential loss)** — the audit says a lost password is "permanently lost". It is
  not: `POST /users/{id}/temporary-password` reissues one, and R64 designed that path deliberately. The
  defect is real (a route change silently destroys a credential Keycloak has already applied) but its
  cost is an extra admin round-trip, not a lockout.
- **H-02 (immutable tables are writable)** — true, but *narrower and more embarrassing* than stated. The
  repo has a genuine REVOKE house style: 13 tables are properly protected (`audit_event`, `capa_stage`,
  `disposition_event`, `acknowledgement`, `kpi_measurement`, `drift_scan`, `improvement_initiative_stage_event`,
  …). Exactly five were missed — `document_version`, `blob`, `record`, `evidence_blob`, `import_decision`
  carry zero REVOKEs. This is a gap in an existing pattern, not an absent pattern.
- **H-11 (setup-state failure renders as first-run)** — the server-side latch still protects every
  mutation, so this is operationally misleading rather than a security hole.

The two upgrades matter more:

- **M-15 → High.** This is not a polish item, it is a **shipped feature that does not work**. The Caddy
  CSP is `img-src 'self' data:` (`Caddyfile:79`) while `VisualDiffViewer.tsx:54` mints a `blob:` object
  URL. The visual diff renders nothing behind the shipped edge. The fix is one token.
- **M-18 → High.** 342 violating nodes on 28/28 routes, in both themes, against a project bar that
  `CLAUDE.md` states as WCAG 2.2 AA + colour-safe RAG. A stated conformance target missed on every
  single route is a product claim that is false, not a backlog item.

### 1.2 What the audit *missed*

The completeness pass found eight areas the audit never opened. Five are verification gaps; **three are
confirmed shipped defects**, and one of them is more serious than anything in the audit's own High list.

#### NEW-01 (Critical) — `worm_lock_period` is accepted, validated, stored, and never applied

This is the load-bearing claim of the entire product. `docs/06-records-and-evidence.md:48` states that
immutability is "a storage-layer guarantee, not merely an application convention."

`retention_policy.worm_lock_period` is declared (`db/models/retention_policy.py:62`), validated to be
≥ the retention duration (`services/records/retention_policies.py:113-125`), accepted and serialized over
the API (`api/retention_policies.py:46,57,74,125`) — and

```
$ rg -n "put_object_retention|ObjectLockRetainUntilDate|ObjectLockMode" apps/api/src -g '*.py'
storage.py:297:   ... BypassGovernanceRetention=True
```

**Zero** code paths ever set per-object retention. The only object-lock write in the codebase is the R27
erasure *bypass*. Every object receives the flat bucket default, `WORM_RETENTION=30d`
(`infra/compose/minio/minio-init.sh:28`).

So a Record under a 10-year retention policy with a matching `worm_lock_period` is **storage-unlocked
after 30 days**. The application-level validation manufactures the appearance of a guarantee that the
storage layer was never told about. Codex missed this because Records/Retention has no SPA route
(`CLAUDE.md`: API/worker-complete, no dedicated browser surface) and a browser-plus-source audit never
reached it.

#### NEW-02 (High) — `easysynq upgrade` migrates over a backup that failed its own checksum

`services/upgrade.py:6-7` promises "a pre-backup failure ABORTS the upgrade — never migrate without a
safety net." Two defects break that promise:

- **The catch is too narrow.** `upgrade.py:129` catches only `BackupError`. `OSError`/`PermissionError`
  (disk full, read-only backup mount — the canonical failure), `psycopg.OperationalError`, and
  `BackupCryptoError` (a *sibling* of `BackupError`, not a subclass) all escape `run_upgrade`, breaking
  its own `"""Never raises"""` docstring at `upgrade.py:105`. Result: an `UPGRADE_STARTED` row committed
  with no terminal event, permanently, in an append-only chain.
- **The verification result is never read.** `archive.py` reports a checksum mismatch by *returning*
  `verified=False`, never by raising. `run_upgrade` takes `backup["archive"]` and proceeds straight to
  `alembic upgrade head`. `UPGRADE_FAILED.after.pre_backup_archive` then points the operator's only
  recovery pointer at an unusable file.

The damning part: **this exact hardening already exists one module over.** `services/backup/service.py:191-209`
fails closed on `not out.get("verified", False)` with a comment that reads, verbatim, *"reports success
over an unusable archive: the finding's whole scenario, one layer down"* — and uses a broad
`except Exception` at :212. Neither guard was propagated to the upgrade path. `run_upgrade` has exactly
two callers and one test, which stubs everything green and asserts `result == "OK"`.

#### NEW-03 (High) — the Keycloak realm implements none of the auth policy the docs claim

`infra/compose/keycloak/realm-export.json` — verified by parsing the file:

```
passwordPolicy        = length(12) and notUsername(undefined)
bruteForceProtected   = True
otpPolicyType         = ABSENT      accessTokenLifespan   = ABSENT
ssoSessionIdleTimeout = ABSENT      ssoSessionMaxLifespan = ABSENT
revokeRefreshToken    = ABSENT      refreshTokenMaxReuse  = ABSENT
requiredActions       = ABSENT      authenticationFlows   = ABSENT
```

No MFA, no token/session lifetimes, no refresh-token rotation, permanent `http://localhost/*` wildcard
redirects on the public PKCE client. The audit probed authentication thoroughly and returned a clean
verdict — over a realm that never got opened. R64 already names this ("the realm enforces no MFA") as the
reason a credential reset is an account-takeover primitive; the realm itself was never hardened.

#### NEW-04 … NEW-08 (verification gaps, no defect proven)

Five subsystems the audit's own scope table claims to cover but never exercised. Each is a place where a
defect would be invisible to every gate we currently run:

| # | Area | Why it matters |
| --- | --- | --- |
| NEW-04 | Audit hash chain + off-host checkpoint | `docs/12:3` says the product's value "rests entirely" on this. No verification-matrix row, `verify-chain` never called, `anchor_checkpoint` never exercised, no tamper test. Its two known holes (newest-checkpoint-only, unchained tail reported `pending`) sit unexamined. |
| NEW-05 | Vault → mirror regeneration (D2) | The lifecycle journey stopped at `Effective`. Nobody checked the released version reached the published tree, opened the controlled copy to confirm `CONTROLLED COPY` banding + verify QR, or exercised `atomic_swap` / tamper-quarantine. This is the artifact an external auditor actually reads. |
| NEW-06 | Retention / disposition / R27 legal erasure | The only path that permanently destroys controlled bytes and deliberately bypasses object-lock — never exercised, and the direct consumer of NEW-01 and C-01. |
| NEW-07 | Workflow engine quorum + every negative path | Reported as "22% coverage" and never investigated. The uncovered 78% decides who may sign and when a stage fails: tri-state quorum, early-fail, the distinct-approver guard, fail-closed totality. The live journey was one single-approver happy path. |
| NEW-08 | Celery fleet under redelivery | `task_acks_late=True` across 26 Beat entries makes redelivery routine by design; only 6 of 16 task modules take any lock. No worker was killed or restarted during the audit. |

### 1.3 One process note, unrelated to the findings

**`audit-results/` is not gitignored.** The audit's own verification matrix records "R61 no-site-data:
Pass — audit fixture kept under *ignored* audit output". That premise is false: `git check-ignore` returns
nothing, and `git add -A` would stage **89 files**, including ~5 MB of PNG screenshots of a live
authenticated instance (Keycloak login, admin user roster, org profile).

`scripts/check-no-site-data.sh` reports clean and **cannot** help here — it is a mechanical text scan and
cannot read a PNG. R61 says sanitize at write time because removal cannot undo publication. Recommended
before anything else: add `audit-results/` to `.gitignore`, or move the directory outside the repo.

---

## 2. Decisions only the owner can make

These block or reshape the slices below. They are worth settling before any code moves.

| # | Decision | Why it is yours |
| --- | --- | --- |
| D-A | **NEW-01**: apply per-object retention to match `worm_lock_period`, *or* delete the field and narrow the docs? Applying it is irreversible per object under COMPLIANCE mode and interacts with R27 erasure. | Register-level: it decides whether "storage-layer guarantee" is a true statement. |
| D-B | **C-01**: which object-durability shape — a bytes leg inside the archive (viable at MVP scale), a replicated immutable object snapshot, or `mc mirror` as the docs already promise? This amends R37. | Changes the recovery objective and the operator's storage bill. |
| D-C | **H-03**: enforce the digest at the object store (`ChecksumSHA256` on the presigned PUT, needs the browser to send the header) or re-hash server-side after promotion? The former may need a MinIO bump. | Trades a client change against an API-side streaming cost. |
| D-D | **H-09**: is a download of the raw source bytes enough for an approver, or must there be an in-browser preview? A preview of a non-Effective version is worker-async by construction and must not persist `rendition_blob_sha256`. Should approval *hard-block* until content is available, and should the signed event bind the content digest? | Control-behaviour change → needs a register entry (next after R64). |
| D-E | **H-04/H-06/H-08**: does the digest have to reach the *deployed stack*, or is it only a bundle-manifest fact? This decides whether `easysynq upgrade` becomes real release orchestration or stays a migration runner with honest docs. | Sets the size of the whole release-identity slice (L vs XL). |
| D-F | **H-05**: implement the advertised at-rest controls, or narrow the language to operator responsibility? For a single-host D1 product, narrowing is the honest answer — but it is a product-claim change. | Product positioning. |
| D-G | **H-07**: flip the scans blocking now, with an expiring VEX/baseline file for the unfixable Debian base CVEs? Note that baseline file is itself a list of the product's weaknesses. | Accepting residual risk. |
| D-H | **NEW-03**: harden the realm now (MFA policy, token lifetimes, refresh rotation, exact redirect URIs)? MFA changes every user's login. | Operational impact on every persona. |
| D-I | **M-21**: dashboard audit noise — collapse ten reads into one filter-not-403 `GET /dashboard/summary` (preferred; contract change, no new key), or gate each hook client-side (cheaper but a SYSTEM-scope probe blinds a bound Process Owner)? | Touches doc 12 §4.1 audit-verbosity policy. |
| D-J | Coverage: a `fail_under` threshold is a policy call. Measure first, gate later — and only on a unit+integration **combined** figure. | Same shape as the security-job ratchet. |

**Not a decision:** the permission catalog stays at 102 keys for the entire programme. No finding needs a
new key. If an implementation reaches for one, the fix has drifted (R38).

---

## 3. The programme

30 slices in five waves. Waves are ordered by *what is currently untrue about the product*, not by the
audit's severity labels. Slice grouping is by **shared chokepoint** — same module, same migration, same
review surface — so each is one coherent PR.

### Wave 0 — make the integrity claims true (release-blocking)

Everything here is a case where the system asserts a guarantee it does not provide.

| Slice | Findings | Effort | What changes |
| --- | --- | --- | --- |
| `S-worm-retention` | NEW-01 | M | Apply `put_object_retention` per object at `finalize_worm`, derived from the resolved retention policy; fail closed when the policy cannot be resolved. Backfill decision for existing objects. Test: assert `get_object_retention` returns the policy's date, not the bucket default. **Blocked on D-A.** |
| `S-upgrade-safety` | NEW-02, H-06(a) | S | `services/upgrade.py`: widen the stage-1 catch to `except Exception` (mirroring `service.py:212`); fail closed on `not backup["verified"]` before `_run_alembic_upgrade`, mirroring `service.py:197-209` verbatim so they cannot drift again. `scripts/easysynq`: build `COMPOSE` from the same overlay set `install.sh` used (`EASYSYNQ_PROFILE` is already persisted). Tests must be written to fail against HEAD — a `BackupError` stub cannot see either defect. |
| `S-upload-integrity` | H-03, M-02(a), L-09(c) | L | Server-verifiable content identity before WORM promotion. Same fix serves all three: the presigned-PUT contract carries no verifiable condition today. **Blocked on D-C.** |
| `S-backup-objects` | C-01 | XL | An object-bytes leg (`services/backup/objects.py` + a `backup objects` CLI verb + Beat task, enumerated under the same REPEATABLE READ snapshot the dump uses), and a cutover-shaped restore: today objects land flat at `{restore_id}/{sha}` while restored `blob` rows keep their original `(bucket, object_key)` — and reads resolve the *stored literal* (`api/documents.py:1974`), so today's shared-scratch-prefix implementation cannot produce a resolvable cutover namespace. Also: restoring an older archive after an R27 erasure now fails the triad, so archives silently expire. **Blocked on D-B.** |
| `S-db-grants` | H-02, L-01 | M | One privilege-only migration `0086` (privilege-only ⇒ no `alembic check` drift — the 0072 precedent). `REVOKE UPDATE, DELETE` on `import_decision`; `REVOKE UPDATE` on `evidence_blob` (keep DELETE for R27); column-scoped grants on `document_version`/`record` following the linker pattern at `0010:229`. Fold in L-01's partition-creation race. Verify head with `uv run alembic heads`, never `ls`. |
| `S-authoring-approval` | H-09, M-22, L-10 | L | A candidate-context pane on `ReviewApprovePage` driven off the existing `useDocumentVersions` payload, with a primary "open the candidate file" action calling the **already-existing, already-gated** `GET /documents/{id}/versions/{vid}/download`. Note the scope correction: the API capability was never missing, only the UI wiring. M-22 is a one-line invalidator fix. **Blocked on D-D** for the hard-block/digest-binding half. |

### Wave 1 — make a release installable and trustworthy

H-04, H-06 and H-08 are **one defect wearing three hats**: nothing addresses the built api/web/keycloak
images by a stable name, so the bundle cannot save them, upgrade cannot deploy them, and the digest
policy has nothing to pin.

| Slice | Findings | Effort | What changes |
| --- | --- | --- | --- |
| `S-release-identity` | H-04, H-06(b), H-08, M-30, M-32 | XL | Give api/web/keycloak a stable `image:` alongside `build:`; a `on: push: tags` release workflow that sets `EASYSYNQ_RELEASE=1` (activating the digest assertion the runbook already promises); an offline overlay with `pull_policy: never`; SBOM + signing + provenance; pinned Actions, explicit workflow permissions, `--frozen` builds. **Blocked on D-E.** |
| `S-container-hardening` | H-01, M-31, M-04 | XL | Per-service env/secret split (Compose `env_file` cannot select keys → explicit maps or Docker `secrets:` with `*_FILE` support in `config.py`), non-root `USER` in both Dockerfiles, multi-stage static web image. Mirror every change into `infra/appliance/` or the appliance re-widens what this narrows. |
| `S-security-ratchet` | H-07, M-27 | M | Take the two Python upgrades now — `cryptography 50.0.0` and `starlette 1.3.1` both clear cleanly, no direct pin blocks either, and the existing gates already exercise Ed25519/AES-GCM/JWT. Then flip the scans blocking with a documented, expiring baseline. **Blocked on D-G.** |
| `S-doc-truth` | H-05, H-04(a), M-33 | L | Narrow every overclaim to what ships: at-rest encryption, the air-gap bundle, protected `main`, the five-required-checks line, R1-R63 → R1-R64, the removed clause rail, the retired paste-a-`sub` flow, checkpoint cadence. Add the honest residual entries. **Blocked on D-F.** ⚠ Every docs sweep collides with the R61 backstop's 4-segment-clause-literal rule. |

### Wave 2 — user-facing correctness

| Slice | Findings | Effort | Note |
| --- | --- | --- | --- |
| `S-edge-trust` | **M-15**, M-05, M-24, M-25, M-23, L-06 | L | One defect: the Caddyfile expresses header/CSP policy as a site-wide block individual routes cannot override. **Start with M-15's one token** (`img-src 'self' data: blob:`) — it un-breaks a shipped feature and the regression pin belongs in the existing `test_caddy_headers.py`. M-05 makes `ip_allow` (and R58's replayed `source_ip`) actually compare against the client rather than Caddy. |
| `S-org-tz-dates` | H-10, M-14 | L | One defect: no org-timezone conversion chokepoint, so governed dates are wrong on write and inconsistent on read. Fix H-10 first — fixing rendering first would make more surfaces *look* right while still writing the wrong instant. DST-aware `orgLocalMidnightIso` in `lib/time.ts`, with the nonexistent-local-midnight rule pinned, not accidental. |
| `S-web-auth-shell` | H-11, M-08, L-11 | M | One defect: no failure/recovery surface, so every abnormal state renders as a permanent spinner, a fresh-install wizard, or an authenticated-looking shell whose calls all 401. Never infer `UNINITIALIZED` from an error. |
| `S-admin-secret-ux` | H-12, L-08 | S | Extend the existing guard predicate from "in flight" to "in flight **or** unacknowledged secret"; add a `beforeunload` guard; make "Done" the single acknowledged exit. |
| `S-web-capability-truth` | M-11, M-21, M-10, M-12 | L | One defect: no shared `action → {key, scope}` source of truth, so affordances probe at SYSTEM scope while the server enforces at resolved ABAC scope. A checked-in capability map plus an apps/api test that walks every `require()` and asserts the map matches. **Blocked on D-I** for M-21. |
| `S-setup-hydration` | M-07, M-09, M-13 | M | One defect: query-data-keyed hydration effects with no dirty guard, converging on the SetupWizard. Snapshot initial values once per entity; suspend hydration after dirty. |

### Wave 3 — accessibility and responsive

| Slice | Findings | Effort | Note |
| --- | --- | --- | --- |
| `S-contrast-tokens` | **M-18** | L | Root-caused, not a 342-node slog. The dominant share falls to **one line**: map `--mantine-color-dimmed` to `--es-text-2` in both schemes (light `#565b6b` = 6.10–6.76:1; dark `#a4a9b8` = 6.61–8.27:1) — retiring most violations without touching any of the 329 call sites. Then stop shipping Mantine's stock indigo as primary; map the `--mantine-color-body/text/default*` family onto the `--es` surfaces. |
| `S-a11y-semantics` | M-19, M-20 | M | Tabs-as-navigation → links with `aria-current`; `h1` per route; real interactive elements for CAPA rows; drop the duplicate banner landmark. |
| `S-responsive-shell` | M-16, M-17 | M | Close the drawer on location change; put wide admin tables in labeled scroll regions; collapse header controls by measured width at 320px. |

**Why 1,458 green web tests with jest-axe did not catch M-18/M-19/M-20 is itself the finding** — establish
what the existing axe setup actually asserts and widen it, or this recurs.

### Wave 4 — depth, protocol, and governance

| Slice | Findings | Effort |
| --- | --- | ---: |
| `S-verify-invariants` | NEW-04, NEW-05, NEW-06, NEW-07, NEW-08 | XL |
| `S-keycloak-hardening` | NEW-03 | M |
| `S-session-revocation` | M-03 | M |
| `S-download-accountability` | M-06, L-02 | L |
| `S-api-protocol` | L-04, L-05 | M |
| `S-worker-liveness` | M-26 | M |
| `S-orphan-reconcile` | L-03 | M |
| `S-web-transfer` | L-07, L-09 | M |
| `S-contract-gate` | M-28 | M |
| `S-ci-gaps` | M-29, COV-TABLE, M-27 | L |
| `S-governance-artifacts` | M-32, M-33 remainder | M |

`S-contract-gate` is a cheap enabler that can jump the queue at any time: `CLAUDE.md` already warns that
no CI job runs `scripts/gen-contracts.sh`, so `.contract.lock` drifts silently on every branch until it
lands.

---

## 4. Execution rules

Carried from `.claude/rules/engineering-patterns.md` and the cross-cutting risk pass. These are the ways
this programme goes wrong.

1. **Migration head serialization.** Head is `0085`. Five slices want a migration. Alembic is a single
   tree — serialize them or rebase, and always read the head with `uv run alembic heads`, never `ls`.
2. **`.github/workflows/ci.yml` is the most contended file** — five slices edit it. Keep them serial.
   `infra/compose/caddy/Caddyfile` + `test_caddy_headers.py` are the second contended pair;
   `scripts/install.sh` is touched by four slices and has an appliance twin that must be mirrored.
3. **Mutation-verify every proof, in the failing direction.** The challenge pass caught *four* proposed
   tests that would pass against the broken code (L-09's Blob assertion, M-24's Referrer-Policy substring,
   NEW-02's `BackupError` stub, M-22's default refetch-on-mount). A green new test is unproven until it
   has been observed RED against HEAD.
4. **Pin every MSW fixture to the real backend serializer.** Several slices write the *first* test for a
   surface (SetupWizard, ShowOncePassword, the approval pane). A hand-typed shape is the repo's documented
   #1 false-PASS.
5. **Run the full `/check-web`.** A test using jest-dom matchers must `import { expect, it } from "vitest"`
   — a per-file vitest run is green while `tsc` fails. Eleven web slices are exposed to this.
6. **Integration assertions stay delta-based and self-provisioning.** One shared DB, `.test_durations`-driven
   shard composition that moves under you, and any `audit_event` write must land in a seeded monthly partition.
7. **State deliberate contradictions between waves in the PR body** so a reviewer does not read them as
   regressions — e.g. `S-doc-truth` writes "the offline path is not supported" and `S-release-identity`
   reverses it.
8. **`main` is unprotected and the five "core" checks are convention.** Nothing mechanically stops a red
   merge; green ≠ done unless all 12 checks are.

---

## 5. Suggested first moves

Independent of every owner decision above, and each individually shippable:

1. `.gitignore` the audit-results directory (§1.3) — the only item with a publication risk that grows.
2. `S-upgrade-safety` (NEW-02) — two small edits to `services/upgrade.py`, copying a guard that already
   exists one module over, closing a defect in the operator's most destructive command.
3. `M-15`'s one token — un-breaks a shipped feature.
4. `S-security-ratchet` part (a) — take `cryptography 50.0.0` + `starlette 1.3.1`; the existing gates
   already cover every affected call path.
5. Settle **D-A** (NEW-01). It is the largest single gap between what the product claims and what it does.

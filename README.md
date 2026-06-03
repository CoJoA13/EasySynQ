# EasySynQ

A **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. A managed **controlled vault**
(PostgreSQL + MinIO WORM) owns the master copy of every controlled document and record; the on-disk filesystem
is a **read-only mirror** regenerated from Released versions only — so document drift becomes an *enforced
invariant* rather than a discipline problem.

## Status

- **Specification:** complete and internally reconciled — see [`docs/`](docs/) (start at
  [`docs/00-overview.md`](docs/00-overview.md); [`docs/decisions-register.md`](docs/decisions-register.md) is
  authoritative).
- **Implementation plan:** [`docs/18-mvp-implementation-plan.md`](docs/18-mvp-implementation-plan.md) (approved).
- **Code:** building the MVP foundation-first, slice by slice (each via a PR with green CI on protected `main`).
  - **S0 — walking skeleton** ✅ — Compose stack, `/healthz`+`/readyz`, reversible Alembic baseline, OpenAPI→client pipeline.
  - **S1 — authentication** ✅ — Keycloak OIDC/PKCE, JWT-vs-JWKS validation, `app_user` + JIT provisioning, `/me`.
  - **S2 — authorization** ✅ — deny-wins PDP/PEP, the closed permission catalog + 8 seeded roles, two-tier grant guard.
  - **S3 — vault** ✅ — document create + the check-out → presigned CAS upload → immutable check-in cycle (MinIO WORM, Redis lock).
  - **S4 — lifecycle** ✅ — the document FSM (Draft→…→Effective) + the atomic single-Effective cutover (SERIALIZABLE + INV-1), 6 named lifecycle actions, R25 singleton index, future-dated Beat sweep.
  - **S5 — approval + SoD** ✅ — the task/decision approval workflow (`POST /tasks/{id}/decision` writes `signature_event`+`task_outcome`+audit in one txn; tasks-canonical), append-only `signature_event` emission on approve/release/obsolete, and the deny-wins separation-of-duties gate (SoD-1 no self-approval, SoD-2 no self-release, SoD-3 auditor independence).
  - **S6 — audit** ✅ — the append-only, monthly-partitioned, hash-chained `audit_event` trail behind DB **role separation** (a non-owner `easysynq_app` role with INSERT/SELECT-only on `audit_event`+`signature_event`, so append-only is structurally enforced — AC#6a); the in-transaction audit writer; the decoupled chain-linker (a dedicated `easysynq_linker` role, advisory-locked, bounded-lag alarm) with a frozen `canonical_serialize` + golden vector; `verify-chain` (detects a mutated row as the first broken link — AC#6b); the off-host `worm_bucket` checkpoint anchor with an honest tamper-evidence soft-gate (R13); and the read-only `/audit-events` API.
  - **S7 — mirror** ✅ — the read-only, Effective-only filesystem mirror, regenerated from the vault via an atomic symlink-repoint swap, mounted RO (AC#2). **S7b** watermarked-PDF rendering (Gotenberg + a deterministic reportlab/pypdf §11.3 band, cached non-WORM rendition); **S7c** the Ed25519 verify-token + QR + the public `GET /verify` (CURRENT/SUPERSEDED/UNKNOWN); **S7d** the per-request export/print stamp.
  - **S8 — first-run setup** ✅ — the 423 setup-latch + the five blocking gates, each shipped as its own slice: **S8a** the bootstrap-of-trust spine (mint-secret → first System Administrator) + org profile + finalize (G-A/G-E); **S8b** the WORM-verify probe (G-B); **S8b2** the backup→restore-into-scratch drill + durable backup (G-C / AC#5); **S8c** auth-config + a non-bootstrap login proof (G-D) + the client router; **S8d** the Users & Roles admin (roster / invite / enable-disable + role/override management).
  - **S9 — clause IA + `clause_mapping`** ✅ — the read-only ISO 9001:2015 clause spine (an 83-clause seeded catalog, the 20 ★ mandatory documented-information items) + the M:N document↔clause mapping (`GET /clauses`, flat `…/clause-mappings` sub-resources) + the lifecycle submit gate (a document needs ≥1 clause mapping before review).
  - **S9b — clause-aligned mirror tree** ✅ — the §10.3 `PLAN/DO/CHECK/ACT → {NN}-{clause}/` tree: each Effective doc lives once under its numerically-lowest mapped clause and is reached from every other mapped clause via a relative symlink (clause 7 splits PLAN/DO); an unmapped (pre-S9 upgrade) doc lands in `_unmapped/`.
  - **S9c — process IA backend** ✅ — the Clause 4.4 process graph (`process`/`process_edge`/`process_link` + `org_role`/`supplier` FK targets) with `GET /processes(/{id})(/map)` + `SEED→ACTIVE` authoring (`POST`/`PATCH /processes`, `…/edges`) + the M:N `…/process-links` sub-resource, all audited (`process.create`/`assign_owner` seeded-but-ungranted → grant via override until the role UI).
  - **S9d — by-process mirror index** ✅ — the §10.3 secondary `current/by-process/{name}/` tree of relative symlinks into the clause-tree doc folders (always-on; bytes never duplicated). **Completes the mirror epic.**
  - **S10 — search + the Compliance Checklist** (next) — the org-wide checklist (reads `is_mandatory_star` + `clause_mapping` coverage, doc 13), `filter[clause_refs][has]` on `GET /documents`, faceted search; then **S11** (backup/restore-CLI hardening + the exit slice).

  Run it: `just up s`, then open **http://localhost** (dev login `demo` / `Demo-Password-1`).

## Repository layout

```
packages/contracts/   OpenAPI-first source of truth (openapi.yaml → generated server models + TS client)
apps/api/             FastAPI / Python 3.12 (the vault, lifecycle, PDP/PEP, audit)
apps/web/             React/TS + Mantine + Tailwind SPA
migrations/           Alembic (single tree)
infra/compose/        Docker Compose stack (S/M profiles) + Caddy / Keycloak / MinIO config
scripts/              install.sh, easysynq (admin CLI), gen-contracts.sh
docs/                 the specification + the implementation plan
```

## Quick start (developer)

Requires Docker Compose v2, [uv](https://docs.astral.sh/uv/), Node 20+, and [just](https://github.com/casey/just).

```bash
just setup            # install deps, pre-commit, generate the contract
just up s             # bring up the stack (S profile)
# the API is reachable behind Caddy; /healthz and /readyz report status
```

> The four locked foundational decisions (D1–D4) and the Decisions Register (R1–R37) govern everything; see
> [`docs/00-overview.md`](docs/00-overview.md). Data never leaves the org boundary; there is no phone-home.

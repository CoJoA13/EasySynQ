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
  - **S7 — mirror** (next) — the read-only filesystem mirror of Effective-only versions, regenerated from the vault.

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

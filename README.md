# EasySynQ

**A self-hosted ISO 9001:2015 Quality Management System where document drift is engineered out, not policed.**

[![CI](https://github.com/CoJoA13/EasySynQ/actions/workflows/ci.yml/badge.svg)](https://github.com/CoJoA13/EasySynQ/actions/workflows/ci.yml)

EasySynQ runs your quality system on your own server, in the browser — and answers the one question every QMS eventually fails: *"which version governs?"*

## What it is

Most quality systems keep their master documents on a file share anyone can overwrite. The result is drift — stale PDFs on desktops, competing copies, and an audit-prep scramble every year.

EasySynQ inverts that. A managed **controlled vault** (PostgreSQL + WORM object storage) owns the master copy of every controlled document and record. The on-disk filesystem is only a **read-only mirror**, regenerated from *Effective* versions. Authority flows **vault → mirror, never the reverse** — so document drift stops being a discipline problem and becomes a solved engineering problem.

The interface flows the way ISO 9001 itself flows — the clause spine, the process map, the PDCA cycle — so the system reads as the standard made operable, not a database with forms bolted on. The payoff is **audit readiness as the default state**: an external auditor can confirm any document's status — effective version, signatures, lineage — in seconds.

> **The core idea:** the vault is the source of truth; the disk is a read-only mirror; drift is an enforced invariant, not a rule people are asked to follow.

## Features

**Controlled documents**
- Master copy of every document held in the vault; check-out → upload → **immutable check-in** under a lock
- Canonical **7-state lifecycle** (Draft → InReview → Approved → Effective → UnderRevision → Superseded → Obsolete)
- **At most one Effective version** per document at any time, database-enforced, with atomic scheduled go-live
- **Document Change Requests** with redline/diff, annotations, and where-used impact
- Periodic-review scheduling and controlled obsolescence

**Read-only mirror & controlled copies**
- Filesystem mirror regenerated from Effective versions only, mounted read-only — the disk can never become a competing truth
- Organized two ways at once: a **clause-aligned PLAN/DO/CHECK/ACT tree** and a **by-process index**
- **Watermarked controlled-copy PDFs**, each carrying a QR **verify token**
- Public `/verify` endpoint (CURRENT / SUPERSEDED / UNKNOWN) — confirm any printout at a glance
- Drift detection: mirror re-hash and auto-correct, stale-revision alerts, scheduled re-review

**Records, evidence & traceability**
- Immutable records; those captured under a controlled document are **pinned to the exact version** in force at capture
- Retention schedules with **controlled disposition** (never a silent delete)
- **Evidence Packs** generated on demand, with revocable external share links
- Clause- and process-scoped traceability from controlled documents to the records that evidence them — the basis for Evidence Packs

**ISO 9001:2015 alignment**
- Seeded **83-clause catalog** plus the 20 ★ mandatory documented-information items
- M:N document↔clause mapping and a **Clause 4.4 process map** with ownership
- **Compliance Checklist** scoring every mandatory clause COVERED / PARTIAL / GAP
- **Quality Objectives** (lifecycle + KPI trend charts) and **Management Review** (inputs → outputs → filed minutes pack)
- **Improvement Initiatives** (clause 10.3)
- Permission-filtered **document metadata search** (PostgreSQL FTS behind an OpenSearch-ready seam)

**Workflows & notifications**
- Approvals with enforced **separation of duties** (no self-approval, no self-release, auditor independence)
- Internal **Audits → Findings → CAPA**, with overdue tracking
- Read-and-understood **Acknowledgements**
- A **My Tasks** inbox with business-day-aware reminders, two-tier escalations, and quiet-hours

**Integrity & access control**
- **Append-only, hash-chained, monthly-partitioned audit trail**, structurally enforced by database role separation
- Append-only approval **signature events** — the reserved 21 CFR Part 11 e-signature hook (full e-signatures are architected, not yet built)
- Off-host tamper-evidence checkpoint anchor plus chain verification
- **Hybrid RBAC + ABAC** authorization — deny-by-default, deny-always-wins, scoped to system/process/folder/document with per-user overrides

**Deployment & operations**
- **Self-hosted** on a single Linux host via Docker Compose (S/M sizing profiles) — air-gap friendly, no phone-home
- Guided **first-run setup** with blocking trust gates (activate → organization → WORM verify →
  backup/restore drill → authentication → finalize); users and roles continue in Administration
- Encrypted database/manifest archives (no object bytes), source-store-dependent **restore integrity
  verification** (not cutover-ready), and health-gated upgrade mechanics whose production eligibility
  still requires a self-contained recovery proof
- **Ingestion engine** to import an existing QMS file tree (scan → classify → dedup → review → commit)
- Optional **1-click Hyper-V appliance** (VHDX + seed ISO + installer)

## Who it's for

A small, role-segmented quality team inside one organization. EasySynQ is built around eight canonical personas (the seeded permission catalog adds a handful of governance roles beyond these):

- **System Administrator** — runs the server, users, backups; sits *outside* the QMS (holds no document permissions)
- **Quality Manager** — owns the QMS: objectives, audits, CAPA, the compliance checklist
- **Process Owner** — accountable for a slice of the process map
- **Author** — drafts and revises controlled documents
- **Approver** — reviews and approves documents (the separation-of-duties counterparty; release is a separate hand)
- **Internal Auditor** — plans audits, raises findings, drives them to CAPA
- **Employee** — reads effective documents and acknowledges assignments
- **External Auditor** — read-only, time-boxed; verifies currency and traceability

## Tech stack

React + TypeScript + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 · PostgreSQL 16 · MinIO (WORM object storage) · Redis · Celery workers · Keycloak (auth) · Gotenberg / LibreOffice (rendering) · Caddy (TLS) · Docker Compose. OpenAPI-first, deny-by-default, and air-gap-safe (system fonts only, no external calls).

## Quick start (developer)

Requires Docker Compose v2, [uv](https://docs.astral.sh/uv/), Node 22, and [just](https://github.com/casey/just).

```bash
cp .env.example .env
chmod 600 .env
```

For the local developer stack, set these values in `.env` before starting:

```dotenv
OIDC_ISSUER=http://localhost/realms/easysynq
OIDC_JWKS_URL=http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs
OIDC_DISCOVERY_URL=http://keycloak:8080/realms/easysynq/.well-known/openid-configuration
```

Then run:

```bash
just setup      # install deps, pre-commit hooks, generate the API contract
just up s       # bring up the stack (S profile)
just demo-user  # create the local demo login
./scripts/easysynq setup mint-bootstrap
```

Then open **http://localhost/setup**, sign in with the dev account `demo` /
`Demo-Password-1`, paste the one-time secret, and complete the six setup screens. After finalization,
normal navigation is available at **http://localhost**. `/healthz` and `/readyz` report stack health
behind Caddy. See the [fresh Linux developer setup](docs/runbooks/fresh-linux-setup.md) for the
complete environment and platform notes.

## Repository layout

```
packages/contracts/   OpenAPI-first source of truth (openapi.yaml → server models + TS client)
apps/api/             FastAPI / Python 3.12 — the vault, lifecycle, PDP/PEP, audit
apps/web/             React / TypeScript SPA (Mantine + Tailwind)
migrations/           Alembic (single tree)
infra/compose/        Docker Compose stack + Caddy / Keycloak / MinIO config
infra/appliance/      1-click Hyper-V appliance build
scripts/              install.sh, the easysynq admin CLI, contract generation
docs/                 the full specification + operator runbooks
```

## Project status

The original **MVP foundation (S0–S11)** and the subsequent ISO 9001 workflow families are delivered on
protected `main`. The React application has routed working surfaces for document control, tasks,
notifications, search, compliance, reporting, CAPA/complaints/NCRs, audits, ingestion, drift,
objectives, management review, DCRs, improvement, risks, context, and interested parties.

Records, retention/disposition, and Evidence Packs are implemented as API/worker capabilities and
feed routed workflows such as CAPA evidence linking, but they do **not** yet have dedicated browser
management screens. “Shipped” in the slice history therefore does not always mean “available as a
standalone page in the SPA.”

For the per-slice history and the deliberate v1 / v1.x deferrals, see [`CLAUDE.md`](CLAUDE.md) and [`docs/slice-history.md`](docs/slice-history.md).

## Documentation

Start with the task-oriented manuals:

- [Installation Guide](docs/manuals/installation-guide.md)
- [User Manual](docs/manuals/user-manual.md)
- [Administrator & IT Manual](docs/manuals/administrator-it-manual.md)

The full specification lives in [`docs/`](docs/) — [`docs/00-overview.md`](docs/00-overview.md) is its front door and [`docs/decisions-register.md`](docs/decisions-register.md) is the authoritative design source. [`PRODUCT.md`](PRODUCT.md) captures the product vision and design principles, while hands-on operator procedures live in [`docs/runbooks/`](docs/runbooks/).

---

> **Self-hosted and single-organization by design.** Data never leaves your infrastructure — no SaaS, no multi-tenancy, no phone-home. Built on an ISO 9001:2015 foundation and *architected* (not yet built) to extend toward 21 CFR Part 11 e-signatures and additional standards.

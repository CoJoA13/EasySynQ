# EasySynQ

A self-hosted quality management system for controlled documents, approvals, records, and audit evidence.

[![CI](https://gitlab.com/synqsuite-group/EasySynQ/badges/main/pipeline.svg)](https://gitlab.com/synqsuite-group/EasySynQ/-/pipelines)

EasySynQ helps a quality team track which document version governs, who approved it, and which
records belong to it. PostgreSQL and WORM object storage hold the controlled master copy. A
read-only filesystem mirror is regenerated from Effective versions; it cannot replace the vault.
The browser interface organizes work around ISO 9001:2015 clauses, processes, and the PDCA cycle.

> **Readiness:** development and verification are ongoing. Source-independent recovery and the
> complete production recovery/cutover proof remain open. Review the [current status](docs/current-status.md)
> and [remaining work](docs/open-residuals.md) before planning an installation or upgrade. Passing CI
> covers the checks that ran; it does not establish complete recovery or security clearance.

## Start here

| I want to… | Read |
| --- | --- |
| Understand the product | [Product vision](PRODUCT.md) and [specification overview](docs/00-overview.md) |
| Install or evaluate a stack | [Installation guide](docs/manuals/installation-guide.md) |
| Use the quality workflows | [User manual](docs/manuals/user-manual.md) |
| Administer, monitor, or troubleshoot | [Administrator & IT manual](docs/manuals/administrator-it-manual.md) |
| Set up a development host | [Fresh Linux setup](docs/runbooks/fresh-linux-setup.md) |
| Report a problem or contribute | [Contribution guide](CONTRIBUTING.md) |
| Check shipped work and limitations | [Current status](docs/current-status.md), [residuals](docs/open-residuals.md), and [slice history](docs/slice-history.md) |

The [GitLab wiki](https://gitlab.com/synqsuite-group/EasySynQ/-/wikis/home) is a navigation index.
The reviewed documentation in this repository remains authoritative.

## Capabilities

- **Controlled documents:** immutable check-in, a seven-state lifecycle, separation of approval and
  release, scheduled go-live, change requests, periodic review, and controlled obsolescence.
- **Controlled copies:** a read-only mirror, watermarked PDFs, and QR verification of document currency.
- **Records and evidence:** immutable records bound to the governing document version, retention and
  controlled disposition, evidence packs, and revocable external share links.
- **Quality workflows:** clause mapping, process ownership, compliance checklists, audits, findings,
  CAPA, objectives, management review, acknowledgements, and task notifications.
- **Access and integrity:** deny-by-default authorization, scoped roles and attributes, append-only
  audit records, hash-chain verification, and off-host checkpoint verification.

The system is single-organization and self-hosted. Full 21 CFR Part 11 e-signatures, expanded
standards support, and the remaining recovery capabilities are future work; see the current residuals.

## Stack and operations

React / TypeScript / Mantine / Tailwind · FastAPI / Python 3.12 · PostgreSQL 18 · MinIO · Redis /
Celery · Keycloak · Gotenberg / LibreOffice · Caddy · Docker Compose.

The supported deployment packaging is a single Linux host with S/M Compose profiles and online or
air-gapped installation paths. Kubernetes/L packaging and a bundled observability stack are reserved
architecture, not supported deployment artifacts.

Operational diagnostics include `/healthz`, dependency readiness at `/readyz`, structured JSON logs,
Compose health, and configured out-of-band alarm channels. See the
[monitoring procedures](docs/manuals/administrator-it-manual.md#8-health-logs-and-monitoring).
GitLab CI status is separate from the health of an installed EasySynQ instance.

## Quick start (developer)

The supported developer host is Ubuntu 26.04 on x86_64. From a fresh clone, inspect the host first;
the doctor never mutates anything:

```bash
./scripts/doctor.sh contributor            # read-only; names each missing tool and its install command
```

Every `FAIL` line carries the exact command that resolves it. Install the tracked Node and Python 3.12
runtimes plus `just`, `pre-commit` and the PostgreSQL 18 client, then re-run the doctor until it
reports `PROFILE_READY`.
The doctor never starts/enables Docker or changes groups, firewall, or SELinux; those operator
actions and the required new login session are explained in the
[fresh Linux developer setup](docs/runbooks/fresh-linux-setup.md). Ubuntu production deployment
remains documented in the
[online](docs/runbooks/install-online.md) and [air-gapped](docs/runbooks/install-airgapped.md) runbooks
and continues to use `scripts/bootstrap-ubuntu.sh` unchanged.

Once the host prerequisites are ready, create the developer environment:

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
just setup      # install API/web deps + packages/contracts/package-lock.json, hooks, and contracts
just up s       # bring up the stack (S profile)
./scripts/easysynq setup mint-bootstrap   # mint the one-time first-administrator secret
```

The contract tools use their own committed lock at `packages/contracts/package-lock.json`; `just setup`
hydrates that separate toolchain as well as the API and web dependencies.

Then open **http://localhost/setup** **without signing in** — do not create any account first. Paste
the one-time secret into the wizard's first step: it creates the first administrator identity itself
and shows a one-time temporary password (Keycloak forces a replacement at first sign-in). Complete
the six setup screens. After finalization, normal navigation is available at **http://localhost**,
and the optional development fixtures (`just demo-user`, `just seed-personas`) may be created —
they are post-finalize fixtures, never a first-install identity path. `/healthz` and `/readyz`
report stack health behind Caddy. See the
[fresh Linux developer setup](docs/runbooks/fresh-linux-setup.md) for the complete environment and
platform notes.

## Repository layout

```text
packages/contracts/   OpenAPI source, generated server models, and TypeScript client
apps/api/             FastAPI services, workers, and tests
apps/web/             React application and browser tests
migrations/           Linear Alembic revision tree
infra/compose/        Compose stack, Caddy, Keycloak, and MinIO configuration
infra/appliance/      Hyper-V appliance packaging
scripts/              Contributor, operator, validation, and release commands
docs/                 Product authority, current status, manuals, and runbooks
.gitlab/              Issue and merge request templates
```

## Project workflow

Development, issues, merge requests, CI, and release publishing use
[GitLab](https://gitlab.com/synqsuite-group/EasySynQ). Renovate proposes dependency changes through
reviewed merge requests; major upgrades remain deliberate. The
[GitLab setup runbook](docs/runbooks/gitlab-repository-setup.md) explains the branch gates, update bot,
security checks, and optional project services.

Use [issues](https://gitlab.com/synqsuite-group/EasySynQ/-/issues) for sanitized bug reports and feature
requests. For a suspected vulnerability, follow the confidential-reporting instructions in
[CONTRIBUTING.md](CONTRIBUTING.md#report-a-security-concern). Do not include credentials or installation data.

## License

EasySynQ's original code and documentation are licensed under
[PolyForm Shield 1.0.0](LICENSE), a source-available license with restrictions on competing products
and services. Internal business use is permitted. IT consultants may charge for installation,
configuration, maintenance, and support of a customer's own instance, subject to the license.

See [licensing guidance](LICENSING.md) for the consulting boundary, competition provisions, and
third-party notices. Third-party components retain their own licenses.

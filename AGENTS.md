# EasySynQ contributor guide

This file is the vendor-neutral repository workflow contract for human contributors and coding agents.
It links to mutable project facts instead of copying them.

## Authority and precedence

- Binding product and domain decisions live in [`docs/decisions-register.md`](docs/decisions-register.md)
  and owner-approved slice designs under `docs/superpowers/specs/`.
- The dated execution snapshot is [`docs/current-status.md`](docs/current-status.md); confirm runtime
  state from the executable sources it names.
- Current owner-visible deferred work lives only in
  [`docs/open-residuals.md`](docs/open-residuals.md).
- Shipped narrative and dated evidence live in [`docs/slice-history.md`](docs/slice-history.md) and Git.
- When sources conflict, preserve the stricter security or data-protection rule and stop for owner review.

## Repository map

- `apps/api/` — FastAPI application, services, workers, and tests.
- `apps/web/` — React/TypeScript SPA and tests.
- `packages/contracts/` — OpenAPI source plus generated server and web contract artifacts.
- `migrations/` — the single Alembic revision tree.
- `infra/compose/` — production and development Compose configuration.
- `scripts/` — contributor, operator, validation, and release entry points.
- `docs/` — product authority, current coordination, historical evidence, manuals, and runbooks.

## Supported contributor workflow

1. On a fresh Fedora developer host, run `./scripts/bootstrap-fedora-dev.sh --check`; follow
   [`docs/runbooks/fresh-linux-setup.md`](docs/runbooks/fresh-linux-setup.md) for the reviewed setup path.
2. Run `./scripts/doctor.sh contributor`, then `just setup` to hydrate the committed Python and npm locks.
3. Work on a branch and keep the change bounded to one reviewed behavior or documentation contract.
4. Run the smallest focused test first, then the affected `just check-*` gates.
5. Run `just authority-check` for repository-authority changes and `just check` when the full local
   dependency set is available.
6. Hand off through a reviewed pull request; do not push directly to `main`.

See [`docs/dev-workflow.md`](docs/dev-workflow.md) and the relevant manual or runbook for detailed setup
and per-stack commands.

The disposable Fedora Workstation proof is a separate PR/release acceptance gate. Its two-media command,
host prerequisites, evidence block, and cleanup rules live only in
[`docs/runbooks/fedora-proof.md`](docs/runbooks/fedora-proof.md); fast contract tests do not replace it.

## Tests and evidence

- Behavior changes start with a focused failing proof and finish with fresh affected-suite evidence.
- Documentation-only migrations use executable content/consumer guards where behavior is machine-read.
- Do not turn a skipped, unavailable, or partial check into a pass claim. Record the exact command,
  result, and environment limitation in the handoff.
- Keep current baseline counts and CI topology in `docs/current-status.md`, not in contributor guides.

## Security and site-data boundaries

- Follow [`docs/12-security-and-audit.md`](docs/12-security-and-audit.md) and the binding site-data rule
  in [`docs/decisions-register.md`](docs/decisions-register.md).
- Never commit real installation records, customer/site identifiers, hostnames, addresses, credentials,
  certificate or key fingerprints, bucket/share names, vendor inventories, or weakness inventories.
- Author deployment examples with placeholders from the first draft and run
  `bash scripts/check-no-site-data.sh` before handoff.
- Preserve deny-by-default, deny-always-wins, append-only audit, WORM, and source-store boundaries unless
  an owner-approved design explicitly changes them.

## Migrations and generated files

- Before writing a migration, run `cd apps/api && uv run alembic heads`; filename sorting and prose
  snapshots are not migration authority.
- Keep the Alembic tree linear unless an approved design says otherwise, and prove populated
  downgrade/upgrade behavior when data semantics change.
- Edit `packages/contracts/openapi.yaml`, then regenerate through the repository command; do not hand-edit
  generated contract outputs.
- Do not commit caches, local environments, build output, test artifacts, or generated secrets.

## Documentation truth

- Describe shipped behavior from code, migrations, Compose, OpenAPI, and executable tests.
- Keep mutable execution facts in `docs/current-status.md`, current residual records in
  `docs/open-residuals.md`, and dated implementation evidence in `docs/slice-history.md`.
- Preserve historical plans, reviews, closed residuals, and dated evidence; label them historical rather
  than rewriting them as current authority.

## Change handoff

- State the observable outcome, exact files changed, tests run with results, compatibility decisions,
  and every unverified or deliberately deferred item.
- Link newly discovered current residuals to a stable `RES-*` record and name a closure contract.
- Keep commits reviewable and scoped; do not mix unrelated cleanup, formatting, schema, deployment, or
  application behavior changes into a documentation or authority checkpoint.

## Tool-specific compatibility

Tool-specific files may add integration instructions but do not own product decisions or current facts.
`CLAUDE.md` and `.claude/` are retained compatibility assets; they defer to this guide and the neutral
authority homes above. Equivalent tool-specific files must follow the same precedence.

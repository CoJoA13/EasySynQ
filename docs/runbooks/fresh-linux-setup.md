# Fresh Linux dev-box setup (developer dev-stack)

> **Developer-facing**, not an operator/production runbook (for production see
> [install-online.md](install-online.md) / [install-airgapped.md](install-airgapped.md)). These are the
> hands-on steps to stand up the EasySynQ dev stack + test gates on a clean Linux workstation — e.g.
> after a distro reinstall, when **no data carries over** (fresh DB / MinIO / Keycloak). Verified on
> Kubuntu / Ubuntu 26.04 (x86_64); other Debian/Ubuntu derivatives are similar. The repo itself (code +
> all `docs/`) is restored by `git clone`; only the gitignored `.env` and the Docker volumes are lost.

## 1. Toolchain (native Linux — no WSL)

```bash
# uv — manages Python 3.12 (the API pins >=3.12,<3.13; a distro python3 3.13+ is too new)
curl -LsSf https://astral.sh/uv/install.sh | sh                       # → ~/.local/bin
# Node 22 via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install 22 && nvm alias default 22
# Docker (engine + compose plugin), just, gh, git, and postgresql-client (pg_dump — see §6)
sudo apt update && sudo apt install -y docker.io docker-compose-v2 just gh git postgresql-client
sudo usermod -aG docker "$USER"        # log out/in so `docker` works without sudo
```

Confirm: `docker --version` (need **v29.x**), `node -v` (22), `uv --version`, `just --version`.

## 2. Clone + build deps

```bash
git clone https://github.com/CoJoA13/EasySynQ.git ~/Documents/EasySynQ && cd ~/Documents/EasySynQ
(cd apps/api && uv sync)            # builds apps/api/.venv
(cd apps/web && npm install)        # builds apps/web/node_modules
```

## 3. The gitignored repo-root `.env`

The `.env` does **not** carry over and must be recreated beside `justfile`/`.env.example`.
⚠ **Do not use `scripts/install.sh`** for the dev box — it mis-points the app at the owner DB role and
leaves OIDC blank. Start from `.env.example` and ensure:

- **DB role separation** (migration `0010` creates these roles): `DATABASE_URL` → `easysynq_app`,
  `DATABASE_URL_SYNC` → owner `easysynq`, `AUDIT_LINKER_DATABASE_URL` → `easysynq_linker`, with
  `APP_DB_PASSWORD` / `LINKER_DB_PASSWORD` matching what `0010` sets.
- **localhost OIDC + S3** (PKCE needs a secure context → `http://localhost` only, never a hostname):
  `OIDC_ISSUER=http://localhost/realms/easysynq`; internal `OIDC_JWKS_URL` /
  `OIDC_DISCOVERY_URL` at `http://keycloak:8080/realms/easysynq/...`; `S3_PUBLIC_ENDPOINT=http://localhost:9000`.
- `AUDIT_SINK_SECRET_KEY=audit-sink-secret-change-me` (the minio-init container gets no `env_file`, so it
  provisions that hardcoded fallback — they must match).

Keep the file `0600`.

## 4. Free port 80

Caddy needs `:80`. If the distro ships a web server enabled at boot (apache2/nginx), free it:

```bash
sudo systemctl disable --now apache2     # reversible; do the same for nginx if present
```

If a first `up` failed with :80 held, the proxy container is left `Created` with no host port → after
freeing :80: `just up s` again (or `docker compose … up -d --force-recreate proxy`).

## 5. Bring up the stack

```bash
just up s                              # 11 services; app at http://localhost
docker ps --format '{{.Names}}\t{{.Status}}' | grep easysynq
```

⚠ Always include the `-f compose.s.yml` profile (`just up s` does) — without it MinIO `:9000` isn't
published and presigned URLs / restore drills break. Verify:

```bash
curl -s localhost/readyz                                                       # → 200
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  exec -T api sh -c "cd /app; uv run alembic current"                          # → 0066_awareness_events (head)
```

All 7 MinIO buckets should exist. (Migration head as of this writing is **0066**; it auto-applies on api
startup — see CLAUDE.md *Current status* for the live head.)

## 6. First-run wizard → OPERATIONAL

A fresh DB boots `setup_state = UNINITIALIZED` — the whole `/api/v1/*` returns `423 setup_incomplete`
until the wizard completes. The old box's OPERATIONAL state lived in the (now-gone) `pgdata` volume.

```bash
# mint the bootstrap token
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  exec -T api sh -c "cd /app; uv run python -m easysynq_api.cli.setup mint-bootstrap --org DEFAULT"
```

Then drive `http://localhost/setup` in the browser: demo Keycloak login → **org profile (short_code
`AHT`**, legal "EasySynQ", tz America/Chicago) → WORM-governance verify → backup + **restore-drill PASS**
→ local-accounts auth verify → **Finalize**. The "Not yet tamper-evident" finalize warning is **expected
and non-blocking in dev** (the audit-checkpoint anchor is same-host MinIO, not off-host).

## 7. Seed logins (Keycloak is ephemeral — re-run after every `up`/recreate)

```bash
just demo-user        # demo / Demo-Password-1   (System Administrator)
just seed-personas    # priya (Author) · ken (Approver) · mara (Releaser) — the SoD trio
```

⚠ `demo` is a System Administrator and holds **no `document.*`/`capa.*`/content keys** (admin sits
outside the QMS, by design) — Home PLAN/CHECK cards showing "No access to this section's data" is
expected. To author/view content: grant SYSTEM overrides (edit `scripts/grant-overrides.py`'s `KEYS` +
`ORG=AHT`, then pipe it into the worker container) **or** use the `priya`/`ken`/`mara` personas.

## 8. Test gates

Docker is native, so the integration suite now runs locally (it was CI-only on the old Windows box).

| Gate | Command |
|---|---|
| API unit | `cd apps/api && uv run pytest -m unit -q` (or the `/check-api` skill: ruff + format + mypy-strict + unit) |
| Migrations | `/check-migrations` (alembic up↔down↔check on a throwaway PG16) |
| Web | `cd apps/web && npx vitest run --pool=forks --maxWorkers=1` + `npm run lint && npm run typecheck && npm run build` (**vitest 4** — the old `--poolOptions.forks.singleFork` flag is gone) |
| Integration | `cd apps/api && uv run pytest -m integration` (needs Docker **and `pg_dump`**) |

⚠ **Two known LOCAL-env test artifacts (not real failures — both pass in CI):**
- `test_restore.py` requires **`pg_dump`** (`postgresql-client`); without it the suite errors `pg_dump not found`.
- `test_notification_settings.py::test_smtp_defaults_are_safe` asserts an empty `smtp_host` default, but a
  dev `.env` with `SMTP_HOST=mailpit` makes it fail locally (it passes in CI's clean env). Settings tests
  read the ambient `.env` — pin or unset `SMTP_HOST` to reproduce CI.

## Notes

- Stop the stack: `just down` (data persists). `just down -v` wipes the volumes (back to a fresh DB).
- `.claude/rules/windows-dev.md` is **historical** — its gotchas (`MSYS_NO_PATHCONV`, "bash.exe on PATH",
  Docker Desktop path mangling) are Windows-only and do not apply on native Linux.
- The "17-failure local unit baseline" in older docs was Windows-native (ProactorEventLoop / `O_NOFOLLOW`)
  and disappears on Linux → `pytest -m unit` is a real clean gate here.

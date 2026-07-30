# Fresh Linux dev-box setup (developer dev-stack)

> **Developer-facing**, not an operator/production runbook (for production see
> [install-online.md](install-online.md) / [install-airgapped.md](install-airgapped.md)). These are the
> hands-on steps to stand up the EasySynQ dev stack + test gates on a clean Linux workstation — e.g.
> after a distro reinstall, when **no data carries over** (fresh DB / MinIO / Keycloak). Verified on
> Kubuntu / Ubuntu 26.04 (x86_64; Debian/Ubuntu derivatives are similar) **and Bazzite-DX (Fedora 44
> atomic, x86_64)** — the rpm-ostree/atomic + Homebrew differences are flagged inline (§1, §8). The repo
> itself (code + all `docs/`) is restored by `git clone`; only the gitignored `.env` and the Docker
> volumes are lost.

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

### 1a. Fedora atomic / Bazzite-DX (immutable — no `apt`)

On an rpm-ostree/atomic distro (e.g. **Bazzite-DX**, Fedora 44) the `apt` line above does not apply. The
toolchain comes from **Homebrew** (pre-installed on Bazzite-DX): `brew install uv node@22 just gh`
(`docker-ce` is already layered into the image; Node can also come from nvm as above). `pg_dump` →
`brew install postgresql@16` (see §8 — the version matters). Two gotchas bite here:

- **Docker has no usable group out of the box.** The image ships `docker-ce` but creates **no `docker`
  group**, and `ujust dx-group` is **broken** for this — it only `usermod -aG docker`s a group that
  doesn't exist, and that missing group also makes `systemctl enable docker.socket` fail (the unit's
  `SocketGroup=docker` can't resolve → socket stays `root:root`/failed). Create the group yourself:
  ```bash
  sudo groupadd -f docker && sudo usermod -aG docker "$USER"
  sudo systemctl enable --now docker.socket docker.service
  ```
  `sudo` is password-prompted (no passwordless), so run these interactively. Then log out/in (or reboot)
  so your shell joins the group. To use Docker **before** re-login (same session) — `sg` reads
  `/etc/group` live, so wrap commands: `sg docker -c 'just up s'`, `sg docker -c 'docker ps'`, etc. This
  wrapper is needed for every `docker`/`just up`/testcontainer command until you re-login.
- **Clean up throwaway containers by explicit `--name` only** — never
  `docker rm -f $(docker ps -aq --filter ancestor=postgres:16)`: that filter also matches the **live
  `easysynq-postgres-1`** and any running **testcontainers**. (The `pgdata` is a named volume, so an
  accidental container kill is recoverable with `just up s`, but avoid the scare.)

## 2. Clone + build deps

```bash
git clone https://github.com/CoJoA13/EasySynQ.git ~/Documents/EasySynQ && cd ~/Documents/EasySynQ
(cd apps/api && uv sync)            # builds apps/api/.venv
(cd apps/web && npm install)        # builds apps/web/node_modules
```

## 3. The gitignored repo-root `.env`

The `.env` does **not** carry over and must be recreated beside `justfile`/`.env.example`.
⚠ **Do not use `scripts/install.sh`** for the dev box — it intentionally configures the production
HTTPS/hostname overlays. Start from `.env.example` and ensure:

- **DB role separation** (migration `0010` creates these roles): `DATABASE_URL` → `easysynq_app`,
  `DATABASE_URL_SYNC` → owner `easysynq`, `AUDIT_LINKER_DATABASE_URL` → `easysynq_linker`, with
  `APP_DB_PASSWORD` / `LINKER_DB_PASSWORD` matching what `0010` sets. Set
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` consistently, and replace
  `KEYCLOAK_DB_PASSWORD=CHANGE_ME` for Keycloak's dedicated durable schema.
- **localhost OIDC + S3** (PKCE needs a secure context → `http://localhost` only, never a hostname):
  `OIDC_ISSUER=http://localhost/realms/easysynq`; internal `OIDC_JWKS_URL` /
  `OIDC_DISCOVERY_URL` at `http://keycloak:8080/realms/easysynq/...`. Browser URLs may stay blank:
  `compose.dev.yml` supplies `S3_PUBLIC_ENDPOINT=http://localhost:9000` and
  `PUBLIC_BASE_URL=APP_BASE_URL=http://localhost`. If `HTTP_PORT` is set to a nondefault host port,
  Keycloak derives its public hostname as `http://localhost:<HTTP_PORT>`; set `OIDC_ISSUER` to the
  same origin plus `/realms/easysynq`.
- Keep `AUDIT_SINK_*` write/read credentials consistent in `.env`. `minio-init` has no `env_file`,
  but Compose passes these variables explicitly, so the sink users and worker use the same values.

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
just up s                              # start the dev stack; app at http://localhost
docker ps --format '{{.Names}}\t{{.Status}}' | grep easysynq
```

⚠ `just up s` includes both the S sizing overlay and `compose.dev.yml`. The latter is the **only**
place MinIO is published, and binds it to `127.0.0.1:9000` so presigned browser traffic works without
opening plaintext S3 to the LAN. Verify:

```bash
curl -s localhost/readyz                                                       # → 200
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.dev.yml \
  exec -T api sh -c "cd /app; uv run alembic current"                          # → <head> (head)
```

All 7 MinIO buckets should exist. Migrations are **not** applied by the api process: the compose
`migrate` one-shot runs `alembic upgrade head` and exits, and `api` gates on it
(`depends_on: migrate: {condition: service_completed_successfully}`). So `alembic current` should
already report the head by the time the app is up. To see what the head *should* be:

```bash
cd apps/api && uv run alembic heads
```

⚠ Deliberately **not** hard-coded here. A head number written into prose goes stale on the next
migration and has repeatedly misled a later session into numbering a new revision wrong — always read
it from Alembic, never from a doc (CLAUDE.md *Current status* carries the same warning). ⚠ Do **not**
substitute `ls migrations/versions/ | tail -1`: `_` sorts after the digits, so it returns
`__pycache__` on any box that has run alembic or pytest. `alembic heads` also reports the real
revision id (not a filename) and stays correct if the tree ever branches.

## 6. First-run wizard → OPERATIONAL

A fresh DB boots `setup_state = UNINITIALIZED` — the whole `/api/v1/*` returns `423 setup_incomplete`
until the wizard completes. The old box's OPERATIONAL state lived in the (now-gone) `pgdata` volume.

```bash
# create the identity used to activate this fresh instance
just demo-user

# mint the bootstrap token
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.dev.yml \
  exec -T api sh -c "cd /app; uv run python -m easysynq_api.cli.setup mint-bootstrap --org DEFAULT"
```

Then drive `http://localhost/setup` in the browser: demo Keycloak login → **org profile (short_code
`AHT`**, legal "EasySynQ", tz America/Chicago) → WORM-governance verify → backup + **restore-drill PASS**
→ local-accounts auth verify → **Finalize**. The "Not yet tamper-evident" finalize warning is **expected
and non-blocking in dev** (the audit-checkpoint anchor is same-host MinIO, not off-host).

## 7. Seed logins (Keycloak persists in PostgreSQL)

```bash
just demo-user        # idempotently reset demo / Demo-Password-1 (the bootstrap administrator)
just seed-personas    # priya (Author) · ken (Approver) · mara (Releaser) — the SoD trio
```

Both commands are idempotent. Accounts and stable Keycloak subjects now survive ordinary restarts,
image upgrades and container recreation because the live Keycloak schema is inside `pgdata`.
Deleting named volumes (`docker compose down -v`) remains a destructive full dev reset.

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
| Integration | Run **CI-sharded** (a single full process pollutes — see ⚠): `cd apps/api && for g in 1 2 3 4; do uv run pytest -m integration --splits 4 --group $g --durations-path .test_durations; done` (needs Docker **and a version-matched `pg_dump`** — see ⚠) |

⚠ **If Docker Desktop for Linux is installed, testcontainers cannot reach the daemon** — this is a
hard blocker, not an ignorable artifact, and it presents as the ENTIRE integration suite erroring out
in seconds (hundreds of `ERROR`s, no failures):

```
docker.errors.DockerException: Error while fetching server API version:
  ('Connection aborted.', PermissionError(13, 'Permission denied'))
```

The trap is that `docker ps` works fine, so Docker looks healthy. Docker Desktop makes `desktop-linux`
the active **context** (`unix://$HOME/.docker/desktop/docker.sock`, owned by you), and the CLI honours
it — but testcontainers-python does **not** read docker contexts. It falls back to
`/var/run/docker.sock`, which is `root:docker` `0660`, so it fails unless you are in the `docker`
group.

**Preferred fix — join the `docker` group.** The *system* `docker.service` is enabled and starts at
boot; Docker Desktop's user unit does **not**, so a machine that reboots comes back with the Desktop
socket gone and integration silently broken again. Group membership plus the default socket needs no
config file at all:

```bash
sudo usermod -aG docker "$USER"
```

⚠ Requires a **full logout/restart** to take effect — group membership is fixed at login, and this
box has no `sg`/`newgrp` shortcut. Once it is active, delete any `~/.testcontainers.properties`
override so testcontainers just uses its `/var/run/docker.sock` default. Verify with `id -nG | grep docker`.

**Fallback (no sudo, no restart)** — point testcontainers at the Docker Desktop socket instead. Works
immediately, but only while Docker Desktop is running (`systemctl --user start docker-desktop`, and
`systemctl --user enable docker-desktop` if you want it back after a reboot):

```bash
printf 'tc.host=unix://%s/.docker/desktop/docker.sock\n' "$HOME" >> ~/.testcontainers.properties
```

Verify with `docker context ls` (which endpoint is starred) and
`cd apps/api && uv run python -c "from testcontainers.core.docker_client import get_docker_host; print(get_docker_host())"`.
⚠ That properties file is parsed by splitting on the **first `=` of every line that contains one**, so
any comment you add must not contain an equals sign or it is silently read as a setting. It also takes
**precedence over `DOCKER_HOST`**, so a stale `tc.host` silently wins over a correct env var.

⚠ **Known LOCAL-env test artifacts (not real failures — all pass in CI):**
- **Run the integration suite sharded, the way CI does.** CI runs it as **4 parallel shards**
  (`--splits 4 --group {1..4}`, each its own process + testcontainers). A single full
  `pytest -m integration` process reuses one shared DB + mirror filesystem across all ~760 tests and
  produces **~44 cross-file-pollution failures** (concentrated in `test_setup` / `test_mirror_scan` /
  `test_restore` — shared `setup_state`, `mirror_build` rows, restore-scratch). The very same files pass
  **in isolation** and **sharded**. Use the sharded command in the table; the single-process number is
  not a clean gate (CI is authoritative). Needs `pytest-split` (a dev dep — included by `uv sync`).
- **`pg_dump` must match the PG16 testcontainer major version.** `test_backup` / `test_restore` shell out
  to `pg_dump`/`pg_restore`; a **newer** client (pg_dump 18 from a recent `postgresql-client` or brew
  `libpq`) makes `pg_restore` fail with `ERROR: unrecognized configuration parameter "transaction_timeout"`
  (a PG17+ GUC the PG16 server rejects → the drill reports `FAIL`). Install **pg_dump 16** and put it
  first on `PATH` — brew: `brew install postgresql@16` then prepend `$(brew --prefix postgresql@16)/bin`.
  On Debian/Ubuntu `apt install postgresql-client-16` only works if that version is still in your
  release's repos — **Ubuntu 26.04 ships 18 only**, so add PGDG first (it does publish a `resolute`
  dist):
  ```bash
  sudo install -d /usr/share/postgresql-common/pgdg
  sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list
  sudo apt update && sudo apt install -y postgresql-client-16
  ```
  Installing **only** `postgresql-client-16` (not the unversioned metapackage) keeps `pg_wrapper` from
  picking a newer client, so a bare `pg_dump` resolves to 16. The **live stack is unaffected** (the api/worker images
  carry pg_dump 16.14, so the wizard restore-drill always uses the matching version). With **no** `pg_dump`
  at all, the suite errors `pg_dump not found`.
- `test_notification_settings.py::test_smtp_defaults_are_safe` asserts an empty `smtp_host` default, but a
  dev `.env` with `SMTP_HOST=mailpit` makes it fail locally (it passes in CI's clean env). Settings tests
  read the ambient `.env` — pin or unset `SMTP_HOST` to reproduce CI. (Running `uv run pytest` without
  exporting the repo `.env` into the shell also avoids it.)

## Notes

- Stop the stack: `just down` (data persists). `just down -v` wipes the volumes (back to a fresh DB).
- **Avoid a repo path with spaces** (e.g. `.../Claude Projects/EasySynQ`). `cd "$REPO"` survives it, but a
  spaced absolute path word-splits through `uv run --env-file "$REPO/.env"` (and similar arg passing) even
  when double-quoted under `sg … -c`. Clone to a space-free path, or use a relative (`../../.env` from
  `apps/api`) / `/tmp`-copied env file.
- `.claude/rules/windows-dev.md` is **historical** — its gotchas (`MSYS_NO_PATHCONV`, "bash.exe on PATH",
  Docker Desktop path mangling) are Windows-only and do not apply on native Linux.
- The "17-failure local unit baseline" in older docs was Windows-native (ProactorEventLoop / `O_NOFOLLOW`)
  and disappears on Linux → `pytest -m unit` is a real clean gate here.

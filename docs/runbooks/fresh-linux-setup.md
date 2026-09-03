# Fresh Linux dev-box setup (developer dev-stack)

> **Developer-facing**, not an operator/production runbook. The supported developer host is
> **Ubuntu 26.04 on x86_64** (R71). For production Ubuntu hosts, keep using
> [install-online.md](install-online.md) / [install-airgapped.md](install-airgapped.md) and the linked
> `scripts/bootstrap-ubuntu.sh` flow; that script provisions a PRODUCTION host and is not a developer
> setup path — do not run it on a dev box.
> These steps create a fresh DB / MinIO / Keycloak state. Only the gitignored `.env` and Docker
> volumes remain local after the repository is restored by `git clone`.

## 1. Clone + toolchain

```bash
git clone https://github.com/CoJoA13/EasySynQ.git ~/Documents/EasySynQ
cd ~/Documents/EasySynQ

# Read-only inventory. Names every missing tool and the exact command that installs it.
./scripts/doctor.sh contributor
```

There is no developer bootstrap script. R71 retired the Fedora one along with the Fedora developer
path, because Fedora ships no package for the tracked Node major. `./scripts/doctor.sh` is the host
contract: work its `FAIL` lines until it prints `PASS PROFILE_READY contributor`.

The tools it requires, and the way each is installed on Ubuntu 26.04 without needing a bootstrap:

| Tool | Install |
|---|---|
| `git`, `curl`, `openssl` | `sudo apt-get install git curl openssl` |
| Node (major per the tracked `.node-version`) | Any version manager, or NodeSource. `nvm install <major> && nvm alias default <major>` keeps it off the system path. |
| `uv` (manages Python 3.12) | Astral's installer, then `uv python install 3.12` |
| `just` | `sudo apt-get install just`, or `uv tool install rust-just` when sudo is unavailable |
| `pre-commit` | `sudo apt-get install pre-commit`, or `uv tool install pre-commit` |
| `pg_dump` **major 18** | `sudo apt-get install postgresql-client-18` — Ubuntu 26.04 ships 18 |
| Docker Engine + Compose v2 | Distribution packages or Docker's official apt repository |

⚠ **`pg_dump` must be major 18**, matching the PostgreSQL server in `infra/images.lock`.
`test_backup`/`test_restore` shell out to `pg_dump`/`pg_restore`, and **pg_dump refuses a newer server
outright** — a 16 client against the 18 testcontainer aborts with `server version mismatch` and 19
tests fail with a message that reads like a database problem rather than a client-version one. Ubuntu
26.04 ships 18 as its default, so no third-party repository is needed:

```bash
sudo apt-get install postgresql-client-18
```

⚠ If an **older** versioned client is also installed (`postgresql-client-16`, say, left over from
before this upgrade), `pg_wrapper` can select it and a bare `pg_dump` resolves to 16 again. Check with
`pg_dump --version`; if it reports the wrong major, remove the stale versioned package or put
`/usr/lib/postgresql/18/bin` first on `PATH`. The containerized stack is unaffected either way — the
api/worker images carry their own matching client, so the wizard's restore drill is never at risk.

Docker host policy stays an explicit operator decision. Review these, then run them separately if
appropriate:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Membership in the `docker` group is root-equivalent. A full logout and login (or a reboot) is the
reliable way to activate the new group in every process; `newgrp docker` only starts a transitional
subshell. To use Docker **before** re-login, wrap commands: `sg docker -c 'just up s'`. Docker also
manages forwarding rules for published container ports — review the Compose host bindings rather than
assuming the host firewall alone blocks a published port.

⚠ **A version manager can shadow the tracked Node major.** If `node --version` disagrees with
`.node-version`, the doctor reports `NODE_UNSUPPORTED_VERSION` or `NODE_PATH_SHADOWED`. Fix the
manager's selection rather than installing a second runtime. Note that a long-running editor or agent
session inherits the PATH of the terminal that launched it, so it can keep reporting a stale Node
after you switch — check in a NEW terminal before concluding the switch failed.

After starting Docker and entering a fresh group-aware login session, confirm the broader test-host
profile:

```bash
./scripts/doctor.sh test
```

If a reason fails, use the reason-to-command table in
[`docs/dev-workflow.md`](../dev-workflow.md#toolchain-linux-ci--a-linux-dev-host); do not guess at
socket, runtime, or package changes. A passing local doctor plus the fast structural tests is
contributor readiness; CI is the acceptance evidence.

⚠ Clean up throwaway containers by explicit `--name` only — never
`docker rm -f $(docker ps -aq --filter ancestor=postgres:18)`: that filter also matches the live
`easysynq-postgres-1` and any running testcontainers. (`pgdata` is a named volume, so an accidental
container kill is recoverable with `just up s`, but avoid the scare.)

## 2. Build project dependencies

```bash
just setup  # hydrates API/web deps plus packages/contracts/package-lock.json and installs hooks
```

The contract toolchain has that separate committed npm lock; setup installs it with the same frozen
`npm ci` path used by CI before generating the contract artifacts.

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
it from Alembic, never from a doc (`docs/current-status.md` is only a dated snapshot). ⚠ Do **not**
substitute `ls migrations/versions/ | tail -1`: `_` sorts after the digits, so it returns
`__pycache__` on any box that has run alembic or pytest. `alembic heads` also reports the real
revision id (not a filename) and stays correct if the tree ever branches.

## 6. First-run wizard → OPERATIONAL

A fresh DB boots `setup_state = UNINITIALIZED` — the whole `/api/v1/*` returns `423 setup_incomplete`
until the wizard completes. A fresh volume intentionally carries no prior `OPERATIONAL` state.

```bash
# mint the bootstrap secret; do not create a demo identity first
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
  -f infra/compose/compose.dev.yml \
  exec -T api sh -c "cd /app; uv run python -m easysynq_api.cli.setup mint-bootstrap --org DEFAULT"
```

Open `http://localhost/setup` without signing in. Enter the secret and create the first
administrator profile. EasySynQ creates its sign-in identity; do not create one in Keycloak first.
Next, copy the shown-once temporary password, then acknowledge the active credential generation. Only
then sign in and change the temporary password when Keycloak prompts.

Continue the wizard with **org profile (short_code `ORG_EXAMPLE`**, legal "Example Organization",
tz UTC) → WORM-governance verify → backup + **restore-drill PASS** → local-accounts auth verify
→ **Finalize**. The "Not yet tamper-evident" finalize warning is **expected and non-blocking in
dev** (the audit-checkpoint anchor is same-host MinIO, not off-host).

## 7. Post-bootstrap development fixtures (Keycloak persists in PostgreSQL)

Run these only after first-administrator setup is complete. They are optional development fixtures,
never a supported first-install identity or credential path.

```bash
just demo-user        # idempotently reset the post-bootstrap demo / Demo-Password-1 login fixture
just seed-personas    # priya (Author) · ken (Approver) · mara (Releaser) — the SoD trio
```

Both commands are idempotent. Accounts and stable Keycloak subjects now survive ordinary restarts,
image upgrades and container recreation because the live Keycloak schema is inside `pgdata`.
Deleting named volumes (`docker compose down -v`) remains a destructive full dev reset.

⚠ `demo` is a System Administrator and holds **no `document.*`/`capa.*`/content keys** (admin sits
outside the QMS, by design) — Home PLAN/CHECK cards showing "No access to this section's data" is
expected. To author/view content: grant SYSTEM overrides (edit `scripts/grant-overrides.py`'s
`KEYS`, export `EASYSYNQ_SMOKE_ORG='ORG_EXAMPLE'`, then pipe it into the worker container with
`docker compose exec -e EASYSYNQ_SMOKE_ORG`) **or** use the `priya`/`ken`/`mara` personas.

## 8. Test gates

With a native Docker engine, the integration suite can run locally.

| Gate | Command |
|---|---|
| API unit | `cd apps/api && uv run pytest tests/unit -m unit -q` (or the `/check-api` skill: ruff + format + mypy-strict + unit) |
| Migrations | `/check-migrations` (alembic up↔down↔check on a throwaway PG18) |
| Web | `(set -e; cd apps/web; for shard in 1 2; do npm test -- --shard="$shard/2"; done; npm run lint; npm run build)` (`build` owns the one `tsc --noEmit` pass) |
| Integration | Run **CI-sharded** (a single full process pollutes — see ⚠): `(set -e; cd apps/api; for g in 1 2 3 4; do uv run pytest tests/integration -m integration --splits 4 --group "$g" --durations-path .test_durations; done)` (needs Docker **and a version-matched `pg_dump`** — see ⚠) |

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

⚠ A **full logout/restart** may be required because group membership is fixed at login. If
`sg`/`newgrp` is unavailable, restart the session. Once it is active, delete any `~/.testcontainers.properties`
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

⚠ **Host-environment test traps (not product regressions when the clean CI gate passes):**
- **Run the integration suite sharded, the way CI does.** CI runs it as **4 parallel shards**
  (`--splits 4 --group {1..4}`, each its own process + testcontainers). A single full
  `pytest tests/integration -m integration` process reuses one shared DB + mirror filesystem across
  all 1,057 current tests and
  produces **~44 cross-file-pollution failures** (concentrated in `test_setup` / `test_mirror_scan` /
  `test_restore` — shared `setup_state`, `mirror_build` rows, restore-scratch). The very same files pass
  **in isolation** and **sharded**. Use the sharded command in the table; the single-process number is
  not a clean gate (CI is authoritative). Needs `pytest-split` (a dev dep — included by `uv sync`).
- **`pg_dump` must match the PG18 testcontainer major version.** `test_backup` / `test_restore` shell
  out to `pg_dump`/`pg_restore`. **pg_dump refuses a NEWER server outright**, so a leftover 16 client
  against the 18 testcontainer aborts every one of them with
  `pg_dump: error: aborting because of server version mismatch` — 19 failures across
  `test_backup.py`, `test_restore.py` and `test_setup.py::test_setup_finalize_requires_restore_pass`,
  none of which names the real cause. Ubuntu 26.04 ships 18 as its default:
  ```bash
  sudo apt-get install postgresql-client-18
  ```
  ⚠ Installing an **older** versioned client alongside it (`postgresql-client-16`) lets `pg_wrapper`
  select that one, and a bare `pg_dump` silently resolves to 16 again. Verify with `pg_dump --version`
  rather than assuming; the fix is to remove the stale versioned package, or to put
  `/usr/lib/postgresql/18/bin` first on `PATH` when another project needs the older client. The
  **containerized stack is unaffected** — the api/worker images carry their own matching client, so
  the wizard restore-drill is never at risk. With **no** `pg_dump` at all, the suite errors
  `pg_dump not found`.
- `test_notification_settings.py::test_smtp_defaults_are_safe` asserts an empty `smtp_host` default, but a
  dev `.env` with `SMTP_HOST=mailpit` makes it fail locally (it passes in CI's clean env). Settings tests
  read the ambient `.env` — pin or unset `SMTP_HOST` to reproduce CI. (Running `uv run pytest` without
  exporting the repo `.env` into the shell also avoids it.)

## Notes

- Stop the stack: `just down` (data persists). The `down` recipe takes no flags; a full volume wipe
  (back to a fresh DB) is
  `docker compose --env-file .env -f infra/compose/compose.yml down -v`.
- **Avoid a repo path with spaces** (e.g. `.../Claude Projects/EasySynQ`). `cd "$REPO"` survives it, but a
  spaced absolute path word-splits through `uv run --env-file "$REPO/.env"` (and similar arg passing) even
  when double-quoted under `sg … -c`. Clone to a space-free path, or use a relative (`../../.env` from
  `apps/api`) / `/tmp`-copied env file.
- `.claude/rules/windows-dev.md` is **historical** — its gotchas (`MSYS_NO_PATHCONV`, "bash.exe on PATH",
  Docker Desktop path mangling) are Windows-only and do not apply on native Linux.
- Older Windows-native baselines involving ProactorEventLoop / `O_NOFOLLOW` do not apply on Linux;
  `pytest tests/unit -m unit` is expected to be a clean gate.

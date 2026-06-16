# Running EasySynQ on native Windows 11 (Git Bash) — read when developing on the owner's Windows box

The repo lives on the **native Windows filesystem** (e.g. `C:\dev\EasySynQ`), cloned fresh so
`.gitattributes` (`* text=auto eol=lf`) keeps `*.sh`/Dockerfiles LF. **No WSL distro** is used for dev
(the owner moved off WSL — Aug 2026). The Docker stack runs on **Docker Desktop** (Linux containers; its
WSL2 *engine* is internal — you never open a WSL shell). Toolchain is native Windows: `uv` (manages
Python 3.12), Node 22, `just`, `gh`, and **Git for Windows** — whose **Git Bash supplies the `bash`
that `just`, the `.sh` glue, and Claude Code's `.claude/hooks/*.sh` all require** (the `justfile` is
`set shell := ["bash", …]`). `winget install`: `Git.Git`, `astral-sh.uv`, `OpenJS.NodeJS.LTS`,
`Casey.Just`, `GitHub.cli`.

## Run it
- `just up s` / `just down` / `just logs` — the stack. App at **http://localhost**. `just` spawns Git
  Bash's `bash` for recipes, so run it from **PowerShell or Git Bash** — either works as long as
  `bash.exe` is on PATH.
- The app is **OPERATIONAL**: log in `demo` / `Demo-Password-1` (System Administrator).
- `just check` runs ruff + ruff-format + mypy-strict + the **full `pytest -m unit`** (api) + the web
  loop (eslint/tsc/build/test); no Docker. ⚠ On this native-Windows box the **`-m unit` leg is RED by
  a known 17-failure baseline** (ProactorEventLoop / `O_NOFOLLOW` native issues — mirror symlinks ×12,
  ingestion `O_NOFOLLOW` ×5, in 3 files), and `pytest -m integration` can't run locally either → treat
  the **full unit + integration suites as CI-authoritative**, NOT a clean local gate. For a clean LOCAL
  signal run the fast sub-checks (ruff + mypy-strict + the web loop) + **targeted** unit files
  (`uv run pytest tests/unit/test_<x>.py`); anything failing outside the 17-baseline is a real regression.
- `just demo-user` — (re)create the `demo` login (see Keycloak note). `just seed-personas` — the SoD
  `priya`/`ken`/`mara` fixture. Both now live in `scripts/demo-user.sh` / `scripts/seed-personas.sh`
  (plain scripts, not justfile shebang recipes) so they run identically on Windows + Git Bash.
- Raw equivalents (no `just`): `docker compose --env-file .env -f infra/compose/compose.yml -f
  infra/compose/compose.s.yml up -d` (= `just up s`); `… -f infra/compose/compose.yml down` (= down).

## Auth: use http://localhost ONLY (never a hostname)
PKCE (S256) needs a **secure context** — HTTPS or `localhost`. A plain-HTTP hostname breaks
`crypto.subtle` (no login), trips the CSP `upgrade-insecure-requests`, and hits vite's host allowlist.
`localhost` avoids all three (Docker Desktop publishes the stack's ports to Windows `localhost`). The
gitignored **repo-root `.env`** therefore uses:
- `OIDC_ISSUER=http://localhost/realms/easysynq` (browser-facing; token `iss`)
- `OIDC_JWKS_URL=http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs` (internal)
- `OIDC_DISCOVERY_URL=http://keycloak:8080/realms/easysynq/.well-known/openid-configuration` (the API
  host can't reach the `localhost` issuer; the first-run G-D probe uses this internal URL)
- `S3_PUBLIC_ENDPOINT=http://localhost:9000` (browser-reachable presigned MinIO — #90; the `s` profile publishes 9000)

## The `.env` (repo root, gitignored)
Lives at **`<repo>\.env`** (beside `justfile`/`.env.example`); gitignored (`.env`, `.env.*`). A fresh
clone has none — provide one: **reuse your prior `.env`** (copy it in — preserves secrets + the
localhost tuning), or `bash scripts/install.sh` (generates secrets from `.env.example`; ⚠ mints a NEW
`BACKUP_ENCRYPTION_KEY` — old encrypted backups become unrecoverable — and leaves OIDC blank to set),
or `cp .env.example .env` + edit. ⚠ This install's org short_code is **`AHT`** → `grant-role` needs `--org AHT`.

## Keycloak is ephemeral (no volume)
After `just down` / any keycloak recreate, the `demo` user is wiped → run **`just demo-user`** (and
`just seed-personas` if you need the SoD trio). The realm re-imports from `realm-export.json` (incl. the
audience mapper + redirect URIs). Postgres/MinIO data persists across `just down` (only `just down -v` wipes).

## Native-Windows gotchas (the ones that bite)
- **`bash.exe` MUST be on PATH** (Git for Windows installer → "Git from the command line and also from
  3rd-party software"). Without it, `just` recipes AND Claude Code's `.sh` format hooks silently no-op.
- **MSYS path conversion mangles container paths.** Git Bash rewrites a Unix-looking absolute path
  passed to a native exe (e.g. `docker compose exec keycloak /opt/keycloak/bin/kcadm.sh`) into a host
  path (`C:/Program Files/Git/opt/…`) → the exec fails with **`exit 127`**. Prefix such calls with
  **`MSYS_NO_PATHCONV=1`** (a no-op off-Windows). The `demo-user`/`seed-personas` scripts already do
  this for their kcadm `exec`; do the same for any new `docker … exec <ctr> /abs/container/path`. ⚠
  This is distinct from `-f infra/compose/...` (a *host* file path, which converts correctly) — only
  **container-internal** absolute paths must be shielded.
- **Keep the repo on the Windows filesystem** (`C:\dev\…`), not inside a WSL distro; avoid paths with
  spaces. Docker Desktop must be **running** (Linux-containers mode) with the repo's drive shared.
- **Demo precondition for authoring:** `demo` (System Administrator) holds **no `document.*`** — grant
  the authoring keys via **SYSTEM overrides** (NOT `grant-role "QMS Owner"`, which is reads-only).
- A **`storage.py` / Dockerfile / new-CLI-module** change isn't in the running container until rebuilt
  (`docker compose … build api` / `up -d --build`) — CI runs from source, not the image. The **web**
  image is the same: `vite preview` serves a baked build with no source mount → rebuild after a code
  change (`… up -d --build web`) + hard-refresh / Incognito to drop the cached bundle.

## Claude Code on this machine
Run Claude Code **natively on Windows** (PowerShell) — it drives the shell directly; **no WSL base64
bridge** (the old `wsl bash -lc "echo <b64> | base64 -d | bash"` dance is gone). Git Bash's `bash` on
PATH is what lets Claude's `.claude/hooks/*.sh` format hooks fire. Don't weaken Keycloak auth (ROPC etc.).

# Running EasySynQ on Windows 11 + WSL2 (read when developing on the owner's Windows box)

The repo lives in **WSL2 Ubuntu** at `~/EasySynQ` (relocated from `/mnt/c/...` via a local `git clone`;
treat the Windows copy as abandoned). Toolchain is user-space in WSL (no sudo): `uv` + `just` + `gh` in
`~/.local/bin`, Node 22 via `nvm`; `uv` manages Python 3.12. The Docker stack runs via **Docker Desktop's
WSL integration** (must be ON for Ubuntu).

## Run it
- `just up s` / `just down` / `just logs` — the stack. App at **http://localhost**.
- The app is **OPERATIONAL**: log in `demo` / `Demo-Password-1` (System Administrator).
- `just check` — the full local CI (api + web fast loops; no Docker needed).
- `just demo-user` — (re)create the `demo` login (see Keycloak note below).

## Auth: use http://localhost ONLY (never a hostname) on this single-host stack
PKCE (S256) needs a **secure context** — HTTPS or `localhost`. A plain-HTTP hostname (e.g. `easysynq.local`)
breaks `crypto.subtle` (no login), trips the CSP `upgrade-insecure-requests`, and hits vite's host allowlist.
`localhost` avoids all three. The gitignored `.env` therefore uses:
- `OIDC_ISSUER=http://localhost/realms/easysynq` (browser-facing; token `iss`)
- `OIDC_JWKS_URL=http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs` (internal)
- `OIDC_DISCOVERY_URL=http://keycloak:8080/realms/easysynq/.well-known/openid-configuration` — the API host
  can't reach the `localhost` issuer, so the first-run G-D probe uses this internal URL (the
  `auth_check.probe_oidc_discovery` change in PR #85).

## Keycloak is ephemeral (no volume)
After `just down` / any keycloak recreate, the `demo` user is wiped → run **`just demo-user`**. The realm
re-imports from `realm-export.json` (incl. the audience mapper + redirect URIs). Postgres/MinIO data persists
across `just down` (volumes are kept; only `just down -v` wipes). Bootstrap secret + setup state live in PG.

## Claude Code on this machine
**Prefer running Claude INSIDE WSL** (`wsl` → `cd ~/EasySynQ` → `claude`): native `bash`/`just`/hooks, visible
commands, fine-grained permissions, no bridging. If running **Windows-native** Claude (PowerShell), drive WSL
via a base64 bridge: `wsl bash -lc "echo <base64> | base64 -d | bash"` (UTF-8, CR-stripped) with a PATH/nvm
preamble; add `</dev/null` to any `docker compose exec`/`kcadm` call or it drains the script's stdin and
everything after the first such call silently vanishes. Don't put `rm`/`/tmp` in the script text (safety
classifier blocks it) and don't try to weaken Keycloak auth (ROPC etc. is blocked).

## Fresh-setup bug fixes (PR #85, branch fix/local-stack-build-and-just)
Dockerfile bookworm pin · `--env-file` on just/install/easysynq · justfile `{{` escape · KC `easysynq-api`
audience mapper · vite `preview.allowedHosts` · `OIDC_DISCOVERY_URL` for the G-D probe · `just demo-user`.
None were caught by CI (it never builds the image / runs compose / runs just).

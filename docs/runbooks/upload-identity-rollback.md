# Exact-version upload compatibility rollback

Use this runbook only when the current API must be replaced temporarily by an older build that cannot
pin and verify staging `VersionId`. This is a fail-closed application rollback. It preserves the new
Compose, Caddy, and MinIO initialization configuration, keeps both temporary buckets versioned, and
blocks promotion until exact-version-capable code is restored.

Run every section in one Bash session with `set -euo pipefail`. Stop immediately if an assertion fails.

## 1. Establish the full Compose command

For an installed appliance, run:

```bash
set -euo pipefail
cd /opt/easysynq
ESQ_COMPOSE=(sudo easysynq-compose)
"${ESQ_COMPOSE[@]}" config --quiet
bash scripts/validate-browser-origins.sh --env-file .env
```

For a repository/online install, change to the repository root containing `.env`, then run this strict
profile selection. The profile filename is selected by a literal `case` arm; unvalidated text is never
interpolated into a path.

```bash
set -euo pipefail
mapfile -t ESQ_PROFILE_LINES < <(grep '^EASYSYNQ_PROFILE=' .env || true)
if [ "${#ESQ_PROFILE_LINES[@]}" -ne 1 ]; then
  echo 'rollback: .env must contain exactly one EASYSYNQ_PROFILE assignment' >&2
  exit 1
fi
ESQ_PROFILE="${ESQ_PROFILE_LINES[0]#EASYSYNQ_PROFILE=}"
case "$ESQ_PROFILE" in
  s) ESQ_PROFILE_FILE='infra/compose/compose.s.yml' ;;
  m) ESQ_PROFILE_FILE='infra/compose/compose.m.yml' ;;
  *) echo 'rollback: EASYSYNQ_PROFILE must be exactly s or m' >&2; exit 1 ;;
esac
ESQ_COMPOSE=(
  docker compose --env-file .env
  -f infra/compose/compose.yml
  -f "$ESQ_PROFILE_FILE"
  -f infra/compose/compose.production.yml
)
"${ESQ_COMPOSE[@]}" config --quiet
bash scripts/validate-browser-origins.sh --env-file .env
```

Do not roll back any Compose file, either Caddyfile, or `infra/compose/minio/minio-init.sh`. Never run
`mc version suspend` on `staging` or `import-staging`. Do not add expiry rules as part of rollback.

Define the following helper once. It updates exactly one `.env` assignment while preserving the file
mode and rejecting duplicate keys.

```bash
esq_set_env() {
  local key="$1" value="$2" count tmp
  count="$(grep -c "^${key}=" .env || true)"
  if [ "$count" -gt 1 ]; then
    echo "rollback: duplicate ${key} assignments in .env" >&2
    return 1
  fi
  umask 077
  tmp="$(mktemp ./.env.upload-identity.XXXXXX)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' .env >"$tmp"
  chmod --reference=.env "$tmp"
  mv "$tmp" .env
}
```

## 2. Enable and prove the compatibility guard

Set the deployment interlock and recreate only the proxy:

```bash
esq_set_env EASYSYNQ_COMPATIBILITY_READ_ONLY 1
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy
```

Load and validate the application origin, then prompt without echo for a bearer token and for one
representative, already-committed vault GET path. Use a document or Record endpoint the token is
authorized to read.

```bash
mapfile -t ESQ_BASE_LINES < <(grep '^PUBLIC_BASE_URL=' .env || true)
if [ "${#ESQ_BASE_LINES[@]}" -ne 1 ]; then
  echo 'rollback: .env must contain exactly one PUBLIC_BASE_URL assignment' >&2
  exit 1
fi
ESQ_BASE_URL="${ESQ_BASE_LINES[0]#PUBLIC_BASE_URL=}"
ESQ_BASE_URL="${ESQ_BASE_URL%/}"
case "$ESQ_BASE_URL" in
  http://*|https://*) ;;
  *) echo 'rollback: PUBLIC_BASE_URL must be an HTTP(S) origin' >&2; exit 1 ;;
esac
case "$ESQ_BASE_URL" in
  *[[:space:]]*|*,*|*\**|*'?'*|*'#'*)
    echo 'rollback: PUBLIC_BASE_URL is not one exact origin' >&2
    exit 1
    ;;
esac
read -rsp 'Bearer token for rollback proof: ' ESQ_ACCESS_TOKEN
printf '\n'
if [ -z "$ESQ_ACCESS_TOKEN" ]; then
  echo 'rollback: bearer token is required' >&2
  exit 1
fi
read -rp 'Representative vault GET path beginning /api/v1/documents/ or /api/v1/records/: ' ESQ_VAULT_GET_PATH
case "$ESQ_VAULT_GET_PATH" in
  /api/v1/documents/*|/api/v1/records/*) ;;
  *) echo 'rollback: representative path must be a document or Record GET path' >&2; exit 1 ;;
esac
case "$ESQ_VAULT_GET_PATH" in
  *[[:space:]]*) echo 'rollback: representative path contains whitespace' >&2; exit 1 ;;
esac
```

Before changing application images, prove the edge guard, liveness, and an authenticated committed
read. The guard body comparison is exact. Abort rollback if any command fails.

```bash
ESQ_PROBE_BODY="$(mktemp)"
ESQ_PROBE_STATUS="$(curl -sS -o "$ESQ_PROBE_BODY" -w '%{http_code}' -X POST \
  "$ESQ_BASE_URL/api/v1/rollback-write-probe")"
test "$ESQ_PROBE_STATUS" = 503
test "$(cat "$ESQ_PROBE_BODY")" = 'Write operations are disabled during compatibility rollback.'
curl -fsS "$ESQ_BASE_URL/healthz"
curl -fsS -H "Authorization: Bearer $ESQ_ACCESS_TOKEN" \
  "$ESQ_BASE_URL$ESQ_VAULT_GET_PATH"
```

## 3. Stop every asynchronous promoter, then start only the older API

Stop worker and Beat before introducing the older API. Confirm neither is running:

```bash
"${ESQ_COMPOSE[@]}" stop worker beat
ESQ_RUNNING_ASYNC="$("${ESQ_COMPOSE[@]}" ps --status running --services worker beat)"
if [ -n "$ESQ_RUNNING_ASYNC" ]; then
  echo 'rollback: worker or beat is still running' >&2
  exit 1
fi
```

Now select the approved older API image/build through the installation's release mechanism, changing
only the API artifact. Keep `EASYSYNQ_COMPATIBILITY_READ_ONLY=1`, then recreate only `api`:

```bash
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate api
```

Never start, recreate, or run an older `worker` or `beat`. Never run any exact-version-incompatible
worker against versioned staging. Keep the compatibility guard enabled for the older API's entire
lifetime.

Direct presigned browser PUTs use the separate MinIO origin and do not pass through Caddy's application
write guard. That is safe only because upload-init/check-in API writes are blocked and all workers are
stopped: the PUT cannot promote itself. Already-issued presigned URLs may leave harmless, versioned
staging objects for a later compatible flow. CORS response visibility at the MinIO origin does not
authorize data access; S3 IAM and the presigned request remain the access boundary.

## 4. Restore exact-version-capable code

Restore an exact-version-capable API artifact while the guard remains enabled, then recreate only API:

```bash
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate api
```

Require readiness. A 200 includes the MinIO probe's checks that both `staging` and `import-staging`
report versioning exactly `Enabled`:

```bash
ESQ_READY_BODY="$(mktemp)"
ESQ_READY_STATUS="$(curl -sS -o "$ESQ_READY_BODY" -w '%{http_code}' \
  "$ESQ_BASE_URL/readyz")"
test "$ESQ_READY_STATUS" = 200
grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' "$ESQ_READY_BODY"
grep -Eq '"name"[[:space:]]*:[[:space:]]*"minio"[[:space:]]*,[[:space:]]*"ready"[[:space:]]*:[[:space:]]*true' "$ESQ_READY_BODY"
```

Only after readiness succeeds, disable the edge guard and recreate only proxy:

```bash
esq_set_env EASYSYNQ_COMPATIBILITY_READ_ONLY 0
"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy
```

Repeat the deliberately non-existent, non-mutating write probe. The static guard response must be gone;
the compatible API normally returns 404. This proves routing without changing business state:

```bash
ESQ_RECOVERY_BODY="$(mktemp)"
ESQ_RECOVERY_STATUS="$(curl -sS -o "$ESQ_RECOVERY_BODY" -w '%{http_code}' -X POST \
  "$ESQ_BASE_URL/api/v1/rollback-write-probe")"
if [ "$ESQ_RECOVERY_STATUS" = 503 ] && \
   [ "$(cat "$ESQ_RECOVERY_BODY")" = 'Write operations are disabled during compatibility rollback.' ]; then
  echo 'rollback: compatibility guard is still active' >&2
  exit 1
fi
```

Start only the compatible worker and Beat processes, then confirm their state:

```bash
"${ESQ_COMPOSE[@]}" start worker beat
"${ESQ_COMPOSE[@]}" ps worker beat
```

Keep both staging buckets versioned and retain the CORS/configuration changes permanently. This rollback
does not persist or establish ownership of target WORM VersionIds; it only prevents an incompatible
producer from selecting or promoting the wrong staged source.

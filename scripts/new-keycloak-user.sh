#!/usr/bin/env bash
# Create a Keycloak sign-in account and print its `sub` for the EasySynQ user invite.
#
#   ./scripts/new-keycloak-user.sh <username> [email] [FirstName] [LastName]
#
# EasySynQ identifies people by their Keycloak `sub` (the user UUID), NOT by username — the
# Administration -> Users invite asks for that value. Finding it by hand means digging through the
# Keycloak admin console, so this prints it directly. The account is created with a TEMPORARY
# password: Keycloak forces the person to choose their own at first login, and EasySynQ flips them
# INVITED -> ACTIVE on that first successful sign-in.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  cat >&2 <<'EOF'
usage: ./scripts/new-keycloak-user.sh <username> [email] [FirstName] [LastName]

  Creates the Keycloak account, sets a temporary password (prompted twice), and prints
  the `sub` to paste into EasySynQ -> Administration -> Users.
EOF
}

USERNAME="${1:-}"
[ -n "$USERNAME" ] || { usage; exit 2; }
EMAIL="${2:-}"; FIRST="${3:-}"; LAST="${4:-}"

[ -f .env ] || { echo "new-keycloak-user: no .env — run scripts/install.sh first" >&2; exit 1; }

# Read .env the way docker compose does. A naive `cut -d=` feeds an inline `# comment` to kcadm as
# part of the value; a naive comment-strip corrupts a QUOTED value that legitimately contains `#`.
# Compose strips surrounding quotes and treats a quoted body as literal, so mirror both rules.
env_val() {
  local v
  v="$(grep -m1 "^$1=" .env | cut -d= -f2-)"
  v="${v%$'\r'}"                                            # tolerate a CRLF .env
  v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+$//')"      # trim first, so a quote ends the value
  case "$v" in
    \"*\"|\'*\') v="${v#?}"; v="${v%?}" ;;                  # quoted: literal, `#` included
    *) v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')" ;;
  esac
  printf '%s' "$v" | sed -E 's/^[[:space:]]*//'
}

PROFILE="$(env_val EASYSYNQ_PROFILE)"; PROFILE="${PROFILE:-s}"
KC_ADMIN="$(env_val KEYCLOAK_ADMIN_USER)"; KC_ADMIN="${KC_ADMIN:-admin}"
KC_PW="$(env_val KEYCLOAK_ADMIN_PASSWORD)"
[ -n "$KC_PW" ] || { echo "new-keycloak-user: KEYCLOAK_ADMIN_PASSWORD is empty in .env" >&2; exit 1; }

# `exec` (not `run`) deliberately: it attaches to the ALREADY-RUNNING keycloak container, so the
# overlay set does not have to match the deployed one and no container is recreated. `docker compose
# run` would start dependencies and recreate any whose resolved config differs from this file set.
#
# MSYS_NO_PATHCONV=1: on native Windows + Git Bash, MSYS rewrites the container path
# `/opt/keycloak/bin/kcadm.sh` into a host path (`C:/Program Files/Git/opt/…`) before docker sees it
# and the exec fails with `exit 127`. Harmless no-op on Linux/macOS.
kc() {
  MSYS_NO_PATHCONV=1 docker compose --env-file .env \
    -f infra/compose/compose.yml -f "infra/compose/compose.${PROFILE}.yml" \
    exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null
}

# ⚠ `-q username=X` is a CONTAINS match: querying `ann` also returns `joann`. Without `exact=true`
# this would report another account's `sub`, or reset the wrong person's password. Re-verify the
# returned username anyway — never act on an account we did not ask for.
user_sub() {
  local want="$1" json id name
  json="$(kc get users -r easysynq -q username="$want" -q exact=true --fields id,username 2>/dev/null || true)"
  id="$(printf '%s' "$json"   | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'       | head -1)"
  name="$(printf '%s' "$json" | sed -n 's/.*"username"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  [ -n "$id" ] || return 1
  if [ "$name" != "$want" ]; then
    echo "new-keycloak-user: refusing to act — asked for '$want', Keycloak returned '$name'" >&2
    exit 1
  fi
  printf '%s' "$id"
}

kc config credentials --server http://localhost:8080 --realm master \
  --user "$KC_ADMIN" --password "$KC_PW" >/dev/null
unset KC_PW

# Prompt twice. A single hidden prompt makes a typo invisible, and the resulting failure is
# indistinguishable from a wrong password at the login screen.
while :; do
  printf 'Password for %s: ' "$USERNAME" >&2; read -rs pw1; printf '\n' >&2
  printf 'Confirm:          ' >&2;            read -rs pw2; printf '\n' >&2
  [ -n "$pw1" ] || { echo "empty — try again" >&2; continue; }
  [ "$pw1" = "$pw2" ] && break
  echo "passwords do not match — try again" >&2
done

if user_sub "$USERNAME" >/dev/null 2>&1; then
  echo "'$USERNAME' already exists — resetting its password only"
else
  args=(-s "username=$USERNAME" -s enabled=true)
  [ -n "$EMAIL" ] && args+=(-s "email=$EMAIL" -s emailVerified=true)
  [ -n "$FIRST" ] && args+=(-s "firstName=$FIRST")
  [ -n "$LAST"  ] && args+=(-s "lastName=$LAST")
  kc create users -r easysynq "${args[@]}"
fi

kc set-password -r easysynq --username "$USERNAME" --new-password "$pw1" --temporary
unset pw1 pw2

SUB="$(user_sub "$USERNAME")" \
  || { echo "new-keycloak-user: created the account but could not resolve its sub" >&2; exit 1; }

cat <<EOF

Keycloak sub for '$USERNAME' — paste this into EasySynQ -> Administration -> Users:

$SUB

Next: assign their seeded role(s). They stay INVITED until their first successful
login, then become ACTIVE automatically.

If a correct password is later rejected, suspect Keycloak's brute-force lockout
(it reports a generic "invalid username or password"):
    ./scripts/clear-keycloak-lockout.sh $USERNAME
EOF

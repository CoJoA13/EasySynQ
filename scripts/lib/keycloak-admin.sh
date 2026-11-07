# Shared by the two host-side Keycloak admin scripts, after changing to the checkout root.
# Compose is the dotenv/interpolation authority; do not source .env or approximate its grammar.
load_keycloak_admin() {
  local root base compose_root project model output config_set existing config_file local_file
  local seen base_found=0
  local -a config_sets=() config_files=() credentials=()
  root="$(pwd -P)"
  base="$root/infra/compose/compose.yml"
  compose_root="$root"
  # Native Docker does not understand /c/... when MSYS path conversion is disabled. Convert
  # host paths explicitly, while leaving the kcadm container path untouched.
  if command -v cygpath >/dev/null 2>&1; then
    compose_root="$(cygpath -m "$root")" || return 1
  fi

  # Resolve the project name with Compose's host/.env precedence. Suppress raw parser diagnostics:
  # malformed dotenv lines and resolved config can contain passwords.
  if ! model="$(MSYS_NO_PATHCONV=1 docker compose --env-file "$compose_root/.env" -f "$compose_root/infra/compose/compose.yml" config --format json 2>/dev/null)" \
    || ! project="$(printf '%s' "$model" | python3 -c '
import json, sys
value = json.load(sys.stdin)["name"]
if not isinstance(value, str) or not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in value):
    raise ValueError("invalid project")
sys.stdout.write(value)
' 2>/dev/null)"; then
    echo "keycloak-admin: cannot resolve Compose project; check Compose, Python 3 and .env" >&2
    return 1
  fi
  if ! output="$(docker ps --filter "label=com.docker.compose.project=$project" \
    --format '{{.Label "com.docker.compose.project.config_files"}}' 2>/dev/null)"; then
    echo "keycloak-admin: cannot inspect the running Compose deployment" >&2
    return 1
  fi
  while IFS= read -r config_set; do
    [ -n "$config_set" ] || continue
    seen=0
    for existing in "${config_sets[@]}"; do
      [ "$existing" != "$config_set" ] || seen=1
    done
    [ "$seen" -eq 1 ] || config_sets+=("$config_set")
  done <<< "$output"
  if [ "${#config_sets[@]}" -ne 1 ]; then
    echo "keycloak-admin: expected one running Compose file set; check the project and deployment" >&2
    return 1
  fi
  IFS=',' read -r -a config_files <<< "${config_sets[0]}"
  KC_COMPOSE=(docker compose --env-file "$compose_root/.env" --project-name "$project")
  for config_file in "${config_files[@]}"; do
    local_file="$config_file"
    if command -v cygpath >/dev/null 2>&1; then
      local_file="$(cygpath -u "$config_file")" || return 1
    fi
    if [ ! -f "$local_file" ] || [ ! -r "$local_file" ]; then
      echo "keycloak-admin: an active Compose file is unavailable; restore the deployed file set" >&2
      return 1
    fi
    [[ "$local_file" -ef "$base" ]] && base_found=1
    KC_COMPOSE+=(-f "$config_file")
  done
  if [ "$base_found" -ne 1 ]; then
    echo "keycloak-admin: running deployment does not belong to this checkout" >&2
    return 1
  fi
  # Keep the existing explicit nonempty source-password requirement even though the base model
  # offers a development fallback. This extension validates with Compose, changes no service,
  # and is deliberately absent from exec's file set.
  if ! model="$(MSYS_NO_PATHCONV=1 "${KC_COMPOSE[@]}" -f - config --format json 2>/dev/null <<'YAML'
x-easysynq-admin-password-required: ${KEYCLOAK_ADMIN_PASSWORD:?required}
YAML
  )"; then
    echo "keycloak-admin: cannot resolve admin credentials; check .env, overlays and nonempty KEYCLOAK_ADMIN_PASSWORD" >&2
    return 1
  fi
  # Validate BOTH fields before emitting anything. NUL delimiters preserve trailing newlines;
  # the count check also catches process-substitution failures without exposing the model.
  mapfile -d '' -t credentials < <(printf '%s' "$model" | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["keycloak"]["environment"]
values = [env["KC_BOOTSTRAP_ADMIN_USERNAME"], env["KC_BOOTSTRAP_ADMIN_PASSWORD"]]
if any(not isinstance(v, str) or not v or "\0" in v for v in values):
    raise ValueError("invalid credentials")
sys.stdout.buffer.write(b"\0".join(v.encode() for v in values) + b"\0")
' 2>/dev/null)
  if [ "${#credentials[@]}" -ne 2 ]; then
    echo "keycloak-admin: final Compose Keycloak admin credentials are missing or invalid" >&2
    return 1
  fi
  KC_ADMIN="${credentials[0]}"
  KC_PW="${credentials[1]}"
}

# Attach only to the existing container. Preserve container paths under native Windows Git Bash.
kc() {
  MSYS_NO_PATHCONV=1 "${KC_COMPOSE[@]}" exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null
}

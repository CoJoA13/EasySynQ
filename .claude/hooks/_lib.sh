# Shared helper for the .claude/hooks/*.sh scripts. Source it: `source "$DIR/_lib.sh"`.
#
# Extracts the edited file path from the Claude Code hook JSON on stdin, normalized to
# forward slashes. We do NOT use jq — it isn't installed on this box (Git Bash on native
# Windows), so the older jq-based hooks were silently no-opping. sed is always present and
# fast (a PreToolUse + 3 PostToolUse hooks fire per edit; avoid a python spawn each time).
#
# Reads stdin; prints the path (or nothing). Handles both forward-slash and JSON
# backslash-escaped Windows paths (C:\\dev\\… -> C:/dev/…).
hook_file_path() {
  sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -1 \
    | sed 's/\\\\/\//g; s/\\/\//g'
}

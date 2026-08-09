# EasySynQ Claude compatibility

Read AGENTS.md before work. Current execution state is in docs/current-status.md; current residuals are in docs/open-residuals.md.

## Claude hooks and commands

- Active Claude hooks live under `.claude/hooks/`.
- Active Claude commands live under `.claude/commands/`.
- Claude hook wiring lives in `.claude/settings.json`.
- Claude session-start behavior is wired in `.claude/settings.json` and implemented by `.claude/hooks/test-baseline.sh`.

## Claude memory behavior

- Claude session memory remains tool-specific.
- Claude `/effort` selection is per-session.
- Claude persistent memory lives outside the repository under `~/.claude/projects/<path-derived-key>/memory/`.
- `MEMORY.md` is the index for Claude persistent memory.
- Claude memory paths are machine- and OS-specific.

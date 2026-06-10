# Mirror drift scan — operator notes (S-drift-2, R11)

The D2+D3 integrity scan re-hashes every mirrored file against the vault-persisted build manifest
(`mirror_build`) on **every mirror-sync** and on an **hourly Beat scan**
(`MIRROR_SCAN_INTERVAL_SECONDS`, default 3600 — the accepted drift window equals this interval;
tighten it to narrow the window at the cost of I/O). Divergence is **quarantined before the
vault-wins rebuild**, audited (`MIRROR_STALE` = an older revision's bytes; `MIRROR_TAMPER` =
foreign bytes / extra / missing files / symlink or `current`-pointer changes — treat as a security
signal), and summarized in the `drift_scan` table. The mirror is never trusted as truth: the
expected state is the PG `mirror_build` manifest, and the on-disk `_meta/manifest.json` is itself
byte-verified against the stored digest.

## Quarantine

- Location: `<mirror_path>/.quarantine/<UTC-stamp>__<scan-id>/` (tree-preserving, re-hashed copies
  of the divergent bytes + a `quarantine.json` index; created `0o700` — not user-browsable).
  Foreign/rogue whole trees (a planted `.builds/` dir, a hijacked `current`) are quarantined **by
  move**, so the bytes are preserved exactly and the sync's build-prune can never destroy them.
  The area inherits the mirror mount contract (writable only by the worker — see
  `nfs-root-squash-mirror-caveat.md`).
- The `current` symlink itself is verified against the build registry: a repointed, rolled-back,
  replaced-by-a-directory, or out-of-tree `current` raises `MIRROR_TAMPER`
  (`classification: POINTER_DIVERGENT`) — treat it as a deliberate-action signal.
- **Never auto-deleted** — it is forensic evidence. Review `MIRROR_TAMPER` events before cleanup;
  the audit rows keep the path + both digests, so digest-level evidence survives deletion. Clean up
  manually once an investigation closes: `rm -rf <mirror_path>/.quarantine/<stamp>__<id>/`.

## Operator commands (inside the api/worker container)

- `python -m easysynq_api.cli.mirror scan` — detect/quarantine/audit only, NO rebuild (exit 1 only
  on a scan infrastructure failure; a DIVERGENT scan exits 0 — read the printed summary / the
  `MIRROR_*` audit rows).
- `python -m easysynq_api.cli.mirror sync` — scan-first full reconcile (the correction).
- `python -m easysynq_api.cli.mirror rebuild` — as `sync`, but clears the Effective renditions
  first (re-render after a template change).

A persistent stream of `FAILED` rows in `drift_scan` (with no `CLEAN`/`DIVERGENT` between) means the
scan itself cannot run (mount/permissions/DB) — investigate; the nightly sync remains the
convergence backstop.

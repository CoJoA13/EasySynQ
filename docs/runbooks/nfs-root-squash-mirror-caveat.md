# NFS / SMB mirror caveat (root_squash + UID mapping)

The read-only filesystem **mirror** (doc 04 §10.3, R11) is written **read-write only by the worker
UID** and mounted **`:ro`** to users + the `api`. The vault (PostgreSQL + MinIO) is the source of
truth; the mirror is **regenerable, never backup-critical** — it is rebuilt from Effective versions
on every Release/Supersede/Obsolete + a nightly Beat reconcile. Caddy must **not** `file_server` it.

## If `MIRROR_PATH` is on an NFS/SMB share
Network filesystems remap UIDs, which can break the worker's write or the `:ro` guarantee:

* **`root_squash`** (the NFS default) maps remote root → `nobody`. If the worker writes as root (or
  a UID the server squashes), the mirror writes fail silently or land owned by `nobody`. **Validate**
  that the worker's container UID maps to a server identity that can write `MIRROR_PATH`, and that
  the user/`api` mount is genuinely read-only for everyone else.
* **UID/GID mapping** must be consistent across the worker (writer) and any host browsing the share,
  or the `:ro` bytes a reader sees may differ from what the worker wrote.

## Validate
```bash
# as the worker, confirm it can write + the file is owned as expected
docker compose -f infra/compose/compose.yml exec worker sh -c \
  'touch "$MIRROR_PATH/.write-probe" && ls -l "$MIRROR_PATH/.write-probe" && rm "$MIRROR_PATH/.write-probe"'
# from a user host, confirm the mount is read-only (a write MUST fail)
touch "$MIRROR_PATH/current/.should-fail" 2>&1 | grep -i "read-only\|permission denied"
```
If the mirror diverges from the vault (a tampered/edited RO file), the next reconcile **overwrites**
it from the vault (AC#2) — the vault always wins. Drift *detection*/quarantine/alarm is v1 (D-6); the
MVP guarantee is RO-mount + regeneration.

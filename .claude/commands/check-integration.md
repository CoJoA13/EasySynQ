---
description: Run the integration suite on testcontainers (the gate /check-api deliberately punts on)
disable-model-invocation: true
---

Run the `-m integration` suite, which `/check-api` deliberately does **not** cover (it needs Docker).

## Run it

First verify `docker info`. On a Linux host whose current shell predates membership in the `docker`
group, use the conditional `sg docker -c "…"` wrapper below. Two things commonly bite:

- `sg docker -c "…"` **inherits the current working directory**, so set the directory *outside* the
  `sg` call and do not `cd` again inside it. Chaining `cd apps/api` inside `sg` when you are already
  there fails with a confusing `No such file or directory`.
- `uv` **is** on PATH inside `sg docker` (`~/.local/bin/uv`) — but pass the absolute path if you see a
  `Failed to spawn: pytest`, which means the cwd was wrong, not the PATH.

Scoped run (the fastest iteration loop; the four CI-shaped isolated shards remain the publication
gate):

```bash
cd <repo>/apps/api && sg docker -c "~/.local/bin/uv run pytest -m integration tests/integration/<file>.py -q"
```

The first run pulls PostgreSQL / MinIO / Redis images and is slow. That is not a hang — wait it out.

## Then prove it is not order-dependent

⚠ **The single most common false PASS in this suite.** All integration files share ONE session
database, so a test that passes alone can fail — or pass for the wrong reason — once another file has
run first. Re-run with a document-creating file ahead of yours:

```bash
cd <repo>/apps/api && sg docker -c "~/.local/bin/uv run pytest -m integration tests/integration/test_vault.py tests/integration/<file>.py -q"
```

If the second run disagrees with the first, the test is the defect, not the ordering.

## What to check in the tests themselves

- **Assert deltas or run-scoped rows, never absolutes.** `count == 0` or "the roster has one user" breaks
  the moment a neighbouring file leaves rows behind. Capture a count before, assert the change after.
- **The inverse is equally real:** never lean on *other* tests' leftovers either. Shard composition is
  data-driven (`.test_durations`) and shifts under you, so a test can suddenly run first in a fresh
  shard. Self-provide every precondition.
- **`audit_event` is partitioned by month** with only a fixed seeded runway. Real `datetime.now(UTC)` is
  fine (the current month is seeded); a pinned far-future/past date fails the insert outright.
- **A service-level test still needs the `app_under_test` fixture** even with no HTTP — it repoints
  `get_sessionmaker()` at the testcontainer. Without it you hit localhost:5432 and get connection refused.
- **Clean up rows your fixtures mint** in a `finally`, especially anything org-scoped — a leaked row
  breaks a later `Organization.scalar_one()` consumer.

## Report

State the file(s) run, pass/fail counts, and explicitly whether the ordering re-run agreed. If anything
failed, quote the real output — do not summarize it away.

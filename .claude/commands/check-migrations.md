---
description: Round-trip Alembic up↔down + alembic check on a throwaway PG16 (mirrors the migrations CI job)
allowed-tools: Bash
---

Reproduce the `migrations` CI job locally — the project's most error-prone area. On a throwaway PG16:

1. `alembic upgrade head` (apply the whole tree to the current head — see CLAUDE.md Current-status / `alembic heads`; never hard-code a head number here).
2. `alembic downgrade base` then `alembic upgrade head` again (prove the round-trip).
3. `alembic check` must be **clean** — no phantom-DROP / phantom-create.

Common failure modes to check against `.claude/rules/engineering-patterns.md` (Migrations section):
- a new model module not imported in `db/models/__init__.py` → phantom-DROP;
- a migration-created FK/CHECK on an existing column not name-mirrored in the ORM;
- an expression/partial index not excluded in `migrations/env.py._include_object`;
- a downgrade seed-delete not guarded by `NOT EXISTS(<child>)` against a RESTRICT FK.

Run it (start an ephemeral PG16 container if Docker is available), then report each step's result. If `alembic check` reports a diff, identify which rule above it maps to and propose the fix.

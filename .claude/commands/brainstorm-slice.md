---
description: Run a full slice end-to-end — brainstorm → spec → plan → subagent-driven build → review → PR — with this repo's gates and conventions pre-loaded
disable-model-invocation: true
---

Run a complete slice using the arc that has now held four times (PRs #426, #427, #428, #429). The
superpowers skills carry the generic process; this command carries **what is specific to this repo**, so
you do not rediscover it each time.

## The arc

`superpowers:brainstorming` → spec → `superpowers:writing-plans` → `superpowers:subagent-driven-development`
→ reviewers → PR → adversarial rounds → owner merges → `/finish-slice`.

Do **not** skip the brainstorm on a backlog item. The §4 items were explicitly recorded as
"brainstorm-first" because their *shape* is an open product question, not just their implementation.

## Before the first commit — the ride-along convention

Check `git status`. `main` often carries uncommitted finish-slice doc edits from the previous slice.
Commit those as the **first commit on the new branch** (`docs: record <prev-slice> (finish-slice) — …`)
before any feature work. This has been the pattern four times running. If the tree is clean, a prior
session already landed them.

Branch: `feat/s-<name>`.

## Spec and plan

- Spec → `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`. Record the **owner's decisions
  explicitly**, including the rejected options and why — those are what a future reader needs.
- Plan → `docs/superpowers/plans/YYYY-MM-DD-s-<name>.md`.
- ⚠ **Verify every signature the plan cites against the tree.** A plan that invents a helper wastes an
  implementer's whole turn — this happened with a non-existent `authorize_or_raise`, caught only by a
  pre-flight scan. Grep for each function you name before writing it into a task.

## Decisions that need the owner, not you

- A **new permission key** (R38 is additive-only, but opening the catalog is a register-level call).
- A **decisions-register entry** — if the slice sets a binding rule, ask whether it warrants an R-number
  rather than adding one unilaterally. R64 came from exactly that question.
- Any **strategic** shape choice. Ask; do not silently pick.

## Gates

`/check-api` · `/check-web` · `/check-contracts` · `/check-integration` · `bash scripts/check-no-site-data.sh`
· `/check-migrations` **only if a migration lands**.

Verify the head with `cd apps/api && uv run alembic heads` — **never** `ls migrations/versions/ | tail -1`,
which returns `__pycache__`.

⚠ **What the gates cannot see:** `redocly lint` passes an omitted or factually wrong status code or
summary, and **no CI job runs `scripts/gen-contracts.sh`**, so `.contract.lock` drift never goes red.

## Reviewers, before the PR

`diff-critic` always. Then by surface: `migration-reviewer` (a migration) · `web-test-trap-reviewer`
(`apps/web`) · `authz-reviewer` (any gate/guard/scope change) · `notification-wiring-reviewer` (a
notification event) · `docs-drift-reviewer` (a changed user-facing flow, endpoint, gate, or register entry).

Fold confirmed findings and **mutation-verify each fix** — see `/mutation-verify`. A fix without a test
that fails without it is not a fix.

## The PR

The body becomes the squash commit **verbatim**, so write it as the permanent record and keep it
R61-clean. Then the owner's sequence: adversarial review rounds → owner authorises the squash-merge.

⚠ **Rounds do not always converge.** When a round stops finding new classes and starts finding variants,
say so and offer to file the remainder as issues rather than chasing them. Reply on every thread and
resolve only what you actually addressed — never sweep-resolve threads you have not read.

## Finally

Ask the owner about `/finish-slice` (they have said yes four out of four times).

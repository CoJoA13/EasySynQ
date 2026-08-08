# EasySynQ Manuals

These manuals describe the EasySynQ behavior and deployment artifacts that ship in the repository
today. They are task-oriented companions to the numbered design specification.

| Manual | Audience | Use it for |
|---|---|---|
| [Installation Guide](installation-guide.md) | Installer / infrastructure owner | Choose a deployment path, prepare the host, install, complete first-run setup, and prove the installation is ready. |
| [User Manual](user-manual.md) | QMS users | Navigate the application and complete document, review, register, audit, CAPA, change, and notification work. |
| [Administrator & IT Manual](administrator-it-manual.md) | System Administrator / IT operations | Manage identity, permissions, configuration, backups, integrity, monitoring, upgrades, and incidents. |

For individual operator procedures, use the [Operator Runbook Index](../runbooks/00-index.md). For
the implementation-backed findings that produced this manual set, see the
[Documentation Accuracy Audit](../documentation-audit-2026-07-30.md).

## Which document wins?

Use this order when two documents appear to disagree:

1. [`decisions-register.md`](../decisions-register.md) is authoritative for binding product and
   domain decisions.
2. Current code, migrations, Compose overlays, and generated API contract define shipped behavior.
3. These manuals and the operator runbooks describe how to use that shipped behavior.
4. Numbered docs 01–18 contain the intended architecture and design; explicit implementation-status
   notes identify designed-but-unshipped portions.
5. [`current-status.md`](../current-status.md) is the dated execution snapshot;
   [`open-residuals.md`](../open-residuals.md) is the current residual ledger; and
   [`slice-history.md`](../slice-history.md) preserves shipped evidence.

## Current packaging boundary

- Production is a single-host Docker Compose deployment.
- S and M sizing overlays ship; L is reserved.
- Both shipped profiles use PostgreSQL full-text search. OpenSearch is an extension seam, not a
  running service.
- The browser setup wizard has six screens. User, role, process-owner, and import work continues
  after finalization.
- Full 21 CFR Part 11 electronic signatures and multi-standard packs are not shipped.

# Generalized deployment lessons and product backlog

> This file does not record the state of a particular installation. Under
> [Decisions Register R61](../../decisions-register.md#r61--site-specific-operational-records-never-enter-this-repository--2026-08-02),
> host identities, topology, access paths, source inventories, progress evidence, and risk
> acceptances belong in the organization's controlled operational documentation. This handoff
> contains only reusable product lessons and the historical product-work sources referenced by
> later plans.

For current installation procedure, use
[install-ubuntu-server.md](../../runbooks/install-ubuntu-server.md).

---

## 1. Compose overlay selection

Host-side administration must use the same base, sizing, production, and optional operator overlay
set that created the deployment. Omitting an operator-owned overlay can resolve different mounts or
service configuration. Treat the concrete overlay filename as installation data; keep it in the
external worksheet. The generic shape is:

```bash
docker compose --env-file .env \
  -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml \
  -f infra/compose/compose.production.yml \
  -f <operator-controlled-overlay>.yml <cmd>
```

This is also why `scripts/easysynq` using only the base file remains a product defect (§2E).

---

## 2. Product work and reusable deployment decisions

### A. Controlled import decision procedure

Use **scan → extract/classify → human review → commit**. Mechanical stages can run unattended;
classification and commit require an authorized human. Controlled documents and retained records
must remain distinct even if they share a source tree. Open corrective actions should normally be
recreated as live CAPAs so they receive workflow, due dates, escalation, and audit treatment; closed
historical actions can be retained as records. Concrete folder counts, names, and exclusions are
installation data and do not belong here.

### B. Clause IA + Library polish — historical approved source, subsequently shipped

1. Clause 7 (Support) belongs wholly in DO; this became Decisions Register R62.
2. Remove exact-match top-level clause dropdowns that returned empty results.
3. Allow wrapped clause-tree titles to grow vertically instead of overlapping adjacent rows.
4. Limit expanded subclauses to the selected top-level clause.

The owner rejected one-line title truncation because clause titles are the vocabulary an auditor
scans. If density ever requires a clamp, use two lines plus an accessible tooltip.

### C. Clause subtree rollup — historical approved source, subsequently shipped

A top-level clause filter must include the clause and descendants. Match `number = :clause OR
number LIKE :clause || '.%'`; never use an unbounded prefix such as `LIKE '8%'`, which conflates
clauses 1 and 10. Preserve the existing per-row authorization filter around the widened query.

### D. Generic go-live security gates

Every deployment worksheet should explicitly track privileged-install access removal, out-of-band
alerting, stable address assignment, service-account logon restrictions, and an off-host audit
checkpoint anchor. The repository documents the procedures; whether a concrete installation has
passed them is external operational state.

### E. Product defects discovered through deployment exercises

1. **`scripts/easysynq` uses only `compose.yml`.** Backup, restore, upgrade, and mirror commands can
   resolve without sizing, production, or operator overlays. Until fixed, run the worker command
   with the exact deployment overlay set.
2. **`backup_policy.cron` is write-only.** The scheduler uses a fixed daily interval and does not
   read the saved cron expression; container recreation can shift wall-clock execution time.
3. **A missing realm-export leg is not evidence that `pg_dump` terminated Keycloak sessions.** The
   capture path uses read transactions and ordinary `pg_dump`. Investigate credentials, Keycloak
   availability, and concurrent service recreation. The leg is best-effort and may be absent.

---

## 3. Generalized traps

| Symptom | Reusable cause / response |
|---|---|
| `Invalid parameter: redirect_uri` | Keycloak compares redirect URIs case-sensitively while browsers normalize hostnames. Use one lowercase FQDN everywhere. |
| Correct password rejected | Check Keycloak brute-force lockout before repeatedly resetting credentials. |
| `Invalid bootstrap secret` | Copy the token without presentation whitespace and verify its exact length. |
| Containers recreate unexpectedly | Ensure the host wrapper uses the deployment's complete overlay set. |
| Import finds nothing | Verify the configured read-only source mount exists before starting containers; a later mount may not appear inside an existing bind mount. |

Schema names that commonly mislead: `system_config` holds bootstrap fields; `role_assignment` maps
users to roles; `role_grant` maps roles to permissions; backup run state lives on `backup_policy`
rather than a `backup_run` table.

---

## 4. Product backlog captured from field evaluation

These are product ideas, not facts about an installation. Each requires its own design and scope
decision before implementation.

1. **Easier user creation.** Remove the seam between creating an identity-provider account and
   linking its subject in Administration → Users.
2. **Permission-key visibility.** Make effective capabilities and their sources legible from the
   user-management surface.
3. **Import source picker.** Offer a constrained browse/picker flow instead of requiring a typed
   source path.
4. **User profile.** Add a user-facing profile surface, coordinated with permission visibility.

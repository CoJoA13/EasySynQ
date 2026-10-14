# Issue #435 Role Picker Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The coordinator dispatches the already-authorized independent plan, implementation, and final reviews; this worker does not spawn agents.

**Goal:** Both user administration role selectors require independent `permission.grant` and `role.read` permissions, without preventing account creation without roles or removal of an existing role assignment.

**Architecture:** Use the existing `usePermissions().can(key: string): boolean` affordance pattern and require both keys for catalog fetching and selection. Keep the `['roles']` query key and API requests unchanged. Assigned roles come from the independently authorized user-assignment endpoint and do not need catalog access.

**Tech Stack:** React 19, TypeScript 6, Mantine 7, TanStack Query 5, Vitest 5, Testing Library, MSW, Playwright Chromium; committed npm lock, Node 26.

**Closure contract:** Adopted `RES-ROLE-PICKER-ROLE-READ` from the [residual record at the planning base](https://github.com/CoJoA13/EasySynQ/blob/ddfbf400d1c3025515682ccf56e603570f1e8b6c/docs/open-residuals.md#L945-L959). [GitHub issue #435](https://github.com/CoJoA13/EasySynQ/issues/435) mirrors that work; [dated candidate evidence](../../slice-history.md#role-picker-access-requires-grant-and-catalog-read-2026-10-02) records its implementation history. Repository workflow and authority precedence are defined in [AGENTS.md](../../../AGENTS.md#authority-and-precedence).

Issue snapshot inspected at `/tmp/easysynq-issue-plan/final-issues.json`. Base: `ddfbf400d1c3025515682ccf56e603570f1e8b6c`; branch: `codex/issue-435-role-picker-access`.

## Global Constraints

- The adopted ledger contract permits either gating on `role.read` or explaining denied reads. Choose its smallest gate option; a no-read caller never requests the catalog or sees an empty selector advertising assignment.
- `permission.grant` does not imply `role.read`. Both are needed for selecting a role; account creation continues to use `user.create` and may submit `role_ids: []`.
- Preserve server authorization, deny-by-default, deny-always-wins, assignment and revoke request shapes, cache invalidation, and credential handling. A catalog-read gate must not gate the assignment list or existing revoke action.
- No API, catalog, contract, migration, dependencies, #430 password-reset semantics, or #436 role scope-binding changes. Do not redesign overrides or introduce a shared permission abstraction for two local conditions.
- Complete separate plan, implementation, and final reviews before a PR. Plan and implementation reviews are accepted; final review and PR preparation remain pending.

## Review Focus

- A granular caller holding grant authority without `role.read` must never see either selector or trigger its denied catalog request.
- A reader with `role.read` but no `permission.grant` must not see an assignment selector or fetch a catalog solely for that selector.
- A populated `['roles']` cache must not bypass either gate, including while permissions are unresolved.
- A caller lacking catalog access must still create an account without roles and remove an existing assignment using the existing capability.
- A caller holding both keys must still select seeded roles and send the correct role identifiers on both surfaces.

## Files and setup

Modify only `apps/web/src/admin/CreateUserModal.tsx`, `CreateUserModal.test.tsx`, `UsersAdmin.tsx`, and `UsersAdmin.test.tsx` for behavior and regressions. Correct the two permission statements in `docs/manuals/administrator-it-manual.md` §5.2 after the behavior is verified. Coordinator owns shared ledger/history reconciliation and links closure evidence for #435 after final review; do not rewrite historical user-create plans/specs.

**Planning setup snapshot (before implementation):** Node `v26.8.1`, npm `11.19.0`, `just`, and Chromium build `1243` are present. `apps/web/node_modules` is absent, so runtime red/green proof has not been attempted during planning. Once implementation is approved, coordinator runs `npm ci --prefix apps/web` from this worktree; do not change the lock. Contracts tooling is also unhydrated; hydrate `packages/contracts` only if an affected check proves it necessary. Coordinator owns finite setup/build/test workloads that may exceed 60 seconds via Codex Process Jobs. No live backend is required for component tests.

### Task 1: Gate both selectors and prove denied and allowed behavior

**Interfaces:** Consume `usePermissions()` and existing `RoleSummary`/local `Role` data; produce no public API or new component props. In each component derive `canAssignRoles = permissions.can('permission.grant') && permissions.can('role.read')` from one local permissions hook result.

- [x] Add regressions to the two existing component test files using real components/providers and MSW at the HTTP boundary. Use dedicated QueryClients and wait for the `/me/permissions` response plus `queryClient.isFetching() === 0` before negative assertions; absence during initial loading alone is insufficient.
  - Create modal: `user.create` + `permission.grant`, without `role.read`; a counted `/roles` handler returns 403 if called. Assert no Roles picker, zero catalog requests; submit a username and assert the provisioning body has `role_ids: []` and the show-once password still renders.
  - Manage drawer: `user.read` + `permission.grant`, without `role.read`; open Manage, wait for assignment/override reads and permissions to settle, then assert no `Assign a role` selector or Assign button and zero catalog requests. Return an existing Employee assignment, click `Revoke role Employee`, and assert DELETE targets its exact assignment URL.
  - Parameterize both surfaces' hidden-selector/no-catalog assertions for `role.read` alone and neither key. Repeat grant-without-read with preseeded `['roles']` data to prove shared cache contents do not render unauthorized selectors. Include a deferred permissions response with cached roles to prove selectors stay hidden until permissions resolve.
  - Create modal with both keys: list Employee/Quality Manager, select Employee, and assert its exact id reaches provisioning `role_ids` (retain existing collision and role-drop-warning coverage). Manage with both keys: select Employee and assert POST `/users/{id}/roles` body `{ role_id: employeeId }`; retain its existing assignment-conflict alert check.
- [x] Update existing positive fixtures that currently grant only `permission.grant` when they interact with a selector to also grant `role.read`. Give the existing Manage assignment-conflict test explicit grant+read permissions instead of depending on the permissive default catalog handler. Keep removal/credential-only fixtures independent of `role.read`.
- [x] Run `npm --prefix apps/web test -- src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx` against unchanged production code. Record the exact failures proving the grant-without-read controls/catalog requests are present; positive fixture updates must not create unrelated failures. An environment error is not red proof.
- [x] In `CreateUserModal.tsx`, use `opened && canAssignRoles && !!token` for catalog `enabled` and `canAssignRoles` for the MultiSelect condition; update its explanatory comment to name both permissions. In `UsersAdmin.tsx`'s `ManageUser`, resolve the same conjunction locally, use it with `!!token` for the catalog query, and wrap only the Select/Assign group in it. Leave assignment/override queries, role list, revoke controls, and their mutations outside this condition.
- [x] Rerun the exact focused command and record green evidence. Confirm hidden picker means no added catalog request even when its MSW handler would deny; confirm both authorized request bodies and grant-without-read revocation. Inspect the diff for credential/reset or scope-binding changes.

### Task 2: Verify and prepare the reviewed handoff

- [x] Correct administrator manual §5.2 to require both `permission.grant` and `role.read` for same-step role selection; keep account creation available with `user.create` alone.
- [x] Run `npm --prefix apps/web run lint`, `npm --prefix apps/web run typecheck`, and `npm --prefix apps/web run build`; each must exit 0. Then run `npm --prefix apps/web test` for the web regression suite. Record exact failures and investigate before classifying them as unrelated or environment limitations.
- [x] Assess browser needs during review: this changes permission gates, without new layout or browser APIs, so the component interaction/network assertions provide direct #435 evidence. The existing browser suite has no admin role-picker scenario. #559 exclusively owns port 4174 during this batch; do not launch a competing browser workload. If review identifies a browser-specific gap, coordinate its deterministic scenario and port with the parent before running `npm --prefix apps/web run test:browser`; record its actual coverage and result.
- [x] Run `bash scripts/check-no-site-data.sh`, `just authority-check`, and `git diff --check`. Review only scoped files, mapping each acceptance criterion to fresh tests. API/full-stack/contract checks are not required by this frontend-only change; report any broader checks not run.
- [x] Hand implementation evidence and diff to the coordinator for independent implementation review. The implementation and scoped test-option re-review are accepted; fresh typecheck and all 42 focused regressions pass after the correction.
- [ ] Coordinator reruns documentation guards on the closure candidate and obtains independent final whole-branch review before preparing the GitHub PR. Do not commit, push, or create a PR before its explicit handoff.

## Planning evidence and execution status

Planning inspected the ledger, issue snapshot, authority guide, `usePermissions`, both production surfaces/tests, shared MSW defaults, package scripts, justfile, CI web gates, browser harness, and administrator manual. The original predicates and selector conditions did not require both permissions; the permissive catalog fixture masked this gap. No product-decision blocker was found.

Execution evidence, 2026-10-02: corrected tests produced nine meaningful failures among 42 before production edits; all 42 passed after the gates and again after the test-option correction. Web lint/typecheck/build and the full 2,372-test suite across 283 files passed. Site-data/authority/whitespace guards passed before closure-document edits. Browser-specific admin proof was not required or run. The narrow ledger deletion and dated history entry are prepared for final review; final documentation guards, whole-branch review and PR preparation remain pending.

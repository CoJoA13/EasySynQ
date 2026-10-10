# Credential reset authority alignment implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans after the coordinator records independent plan-review clearance. Steps use checkbox syntax. The assigned worker implements this bounded issue; the coordinator runs every command, generates contracts, reviews the integrated branch, and handles Git/PR actions. Do not spawn agents.

**Goal:** Offer roster password reset only to callers who hold `user.create` and satisfy the server's existing system-tier rule, and make current authority wording describe every reset, including self-reset.

**Architecture:** Publish the existing PEP tier calculation through the caller's `/me/permissions` response. Consume that boolean through the existing permission hook and combine it with `user.create` in UsersAdmin. Preserve the tier calculation and unconditional reset enforcement; update their wording and verify both allowed and denied behavior.

**Tech stack:** FastAPI, SQLAlchemy, the existing pure authorization PDP, OpenAPI generation, React/TypeScript, TanStack Query, Vitest/Testing Library/MSW, pytest.

**Spec:** [Issue #430](https://github.com/CoJoA13/EasySynQ/issues/430), the [RES-CREDENTIAL-RESET-R64-ALIGNMENT closure contract](../../open-residuals.md#res-credential-reset-r64-alignment), [R64 rule 5](../../decisions-register.md#r64--in-app-identity-provisioning-ordering-non-deletion-and-credential-reset-authority--2026-08-03), and [doc 07 §4.3.1](../../07-authorization-model.md). R64 and doc 07 are binding, subject to this explicitly authorized wording alignment. Independent review approved specification and engineering quality on 2026-10-10.

## Global constraints

- Keep unconditional system-tier enforcement for every reset, including self-reset. Do not inspect the target's permissions or introduce a new permission key.
- `user.create` alone is insufficient; the endpoint still requires it in addition to system tier.
- Do not edit `apps/api/src/easysynq_api/api/users.py` without coordinator agreement. Its current route docstring and guard are already unconditional.
- Preserve the existing helper's filtering, scope/ABAC checks, deny precedence, request context, audit behavior, and all sibling two-tier guards.
- Workflow stages: independent plan review and coordinator red execution are complete. Production/API/schema/authority implementation is cleared within the listed ownership. Worker source implementation is ready for coordinator generation and green verification. The coordinator owns all tests, builds, installs, generation, commits and external writes in every stage.
- Implementation branch/worktree supplied by coordinator: `codex/issue-430-reset-alignment`, `.worktrees/issue-430`, base `3d1bb16`. Recheck sources there before implementation; do not create another worktree.
- Coordinator confirmed #430 owns its `/me/permissions` OpenAPI source change; coordinator owns generated artifacts and commands. Report any additional contract delta before editing.

## Diagnosis and compatibility decisions

Current `UsersAdmin.tsx` derives `canCreate` from `usePermissions().can("user.create")` and passes that value as `canIssuePassword`. The hook exposes effective keys and provenance, but no tier result. `permission.grant` can itself be content-only, so adding that key to the UI condition would still be wrong; role names and provenance cannot capture scoped overrides or predicates.

`services/authz/pep.py::_is_system_tier` gathers the caller's `permission.grant` grants, filters out entries with truthy `content_only`, then runs the existing PDP at `ResourceContext.system()` with the current time and no source IP. The full policy decision on those remaining grants governs tier. Expired, premature, narrower-scope and IP-restricted grants cannot confer tier; applicable DENY on a remaining grant wins. A content-only entry continues to be filtered exactly as today, including content-only DENY entries: this issue must not alter that established selection rule.

`assert_can_reset_credential` invokes that helper unconditionally. Its docstring/detail, R64 and doc 07 retain misleading “another user” wording. The route and published reset contract already state every existing linked user; preserve those aligned statements.

Expose a public `is_system_tier(session: AsyncSession, granter: AppUser) -> bool` by renaming the existing helper and updating its five PEP call sites without changing the body. Export it from `services/authz/__init__.py`; `/me/permissions` calls the same function. Add required response field `system_tier: boolean` to a new dedicated OpenAPI `MyEffectivePermissions` schema, with the same `scope` and `permissions` definitions as `EffectivePermissions`. Only `/me/permissions` switches to that schema. The admin `/users/{id}/effective-permissions` contract and shared computation remain unchanged.

The handwritten TypeScript `MePermissions` declares required `system_tier: boolean`, matching the always-emitted server response and required OpenAPI field. Update its four typed CAPA test fixtures with false; these are compatibility edits to satisfy the new response type. Runtime payloads from an older server can still omit the property, so `usePermissions` adds `isSystemTier: query.isSuccess && query.data?.system_tier === true`; it must not infer tier from permission keys. Model deliberately missing response data as an untyped MSW wire fixture in the regression tests. Existing `can(key)` behavior and cache policy stay as shipped. Background refresh can retain a successful cached affordance; an errored query hides reset even if stale data remains. The API rechecks every actual reset, so the ordinary five-minute affordance cache is not authorization.

## Exact file ownership

| Owner | Path | Bounded change |
| --- | --- | --- |
| #430 worker | `apps/api/src/easysynq_api/services/authz/pep.py` | Public helper rename; sibling call sites; unconditional reset docstring and detail |
| #430 worker | `apps/api/src/easysynq_api/services/authz/__init__.py` | Export `is_system_tier` |
| #430 worker | `apps/api/src/easysynq_api/api/auth.py` | Add server tier result to self permission response and document it |
| #430 worker | `packages/contracts/openapi.yaml` | Self response schema and self endpoint description only |
| #430 worker | `apps/web/src/lib/types.ts` | Required `system_tier` field on `MePermissions` matching server output |
| #430 worker | `apps/web/src/app/shell/usePermissions.ts` | Fail-closed `isSystemTier` result |
| #430 worker | `apps/web/src/app/shell/usePermissions.test.tsx` | Capability success/missing/loading/error tests |
| #430 worker | `apps/web/src/admin/UsersAdmin.tsx` | `canCreate && permissions.isSystemTier` for reset; preserve create gate |
| #430 worker | `apps/web/src/admin/UsersAdmin.test.tsx` | Granular override regression; negative states; truthful reset-success fixtures |
| #430 worker | `apps/web/src/test/msw/handlers.ts` | Default self permission response explicitly has `system_tier: false` |
| #430 worker | `apps/web/src/features/capa/CapaBoardPage.test.tsx` | Add `system_tier: false` to four `satisfies MePermissions` fixtures only |
| #430 worker | `apps/api/tests/unit/test_authz_system_tier.py` (new) | Real PDP characterization of the public tier helper |
| #430 worker | `apps/api/tests/integration/test_me_permissions.py` | Real DB/API tier publication proofs |
| #430 worker | `apps/api/tests/integration/test_credential_reset_authority.py` (new) | Self-reset denial, aligned detail, zero Keycloak side effects and audit attribution |
| #430 worker | `apps/api/tests/unit/test_identity_onboarding_contract.py` | Guard current R64/doc 07 wording and self permission contract schema |
| #430 worker | `docs/decisions-register.md` | R64 rule 5 sentence only: every existing user including self |
| #430 worker | `docs/07-authorization-model.md` | §4.3.1 canonical sentence only, plus explain self included |
| Coordinator | `packages/contracts/.contract.lock`, `packages/contracts/dist/openapi.json`, `apps/api/src/easysynq_api/_generated/models.py`, `apps/web/src/api/_generated/schema.d.ts` | Regenerate together through `just contracts`; never hand-edit |
| Coordinator | `docs/open-residuals.md`, `docs/slice-history.md`, `docs/current-status.md` if warranted | Closure/evidence reconciliation after fresh integrated verification |

The new reset integration file may import the existing synthetic helpers `_sub`, `_install_kc`, `_grant_user_create_only` from `test_users_provision.py` and `_auth` from `test_vault.py`; it does not edit that shared test surface, which #431 owns. Reviewers must assess this test-module coupling. Recheck helper signatures after #431 integrates; if safe reuse requires shared-helper extraction, report that expanded ownership before making it. No migrations, lockfiles, dependency changes, or additional production surfaces are planned.

## Review focus

1. A granular `user.create` caller sees Create user, but no Credentials/reset control after responses settle; a content-only grant cannot masquerade as system tier.
2. Missing tier data and initial loading hide reset; a refresh error hides it even when the query holds previously true data.
3. System-tier authority without `user.create` does not expose reset; both authorities allow it and preserve the existing show-once flow.
4. Self-reset stays refused for a non-system-tier caller before any Keycloak password call; its audit names the reset operation.
5. Scope, expiry, future validity, IP predicates, mixed content/system grants, and applicable DENY continue to produce the same tier decision the PEP enforces.

## Task 1: Publish the existing server tier calculation

**Interfaces:** Produce `is_system_tier(session: AsyncSession, granter: AppUser) -> bool` and self response `{scope, permissions, system_tier: bool}`. The boolean always evaluates SYSTEM tier, even when permission keys are requested at a narrower scope. It is an unaudited affordance probe and introduces no enforcement dependency.

- [x] Add `test_me_permissions_reports_granular_user_create_as_non_system_tier`: grant only `user.create`, request self permissions, assert that key is ALLOW and `system_tier is False` (no `user.read` needed).
- [x] Add `test_me_permissions_reports_content_only_grant_as_non_system_tier`: grant seeded QMS Owner plus granular `user.create`, assert effective `permission.grant` ALLOW can coexist with `system_tier is False`.
- [x] Add `test_me_permissions_reports_system_tier_independently_of_requested_scope`: seeded System Administrator returns `True` at default SYSTEM and at a narrower requested scope. Existing self/scoped permission tests keep their permission-entry assertions.
- [x] Ask coordinator to run `cd apps/api && uv run pytest tests/integration/test_me_permissions.py -m integration -q`. New tests must fail because `system_tier` is absent, not from setup failure.
- [x] Add new unit `test_system_tier_uses_effective_non_content_only_grants`, parametrized with real `ResolvedGrant` objects and a fixed UTC clock. Stub only DB `gather_grants` and the clock; run the real helper/PDP. Expected results: empty false; content-only ALLOW false; live unrestricted SYSTEM ALLOW true; mixed content/system ALLOW in either order true; live SYSTEM ALLOW plus applicable SYSTEM DENY false; expired false; future-valid false; FOLDER scoped false; IP-restricted with no request IP false. Assert the gather call asks for the correct caller/org and `permission.grant`. For the test-only stage, call the existing private `_is_system_tier` helper so collection succeeds and these characterization cases pass on unchanged production. After the semantics-preserving public rename, update only that call to `is_system_tier` and rerun the same cases; HTTP/UI/wording assertions provide behavioral red evidence.
- [x] Rename/export the helper without altering its body. In `auth.me_permissions`, compute the existing response, append `system_tier = await is_system_tier(session, user)`, return it. Do not change `compute_effective_permissions` or the admin endpoint.
- [x] Add `MyEffectivePermissions` with `additionalProperties: false`, `required: [scope, permissions, system_tier]`, scope shape and entry reference matching current EffectivePermissions, and boolean field documentation identifying the PEP's SYSTEM tier test. Switch only `getMyPermissions`'s 200 response to it. Do not edit the already aligned reset operation.
- [ ] Ask coordinator for focused unit and integration green evidence, then coordinator runs `just contracts` and `just contracts-check`. Review generated deltas for only the added self response shape.

## Task 2: Gate the roster on the published tier

**Interfaces:** Consume `MePermissions.system_tier`; produce hook `isSystemTier: boolean`. UsersAdmin resolves permissions once, derives `canCreate = permissions.can("user.create")`, and passes `canIssuePassword = canCreate && permissions.isSystemTier`.

- [x] Add hook tests `isSystemTier requires a successful explicit true server answer`, `isSystemTier is false while loading`, and `isSystemTier becomes false after a failed refresh with cached true data`. Use real query execution/MSW, `retry: false`, and settled notifications. Cover false, missing, true; do not mock the hook. The existing implementation fails because it exposes no boolean.
- [x] Add component `a granular user.create holder can create users but cannot reset credentials`. Mock permission source `user_override`, `system_tier: false`, render a roster, open Manage, settle permission/drawer queries and assert Create user exists, Credentials/reset absent, reset POST count zero. This is the core red test: old code displays reset despite the false server tier.
- [x] Add parameterized component absence checks for explicit true tier with missing/DENY `user.create`, missing tier with ALLOW `user.create`, initial loading, and permission endpoint error. For missing/error checks, await query success/error respectively before asserting absence; release delayed handlers in `finally`.
- [x] Split test fixtures into granular `grantUserCreate()` (tier false) and reset-authorized helper (user.create plus tier true). Update only reset success/secrecy/drawer lifecycle tests to use the authorized fixture. Preserve the header create test's granular identity. Preserve its show-once secrecy, cache clearing and in-flight closure assertions.
- [x] Ask coordinator to run `cd apps/web && npm test -- src/admin/UsersAdmin.test.tsx src/app/shell/usePermissions.test.tsx` for intended red evidence before hook/roster source changes.
- [x] Add the required response field and fail-closed hook boolean specified above, then replace the reset prop expression and stale explanatory comment. Set the shared default MSW response's tier false. Add false to the four typed CAPA fixtures without changing their permission keys or assertions.
- [ ] Ask coordinator to repeat the focused command for green evidence, then web lint/typecheck/build and affected full web tests, including `src/features/capa/CapaBoardPage.test.tsx` to verify the fixture compatibility edits.

## Task 3: Align authority and prove unconditional self-reset

**Interfaces:** Preserve 422 `two_tier_violation` and audit `permission_key="user.reset_credential"`. Exact new reset detail: `credential reset always requires a system-tier caller, including for the caller's own account`.

- [x] Create integration `test_user_create_only_caller_cannot_reset_own_credential`: give a linked caller only user.create, POST its own user ID, assert 422/code/exact new detail, zero scripted Keycloak password calls, and a TWO_TIER_VIOLATION audit for that caller/target with reset operation key. It already refuses today; the detail assertion provides the intended red evidence.
- [x] Extend onboarding contract tests to check R64 rule 5 and doc 07 §4.3.1 include every existing user and self, and no normative “another user” restriction remains in those bounded passages or the reset guard docstring. Add `test_self_permission_contract_requires_system_tier`: self 200 references `MyEffectivePermissions`, its boolean is required, and the admin response still references `EffectivePermissions`. Ask coordinator for red evidence; do not assert historical narrative elsewhere is absent.
- [x] Update reset guard docstring to every existing user's credential including self, and use exact detail above. Keep guard execution/target handling/audit call intact; do not change the shared `_two_tier_deny` title, status or sibling details.
- [x] In R64 rule 5, use: `A caller may reset any existing user's credential, including their own, **only if the caller is system-tier — full stop**;` followed by the existing target-authority explanation. In doc 07 use: `**Every existing linked user's credential reset, including the caller's own, requires the caller to be system-tier.**` Keep new-user provisioning exemption and historical rationale intact.
- [ ] Ask coordinator to run `cd apps/api && uv run pytest tests/unit/test_identity_onboarding_contract.py tests/unit/test_authz_system_tier.py -m unit -q` and `cd apps/api && uv run pytest tests/integration/test_credential_reset_authority.py tests/integration/test_me_permissions.py tests/integration/test_users_provision.py tests/integration/test_authz.py -m integration -q`. Existing target-independent refusal and System Administrator reset positive controls must remain green.

## Coordinator verification and closure evidence

- [ ] Independently review final source diff for unconditional enforcement and the same tier helper used by UI probe and reset PEP; verify no new permission key, self carve-out, target inspection, shared users-route edit, migration or unrelated cleanup.
- [ ] Record actual focused red/green commands, exit statuses, failing assertion reason, and fresh final results. If any test cannot execute, mark that criterion unverified; source reading alone is not closure.
- [ ] Run affected API lint/format/mypy and unit suite plus web lint/build/tests using repository commands. Run `just contracts-check`; run the affected response contract case or `just test-contract` as environment permits to prove the self response shape.
- [ ] Run `just authority-check`, `bash scripts/check-no-site-data.sh`, and final `git diff --check`; inspect generated artifacts after all source changes. Coordinator owns execution, not the worker.
- [ ] Map closure: granular UI denial -> component red/green; true-tier allowed reset -> existing show-once component and API positive controls; exact server rule -> shared helper plus real PDP and real DB self response proofs; every reset/self -> denial/audit/no-Keycloak proof; authority alignment -> contract guard plus R64/doc07 diff; generated compatibility -> fresh contract check.
- [ ] Coordinator closes RES-CREDENTIAL-RESET-R64-ALIGNMENT only after independent implementation review and fresh integrated evidence, and links the reviewed PR/commit/test evidence in the authority homes. Preserve historical evidence and do not fabricate status/baseline counts.

## Risks and remaining limits

- A public helper rename touches sibling guard call sites; characterization and existing `test_authz.py` cover accidental policy change. Do not refactor policy logic during this fix.
- `/me/permissions` now gathers permission.grant once more to evaluate tier. This is bounded read-only work with no audit side effects; avoid introducing caching or a second tier algorithm to optimize it.
- Affordance caching can lag permission changes outside the existing invalidation flow. Server enforcement remains fresh; cache redesign is out of scope.
- #431 may modify shared synthetic provisioning helpers and reset response documentation. Coordinator must reconcile interface/contract deltas and rerun both issues' proofs on the integrated tree.
- Generated outputs and closure ledger are serialized coordinator surfaces. Their execution and review remain required before completion.

**Workflow status (2026-10-10):** Independent plan review approved both axes. The coordinator verified red against unchanged production: unit 13 pass / 2 intended failures; web 24 pass / 8 intended failures; CAPA fixture control 21 pass; real PostgreSQL/API integration 3 pass / 5 intended failures (CPJ `job-mv2jion8-16d24d96`). Production implementation was then cleared and the source changes are now written. Checked steps reflect source/test creation or the supplied red evidence. Green verification, canonical contract generation, independent implementation review and integrated closure evidence remain pending; the worker has executed no tests/builds/codegen.

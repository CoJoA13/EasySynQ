# Keycloak error classification implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. The coordinator owns execution and integration gates; do not dispatch agents from this lane.

**Goal:** Make provider input errors, admin-access failures, and missing linked identities lead operators to the right action, including an executable manual recovery procedure.

**Architecture:** Keep the existing credentialless-create → durable user/roles/audit → credential ordering. Narrow create validation to provider 400, distinguish reset 404 through `KeycloakSubjectMissing`, and map that exception to a documented replacement-account procedure using existing authorized endpoints. Preserve the old application principal and its historical attribution.

**Tech stack:** FastAPI, SQLAlchemy/PostgreSQL, httpx MockTransport, pytest, OpenAPI-generated Python/TypeScript contracts.

**Spec:** [GitHub issue #431](https://github.com/CoJoA13/EasySynQ/issues/431), [RES-KEYCLOAK-ERROR-CLASSIFICATION closure contract](../../open-residuals.md#res-keycloak-error-classification), [R64 in the decisions register](../../decisions-register.md), and [contributor contract](../../../AGENTS.md). The scope is provider status classification, users-route exception mapping, focused tests, and documented manual recovery through existing authorized operations.

**Execution status (2026-10-10):** Independent plan review and scoped re-review approved this design.
The parent confirmed successful collection, provider red with 9 intended failures and 22 controls
passing, and real PostgreSQL/API red with 16 intended failures and 29 existing cases deselected
(`job-mv2jion8-16d24d96`). Production, problem-vocabulary/OpenAPI source and manual edits are now
prepared. Focused green, broader regression checks, formatting/type checks, generated contracts and
independent implementation review remain pending; this document does not claim the fix passes.

## Global constraints

- Staged execution: independent plan review cleared this design. The initial authorized phase writes tests and this durable plan only; the coordinator runs the red proof before clearing production/schema/manual edits.
- Implementation uses coordinator-created `.worktrees/issue-431`, branch `codex/issue-431-keycloak-errors`, base `3d1bb16`; preserve coordinator phase gates.
- Parent runs all tests, builds, installs, contract generation, commits, PR operations, and integrated verification. Worker sends exact test requests and inspects reported evidence.
- Preserve R64 non-deletion, credential secrecy, truthful append-only audit, unconditional system-tier reset guard, org-scoped target lookup, and last-active-administrator guard.
- No database mutation instructions, principal relink, migration, new permission, automatic identity adoption, role copying, or ownership transfer.
- Existing #621 (`9f3752e`) post-commit database readback recovery and #620 Location validation remain intact.

## Review focus

- Provider 401/403 at create must never become a form validation response; lookup/token/profile failures must remain unavailable and never fall through to create.
- A reset 404 after durable creation needs stale-link recovery, while a normal transient reset failure still needs the existing reissue path.
- A generated-password provider 400 is not submitted profile input and must not expose password/body content or emit credential-issued audit.
- A stale application's email must not make replacement impossible; provider collisions require a verified existing identity or different username/omitted optional email.
- Replacement preserves previous principal attribution, grants and outstanding ownership; only explicitly reviewed replacement access is assigned.

## Evidence and root cause

Read-only inspection of executable sources at `3d1bb16` confirms:

1. `create_user()` checks 409 first, then maps *every remaining 4xx* to `KeycloakRejected`. The route maps that class to 422 `validation_error`; therefore admin authorization, missing endpoint, unsupported-method, and throttling responses incorrectly blame submitted details.
2. `set_temporary_password()` uses `raise_for_status()`, then wraps every failure as `KeycloakUnavailable`. Both ordinary reset and the post-commit provision leg return 502, advising retries of a dead subject.
3. Token acquisition, exact username lookup and realm-profile policy failures already fail closed as unavailable. Exact claimed-bootstrap profile PUT intentionally treats 400/409 as rejected recovery input; keep that established setup behavior.
4. #621 commits before response reads, protects those reads with SQLAlchemyError recovery, and proves successful reissue without another identity. The existing UI automatically refreshes the roster for `keycloak_unavailable`; the new missing-subject detail must explicitly instruct a roster reload after a committed-create failure.
5. `AppUser` has unique `keycloak_subject`, **no stored username**, and a nullable **non-unique email** (`db/models/app_user.py`). A stale row cannot reserve a username or email in the application. Keycloak has its own uniqueness rules, so replacement must be provider-aware.
6. No supported endpoint edits a user's subject. Existing APIs support creating/adopting a separate user, audited role grants, audited disable, and managed process-owner assignment/revocation. The manual already prohibits changing historical identifiers (§5.4).

The parent obtained the focused runtime red proofs before authorizing production edits; see the dated execution status above. The worker has performed only permitted static inspection and edits, not test or build execution.

## Ownership and interfaces

**This lane, after clearance:**

- `apps/api/src/easysynq_api/services/keycloak_provisioning.py`: narrow create status classification; add `KeycloakSubjectMissing`; reset status handling and safe error copy/docstrings.
- `apps/api/src/easysynq_api/api/users.py`: explicit missing-subject mapping before broad unavailable catches in both credential legs; accurate unavailable guidance.
- `apps/api/tests/unit/test_keycloak_provisioning.py`: status matrix and no secret/provider-body disclosure.
- `apps/api/tests/integration/test_users_provision.py`: public problem behavior, committed-state/audit preservation, executable replacement/disable proof.
- `apps/api/src/easysynq_api/problems.py`: append `keycloak_subject_missing` to the typed problem vocabulary.
- `packages/contracts/openapi.yaml`: only Problem.code enum and provisioning/reset response descriptions. `/users/provision` and temporary-password already declare 409; no new response status or endpoint.
- `docs/manuals/administrator-it-manual.md`: add §5.3.1 **Missing linked sign-in account recovery** between existing §5.3 and §5.4. Add create-400 versus admin-access guidance in §5.2 without changing #430's reset-authority wording.

**Coordinator-only shared surfaces:** contract generation (`packages/contracts/dist/openapi.json`, `.contract.lock`, API `_generated/models.py`, web `_generated/schema.d.ts`), eventual contract reconciliation with #430's `/me/permissions` region, ledger closure/current-status/history evidence. Do not hand-edit generated files. #430 owns R64 and doc07 reset wording; this lane does not modify them. No UI source ownership requested.

**Service interface:** `class KeycloakSubjectMissing(KeycloakUnavailable)` with no subject/password/body properties and fixed safe exception text. `create_user(...) -> str` and `set_temporary_password(*, subject: str, password: str) -> None` keep their signatures. Subclassing preserves existing setup/bootstrap catch behavior; users catches the subclass first. Missing-subject semantics apply only to reset-password 404, not realm policy, collection lookup, create 404, or claimed-bootstrap profile reads.

## Public behavior

| Provider condition | Service meaning | Users API result/action |
|---|---|---|
| Create 200/201 with usable subject | Created credentialless identity | Existing 201 after durable work and successful reset |
| Create 400 | `KeycloakRejected`, existing bounded message | Existing 422 `validation_error`, fix submitted profile |
| Create 409 | Existing exact lookup conflict classification | Existing 409 linked/unlinked/email behavior |
| Create 401/403 | `KeycloakUnavailable` | 502 `keycloak_unavailable`, check deployment admin credentials/realm permissions; no form-edit advice |
| Create other 4xx, 3xx, other unexpected 2xx, 5xx, network failure, unusable success | `KeycloakUnavailable` | 502 `keycloak_unavailable`, dependency/configuration guidance; no inferred absence |
| Reset 404 | `KeycloakSubjectMissing` | 409 `keycloak_subject_missing`, documented manual recovery |
| Reset other failure, including 400/401/403/409/429/5xx/3xx/transport | `KeycloakUnavailable` | 502 `keycloak_unavailable`, restore availability/admin access then reissue; no credential audit |
| Reset successful 2xx | Existing success semantics | Existing shown-once password and non-fatal credential-audit commit |
| Missing local admin settings | Existing `KeycloakNotConfigured` | Existing 503 `keycloak_not_configured` |
| Token, lookup, realm-profile errors | Existing unavailable behavior | Existing availability path; no create on uncertain lookup |

Exact new 409 title: **The linked sign-in account could not be found**.

Exact reset detail: **Verify that this install is using the correct Keycloak realm. If the linked account is missing, follow Administrator & IT Manual §5.3.1, Missing linked sign-in account recovery: create or link a verified replacement account, review its access, and disable the old user while preserving its history. Retrying this password reset cannot repair the link.**

For post-commit provision 404, prepend: **The EasySynQ user was created, but no temporary password was set. Do not retry this create request. Reload the user list.** Then use the same manual-recovery detail. Do not claim the external account still exists.

Use fixed safe copy for generic pre-commit/reset unavailable problems: title **Keycloak could not complete the request**; detail **Check Keycloak availability and the configured admin credentials and realm permissions. Account details do not need to be changed for an admin-access failure.** Preserve #621's SQL readback title/detail unchanged. For post-commit ordinary unavailable reset, retain “User created but the temporary password could not be set” and “Do not retry create”, replace the assertion that Keycloak could not be reached with guidance to resolve availability/admin access before reissuing.

New problem bodies contain no provider diagnostics, password, bearer, subject, or bootstrap marker. Existing provider 400 bounded detail behavior remains as-is; do not broaden sanitization or provider error parsing in this issue.

## Task 1: Pin and fix provider classification

- [x] Add `test_create_user_non_validation_status_raises_unavailable`, parameterized for 401, 403, 404, 405, 408, 415, 422, 429, 500, 503, 302 and 204. Assert `KeycloakUnavailable`, not `KeycloakRejected`, and no raw diagnostic body. Existing 400/409 tests remain expected-green characterization.
- [x] Add `test_set_temporary_password_404_raises_subject_missing`: valid token, reset 404 with synthetic secret/body, capture the existing `KeycloakUnavailable` base and assert that the exception's concrete type is `KeycloakSubjectMissing`, plus absence of subject/password/body from exception text. Do not import a nonexistent class: the initial red proof is wrong runtime classification, never collection failure.
- [x] Add `test_set_temporary_password_other_failures_remain_unavailable` for 400, 401, 403, 409, 429, 500, 503, 302; assert these are not missing/rejected and do not disclose password/body. Keep successful 200/204 and encoded opaque subject behavior characterized. Existing transport failures remain unavailable.
- [x] Request coordinator red command: `cd apps/api && uv run pytest tests/unit/test_keycloak_provisioning.py -q -k 'non_validation_status or 404_raises_subject_missing or other_failures'`. Expected initial create 4xx failures and wrong reset404 subtype assertion. The test module must collect successfully against the original production source.
- [x] Implement only `create_user()`'s 400 whitelist and reset 404 check; preserve create409 handling, Location fallback, exact lookup, reset2xx semantics and safe generic reset HTTP failure handling. Update exception docstrings/comments that currently claim every non409 4xx is submitted input.
- [ ] Request same focused green command, then whole `test_keycloak_provisioning.py` and `test_identity_provisioning.py`. Setup/bootstrap tests belong to the coordinator's neighboring verification since the exception subclasses unavailable.

## Task 2: Public response, state preservation and supported recovery

- [x] Add `test_create_provider_non_validation_error_returns_availability_problem` for create statuses 401/403/404/422/429/503. Assert 502/code/title/admin guidance, no private body, no app_user delta, no credential request/audit. Existing create400 test proves 422 remains valid.
- [x] Add `test_missing_subject_reset_returns_documented_recovery`: seed or invite an org-scoped linked user, script reset404, reset as authorized system-tier caller; assert 409/new code/manual section/retry guidance, no password field, unchanged old id/subject/status/roles and no credential-issued audit. Existing permission, cross-org and system-tier tests must remain green.
- [x] Add `test_missing_subject_after_provision_preserves_committed_user`: create with role, reset404; assert committed INVITED row, role and USER_CREATED/ROLE_ASSIGN survive, no credential audit/password, 409 manual guidance plus reload/do-not-retry-create. Use separate-session reads as #621 does.
- [x] Add `test_generated_password_rejection_returns_availability_problem`, parameterized for reset400/401/403, exercising ordinary reset and post-commit provision. Assert availability response, accurate post-commit guidance where relevant, unchanged prior state, no credential audit and no secret/body disclosure.
- [x] Add `test_missing_subject_manual_replacement_preserves_old_principal`: invite old dead subject with display name/email, give it a role through the authorized API and snapshot its creation/role audit. Prove reset404. Provision replacement with the same display name/email and a distinct new subject (real PostgreSQL constraints, real provisioning service over MockTransport); request only reviewed ordinary role via `role_ids`; assert 201, distinct user id, both rows keep that email, replacement role/creation/credential audits exist. PATCH old row DISABLED; assert USER_STATUS_CHANGED and that old id/subject/creation/role audit remain unchanged. Prove successful authorized reset of the replacement and no additional create. This validates account access recovery without claiming principal/ownership migration.
- [x] Add `test_missing_subject_manual_adoption_requires_explicit_grant_and_reset`: invite the old dead subject, assign its reviewed ordinary role through the authorized API and snapshot its historical creation/role audit. Prove its reset404; then script a verified different existing unlinked subject. Submit replacement provisioning with that username and selected ordinary role; assert 409 `keycloak_username_exists_unlinked` with that subject and no provider create/reset. Adopt using `POST /users` with the verified subject/profile; assert 201, a distinct INVITED application principal, unchanged old id/subject/audit, no provider create/reset, no temporary-password field, no replacement role assignment, USER_CREATED only and **no** USER_CREDENTIAL_ISSUED. Explicitly assign the reviewed role through `POST /users/{replacement_id}/roles`; assert role assignment and ROLE_ASSIGN audit, still no reset or credential audit. Only then call `POST /users/{replacement_id}/temporary-password` as the authorized system-tier administrator; assert one reset against the replacement subject, a shown-once credential and exactly one credential-issued audit after provider success. Disable the old row through PATCH; assert status audit and preserved old attribution. This covers a credentialless orphan and proves adoption never resets an identity or grants selected roles automatically.
- [ ] Request coordinator red for these new integration names before route edits: `cd apps/api && uv run pytest tests/integration/test_users_provision.py -q -k 'non_validation_error or missing_subject or generated_password_rejection'`; then add the typed problem code/OpenAPI source together and users exception mappings/copy. Request focused green followed by the complete users provisioning/admin suites and existing #621 readback tests.
- [ ] Extend only the focused integration transport fixture as necessary; record attempted requests and script provider status at genuine HTTP seams. In the adoption proof, snapshot counters after the old subject's failed reset and compare each later stage's delta; query audits by replacement id so the earlier old-subject attempt cannot be mistaken for a replacement reset. Preserve `_install_kc` and other shared helper signatures consumed by #430. Do not mock users exception mapping or persistence/audit behavior.

## Task 3: Manual and contract closure evidence

- [x] Write §5.3.1 with these ordered supported steps:
  1. Use another authenticated System Administrator, open an independent incident record, identify the old EasySynQ row and confirm the deployed realm/admin configuration. A realm/configuration error is resolved first; a 404 alone must not cause unreviewed account retirement.
  2. Verify the person's identity and inventory their roles, direct ALLOW/DENY scopes and recorded process ownership. The procedure restores access with a separate principal; it does not relink historical records.
  3. Use **Create user** / `POST /users/provision` for a genuinely absent identity. A deleted Keycloak account's username/email can be reused because the application reserves neither. If the username is occupied, use a different username or, only after identity verification, the existing unlinked-account adoption affordance. If email is occupied in Keycloak, omit the optional email or provide an approved different one. A username already linked to another application user is not adopted or reset as this person's replacement.
  4. Branch on the replacement path. **Created replacement:** successful `POST /users/provision` returns its shown-once temporary credential and assigns only the ordinary roles explicitly submitted in `role_ids`. **Adopted replacement:** **Link the existing account** / `POST /users` only creates the application row; it supplies no credential, changes no provider password, and grants none of the roles selected in the Create form. After verified adoption, explicitly assign the reviewed roles through **Manage** / `POST /users/{replacement_id}/roles`. Do not treat the adopted row as usable merely because linking succeeded.
  5. Complete only the reviewed necessary access with existing authority/SoD guards. For scoped bundles use `POST /users/{id}/roles` with the reviewed `bound_scope`; recreate needed direct ALLOW and DENY entries through supported overrides API without omitting DENYs or broadening scope. Managed Process Owner grants use **Administration → Processes → Manage owners** / process-owner APIs, not copied role markers. Obtain the QMS owner's decisions for content/work reassignment.
  6. **Created replacement:** deliver the returned shown-once credential. **Adopted replacement:** if it lacks a usable credential (including an orphan from failed provisioning), have the verified administrator holding `user.create` and system-tier authority explicitly choose **Manage → Issue new temp password** / `POST /users/{replacement_id}/temporary-password`; deliver that response's shown-once credential. If an existing credential already works, do not reset it merely because adoption occurred. Credential issuance is a separate explicit authorized operation and is audited only after provider acceptance. Confirm replacement sign-in before disabling the old row through **Manage** / PATCH status DISABLED, preserving its audit/signature references. Maintain another active administrator; the existing last-admin guard applies. Keep the incident record relating the separate principals.
  7. Review pending approvals, assignments and managed process ownership separately with their authorized workflows. Existing generic user lifecycle APIs do not migrate these references, and there is no general outstanding-work transfer operation. Historical actions remain attributed to the old principal. Do not edit database keys, subjects, signatures or audit history.
  If no authenticated administrator remains, point to existing §5.3 controlled host break-glass/orphan-adoption recovery and its independent incident-record requirement before this procedure. Do not reuse UNINITIALIZED-only setup recovery on a completed install.
- [x] Annotate provisioning/reset OpenAPI 409 descriptions with `keycloak_subject_missing` and the manual path; append the new Problem enum member. Preserve all other #430-owned contract edits.
- [ ] Request coordinator generation (`just contracts`) and vocabulary test (`cd apps/api && uv run pytest tests/unit/test_problem_code_contract.py -q`), then `just contracts-check`. Parent owns generated outputs and combined source reconciliation.
- [ ] Request affected API Ruff/format/mypy, identity/setup neighboring tests, users integration suites, contract response-schema cohort, `just authority-check`, and `bash scripts/check-no-site-data.sh`. Request existing web CreateUserModal/UsersAdmin tests to protect #621's roster-refresh and generic server-detail display after regenerated types. No new UI behavior is required: the new committed404 explicitly instructs close/reload.
- [ ] Coordinator records exact red/green commands and results and independent review evidence, then handles residual closure/history/current-state edits and branch/PR operations. Worker does not claim runtime completion from this plan.

## Limits and review decisions

- Manual recovery restores access under a **new** principal; there is intentionally no promise of preserving the old user's id for future ownership or pending work. Stable history/old grants remain attached to the disabled old row. Reviewer must accept this as the ledger's documented-manual-recovery option rather than an audited relink.
- A reset404 can also arise from wrong realm/path configuration. Fixed copy requires realm verification before replacement; no automatic mutation follows a provider404.
- The CreateUserModal auto-refetch trigger remains `keycloak_unavailable`; missing-subject postcommit409 needs the documented reload action. If review requires automatic visibility instead, ask coordinator to allocate a minimal UI change and its regression test rather than edit #430's UI lane concurrently.
- Shared client setup callers catch the missing exception through its unavailable base and retain setup-specific recovery semantics. This plan does not expand ordinary replacement-account recovery into durable first-admin claim recovery.

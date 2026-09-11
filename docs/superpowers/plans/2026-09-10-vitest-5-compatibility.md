# Vitest 5 compatibility implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this bounded dependency change with a fresh independent review before merge.

**Goal:** Complete the existing Vitest 5 dependency MR with a consistent npm lock and preserved web verification.

**Architecture:** Retain the Renovate commit and current main history in the same MR. Regenerate the web lock with npm, diagnose compatibility failures before changing test declarations or setup, and retain the serial isolated jsdom configuration and shared teardown barriers.

**Tech Stack:** Node 26, npm 11, Vite 8, Vitest 5, TypeScript 6, React 19, Mantine 7, jsdom 30.

**Spec:** The owner-approved dependency maintenance sequence; [MR !9](https://gitlab.com/synqsuite-group/EasySynQ/-/merge_requests/9), the repository's [contributor contract](../../../AGENTS.md), and the completed [shared transition teardown prerequisite](../../slice-history.md#s-web-transition-teardown). This changes development tooling, not product behavior.

## Constraints

- Use GitLab and npm registry services; do not use GitHub services or downloads.
- Preserve required tests, accessibility assertions, unhandled-error detection, per-file isolation, and existing security gates.
- Do not bypass peer constraints, suppress warnings, disable tests, or update unrelated dependencies to obtain a pass.
- Keep detailed diagnostics outside Git. No deployment or production activation is included.
- Accept Vitest 5's `clearMocks: true` default: each test starts with cleared mock call history while implementations remain. Preserve the existing serial isolated-file configuration.

## Task 1: Resolve and measure the candidate

**Files:** `apps/web/package.json`, `apps/web/package-lock.json`.

**Interfaces:** npm's manifest and lock feed both local checks and the two GitLab web shards. Preserve the existing `npm test`, lint, and build entry points.

- [x] Preserve the existing Renovate version commit and merge the current main baseline into its ancestry without force-pushing.
- [x] Confirm that the initial MR changes the manifest to `^5.0.0` while leaving the lock on Vitest 4; record this artifact mismatch.
- [x] Generate the lock with `npm install --package-lock-only --ignore-scripts --no-audit --no-fund`, then inspect package changes and registry origins. Run a clean `npm ci` without peer overrides.
- [x] Run `npm run typecheck` and the existing harness, query notification, and transition lifecycle tests. Preserve failing diagnostics before changing compatibility code.

## Task 2: Resolve demonstrated compatibility failures

**Files:** `apps/web/src/test/vitest.d.ts`, `apps/web/src/test/setup.ts`, and `apps/web/src/test/matchers.test.ts`. Configuration changes require evidence that existing behavior or artifact handling needs an explicit setting.

**Interfaces:** The custom `toHaveNoViolations` matcher and Testing Library matchers must retain runtime assertions and correctly typed synchronous/asynchronous calls. Shared render cleanup must still report errors and preserve unrelated timers.

- [x] Inspect installed Vitest 5 declarations against the existing matcher augmentation. If a declaration fails, retain the compiler failure and migrate only the incompatible augmentation to the published matcher interface.
- [x] For any runtime failure, identify the changed contract and retain a focused failing proof before applying its narrow fix. Do not relax the asserted application outcome.
- [x] Rerun affected checks and inspect the complete manifest/lock diff for unintended upgrades.

The initial full suite passed, but eight focused compiler assertions exposed incorrect matcher return types hidden by `skipLibCheck`. Register standalone jest-dom implementations and augment Vitest 5's `Matchers<R,T>`, keeping the expected-value extension open. The regression covers direct, negated, resolved and rejected calls, invalid names/arguments, string/regular-expression/asymmetric expectations, and actual failing DOM/accessibility conditions. All 20 focused tests and the corrected compiler checks pass. Assertions typed `void` can still return a fluent object at runtime; the test checks the published types without imposing an undefined runtime value.

## Task 3: Verify and finish the existing MR

**Files:** The final dependency/compatibility diff, this plan, and dated evidence in `docs/slice-history.md` if the upgrade is accepted.

- [ ] Pass full web lint, type checking, production build, all web tests, and the existing npm lock/audit gates. Compare test inventory with the merged baseline of 2,354 tests in 282 files; explain any runner reporting differences.
- [ ] Run repository authority, site-data, formatting, and whitespace checks. Preserve existing React warnings as visible diagnostics.
- [ ] Obtain fresh independent review of the complete candidate and actual verification evidence.
- [ ] Update MR !9 through a normal push only after verifying its source has not changed. Remove Draft/deferred status only when compatibility is established.
- [ ] Require all 14 nonoptional CI jobs on the exact reviewed source before merging. Verify merge tree identity and the automatic main pipeline afterward.

If a required dependency remains incompatible, keep MR !9 Draft and record the precise reopening condition instead of merging an incomplete upgrade.

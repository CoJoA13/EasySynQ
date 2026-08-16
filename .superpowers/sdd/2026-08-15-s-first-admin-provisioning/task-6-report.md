# Task 6 report — volatile first-administrator setup UI

## Outcome

The pre-authentication setup route now creates the first administrator natively, presents the
temporary password once, records receipt, and hands the state transition back to `App`. The setup
secret and temporary password remain in React component memory only. `UNINITIALIZED` remains public;
`IN_SETUP` reuses the existing one-shot OIDC redirect latch, while the authenticated operational
wizard and existing in-app show-once password callers retain their behavior.

## Changed files

- `apps/web/src/setup/FirstAdministratorStep.tsx`
- `apps/web/src/setup/FirstAdministratorStep.test.tsx`
- `apps/web/src/lib/types.ts`
- `apps/web/src/admin/ShowOncePassword.tsx`
- `apps/web/src/SetupWizard.tsx`
- `apps/web/src/SetupWizard.test.tsx`
- `apps/web/src/App.tsx`
- `apps/web/src/App.test.tsx`
- `.superpowers/sdd/2026-08-15-s-first-admin-provisioning/task-6-report.md`

`CreateUserModal.test.tsx` and `UsersAdmin.test.tsx` needed no source edit: their existing default-copy,
Done-action, clearing, and mutation-cache tests were retained and run in the affected gate.

## RED evidence

The first focused interaction suite was added before the component existed:

```text
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx

FAIL  src/setup/FirstAdministratorStep.test.tsx
Error: Failed to resolve import "./FirstAdministratorStep"
Test Files  1 failed (1)
Tests       no tests
EXIT=1
```

After the focused component boundary was green, the App/SetupWizard routing tests were added before
routing changed:

```text
npm --prefix apps/web test -- src/SetupWizard.test.tsx src/App.test.tsx

Test Files  2 failed (2)
Tests       6 failed | 41 passed (47)
EXIT=1
```

The six failures were exactly the new public `UNINITIALIZED`, tokenless `IN_SETUP`, and
acknowledgment/refetch success/failure seams; every existing routing test remained green.

## GREEN evidence

Focused volatile-credential interactions:

```text
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx
Test Files  1 passed (1)
Tests       13 passed (13)
EXIT=0
```

App and setup routing:

```text
npm --prefix apps/web test -- src/SetupWizard.test.tsx src/App.test.tsx
Test Files  2 passed (2)
Tests       47 passed (47)
EXIT=0
```

Preserved shared show-once callers:

```text
npm --prefix apps/web test -- src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx
Test Files  2 passed (2)
Tests       27 passed (27)
EXIT=0
```

Final combined affected gate:

```text
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx \
  src/SetupWizard.test.tsx src/App.test.tsx \
  src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx
Test Files  5 passed (5)
Tests       87 passed (87)
EXIT=0
```

Static and production gates:

```text
npm --prefix apps/web run lint
EXIT=0
```

```text
npm --prefix apps/web run build
tsc --noEmit && vite build
✓ 1107 modules transformed.
✓ built
EXIT=0
```

Vite emitted the existing advisory that the main minified chunk is larger than 500 kB; typecheck
and build completed successfully.

Vitest workers emitted Node's experimental warning that command-line `localStorage` has no
`--localstorage-file`. The volatile-secret suite installs a real in-memory `Storage` implementation
inside its isolated worker, so its local-storage non-retention assertion does not pass vacuously.

Retired-route and whitespace review:

```text
rg -n '/api/v1/setup/bootstrap' apps/web/src apps/web/e2e
EXIT=1
```

No match is the required result. The exact Task 6 `git diff --check` command passed.

## Security self-review

- Both public mutations use direct `apiSend` calls with `token=null`; neither uses a TanStack query
  or mutation result. The setup-detail query is enabled only for `IN_SETUP` with a non-null token.
- Provision success destructures `temporary_password` directly into component-local state. The
  setup secret and password never enter a URL, browser storage, query/mutation cache, toast, log, or
  persisted error value.
- Acknowledgment sends exactly `{secret}`, keeps the credential panel visible and both actions
  disabled while pending, and does not call the transition callback after an acknowledgment error.
  Password and secret refs/state are synchronously cleared before the callback.
- A single-flight ref plus disabled actions prevents duplicate provisioning/acknowledgment. The
  focused proof observes one callback. Response-loss resubmission exposes only the newly reset
  password, and remount/reload cannot recover the prior value.
- `beforeunload` is active during provision, password presentation, acknowledgment, and callback
  transition; the listener is removed after completion and on component cleanup.
- Public problem copy is code-mapped and does not render raw identity-provider title/detail data.
  Only `bound_username` is projected for `bootstrap_identity_bound`; no Keycloak subject is read or
  rendered.
- Acknowledgment refetches only public setup state. `IN_SETUP` then activates the established
  `es_auth_redirect` latch and starts login once; failed refetch never starts login.

## Accessibility and responsive self-review

- Native labeled controls cover all six approved fields; required identity fields reject blank-only
  submission. Show-once and error headings receive focus, and pending/error feedback uses live
  regions.
- Form, show-once, bound-collision, and outage states pass `axe` in the focused suite.
- Both show-once actions, the provision action, and sign-in recovery action retain a 44 CSS-pixel
  minimum target. Buttons are disabled while the associated operation is pending.
- Long maximum values and a 512-character credential use the same shrinkable DOM at 320 CSS pixels;
  wrapping prevents document-level overflow. The forced-colors proof retains native focusable
  actions without a duplicate mobile tree.

## Concerns and deferred work

No new compromise or deferred decision was introduced, so no debt record was added. The existing
live-Keycloak CI and bootstrap credential-lock debts remain unchanged. Browser E2E was intentionally
not run because it belongs to Task 8.

## Commit

`feat: create first administrator in setup`

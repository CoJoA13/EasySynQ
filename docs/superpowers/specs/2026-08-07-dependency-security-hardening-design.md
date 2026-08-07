# Dependency Security Hardening Design

**Date:** 2026-08-07

**Status:** Approved for implementation by the repository owner

**Scope:** Contract-generation tools, the Python audit runner, web dependency remediation,
the npm advisory gate, and Dependabot vulnerability automation

## Context

The repository's version-update automation is broad, but several executable tools are outside any
committed dependency manifest:

- CI, pre-commit, and contract generation invoke a floating `@redocly/cli` through `npx`.
- full contract generation invokes a floating `openapi-typescript` through `npx`.
- the security job invokes a floating `pip-audit` through `uvx`.

The current Redocly resolution is `2.46.0`, but the unqualified command follows the publisher's moving
`latest` tag. The contract generator also omits the explicit Redocly config. From the repository root,
that produces 19 warnings and reports that no configuration was supplied; from
`packages/contracts`, config discovery succeeds. Bundle bytes happen to be identical today, but the
lint and future transform behavior are working-directory dependent.

The web lock's compatible patch selections remediate the original high-severity `brace-expansion`,
`undici`, `react-router`, and inherited `react-router-dom` records, plus the later transitive
`nanoid` advisory `GHSA-2v37-7h3g-55p8`, without a major upgrade:

- `brace-expansion` to `1.1.18` and `5.0.9`, as selected by each dependency range;
- `undici` to `7.29.0`;
- `react-router` and `react-router-dom` to `7.18.2`.
- `nanoid` to `3.3.18`, which is in the advisory's patched 3.x range beginning at `3.3.17`.

The React Router maintainer advisory identifies `7.18.2` as patched and limits the affected path to
unstable React Server Component APIs. The repository does not import those APIs. The global advisory
record currently models one continuous range below `8.3.0`, so npm continues to report the patched
7.18.2 tree as high severity. The design must distinguish that feed inconsistency from a blanket audit
waiver.

Finally, GitHub's dependency graph is populated and version-update pull requests work, but Dependabot
vulnerability alerts and automated security updates are disabled. Those are repository settings; the
existing `.github/dependabot.yml` cannot enable them.

## Objective

Make dependency-related execution reproducible and make every new high or critical npm advisory stop
the CI security job, without introducing a knowingly false failure for the patched React Router release.

The change must preserve these invariants:

- no contract or audit tool is fetched from an unqualified moving tag at execution time;
- contract lint and bundle behavior is independent of the caller's working directory;
- every accepted advisory exception is narrow, machine-checked, documented, and time-limited;
- audit transport, schema, or policy errors fail closed;
- existing Python, web, contract, and generated-artifact behavior remains unchanged;
- security-update pull requests remain individually reviewable.

## Approaches Considered

### Dedicated manifests, locks, and policy code — selected

Declare executable tools in the ecosystem that owns them, commit their lock data, and use a small,
dependency-free policy program to interpret npm's machine-readable audit result. This adds several
focused files, but gives dependency tooling a single source of truth and makes exceptional behavior
testable.

### Inline exact tool versions and shell filtering

Commands such as `npx @redocly/cli@2.46.0` are a smaller diff. They still resolve and download a
transitive tree outside a committed integrity lock, duplicate version declarations, and are difficult
for Dependabot to maintain. A shell-only `jq` filter is also fragile around npm's inherited advisory
records and error responses.

### Broad repository security ratchet

The same pull request could SHA-pin every GitHub Action, restructure the web image, baseline Trivy
findings, and configure branch enforcement. Those are worthwhile follow-ups, but each changes a
different trust boundary and has distinct acceptance criteria. Combining them would make review and
rollback materially harder.

## Design

### 1. Locked contract toolchain

Add a private `packages/contracts/package.json` with exact development dependencies:

- `@redocly/cli`: `2.46.0`
- `openapi-typescript`: `7.13.0`

Commit the generated npm lockfile. CI and local setup install it with
`npm ci --prefix packages/contracts --ignore-scripts`; execution never uses `npx` or an npm fallback.
Add `/packages/contracts` as its own weekly npm entry in `.github/dependabot.yml`, preserving the
repository's existing minor-and-patch grouping for version updates. Security updates are not grouped.

Add one root-aware wrapper, `scripts/run-contract-tool.sh`, with an allowlist containing only
`redocly` and `openapi-typescript`. It resolves the repository root from its own path, verifies the
requested executable exists under `packages/contracts/node_modules/.bin`, and exits with a setup
instruction when dependencies are absent. It never downloads a replacement. For Redocly invocations,
it exports `REDOCLY_TELEMETRY=off` and `REDOCLY_SUPPRESS_UPDATE_NOTICE=true`.

Route these active paths through the wrapper:

- the `contracts` CI job;
- the contract pre-commit hook;
- `scripts/gen-contracts.sh`;
- `.claude/commands/check-contracts.md`;
- the corresponding `just` setup/check entry points and active contributor documentation.

Both Redocly `lint` and `bundle` calls pass the repository-relative
`packages/contracts/redocly.yaml` explicitly. `scripts/gen-contracts.sh` therefore produces the same
result whether invoked from the root, the contracts directory, or another working directory. Existing
historical implementation plans remain historical records and are not rewritten.

The generator resolves its root from `scripts/gen-contracts.sh` itself, never from the caller's Git
repository. Add datamodel-code-generator's `--disable-timestamp` flag so two full generations have
byte-identical Python output; the timestamp is generator metadata, not an API contract field.

### 2. Locked Python audit runner

Add a `security` dependency group to `apps/api/pyproject.toml` containing the exact
`pip-audit==2.10.1` requirement and update `apps/api/uv.lock`. Preserve the existing frozen
`uv export --no-emit-project --format requirements-txt` step because pip-audit does not consume
`uv.lock` directly. The CI security job audits that exported requirements file with
`uv run --frozen --only-group security pip-audit -r "$RUNNER_TEMP/py-requirements.txt"`, so neither
the audit runner nor its transitive tree is resolved ad hoc while the audited package versions still
come from `uv.lock`.

This slice changes tool provenance, not Python vulnerability policy. Current pip-audit findings remain
reported rather than blocked until the separate vulnerability-baseline slice defines which findings
are actionable and how they expire. The step captures pip-audit's status, accepts status `0` for a
valid clean report and status `1` only for a valid report containing vulnerabilities, and fails on any
other status, missing report, invalid JSON, or unexpected report schema.

### 3. Compatible web dependency remediation

Raise the direct `react-router-dom` floor from `^7.18.1` to `^7.18.2` and refresh only compatible
lockfile selections needed to reach the patched versions listed in Context, including transitive
`nanoid` `3.3.18` for `GHSA-2v37-7h3g-55p8`. Do not use
`npm audit fix --force`, broad dependency overrides, a Router downgrade, or a jsdom major upgrade.
The sole exception is the narrowly targeted contract override
`@redocly/openapi-core` → `js-yaml` 4.3.1 for GHSA-5p4m-2wfm-xmq; remove it when Redocly ships
the patched resolution.

The manifest change records the patched minimum for the production dependency. The lock refresh fixes
the transitive development dependencies without adding unnecessary direct requirements. Full web tests,
lint, type checking, and the production build verify the refreshed tree.

### 4. Fail-closed npm advisory policy

Add a dependency-free Node program, `scripts/check-npm-audit.mjs`. It runs npm audit against
`apps/web/package-lock.json` with `--package-lock-only`, JSON output, and a fresh cache directory, then
applies repository policy. An npm exit caused by reported vulnerabilities is parsed; an invalid response,
command failure, or unexpected exit fails the check.

The npm executable is the one bundled with the CI Node runtime, not an ad hoc download. The checker
supports npm `10.9.x` and npm audit report version `2`; it validates both before interpreting findings
and fails closed when either contract changes. Exact Node runtime and GitHub Action pinning remains the
next repository-runner hardening slice.

The checker evaluates high and critical findings by advisory identity, affected package, inherited
cause chain, and installed lockfile version. It fails for every finding except this single policy record:

| Field | Required value |
|---|---|
| Advisory | `GHSA-qwww-vcr4-c8h2` |
| Vulnerable root | `react-router` |
| Installed packages | `react-router@7.18.2`, `react-router-dom@7.18.2` |
| Allowed inherited record | `react-router-dom` caused only by the allowed root |
| Expiry instant | `2026-08-22T00:00:00Z` (exclusive; valid through 2026-08-21 UTC) |
| Usage constraint | no Router RSC dependency or affected unstable Router RSC API under `apps/web` |

The exception is stored in `.github/security/npm-audit-exceptions.json` rather than embedded in control
flow. It links to the maintainer advisory and explains the global-feed discrepancy.

A companion source-policy checker uses the TypeScript compiler API from the frozen web lock. It rejects
the RSC packages `@react-router/dev` and `@vitejs/plugin-rsc` in the web manifest, identifies static
imports and re-exports of the six unstable RSC APIs documented by React Router, and rejects dynamic
imports whose specifier is `react-router` or `react-router/dom`, or whose `react-router/` subpath
contains `rsc` or `react-server`, while the exception is active. The six APIs are
`unstable_RSCHydratedRouter`, `unstable_RSCStaticRouter`,
`unstable_createCallServer`, `unstable_getRSCStream`, `unstable_matchRSCServerRequest`, and
`unstable_routeRSCServerRequest`. Aliases are evaluated by their imported symbol, not their local name.
The checker examines Git-tracked first-party `.ts`, `.tsx`, `.js`, `.jsx`, `.mts`, `.mjs`, `.cts`, and
`.cjs` files under `apps/web/src`; it excludes generated output, tests, fixtures, build output, and
`node_modules`. AST inspection prevents comments and ordinary string contents from creating false
positives.

The audit CLI uses its real UTC clock; tests inject a clock through the policy module rather than
exposing a production time-bypass flag. The RFC 3339 expiry is an exclusive boundary: the exception
passes immediately before it and fails at or after it.

The two Router audit records are accepted atomically. When—and only when—the audit exception is used,
the same CLI runs the RSC usage checker before returning success. A missing root/inherited record or an
RSC-policy failure therefore cannot leave half of the exception accepted.

If the advisory disappears from npm's feed, the exception is simply unused and the check passes. If
the feed changes shape, either installed version changes, a prohibited RSC import appears, another
high/critical advisory is introduced, or the expiry instant is reached, CI fails.
Renewing or replacing the exception therefore requires a reviewed repository change.

### 5. CI and local flow

The contracts job installs the locked contract dependencies once before linting and checksum
verification. The security job replaces its non-blocking npm-audit shell block with the policy checker
and installs the frozen web tree needed by the TypeScript source-policy checker. The checker prints a
concise finding summary and the reason for any accepted exception without logging environment data.

Update the security-job preamble to say that npm high/critical findings are now gated while pip-audit
and Trivy findings remain report-only. Update `.claude/hooks/contract-lock-drift.sh` to remove its stale
claim that CI does not run `gen-contracts.sh`.

`just setup` installs both web and contract npm locks plus the existing Python environment. Existing
contract commands remain the human-facing interface. A contributor who runs a contract hook without
setup receives one deterministic remediation command rather than triggering a network install.

### 6. Dependabot repository settings

After the code and policy checks are locally green, enable Dependabot vulnerability alerts and
automated security updates through the repository API. Verify both settings by reading them back.
Do not enable grouped security updates or automatic merging. Existing open version-update pull requests
are neither closed nor modified by this work.

Enabling the settings may create new security-update pull requests. Those are expected external effects
and remain subject to the normal CI and owner review process.

## Error Handling

- Missing contract `node_modules` or binary: fail with the exact setup command; never call the network.
- Lockfile/manifest disagreement: frozen npm or uv installation fails.
- Contract lint/config failure: fail before generation or checksum comparison.
- pip-audit status `1` with a valid vulnerability report: report and continue; any operational status,
  absent output, or invalid schema fails.
- npm audit finding exit: parse and enforce the policy.
- Unsupported npm version or npm audit report version: fail before evaluating exceptions.
- npm transport failure, malformed JSON, unknown schema, or unexplained nonzero exit: fail closed.
- Unknown inherited vulnerability chain: fail closed instead of treating a package-name match as safe.
- Allowed advisory on any version other than the two exact 7.18.2 packages: fail.
- Allowed advisory at or after `2026-08-22T00:00:00Z`, or alongside a prohibited RSC
  dependency/API: fail.
- GitHub setting mutation or read-back mismatch: stop and report the setting that was not enabled.

## Testing Strategy

### Contract and tool provenance

- Assert manifest and lock agree on both exact contract-tool versions.
- Assert active execution paths contain no `npx` Redocly/OpenAPI TypeScript call and no floating
  `uvx pip-audit` call.
- Assert the wrapper rejects unknown tools and missing local binaries.
- Run Redocly version/lint and contract checksum verification from both the repository root and
  `packages/contracts`.
- Run one full generation from a clean ignored-output state, assert both generated files are nonempty,
  compile the generated Python module, and include the generated TypeScript declaration in type checking.
- Invoke full generation through the script's absolute path from outside the repository and compare
  hashes for the bundle and both generated files with the root invocation. The wrapper changes to the
  resolved repository root before executing tools, so relative config arguments have one meaning. The
  outside invocation runs from an initialized unrelated temporary Git repository to prove caller Git
  discovery cannot redirect the generator.
- Run the pre-commit contract hook against all files.
- Assert the wrapper exports the Redocly telemetry/update-notice opt-outs.

### Python audit runner

- Assert `pip-audit` and its transitive environment resolve from the committed `security` group.
- Assert the audit input is produced by frozen `uv export`, not by re-resolving `pyproject.toml`.
- Use a small lock/export fixture mutation to prove changing a selected `uv.lock` package version
  changes the version presented to pip-audit.
- Prove valid clean status `0` and vulnerability status `1` reports are accepted, while execution
  failure, absent output, malformed JSON, and unexpected schema are rejected.

### Advisory policy

Use committed synthetic npm-audit fixtures and temporary lock/source trees to prove:

- no findings passes;
- an unexpected high or critical finding fails;
- low and moderate findings do not fail this policy;
- the exact Router advisory and inherited DOM record pass before expiration on 7.18.2;
- the exception fails for another Router version, an additional cause, an RSC import, or an expired
  clock;
- the exception passes immediately before `2026-08-22T00:00:00Z` and fails exactly at and after it;
- the RSC source checker catches aliased imports, re-exports, Router RSC dynamic imports, and manifest
  packages while ignoring comments, ordinary strings, tests, generated files, and installed dependencies;
- unsupported npm versions and audit report versions fail before policy evaluation;
- malformed JSON, a changed schema, and simulated command failure fail closed.

Run one live npm audit after the fixture tests. The expected current result is zero high or critical
findings. The Router fixture and time-limited exception remain covered by synthetic tests: a clean feed
leaves the exception unused and passes, while the exact documented Router pair may still be accepted
before expiry. No other high or critical finding is accepted.

### Regression suite

- Run web unit tests, lint, and build against the refreshed lock.
- Run the CI workflow contract tests after changing job commands.
- Run contract lint/bundle/check and the existing API unit tests covering workflow configuration.
- Assert the security-job preamble matches the new mixed enforcement posture and the contract-lock hook
  no longer says CI omits generation checks.
- Inspect the final diff for accidental edits to `.codex/`, historical plans, or unrelated Dependabot
  pull requests.

## Acceptance Criteria

- Every active Redocly and OpenAPI TypeScript invocation executes the committed contract lock.
- `pip-audit` executes from the committed uv lock at version 2.10.1.
- Python audit input is exported from the frozen application lock; valid findings remain report-only,
  while tool/report failures stop the security job.
- Contract lint uses the explicit config and contract checksum verification passes from multiple CWDs.
- Full contract generation creates valid Python and TypeScript output with identical hashes from inside
  and outside the repository.
- Redocly telemetry and update notices are disabled in every active wrapper invocation.
- The web lock contains the patched brace-expansion, Undici, Router, and nanoid versions.
- A synthetic new high/critical npm advisory makes the security check red.
- Only the exact Router exception before its exclusive expiry passes, and mutation tests prove every
  version, inheritance, RSC-usage, and time constraint.
- The policy rejects unsupported npm/audit-report versions instead of guessing at changed output.
- No force, major, or broad override-based npm remediation is introduced. The contract toolchain has
  the sole narrowly targeted security override `@redocly/openapi-core` → `js-yaml` 4.3.1 for
  GHSA-5p4m-2wfm-xmq; remove it when Redocly ships a resolution that no longer requires it.
- Dependabot covers the contract manifest; vulnerability alerts and automated security updates read
  back as enabled.
- Existing web, contract, API workflow, and CI-hardening regressions pass.

## Non-goals and Follow-ups

- Full-SHA pinning of GitHub Actions and repository action-policy restrictions
- Exact Node runtime pinning; this slice instead validates its supported npm/audit schema contract
- Trivy filesystem/container remediation and a baseline for current image findings
- Python vulnerability blocking policy
- Converting the web image to a production-only multi-stage runtime
- Branch rulesets or required-check enforcement, which depend on repository plan capabilities
- CodeQL or secret scanning, which depend on private-repository eligibility
- Automatic merging or grouping of Dependabot security pull requests
- Editing or closing existing Dependabot pull requests

## References

- [React Router maintainer advisory GHSA-qwww-vcr4-c8h2](https://github.com/remix-run/react-router/security/advisories/GHSA-qwww-vcr4-c8h2)
- [nanoid advisory GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8)
- [React Router v7 changelog: unstable RSC APIs](https://reactrouter.com/start/start/changelog#v770)
- [Redocly CLI changelog](https://redocly.com/docs/cli/changelog)
- [npm exec behavior](https://docs.npmjs.com/cli/v11/commands/npm-exec/)
- [uv dependency groups](https://docs.astral.sh/uv/concepts/projects/dependencies/)
- [GitHub: configuring Dependabot security updates](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-security-updates)

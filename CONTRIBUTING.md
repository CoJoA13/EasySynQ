# Contributing to EasySynQ

Use the [GitLab project](https://gitlab.com/synqsuite-group/EasySynQ) for issues and merge requests.
Start with [AGENTS.md](AGENTS.md) for the shared contributor contract and
[docs/dev-workflow.md](docs/dev-workflow.md) for commands and toolchain details.

## Report a problem or propose a change

Check existing issues and [current residuals](docs/open-residuals.md) first. Choose the bug-report or
feature-request template, describe the observed behavior or need, and identify the affected version
or commit. Include a small reproduction with synthetic data and the exact expected result.

Keep credentials, customer/site identifiers, installation addresses, real document contents, and
unredacted logs out of the repository and issue tracker. Use placeholders and sanitized evidence.

## Report a security concern

Create a **confidential** GitLab issue and verify its visibility before submitting. If you cannot
create a confidential issue, contact a project Owner through an existing private channel. Do not
post vulnerability details in a normal issue while waiting for access.

Include the affected commit, the suspected security boundary, and a reproduction using synthetic
fixtures. Never include live credentials, private keys, customer data, or production exploit targets.
Confidentiality does not make the tracker a secret store. This project does not promise a response SLA.

## Make a merge request

1. Set up the supported host using the [fresh Linux guide](docs/runbooks/fresh-linux-setup.md).
2. Start a scoped branch from current `main`; keep unrelated changes separate.
3. Check [binding decisions](docs/decisions-register.md) and existing designs before changing behavior.
4. For a behavior change, demonstrate the gap and run the smallest affected checks, then the applicable
   stack gates from the `justfile`. For documentation, run `just authority-check`,
   `bash scripts/check-no-site-data.sh`, and `git diff --check`.
5. Open a merge request describing the resulting behavior, evidence, compatibility impact, and
   remaining limitations. Use the default template and link the related issue or residual.
6. Complete review, resolve discussions, and wait for the required pipeline. Protected `main` accepts
   changes through reviewed merge requests; a skipped pipeline does not satisfy the merge gate.

Keep current execution facts in `docs/current-status.md`, current deferred work in
`docs/open-residuals.md`, and dated evidence in `docs/slice-history.md`. Preserve historical records.
New source must not introduce GitHub services, hosting, credentials, or download fallbacks; see the
[GitLab setup policy](docs/runbooks/gitlab-repository-setup.md).

A merge is not a production deployment or release approval. Follow the operator runbooks and the
remaining recovery acceptance requirements before changing a live installation.

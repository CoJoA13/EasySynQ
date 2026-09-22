# Contributing to EasySynQ

Use the [GitHub project](https://github.com/CoJoA13/EasySynQ) for issues and pull requests.
Start with [AGENTS.md](AGENTS.md) for the shared contributor contract and
[docs/dev-workflow.md](docs/dev-workflow.md) for commands and toolchain details.

## License and contribution rights

The project's original code and documentation use [PolyForm Shield 1.0.0](LICENSE).
Submit original contributions under those terms only when you have the right to do so. Identify
third-party material and preserve its license and notices; do not assume Shield replaces them.
The [licensing guide](LICENSING.md) explains permitted consulting and the restriction on competing
products and services. Upstream contributions are welcome; the license does not require them.

## Report a problem or propose a change

Check existing issues and [current residuals](docs/open-residuals.md) first. Choose the bug-report or
feature-request template, describe the observed behavior or need, and identify the affected version
or commit. Include a small reproduction with synthetic data and the exact expected result.

Keep credentials, customer/site identifiers, installation addresses, real document contents, and
unredacted logs out of the repository and issue tracker. Use placeholders and sanitized evidence.

## Report a security concern

Use GitHub's private vulnerability reporting (the repository's **Security** tab, "Report a
vulnerability") and verify the report is private before submitting. If private reporting is
unavailable to you, contact a project owner through an existing private channel. Do not post
vulnerability details in a normal issue or pull request while waiting for access.

Include the affected commit, the suspected security boundary, and a reproduction using synthetic
fixtures. Never include live credentials, private keys, customer data, or production exploit targets.
Confidentiality does not make the tracker a secret store. This project does not promise a response SLA.

## Make a pull request

1. Set up the supported host using the [fresh Linux guide](docs/runbooks/fresh-linux-setup.md).
2. Start a scoped branch from current `main`; keep unrelated changes separate.
3. Check [binding decisions](docs/decisions-register.md) and existing designs before changing behavior.
4. For a behavior change, demonstrate the gap and run the smallest affected checks, then the applicable
   stack gates from the `justfile`. For documentation, run `just authority-check`,
   `bash scripts/check-no-site-data.sh`, and `git diff --check`.
5. Open a pull request describing the resulting behavior, evidence, compatibility impact, and
   remaining limitations. Use the pull request template and link the related issue or residual.
6. Complete review, resolve every conversation, and wait for the required `gate` check on the
   pull request's head. Rulesets on `main` accept only squash merges of up-to-date, reviewed pull
   requests; a skipped or stale check run does not satisfy the merge gate.

Keep current execution facts in `docs/current-status.md`, current deferred work in
`docs/open-residuals.md`, and dated evidence in `docs/slice-history.md`. Preserve historical records.
GitHub (`CoJoA13/EasySynQ`) is the primary hosting, CI, and dependency-update platform (R86); the
GitLab project is archived and its dated evidence links stay as they are. Hosting settings that
live outside Git are recorded in the [GitHub setup runbook](docs/runbooks/github-repository-setup.md).

A merge is not a production deployment or release approval. Follow the operator runbooks and the
remaining recovery acceptance requirements before changing a live installation.

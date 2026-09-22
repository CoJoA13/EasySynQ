# Security policy

## Report a vulnerability

Report suspected vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/CoJoA13/EasySynQ/security/advisories/new)
(the repository's **Security** tab, then **Report a vulnerability**). Do not open a public issue,
pull request or discussion for a suspected vulnerability.

Include the affected commit, the security boundary you believe is crossed, and a reproduction that
uses synthetic fixtures. Never include live credentials, private keys, customer data, installation
addresses or production exploit targets. The full instructions are in
[CONTRIBUTING.md](CONTRIBUTING.md#report-a-security-concern).

This project does not promise a response time.

## Supported versions

EasySynQ has no released versions yet. Fixes land on `main`; there is no backport branch. Read the
[readiness note](README.md) and the [open residuals](docs/open-residuals.md) before relying on an
installation.

## What the repository checks

Every pull request must pass the required `gate` check. Its security coverage is described in the
[GitHub setup runbook](docs/runbooks/github-repository-setup.md#security-evidence) and the design
baseline in [docs/12-security-and-audit.md](docs/12-security-and-audit.md). A green check covers the
checks that ran; it is not a security clearance.

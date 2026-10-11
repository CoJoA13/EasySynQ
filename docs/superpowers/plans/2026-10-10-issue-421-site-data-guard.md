# Issue #421 Site-data Guard Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task after independent plan review. The assigned worker owns the two source files below; the parent runs commands and owns worktrees, commits, PRs, and shared closure documents. No further delegation.

**Goal:** Close all four RES-SITE-DATA-GUARD-GAPS gaps while retaining useful false-positive controls and Git Bash compatibility.

**Architecture:** Keep the existing Bash/Git/grep/sed backstop and its temporary-Git-repository behavioral harness. Add one context-aware leading-compression IPv6 path, include textual locks, broaden only the legacy colon-hex fingerprint character class, and terminate options on every file-consuming matcher. Human sanitization and R61 remain the primary control.

**Tech Stack:** Bash, Git, GNU grep and sed, existing shell fixture harness; no language interpreter, dependency install, or new runtime tool required by the scanner.

**Spec:** [Issue #421](https://github.com/CoJoA13/EasySynQ/issues/421), [RES-SITE-DATA-GUARD-GAPS closure contract](https://github.com/CoJoA13/EasySynQ/blob/3d1bb1632502155f2f9bb9c74737e86d8856f6ad/docs/open-residuals.md#res-site-data-guard-gaps), [R61](../../decisions-register.md#r61--site-specific-operational-records-never-enter-this-repository--2026-08-02), and [contributor guide](../../../AGENTS.md). All four issue gaps and acceptance controls are reproduced in this self-contained plan. Base: `3d1bb1632502155f2f9bb9c74737e86d8856f6ad`.

## Global constraints

- "Use only synthetic fixtures."
- "Avoid recording real site-sensitive values or weakening broad scanner policy to pass fixtures."
- "Preserve Git Bash portability and the documented false-positive doctrine."
- The residual requires a context-aware leading-compression rule, an evidence-backed lock policy, case-insensitive legacy fingerprints, `--` on every matcher, failing fixtures, and a clean full tracked-tree scan.
- All ordinary tracked text is scanned; binaries remain excluded by `grep -I`, and only the scanner's own source remains excluded from default enumeration.
- Keep OpenSSH uppercase `SHA256:` case-sensitive. Lowercase `sha256:` dependency/image digests are legitimate machine metadata.
- Construct rejected tokens from split components at fixture runtime so the harness itself passes the strengthened guard.
- Committed plan/history prose uses named rejected fixtures or split components. Full synthetic rejected probe output stays in ignored execution artifacts and must not be copied into tracked documentation.
- Stage fixture files only in the harness's disposable repositories. Parent alone stages production changes and any final candidate-tree probes.
- Do not edit lockfiles, CI, shared residual/history documents, or application files. Parent integrates closure evidence and repeats the scan after batch integration.

## Owning files and interfaces

- Modify `scripts/check-no-site-data.sh`: tracked-file selection, matcher option termination, leading-compression rule and its slice context, lock-related sanctioned version context, legacy fingerprint character class/comments.
- Modify `scripts/tests/test-check-no-site-data.sh`: isolated fixtures, optional path arguments, and regression assertions.
- Existing public scanner interface remains `bash scripts/check-no-site-data.sh [path ...]`: exit 0 for clean selected tracked text; exit 1 plus rule/file/line diagnostics for rejected shapes.
- Extend harness `run_gate(repo, execution_path, [path ...])` by shifting its first two arguments and forwarding remaining arguments, quoted, to the scanner. Existing two-argument calls retain behavior; outputs remain `RUN_CODE` and `RUN_OUTPUT`.
- If needed, introduce internal `scan_leading_ipv6()` emitting `path:line:matched-token` records only. It reads the selected `FILES` array and returns through the existing `hits`/`report` contract; no full source line should enter reported output. Its internal `mask_leading_slice_spans(text)` transforms only recognized slice spans according to the exact context below.
- Parent owns `docs/open-residuals.md`, `docs/slice-history.md`, and the batch ledger. This worker supplies result summaries without modifying those files.

## Root-cause evidence and decisions to review

1. **Leading compression:** both current alternatives start with a hex group. The comment asserting every real address has leading groups is wrong. Existing tracked slice examples include numeric indexed slices in `test_audit_lineage.py` and the residual's illustrative slice. Adding a bare `::` alternative directly loses surrounding syntax and flags those examples.
2. **Locks:** default enumeration explicitly removes all `.lock` paths; explicit selection already scans them. Inspecting tracked lock content found no binary NULs. Public distribution hashes and registry URLs are benign; scanning does not require an exemption for all registry hostnames or digests.
3. **Legacy fingerprints:** the 16-byte colon-hex branch restricts bytes to uppercase. Lowercase and mixed-case forms need the same minimum 16 groups; six-group MACs keep their own rule.
4. **Options:** 11 file-consuming grep calls lack `--`; the repository-owner matcher already has it. A tracked root filename acting as a grep exclusion option can suppress matches, then `|| true` yields a false clean verdict.

### IPv6 rule alternatives

- **Selected: context-aware token masking.** Preserve the existing full/leading-group matchers. For the additional leading-compression path, inspect only candidate lines, remove recognized indexed slice fragments from their content, then extract and report remaining leading-compression tokens. Mask fragments rather than rejecting the whole line. Recognize an identifier/dotted receiver or a closing call/index delimiter followed by optional whitespace and a bracketed slice with omitted start/stop. The bounded lexical step grammar below includes arithmetic, calls, and parenthesized expressions. This adds no `.py` or whole-line exemption. Existing leading-group ambiguities such as a numeric start followed by omitted stop are outside this added rule's scope.
- **Rejected: minimum segment/length heuristic.** Requiring two trailing groups misses the issue's single-group example; requiring letters misses numeric-only addresses and still collides with identifier slice steps. A heuristic alone does not meet the residual's context-aware closure.
- **Rejected: sanctioned list of leading tokens.** Exempting arbitrary step-like values globally leaves address-shaped tokens uninspected outside slices and cannot scale with source expressions.

**Exact recognized slice context:** the bracket immediately follows an identifier/dotted identifier or `)`/`]`, allowing intervening spaces/tabs. Inside that bracket, allow spaces/tabs around the two omitted-bound colons and the step. The step is a nonempty sequence drawn only from ASCII letters, digits, underscore, dot, spaces/tabs, unary/binary arithmetic characters `+ - * / %`, parentheses, and comma. Require parentheses to balance with depth at most two; prohibit quotes, backticks, square brackets, colon, backslash, newline, and every other character in the step. These lexical checks intentionally accept ordinary expression syntax without claiming to parse Python. They support numeric/identifier steps, arithmetic with whitespace, a dotted function call with zero or simple positional arguments, parenthesized arithmetic, and a call containing one parenthesized arithmetic argument. A Bash character loop can enforce the balance/depth bound with no added tool. Replace only the bracketed span with a non-address marker, preserving the receiver and all surrounding text. Continue searching after each span so multiple slices are handled independently.

**Terminal sentence-period normalization:** collect the complete maximal leading-compression colon/hex/dot candidate and retain its following character. Before classification, remove exactly one final period only when followed by end-of-line or a space/tab, not preceded by another period, and removing it yields an entire valid pure-hex or mapped-dotted shape defined below. This is a terminal punctuation decision, not a search for a shorter matching prefix. Preserve every internal dot, repeated terminal dots, and the whole candidate when its remainder fails either complete-shape check. A malformed mapped token therefore cannot become a shorter hex candidate. Keep the period as surrounding prose; only the matched address token omits it. Apply the same normalization after a recognized slice mask and independently to each neighboring candidate.

**Whole-token classification:** classify the complete candidate after only the allowed terminal-period normalization; never fall back to a shorter hex prefix of a dotted or overlong candidate. The left and right boundaries exclude ASCII identifier characters, dot, and colon, except for the one proven sentence period handled above, preventing partial matches of a longer identifier/group. For pure hex form, require 1–7 trailing groups of 1–4 digits, including a single group. Classify the IPv4-mapped dotted form separately: compression marker + the case-insensitive mapped group `ffff` + exactly four decimal octets of 1–3 digits. Retain it as one whole token and leave its dotted endpoint to the existing IPv4 rule, whose existing sanctioned ranges decide its result. Do not globally exempt mapped addresses. As the existing IPv4 backstop is shape-based, it remains responsible even for an invalid-octet documentation fixture. An unrecognized dotted form is not eligible for shorter pure-hex extraction. Keep structural unspecified/loopback forms allowed by exact token identity; never exempt an address merely because its first group begins with the loopback digit. Quoted bare addresses and URI-bracketed hosts remain candidates. Existing RFC documentation prefix and expanded loopback sanctions remain effective.

The helper can use grep to select candidate lines with options terminated, a Bash read loop to separate relative path and line metadata from content, and Bash/sed for the bounded span mask and whole-token classifier. Process content separately from metadata so filenames cannot masquerade as addresses. Use the existing relative-path diagnostic convention; Git Bash disallows colon filenames. Do not introduce PCRE/lookbehind, Python, awk, or language setup. Every new grep invocation also terminates options.

**Known lexical ambiguity:** a string deliberately spelling an indexed slice is indistinguishable from code in this shell backstop. Steps containing strings, square-bracket indexing, or parentheses deeper than two are outside the recognized grammar; do not silently widen the mask to make a newly encountered case pass. Pin the recognized context and exclusions in fixtures. A quoted bare address, a URI host, or an address elsewhere on the same line must remain rejected.

### Lock policy and exact evidence

Inspected textual tracked locks: `apps/api/uv.lock`, `apps/web/package-lock.json`, `infra/images.lock`, `packages/contracts/.contract.lock`, `packages/contracts/package-lock.json`, the npm-audit package-lock fixture, and both uv-provenance lock fixtures. No binary NULs, uppercase OpenSSH-shaped hashes, or 16-byte colon-hex fingerprints were observed. Registry hosts are public package/distribution/funding metadata. Do not exempt a file, registry field, URL line, or all version-shaped text.

Removing exclusions exposes `click-plugins`' public version: in the lock it consists of decimal components `1`, `1`, `1`, `2`, at lines 347, 352, and 354. The first occurrence is its TOML version; the other two are public `click_plugins` distribution basenames. The already-sanctioned four-part tika version occurs in `infra/images.lock`. Lowercase dependency digests do not match uppercase OpenSSH fingerprints and need no new exemption.

**Selected sanction:** approve only the observed public package version in exact version syntax and public distribution-basename context, preserving an IPv4-shaped value used as a URL host or address field. Implement match-local filtering before IPv4 token extraction or retain exact file/line/token context to subtract only approved occurrences. A same-line unrelated address must survive. Anchor the public distribution URL's scheme/host and package basename; do not approve by a substring elsewhere on the line. Document this as a semantic sanctioned metadata shape next to the IPv4 rule, consistent with the existing explained version sanction.

The independent review approved this exact context-local semantic sanction. The global-token sanction is rejected and is not an implementation choice. Any new full-tree hit needs evidence and review, never silent expansion to arbitrary versions or lock paths.

## Review focus

- A slice and a rejected address on one line: only the slice span is removed; the address remains reported (Task 1).
- Arithmetic/call/parenthesized slice steps and dotted mapped documentation hosts: keep recognized slices and sanctioned mapped endpoints clean while rejecting neighboring endpoints without partial hex extraction (Task 1).
- Multiple same-line lock tokens: sanctioned package metadata does not hide another endpoint (Task 2).
- No interpreter and binary content: use the existing constrained shell PATH and retain binary skipping for `.lock` content (Tasks 1–2).
- A grep-option filename alongside each rule's rejected shape: each rule still executes and emits its own diagnostic (Task 4).

## Task 1: Leading-compression IPv6 with slice controls

- [x] Add separate disposable fixture repos/cases producing tokens by joining `::` to synthetic `abcd`, colon-joined components `ffff`, `c0a8`, `0101`, and numeric `2`. Assert exit 1 and the IPv6 diagnostic for bare text, quoted text, and URI-bracketed/port forms. Require file/line diagnostics and no clean verdict.
- [x] Add passing fixtures for unspecified/loopback forms and indexed slices with numeric, signed numeric, spaced, identifier, call receiver, and nested index receiver contexts. Pin expression steps with `values[::2 + 1]`, `values[::f()]`, `values[::(2 + 1)]`, `values[::f(2 + 1)]`, and `values[::f((2 + 1))]`; include multiple slice spans on one line. Include ordinary three-field slices with explicit start/stop, existing documentation-prefix, expanded loopback, ordinary time/ratio, and sanctioned MAC controls. Unsanctioned full-group addresses are rejection controls, not passing fixtures.
- [x] Add every expression-step fixture plus a generated rejected address on the same line; require only the address's IPv6 diagnostic and correct line. Add two addresses on one line, a URI-bracketed address after a slice, and identifier-adjacent/overlong hex-group controls. Add bracket-like fragments that fail the receiver or step whitelist/balance rules; any contained eligible address must remain detectable. Do not mask text outside the recognized span.
- [x] Add named pure-hex rejected fixtures with exactly one terminal period at end-of-line and before spaces/tabs. Generate address and period separately; assert exit 1, the IPv6 rule, correct line, and the complete address token without the prose period. Repeat both punctuation contexts after a recognized arithmetic/call slice, then with a second neighboring rejected address; both addresses must remain reported. Add a valid mapped documentation token with terminal period, an unsanctioned mapped neighbor with terminal period, repeated terminal dots, an overlong hex group with terminal period, and malformed mapped candidates with internal/trailing dots. Require sanctioned mapped content to stay clean, its unsafe neighbor to remain rejected by IPv4, and every malformed/repeated-dot control to produce no shortened pure-hex IPv6 match. Preserve the previously specified bare/quoted/URI and identifier controls.
- [x] Add dotted mapped documentation fixtures matching the existing source cases at `test_deploy_configuration.py:443` and `:469`, constructed from the compression marker, mapped-group component, and split documentation octets. Assert exit 0 and no IPv6 partial-prefix diagnostic for bare, quoted, and URI-bracketed forms, including uppercase mapped-group spelling and the existing invalid-octet documentation control. Add neighboring unsanctioned dotted endpoints assembled from split octets; require IPv4 rejection and no partial-prefix IPv6 diagnostic. On one line, combine an expression slice, a sanctioned mapped endpoint, and an unsanctioned mapped endpoint; require the unsafe endpoint to survive both masks. Add overlong/malformed dotted token controls that prohibit shorter pure-hex extraction.
- [x] Parent runs `bash scripts/tests/test-check-no-site-data.sh` on the unchanged scanner. New rejection cases must fail for a false clean verdict; existing fixtures and passing controls must remain green. Record exact labels and statuses.
- [x] Implement only the selected context-aware path; update the incorrect IPv6 rationale. Keep raw line content internal and reuse the existing report message.
- [x] Parent reruns the harness. Expect exit 0, all assertions passing, and no newly rejected slice controls. Rerun the scanner with its constrained shell PATH fixture so the new path itself is exercised without an interpreter.

## Task 2: Scan textual locks and prove metadata sanctions

- [x] Add default-enumeration rejection cases using generated non-sanctioned endpoint/hostname tokens in `uv.lock`, `images.lock`, and an arbitrary `.lock`; assert rule and path. Add a package-lock case for policy consistency. Explicit path selection must have the same result.
- [x] Add a tracked binary `.lock` containing NUL and a generated otherwise-rejected token; expect clean. Add benign public registry metadata, lowercase generated 64-hex digests, and the exact observed package-version contexts; expect clean.
- [x] Add anti-exemption cases placing the observed version-shaped token in an endpoint field and as a URL host; expect IPv4 rejection. Place approved package metadata and another generated address on one line; require the other address's diagnostic. Put unsafe text in the same lock as benign metadata; require rejection.
- [x] Parent runs the harness against the prior scanner. `.lock` default rejection cases must fail because enumeration excludes them; explicit-selection cases characterize current behavior. The benign new version controls may currently fail in explicit selection and form the sanction's red proof.
- [x] Remove only the lock exclusions from default enumeration. Implement the reviewed, exact metadata sanction. Preserve content-based binary skipping, scanner self-exclusion, and case-sensitive uppercase OpenSSH detection.
- [x] Parent reruns the harness, then `bash scripts/check-no-site-data.sh apps/api/uv.lock apps/web/package-lock.json infra/images.lock packages/contracts/.contract.lock packages/contracts/package-lock.json scripts/tests/fixtures/npm-audit/package-lock.json scripts/tests/fixtures/uv-lock-provenance/uv.lock.pypdf-6.13.2 scripts/tests/fixtures/uv-lock-provenance/uv.lock.pypdf-6.14.2`. Expect exit 0; inspect any unexpected hit without recording sensitive values.

## Task 3: Legacy MD5 byte case

- [x] Generate lowercase and mixed-case 16-byte colon-hex values from split byte components, optionally prefixed with `MD5:`. Assert exit 1 and the certificate/key fingerprint diagnostic; existing uppercase 16-byte behavior is a characterization control.
- [x] Generate a 15-byte colon sequence and sanctioned six-byte MAC control; assert no certificate/key fingerprint diagnostic. For an unsanctioned six-byte MAC, require its own MAC diagnostic and no fingerprint diagnostic. Other scanner rules may reject byte sequences: assert the specific fingerprint message for length controls rather than assuming total exit 0.
- [x] Parent runs the harness against the prior fingerprint rule and records failure of lowercase/mixed-case fingerprint assertions for the intended reason.
- [x] Expand that rule to `[0-9A-Fa-f]` or use `-i` on that matcher alone; retain `{15,}`. Do not alter uppercase OpenSSH `SHA256:` case behavior.
- [x] Parent reruns the harness and requires all fingerprint assertions green plus clean lowercase OCI/dependency digest fixtures.

## Task 4: Terminate options for every matcher

- [x] Add the Windows-valid root fixture filename `--exclude=probe.txt`, created and staged with quoted paths and `git add --`. Track a real ordinary `probe.txt` alongside it. First put benign content in both and require clean for default enumeration and explicit selection of each file.
- [x] Table-drive synthetic rejection cases for all 12 file-consuming rules: Linux home, Windows profile, workstation prose, email, deployment prose, repository-owner identity, IPv4, IPv6, `.local`, MAC, colon-hex fingerprint, and uppercase OpenSSH fingerprint. Keep `--exclude=probe.txt` tracked and benign while each case's rejected payload is placed in the actual tracked ordinary file `probe.txt`, so the unchanged matcher interprets the option filename and suppresses exactly that payload. Choose payload shapes detectable by each existing rule without this option filename; the owner case configures only a synthetic remote and retains redaction assertions.
- [x] Separately put a rejected shape inside `--exclude=probe.txt` itself, restore `probe.txt` to benign content, and invoke the leading-dash filename through the harness's optional path argument. Require exit 1 and that exact filename; also require rejection under default enumeration. This tests payload scanning inside the option-like filename separately from suppression of `probe.txt`.
- [x] Parent runs the harness against prior option handling; every previously unterminated matcher case must fail for a missing rejection. The owner matcher should already pass and is a regression control.
- [x] Add `--` between pattern and `"${FILES[@]}"` on every file-consuming grep and preserve the owner's existing separator. New helper file/stream matchers also use `--`; stdin-only filters receive it for consistency where compatible. Do not repair unrelated file-enumeration/newline behavior in this issue.
- [x] Parent reruns the harness and requires all cases green. Inspect every `FILES` expansion and every new grep invocation; no file argument may enter an unterminated option list.

## Integrated validation and handoff (parent execution)

- [x] Run `bash -n scripts/check-no-site-data.sh scripts/tests/test-check-no-site-data.sh` and `bash scripts/tests/test-check-no-site-data.sh`; expect exit 0 and zero failed assertions. Inspect the constrained-PATH case to confirm it exercises the added code.
- [ ] Run ShellCheck if present using the repository's shellcheck configuration; do not install tools from this worker. Record unavailable tools accurately.
- [x] Run `git diff --check` and review the exact two-file diff for scope, sensitive literals, broad exclusions, and option termination.
- [x] Run `bash scripts/check-no-site-data.sh` on the current tracked tree. Expect exit 0 and the normal clean verdict. Separately run the explicit lock selection above, because default self-exclusion must not accidentally imply lock exclusion.
- [x] Parent stages synthetic probe files in an isolated candidate checkout for each gap, runs the real gate expecting exit 1 plus the relevant diagnostic, and removes probes from that checkout. Do not stage probes in the user's shared checkout. Use only split/generated synthetic content and portable filenames.
- [x] Parent repeats harness and full-tree scan on the final integrated candidate, including all current lock changes. Preserve any new hit as a finding for evidence-backed review; do not change locks or weaken rules merely to force a clean result.
- [x] Parent records exact commands/statuses and Git Bash evidence. A GNU Bash shell-only constrained PATH run supports the dependency contract but is not a native Git Bash run; report native Git Bash as unverified if unavailable. The harness's pre-existing symlink-based constrained PATH setup may need the parent's compatible Git Bash environment.
- [ ] Parent closes the residual only when all four red/green proof chains and the final full-tree scan are available, then performs the repository's reviewed-PR handoff. Application lint/build/test suites are unnecessary for this two-shell-file change unless parent integration changes their scope.

## Execution phase

Independent plan review approved specification and engineering quality at reviewed plan SHA256 `9ad3eab53ddec285e0f04d8aebd312a940b281391fe5fc32d505aea5560694a4`. This tracked copy substitutes permanent issue/authority links for ignored planning inputs and records the implementation phase.

Parent verified the unchanged scanner against the new harness: syntax/diff checks passed, 275 assertions passed, and 316 assertions failed for the intended missing behavior. Parent inspected failures covering leading compression and punctuation, skipped locks, public-version false positives, lowercase/mixed-case fingerprint diagnostics, and option suppression; existing slice/mapped/binary/boundary and owner-option controls passed. The original 23-assertion harness and full-tree baseline were clean. This is the failing proof that authorized production implementation.

The scanner implementation passed all 591 unchanged harness assertions, explicit lock scans and the
full tracked-tree scan. Independent implementation review approved both axes. The combined candidate
passed the harness and complete scan again, plus four staged real-gate rejection probes followed by
a clean restored tree. Dated [closure evidence](../../slice-history.md#site-data-guard-covers-all-four-deferred-shapes-2026-10-10)
records commands and limits. Final independent review and PR handoff remain pending. Native Git Bash and ShellCheck are unavailable in the current environment. Constrained GNU shell PATH fixtures prove the dependency boundary separately and do not establish native Git Bash compatibility. Parent owns execution, review, integration, shared closure documents, commits, and PRs; this worker claims no new passing command result.


## Receiver-context amendment (2026-10-11)

A later PR review reproduced ordinary prose being classified as an indexed slice, including call-shaped and indexed prose receivers. This amendment narrows the receiver-discovery rule above; the original plan and its execution evidence remain historical. Optional spaces/tabs and the original step alphabet/balance limit remain supported.

- A fresh identifier/dotted identifier, bounded call or bounded parenthesized receiver needs an original-line start or an explicit source expression delimiter. An arbitrary whitespace boundary between prose words is insufficient. Supported delimiters are assignment, container/call punctuation, comma, semicolon, arithmetic/bitwise/comparison punctuation, and opening quote/backtick cues used by source examples.
- Every receiver kind uses the same framing rule. A closing call/index character alone grants no allowance. Chained indexing carries the qualification of its original receiver, including across spaces/tabs; starting a new emitted chunk never invents a line-start cue.
- A nonnested ordinary index is emitted unchanged with its provenance. Nested unsupported outer content remains available for inner inspection, and every unmasked address remains available for token extraction.
- The existing 17 slice positives and all 952 earlier assertions remain required. New prose/call/index/two-token controls assert each complete address diagnostic, while framed source controls preserve valid slicing.
- Two sparse progress fixtures gain explicit semicolon framing without reducing their long padding, Unicode content, token inventory, diagnostic assertions or five-second limits. The small control uses byte-equivalent string assembly so the raw fixture source also has a recognized source cue.

This is a bounded lexical recognizer, not a Python parser. Prose deliberately spelling an accepted source expression remains ambiguous; unsupported receiver spellings may require review. The package-context qualifier and other scanner rules are unchanged. Fresh expanded harness, all eight bounded progress cases, eight-lock and full-tree checks must verify the changed helper. The earlier 4.45-fold comparison remains historical for its exact source pair; no current speedup is inferred from it.

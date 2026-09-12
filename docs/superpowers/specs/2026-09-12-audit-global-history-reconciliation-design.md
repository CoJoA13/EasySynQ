# Bounded global reconciliation of collected audit history

September 12, 2026. **Owner-approved design, implemented under R85.** This contract adds
inactive observed-history reconciliation to the R84 traversal foundation. The protected
starting baseline is MR !45's merge `b2885e1300ca1fb69a4ab0917301f344c9ed0e62`.
Read this with [R85](../../decisions-register.md#r85--collected-audit-history-requires-global-closure-before-bounded-output--2026-09-12),
[R84's design](2026-09-11-audit-required-witness-collection-design.md), and the unchanged
R77/R78 supplied-input evaluators. [Slice history](../../slice-history.md#s-audit-global-history-reconciliation--bounded-global-closure)
records measured acceptance; [current status](../../current-status.md) records integration state.
Operational activation, deployment, restore and deferred trust/recovery policy changes remain
outside this increment. All source paths below are repository-relative.

## Outcome and scope

Add one inactive Python entry point that binds the complete external witness inventory, collects
the original evidence using R84 traversal, reconciles the supplied bootstrap package and all
collected checkpoint bodies globally, and publishes a bounded result only after owned cleanup.
The new evaluator must work beyond 4,096 observations and eight bridge pages. It never obtains
history by calling the finished diagnostic collector or by reopening its discarded database.

The first supported case has a positive externally pinned legacy boundary and a nonempty v2
graph. A valid bridge with no observed v2 graph remains incomplete (`EMPTY_GRAPH`); this selects
no empty-history, reset or legacy-only success policy. Every body, marker, unavailable read and
traversal gap must affect accounting or a diagnostic; none is filtered by filename, timestamp,
manifest membership or current-version status.

Existing R77/R78 APIs, limits, result meanings and assurance fields remain unchanged. Existing
R84 diagnostic callers retain a storage-only worker and their current report/cleanup contract.
The new entry point is not wired into CLI, API, scheduled verification, issuance, recovery or
enrollment. R79's 64 KiB exact-version reader cannot fetch oversized bridge roots/pages;
those continue to arrive as separately supplied original bytes.

## Alternatives and recommendation

| Approach | Assessment |
| --- | --- |
| One fresh worker owns collection storage and then fixed reconciliation operations | Recommended. All evidence and global indexes remain under one lifetime, one SQLite file and the existing process containment model. The new mode adds pure parsing/cryptography after traversal; it receives no credentials. |
| Keep storage-only worker and add a separate persistent cryptographic worker | Possible, but requires another owned process, a second bounded protocol and a body/result routing contract. This adds lifecycle and fault-composition work without improving the initial acceptance boundary. |
| Reconstruct tuples, enlarge old evaluator constants, or reconcile independent windows | Rejected. Tuple reconstruction moves history-sized state into the parent; independent windows miss cross-window conflicts and material/edge dependencies. Neither uses the existing spool to establish global closure. |

The approved worker mode is new behavior. R84's existing storage-only claim
continues to describe its diagnostic entry point. Do not retroactively describe the old spool as
a shipped reconciliation engine.

## Public seam and admission

Public entry point in `history_reconciliation.py`:

```python
def collect_and_reconcile_checkpoint_history(
    enrollment: BridgeEnrollment,
    root_body: bytes | None,
    pages: tuple[BridgePageObservation, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryReconciliationLimits,
    *, cancel: threading.Event | None = None,
) -> HistoryReconciliationReport: ...
```

`BridgeEnrollment.stream` is the sole stream/bootstrap/required-checkpoint authority;
`BridgeEnrollment.witnesses` is the sole required witness inventory. Reuse their current
exact-type, identity, key-admission and positive-boundary rules. Validate all readers and
recompute their R78 namespace commitments before any worker, directory, watchdog or network I/O.
Root contents, provider responses and the checked database cannot change enrollment.

Require exact immutable records, tuples and byte strings, built-in integers excluding booleans,
and the current exact cancellation Event type. Count over-limit page tuples without inspecting
their elements; otherwise check every member's type and cheap length before aggregate disposition.
No generator, file handle, path, caller SQL, callback, preverified body/result, mutable cache,
supplied SQLite file or continuation token is accepted. Do not duplicate whole caller byte tuples.

Malformed supplied package bytes are evidence failures, not argument-shape errors. An individual
wire-size violation within an otherwise admitted aggregate follows R78's root/page-invalid
semantics. No malformed record may trigger network fallback. A missing root remains a diagnostic
dependency; it cannot make keys, page membership or an empty history authoritative.

## Owned lifetime and protocol

The parent owns the watchdog, one fresh private storage/reconciliation worker, and at most one
unchanged one-shot R80/R83 network worker at a time. The worker owns its single new SQLite
connection from initialization through reconciliation. It never spawns network children.

Lifecycle, in order:

1. Admit the entire caller request and compute one absolute whole-attempt deadline.
2. Start the owner and fresh worker; initialize fixed schema, public enrollment and limits.
   Upload supplied root/page deliveries through bounded metadata and binary frames.
3. Traverse every required namespace using the current R84 original-page/body operations.
   Preserve each delivery, pending/resolved outcome, opaque cursor, marker and controlled gap.
4. Seal collection inside the worker. Verify all expected witness states and resolved outcomes.
   Controlled gaps permit diagnostic reconciliation; pending operations, unexplained counts or
   a dead/poisoned worker do not. Sealing permanently forbids further observation/package writes.
5. Run bounded reconciliation steps until all phase cursors and dependency queues are exhausted.
   Progress acknowledgments carry only fixed counters and phase state, never usable partial results.
6. Produce one bounded final result, require exact EOF and zero exit, reap and close all owned
   resources, remove the private directory, stop/join the watchdog, then perform final deadline
   and cancellation checks. Only the outer entry point may return the result.

R84 originally combined traversal with `spool.finish()`, whose terminal operation consumes EOF,
reaps and removes the spool. The implementation extracts the private traversal loop from that
terminal step. The diagnostic wrapper still finishes immediately; the new wrapper seals,
reconciles and finishes. There is no public callback or returned live session object.

Use a separately versioned private protocol for the new mode, selected before the worker starts.
Preserve the old mode's accepted operations. Fixed new operations cover package upload, collection
seal, deterministic next reconciliation step, and finalization. No caller-selected phase order,
SQL, cursor position or trust-bearing result is accepted. The worker owns step cursors; every
reply binds the sole outstanding sequence ID, phase and monotone work counters.

Retain the 131,072-byte frame ceiling, 65,536-byte binary chunks, exact lengths and strict metadata
admission. A step processes at most 64 body/node/event work items or one bridge page of at most
512 entries. Large fan-outs are resumed by worker-owned keyset cursors without dropping waiting
items. Each command, including an entire upload or step, retains one 10-second deadline.
Progress never renews the whole-attempt deadline or cumulative worker CPU allowance.

All computation remains synchronous within the owned worker's bound. There is no public iterator,
lazy result or output sink that can keep a worker alive after return. Any protocol, SQL, process
or resource fault poisons the attempt. Cancellation and fatal exceptions retain R84's cleanup
and exception-group behavior; no ordinary unavailable-read record may hide a cleanup failure.

## Global indexes and bounded state

Create all tables/indexes before locking down the schema authorizer. Preserve original collection
tables and rows. Derived tables refer to those immutable row identities; they are not replacement
evidence. All SQL is fixed with bound parameters. Fetch bounded batches using indexed keyset
queries, never unbounded `fetchall`, large `OFFSET` loops, per-node whole-table scans or recursive walks.

| Index/state | Key and purpose |
| --- | --- |
| Package deliveries and validated pages | Delivery position, canonical page hash, page index; retain raw deliveries, canonical duplicates, extra pages and competing pages. |
| Committed entries | Page/entry position plus a nonunique BLOB locator index; preserve duplicates until the prescribed manifest validation stage diagnoses them. |
| Raw-body identities | Raw SHA256 and length as lookup accelerators, followed by exact byte comparison; link every original delivery to a distinct raw identity. |
| Locator/body deliveries | Witness UUID, UTF8 key, UTF8 version, raw identity; detect byte conflicts across all formats and preserve repeated deliveries. |
| Legacy authentication and heads | Raw identity; signed audit ID/hash; per-witness exact committed membership and endpoints. |
| V2 pending material | Declared key ID and raw identity; authenticate only when that exact material is established. |
| Authenticated nodes and pending edges | Envelope hash, anchor UUID, predecessor hash; track one edge assessment per canonical authenticated node. |
| Established material and event queues | Key ID/material and admitted predecessor events, each with a consumed state; never discover material from routing hints. |
| Admitted children and path | Predecessor/envelope hash, numeric sequence, epoch; global forks, required-pin relation, unique path and used epochs. |
| Witness delivery coverage | Witness UUID plus authenticated envelope hash; compare each required witness with the complete admitted path. |
| Issues | Component, severity, code, stable subject and at most two stable references to distinct conflicting values where required. |

Locator identity uses exact BLOB values, not normalization or locale collation. Numeric sequences,
heads and epochs use numeric ordering. Diagnostic sort keys reproduce the reference evaluators'
ordering, including padded numeric subjects and their representative-selection rules; do not
reuse a convenient SQL collation where it changes those results. Digests identify commitments
as specified by R76/R78, but raw duplicate/conflict decisions still compare exact bytes.

The database holds all history-sized maps, queues, groups and path rows. Python holds one page
or a bounded work batch and a bounded cache; no in-memory map grows with total history or number
of discovered keys. A key lookup may be repeated for bounded cache misses, but each distinct raw
v2 body is fully verified at most once and each node's edge assessed at most once. Each legacy
raw body gets at most eight retained-key trials. Each delivery remains independently budgeted.

## Bootstrap reconciliation

Preserve R78's stage order and diagnostic independence:

1. Validate root structure, commitment and external bindings in that order, stopping at the first
   root category. Only a bound root defines page inventory and legacy-key completeness.
2. Decode every admitted supplied page delivery independently. Preserve canonical duplicates,
   identity failures, extras, competing indexes and missing required hashes.
3. Establish the committed map only after required pages are present and partition, duplicate
   locator, global locator order and aggregate counts pass, in that order. Count all duplicate
   locator groups when that category applies. Extra pages may fail beside an otherwise complete map.
4. Authenticate every scoped legacy raw body with unchanged historical normalization and retained
   key admission. Match raw commitment and length at each exact committed locator. Present wrong
   bytes are a commitment failure, not an additional missing-body diagnosis.
5. Detect authentic unlisted legacy bodies only with a complete committed map, including older,
   equal-boundary and ahead records. Check all authenticated legacy head conflicts and the
   positive external boundary. Recompute each witness's endpoints without inventing a smaller
   summary when inputs are ambiguous or incomplete.

Every original R78 issue code, grouping, precedence and duplicate-count rule remains the legacy
kernel's contract. Root/page/body bytes and signatures are never reconstructed from JSON results.
Copies at another witness or version cannot satisfy an exact manifest locator.

## V2 material, edge and graph reconciliation

Preserve R77's event-driven closure rather than traversing a selected branch:

1. Strict route inspection gives only unverified identity and dependency hints. Structural
   identity mismatch and malformed envelopes retain their existing diagnoses.
2. Start available material with the externally admitted initial key. When a key becomes
   established, verify each waiting raw body with that exact declared key and unchanged R76.
   An unavailable key is not an invalid-signature finding.
3. When a node's predecessor is the pinned bootstrap or an admitted node, assess permitted
   key/epoch, exact next sequence, then nondecreasing audit head/equal-ID hash agreement.
   Report only its first edge fault. Only admitted transition edges introduce next material.
4. Continue every independently admissible fork branch and all material/parent events. Detached
   or rejected transitions grant nothing. Rediscovery of earlier material at a later authorized
   epoch is allowed. Timestamps order neither graph nor key authority.
5. After both event queues close, diagnose remaining unknown keys and disconnected nodes,
   global anchor-ID conflicts and forks grouped by admitted predecessor. Compute required-pin
   relation with contradictions before matches, just as R77 does.

Only a zero-issue graph has a unique ordered path and used-key history. A terminal transition's
unused next material is not a used epoch. The private graph kernel may diagnose an included
required pin while another component fails; that diagnostic never authorizes a usable result.

## Composition rules

These rules make the new mixed-history contract explicit. They are additions at the composition
boundary, not silent changes to the old supplied-input APIs.

**Format accounting.** Recognize the exact unversioned legacy envelope shape or a versioned
checkpoint candidate from decoded member names under bounded, duplicate-rejecting admission.
Presence of a version discriminator reserves the versioned route even if its value is invalid;
candidate v2 parse/signature faults retain the lineage kernel's `ENVELOPE_INVALID` diagnosis.
An exact legacy shape enters the legacy kernel even if its field values or signature are invalid.
Never retry a failed versioned parse/signature as legacy. Preserve the legacy decoder's numeric,
timestamp, hash and Base64 compatibility. Bodies with unparseable, unknown or ambiguous routing
shape receive a fixed failed `CHECKPOINT_BODY_INVALID` group by raw digest. All categories remain
in locator-conflict accounting. Shape inspection confers no authentication or membership authority.
Valid bridge objects found inside the checkpoint namespace are not silently removed as metadata;
this slice defines no naming exception or discovery policy for them.

**Manifest membership precedes format exclusion.** When the committed map exists, compare every
present body at a committed locator with its exact committed digest/length before applying a
format partition. A versioned or malformed body there cannot masquerade as an absent legacy body,
or escape the required legacy authentication obligation. If it is structurally v2, also evaluate
it as v2; manifest membership cannot suppress graph evidence. An authentic uncommitted legacy
record still fails R78; a valid v2 record outside the legacy map is normal v2 evidence.

**Transport faults remain global.** Every R84 failed/incomplete group blocks composite success
regardless of a format hint or a readable duplicate. Markers and unavailable/ineligible versions
whose format is unknown remain collection issues. Translate list failures/cursor cycles to the
legacy kernel's gap reasons where needed for its diagnostics, but retain original fixed transport
codes internally. Do not assign an unreadable body to a format based on its filename.

**Require the entire v2 path at each required witness.** Once the graph closes, each external
witness must have an authenticated delivery of every admitted canonical envelope on the path.
Perform this coverage comparison only when the graph kernel establishes a unique consistent
path. A failed/incomplete graph has no selected path and cannot generate a smaller coverage claim.
A healthy union is insufficient. Copies may use different exact locators and benign canonical
transport variants across witnesses. Add incomplete `V2_WITNESS_COVERAGE_MISSING`, grouped by
witness UUID and missing envelope hash. A successful copy never erases another unavailable
observation or byte conflict. This is an explicit coverage rule; R77 alone does not
define a required witness inventory.

**Compare signed heads across formats.** Group differing authenticated row hashes at the same
numeric audit ID across legacy and all authenticated v2 nodes, including detached/rejected nodes.
Add failed `GLOBAL_SIGNED_HEAD_CONFLICT`; avoid duplicating the existing legacy-only group when
all conflicting participants are legacy. This compares signed assertions, not database rows.
Same-hash equal-ID transitions remain valid. The bridge boundary remains the predecessor head
assertion for the first v2 edge. This adds no legacy predecessor chain or legacy key-era inference.

## Limits and failure disposition

The new flat immutable `HistoryReconciliationLimits` requires explicit values. R84's public
limits retain their existing meanings; the new entry point has its own aggregate accounting.

| Field/resource | Inclusive bound |
| --- | --- |
| `maximum_pages` | 1–4,096 total attempted provider LIST requests |
| `maximum_observations` | 1–100,000 original version/marker deliveries, duplicates included |
| `maximum_bridge_pages` | 1–1,024 supplied page deliveries, duplicates/extras included |
| `maximum_manifest_entries` | 1–100,000 committed entries in an externally bound root |
| `maximum_total_bytes` | 1–1,073,741,824 original XML, successful checkpoint bodies and supplied package bytes combined, including duplicates |
| `maximum_spool_bytes` | 65,536–1,073,741,824 logical database bytes, divisible by 4,096; includes all derived indexes |
| `maximum_wall_seconds` | 1–86,400 for upload, collection, reconciliation, finalization and cleanup together |
| `maximum_issue_groups` | 1–1,000,000 complete internal groups across components |
| `maximum_issues` | 1–32 displayed groups across components |
| Individual wire objects | Unchanged 256 KiB root, 2 MiB bridge page, 64 KiB checkpoint body, 16 MiB/1,000-entry original provider page |
| Derived cardinalities | At most 100,000 distinct checkpoint bodies/nodes/used epochs, 100,001 public materials, 400,000 witness/path membership obligations; at most 524,288 parsed supplied-page entries before committed-map selection |
| Worker resources | Retain 512 MiB address space, 120 cumulative CPU seconds, 32 FDs, zero core bytes and file ceiling lowered to admitted spool limit |
| SQLite | Retain 4 KiB pages, MEMORY journal/temp, 64 MiB hard heap, 1 MiB cache, no mmap, extensions, attachments, caller SQL, schema changes or disk spill |
| Protocol | 128 KiB frame, 64 KiB chunk, 10-second command; at most 64 work items or one bridge page per step |
| Final result | At most 65,536 encoded bytes in one bounded result frame |

The current bridge wire format still allows a root declaring 524,288 entries; this first
reconciliation capacity is smaller. Check a root's committed count only after structure,
commitment and external binding succeed. An unauthenticated large count cannot hide root failure.
An authenticated count above the admitted ceiling aborts with resource-incomplete evidence.
No constant in the old R77/R78 evaluators is increased. The new entry point admits all supplied
package bytes against its aggregate before I/O; subsequent XML/body admission checks the remaining
same budget. Package, collection and derived-row counters stay separate even though they share
the database and overall byte ceiling.

These are rejection ceilings, not promises that every combination fits or completes within
120 CPU seconds. Metadata, original bytes, derived indexes, SQLite memory and total runtime
compete for their separate limits. A capacity failure is acceptable only as incomplete evidence
with no usable prefix. More capable hardware does not authorize increasing a fixed limit.

Invalid argument shape raises fixed `HistoryReconciliationInputError`. Aggregate preflight,
internal group, file, heap or other resource exhaustion raises fixed
`HistoryReconciliationError("RESOURCE_LIMIT")` with no report. Deadline, startup, protocol,
storage, worker and cleanup codes mirror R84's fixed vocabulary; cancellation remains a
BaseException subclass. Underlying fatal identities survive cleanup. Do not promise that a
poisoned attempt can return all failures observed before it was aborted.

Completed evidence failures produce a `failed` report; completed dependency gaps alone produce
`incomplete`. Failures dominate incomplete groups. Neither state exposes a bootstrap pin, tip,
path, key history, witness boundary summary or established-check list. A fatal/incomplete run
cannot be interpreted as an empty witness history by a later consumer.

## Bounded report and assurance

`HistoryReconciliationReport` is frozen and slotted. Its scope is exactly
`collected-required-witness-history`. It contains status (`consistent`/`failed`/`incomplete`),
bounded per-witness traversal counters, aggregate input/classification/duplicate counts,
component-qualified issues, complete failed/incomplete group counts, `issues_omitted`, the
required-checkpoint relation, established/unproved check tuples, and optional usable summary.

Issue identities retain separate `collection`, `bridge`, `lineage` and `composition` components.
Order failed before incomplete, then component/code and the domain's stable subject. Collection
groups keep R84's counts; bridge and lineage groups keep their own grouping rules. Counts refer
to component groups and are not presented as counts of independent physical faults. A displayed
issue has at most two tagged numeric references (observation ordinal or supplied page position)
and optional enrolled witness UUID; no raw subject, locator, signature, key or provider text.

Only global consistency exposes the original external bootstrap pin, a compact tip
(hash, sequence, signed audit head, key ID/epoch), path/used-epoch counts, and at most four
R78 witness boundary summaries. The result contains no full path, public-material history or
raw envelope list. The internal path is complete before this bounded projection is made; no
path prefix or omitted-tail flag can qualify as success. If future DB comparison needs the
whole path, it must receive a separately reviewed internal phase within a fresh owned lifetime.
This result is not a retained history store, rollback-memory token or future resume handle.

On consistency, established checks are exactly:

- `required-witness-provider-traversal`
- `external-root-content-binding`
- `committed-page-and-locator-closure`
- `retained-legacy-signature-authentication`
- `per-witness-signed-boundary-binding`
- `collected-observation-reconciliation`
- `v2-material-and-edge-consistency`
- `per-witness-v2-path-coverage`
- `cross-format-signed-head-consistency`

Every result retains these unproved checks, in this order: `provider-non-omission`,
`atomic-snapshot`, `historical-deletion-absence`, `witness-custody`, `database-chain-agreement`,
`freshness`, `rollback-memory-continuity`, `durable-delivery`, `operational-key-activation`,
`source-independent-recovery`. A terminal list page and complete observed witness coverage
do not establish any of them. An authentic old prefix can still be consistent without a
separately retained newer pin. No general verified/recovery-ready/activation Boolean is added.

## Distinguishing acceptance

The following are required evidence obligations. Dated measurements and their scope belong in
[slice history](../../slice-history.md#s-audit-global-history-reconciliation--bounded-global-closure),
not in the behavioral limits below.

| Risk/requirement | Required evidence and wrong implementation it must reject |
| --- | --- |
| R77/R78 semantic equivalence | Run every current independent fixture and relevant negative/property case through the real new worker's respective private kernel and the unchanged pure evaluator within their shared capacities. Compare full normalized status, issue groups/references, precedence, duplicates, required-pin relation and usable path/epoch or bridge results. Use independently authored expectations as well; shared cryptographic helpers alone are not an independent oracle. |
| Test projection is honest | Private fixture setup may seed typed domain observations in a fresh owned test spool; no test seed/format assertion/preverified result is reachable from the packaged public protocol. Kernel equivalence does not claim mixed-format or network acceptance. Separate public-entry tests exercise classification and joins. |
| More than old capacity | A consistent fixture with at least 5,002 total collected checkpoint deliveries, at least 4,097 distinct v2 nodes, and more than eight complete committed legacy pages. Separate focused fixtures may isolate these boundaries; copies for every required witness count toward the real collection ceiling. No deduped/raw count substitution. |
| Late contradictions | Move fork, anchor-ID conflict, raw locator variant, legacy signed-head contradiction, cross-format conflict, manifest order/duplicate fault and missing witness copy past 4,096 and across bridge/LIST page seams. A windowed implementation and success-before-final-closure must fail these controls. |
| Material versus edge permission | Out-of-order transitions, children preceding material, detached/rejected transitions advertising needed keys, fork branches, late unknown keys and legal return to earlier material at a later epoch. Measure one full authentication per distinct raw and one edge assessment per authenticated node. |
| Whole namespace and format accounting | Unknown/ambiguous bodies, invalid version discriminators, duplicate decoded fields, invalid v2 signatures, wrong-format bytes at a committed legacy locator, authentic unlisted legacy and old/equal/ahead records. Filtering by manifest, naming, timestamp or fallback-to-legacy must change a required verdict. |
| Every witness remains required | Healthy A beside empty, omitted-copy, denied, cyclic, marked or unreadable B; all must block composite success. Include a readable duplicate beside an unavailable delivery. Verify semantic canonical duplicates across witnesses without permitting same-locator byte conflicts. |
| Global closure and bounded projection | Late failure after an internally matched required pin; no pin/tip/path/epochs escapes. Consistent large graph yields the independently expected compact tip and exact counts after complete internal path comparison. A deliberately capped internal scan must fail. |
| Indexed scaling | Real SQLite and crypto across increasing finite sizes; inspect query plans, bounded fetches, work counts and peak process memory. Force long fan-outs and reverse-order dependency chains. Reject a full-history Python map, repeated all-key trials or rescanning an entire graph per event. Record measurements without extrapolating a maximum-size throughput guarantee. |
| All limits and poisoned state | Lowered/exact/overflow budgets, 32 displayed groups with more internal groups, internal-group overflow, database full, heap/CPU/FD limits, command/whole deadline and cancellation in each phase. Force actual worker death after seal, during graph work and after provisional final bytes; no report/reopen/later I/O. |
| Original bytes and SQL containment | Actual 16 MiB page, 64 KiB body, 2 MiB bridge page, schema/authorizer and temporary-storage checks. Disk-journal/sort mutants, open-unlinked FDs, malicious IPC, stale/replayed/trailing replies and output after finish must be caught. |
| Genuine transport composition | A new bounded two-witness fixture in the actual immutable API image uses R80/R83 over verified TLS and independently seeded exact versions/bytes. Bind original LIST/GET receipts to the reconciled identities. Synthetic transport scaling is a separate proof and is labeled accordingly. |
| Owned lifecycle and packaged runtime | Verify exact source/build/proof identities, installed application, non-root/read-only-root/no-dev-dependency execution, actual OS limits, no credentials in spool/argv/environment/report, and absence of every owned file/process/pipe/watchdog before return. |
| Compatibility | Existing R77/R78/R84 public behavior and limits remain unchanged. New private traversal extraction gets focused behavioral parity evidence. No new active caller, format, grant, migration, writer or recovery decision. |

Legacy-kernel equivalence preserves all R78 vocabulary and stage stopping rules, including root
failure masking, incomplete-manifest suppression, raw-versus-normalized identity and separate
marker/unavailable/gap groups. Lineage-kernel equivalence includes admissible forks, canonical
duplicates, terminal next-material exclusion, required-pin conflict precedence and deterministic
representatives under input permutation. Map reference indexes to stable test ordinals explicitly.
Outside shared capacities, the old evaluators correctly return resource-incomplete; compare
independently constructed large-case expectations, never treat those reference limits as success.

The complete new runtime case has a 300-second allowance including fixtures and teardown.
Preserve existing R84 collector/history/harness limits of 450/780/1,200 seconds and every
existing mandatory case. Complete acceptance must not remove old coverage, shrink required
counts or raise those caps. Increasing finite measurements establish bounded behavior at the
measured sizes, not a maximum-bound throughput guarantee.

## Implementation boundary

`history_reconciliation.py` owns public admission and publication. Separate private modules own
storage/indexes, issue groups, bootstrap stages, lineage material/edge work, global composition,
protocol/session/worker lifetime, and exact final-report admission. `history_collection.py` shares
only the extracted private traversal loop. Pure reference evaluators remain independent; unchanged
codecs and legacy authentication are reused. Test-only typed kernel projections are absent from
the packaged public protocol. The mandatory runtime case checks installed application source and
separately labels genuine TLS transport and synthetic scale.

## Obligations deliberately still open

Protected public enrollment remains under the owner's control on a separate verifier machine;
no mandatory offline-signing-key policy is added. GitLab remains the project host. Node 26 and
PolyForm Shield 1.0.0 remain selected; this source-available project is not relabeled open source.

Actual consistent DB-chain comparison, protected rollback memory/custody, provider non-omission,
durable issuance/delivery, operational activation, key rotation and pre-rotation restore,
complete independent encrypted recovery generations and source-denied recovered-stack boot/content
reads remain separate reviewed obligations. Production recovery/upgrade/cutover remains blocked.
Neither this draft nor a future consistent observed-history result closes issue #3 by itself.

Keep `RES-AUDIT-RUNTIME-ACCEPTANCE-FAILURE` unattributed until its closure evidence exists;
keep `RES-TESTCONTAINERS-IMPORT-DEPRECATIONS`, `RES-MINIO-VERSION-LIST-DENY`, checkpoint lineage,
key rotation and source-independent recovery under their existing closure contracts. The
85 API / 53 web no-fix findings recorded in the handoff remain open, not newly rescanned here.
Deferred dependency MRs !43, !8 and !7 are outside this task.

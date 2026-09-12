# Required-witness traversal with a fresh bounded spool

September 11, 2026. Exact technical refinement of the owner-approved parent Task 6
and the coordination-only parent design `2026-09-09-audit-history-collection-design.md`.
That parent document is not copied into this repository by this slice. The owner authorized starting
this next collector/spool step after R83 merged. No new trust-policy choice or
operational activation is requested or inferred. Implementation is not yet shipped.

## Outcome and boundary

An inactive Python API binds every externally required witness to its exact enrolled
namespace before any I/O, traverses each observed namespace through unchanged R83,
reads every R79-eligible listed version through unchanged R80, and retains every
admitted page, delivery, marker, unavailable read and traversal gap in one fresh
private SQLite spool. After supervised worker shutdown and spool removal, it returns
bounded traversal diagnostics. It never returns a verified history, usable path,
bootstrap/checkpoint pin, key permission, activation decision or database agreement.

The raw spool is an internal seam for the later global reconciler. No public spool
path, SQL connection, callback, resume token, supplied database, exported body list or
reopen/resume API is added. The diagnostic entry point discards the spool on exit;
the later reconciler must operate within this ownership lifetime before publishing
its own post-cleanup result. It must not reconstruct history from diagnostic counts.

Existing R73 enrollment files, R75 reports, R76–R83 APIs, operational jobs, CLI/API
routes, migrations, writer naming, bridge formats and 64 KiB raw-reader limit remain
unchanged. The collector does not choose legacy/v2 membership or validate signatures.
All readable namespace bodies are retained opaquely, so malformed/unknown content
cannot disappear through classification or a legacy fallback. Later reconciliation
must classify and account for them all. Bridge roots/pages still arrive via the
separate bounded supplied-package contract; this API does not discover them.

## Design choice and evidence

1. **Chosen:** one storage-only worker owned directly by the collector parent,
   alongside serial existing one-shot R80/R83 workers. The storage process owns
   SQLite memory/CPU/FD limits and one database. It receives no credentials and
   performs no network operations. The parent owns the watchdog and every worker
   lifetime without nesting network children beneath a killable collector child.
2. Caller-process SQLite was rejected: its page limit does not contain SQLite heap,
   CPU or a blocked storage operation independently of the caller.
3. A whole-collector child was rejected for this slice: the existing transports
   create separate process groups; killing an outer collector would complicate
   network-child ownership and cleanup. Changing those transports is unnecessary.

The private 15-case storage spike ran in unchanged API image
`sha256:076581f877c3256909ad28e7294582e00a68a8cc8f9475e85b59ef1abef0b1a8`,
Python 3.12.14 / SQLite 3.40.1, UID 10001, no development dependencies and no network.
It retained 5,004 rows, sorted 18,000 rows, distinguished opaque cursor pairs, exercised
transaction/statement rollback and resource failures, and killed/reaped an open
transaction before removing its workspace. Disk-journal and disk-sort controls
revealed both ordinary journals and already-unlinked temporary file descriptors.
Largest observed RSS was 44,296 KiB. All 2,043 source hashes and 155 directory modes,
raw output hashes and owned cleanup were independently checked.

Private coordination evidence identifier:
`2026-09-09-audit-history-collection/storage-feasibility-1-inspection.json`.
This historical artifact is not a checked-in repository acceptance path.
This is design feasibility, not acceptance of the implementation below. Sampled file
sizes are not an aggregate physical-storage quota. [SQLite journal semantics](https://www.sqlite.org/pragma.html#pragma_journal_mode)
and [temporary-file behavior](https://www.sqlite.org/tempfiles.html) motivate explicit
MEMORY journal/temp policy and complete abandonment after failure.

## Public types and admission

Add [history_collection.py](../../../apps/api/src/easysynq_api/services/audit/history_collection.py)
with frozen, slotted types and a fixed-text input error:

```python
@dataclass(frozen=True, slots=True)
class RequiredHistoryWitness:
    witness_id: UUID
    reader: ExplicitHistoryReader = field(repr=False)

@dataclass(frozen=True, slots=True)
class HistoryCollectionLimits:
    maximum_pages: int
    maximum_observations: int
    maximum_total_bytes: int
    maximum_spool_bytes: int
    maximum_wall_seconds: int
    maximum_issues: int

def collect_required_checkpoint_history(
    org_id: UUID,
    required_witnesses: tuple[BridgeWitnessPin, ...],
    readers: tuple[RequiredHistoryWitness, ...],
    limits: HistoryCollectionLimits,
    *, cancel: threading.Event | None = None,
) -> HistoryCollectionReport: ...
```

Exact built-in types are required, including integers excluding booleans, UUIDs,
tuples, dataclasses and an optional exact `threading.Event`. Require one through four
unique witness UUIDs and exactly the same reader UUID set, with no duplicates,
missing readers or extras. Namespace digests are exactly 64 lowercase hex characters.
Validate every reader using the existing pure R83 reader/scope validator, including
nonempty region and exact R73 endpoint normalization. Validate the whole request
before starting a watchdog, worker, directory or network request. Sort the admitted
witnesses by UUID bytes for deterministic traversal; never let provider data choose
or rename a witness.

Recompute the unchanged R78 namespace hash as SHA256 of
`b"EasySynQ/AuditLegacyBridge/v1/namespace\0"` plus RFC8785 bytes of exactly
`{"kind":"worm_bucket","endpoint":reader.endpoint,"bucket":reader.bucket,
"region":reader.region,"prefix":f"checkpoints/{org_id}/"}`. Compare against that
witness's external pin before I/O. Implement this small producer explicitly: R78
currently consumes supplied digests and has no production namespace-hash function.
Independent existing R78 canonical/hash vectors test the hash producer; their empty
regions remain intentionally ineligible for collection under R83. Add separately
authored nonempty-region canonical/hash vectors for accepted collection inputs.
Reject a mismatch as `HistoryCollectionInputError` with fixed text only.

Limits are explicit, with no ambient configuration defaults:

| Field | Inclusive admitted range |
| --- | --- |
| maximum_pages | 1–4,096 total attempted list requests |
| maximum_observations | 1–100,000 combined version/marker deliveries |
| maximum_total_bytes | 1–1,073,741,824 admitted original XML plus successful raw bodies, duplicates included |
| maximum_spool_bytes | 65,536–1,073,741,824, an exact multiple of 4,096 |
| maximum_wall_seconds | 1–86,400 for the whole ownership lifetime |
| maximum_issues | 1–32 displayed diagnostic groups |

Failed transport payloads are not admitted bytes; their work is separately bounded
by attempt counts, existing worker ceilings and the whole-attempt watchdog. This is
not a network-transfer quota. Bounds may prevent otherwise legitimate histories
from finishing; exhaustion never authorizes a successful prefix. No ceiling expands
R79/R81 or R77/R78.

## Traversal and retained observations

For every admitted witness, request the entire exact organization prefix, initially
with both markers absent. Use only `read_raw_checkpoint_version_page_isolated`,
then `read_raw_checkpoint_version_isolated` for each eligible version, including
repeated locators. No delimiter, date/current-version filter, manifest-selected GET,
global latest object, normalization, fallback reader, retry, or deduplication of GETs.

Before each list request, commit its witness and exact cursor pair in the spool.
Both `None` and a literal `"null"` remain distinct. Store opaque UTF8 labels as BLOBs;
encode optional cursor components with an explicit presence byte plus UTF8 bytes.
The per-witness cursor index includes every attempted pair, not just the preceding
one. A previously attempted next pair ends that witness with `CURSOR_CYCLE`; preserve
the admitted page and its bodies before stopping. Preserve R81's binding continuation
rule: a truncated page must contain observations; an empty truncated response is
rejected as CURSOR_INVALID and retained as a controlled list gap. An empty terminal
page is allowed. A terminal page only records a provider-declared end during this
traversal interval.

Store each accepted original XML page once per delivery. The storage worker decodes
those same bytes with R81 under the parent-bound witness scope and requested cursor;
it does not accept a child-supplied authoritative witness or a second parsed page
dictionary. Each version and marker gets a monotonically increasing observation
ordinal, versions in their admitted tuple order then markers in theirs. Original XML
retains the provider's full interleaving and discarded bounded metadata. The parent
cross-checks worker page counts/ordinals against its R83 result before body reads.

Store a body result against exactly that pending version ordinal. Check the returned
key/version against the requested ref again at the composition boundary. Every
version must end as raw body, controlled read-unavailable, or R79-ineligible locator.
R81-admitted control-character locators are retained as ineligible without GET;
literal `null` is passed unchanged to R80, whose returned-identity rules still apply.
Markers never receive a GET and are sticky failures. No readable copy at A erases
an unavailable observation, marker, cursor gap or denied list at required witness B.

For a byte-identical body delivered repeatedly at the same witness/key/version,
increment duplicate deliveries while preserving each row. A byte-different body at
that exact locator sets a sticky locator-conflict group. SHA256 may accelerate lookup,
but equality/conflict decisions compare exact bytes; a digest is not a substitute.
Index by witness/key/version/hash to avoid scanning all observations for each insert.
Different witnesses never share availability or locator-conflict state.

Ordinary controlled read failures are retained and traversal continues. A controlled
list failure is retained as that witness's traversal gap, then other required
witnesses are attempted. Preserve the fixed underlying transport code internally,
without provider exception text. A transport `CLEANUP_FAILED`, any mixed/fatal
`BaseExceptionGroup`, unexpected parent exception, or unproved worker cleanup aborts
the entire attempt; it cannot be downgraded into an ordinary gap. Pure known
transport errors remain unavailable observations; fatal exception identities survive
cleanup and grouping. Cancellation wins over controlled outcomes, never over the
obligation to attempt every owned cleanup.

## Private storage and protocol

Add [_history_spool.py](../../../apps/api/src/easysynq_api/services/audit/_history_spool.py)
for parent supervision,
[_history_spool_store.py](../../../apps/api/src/easysynq_api/services/audit/_history_spool_store.py)
for fixed SQLite operations,
[_history_spool_protocol.py](../../../apps/api/src/easysynq_api/services/audit/_history_spool_protocol.py)
for bounded frames and data contracts, and
[_history_spool_worker.py](../../../apps/api/src/easysynq_api/services/audit/_history_spool_worker.py)
as the executed storage-only worker. Keep R80/R83
implementations unchanged; this persistent protocol has a different lifecycle.

The parent creates a new unpredictable 0700 directory using the platform temporary
directory facility. Only this fresh directory can become the worker's cwd. Verify
ownership/type/mode; reject a symlink or unexpected occupant. The worker exclusively
creates the single 0600 `spool.sqlite3`. Never accept a caller database, schema, SQL,
backup, path to reopen, or resume token. Temporary-directory placement is an ordinary
caller environment setting, not trust authority. The actual-image proof supplies a
disposable writable parent; production deployment integration remains inactive.

SQLite policy: 4,096-byte pages; max_page_count = maximum_spool_bytes / 4,096;
journal_mode=MEMORY; temp_store=MEMORY; mmap_size=0; cache_size=-1024;
hard_heap_limit=67,108,864; synchronous=OFF; trusted_schema=OFF; threads=0;
busy_timeout=0. Read back the settings and reject a build with TEMP_STORE=0 or an
unconfirmed setting. Disable extension loading; set SQLITE_LIMIT_ATTACHED=0;
after fixed schema creation deny ATTACH, DETACH, schema mutations, mutable PRAGMAs,
load_extension and VACUUM through an authorizer as well as a fixed command surface.
No backup API, virtual table, extra connection, WAL, disk journal, SQL supplied by a
caller or arbitrary SQL operation is supported. Bound SQLite string/statement and
parameter limits to the fixed operations, permitting the existing 16 MiB XML cap.

The application-owned file budget is the logical length of that single database.
Memory-only rollback/statement journals and sorting use the SQLite/process memory
ceilings. This does not promise a host filesystem quota, bound filesystem metadata
or allocator overhead, or sandbox arbitrary executable code. A crash may corrupt
the temporary database: every SQLite/protocol/process failure poisons the attempt,
forbids later reads or results, and causes cleanup without reopening. Never rely on
rollback succeeding to turn an unexpected storage error into acceptable evidence.

Use fixed schema tables for required witnesses, page attempts/original pages, used
cursors, observations/body outcomes, locator summaries and issue groups. Columns
holding locators are BLOBs, not collated text. All values use bound parameters. Page
admission is one bounded transaction of at most 16 MiB original XML and 1,000 rows;
one body outcome transaction adds at most 64 KiB. Cursor/page-request reservations,
controlled failures and finalization are small fixed transactions. No transaction
remains open during a network call. Chunked page upload is within the storage worker,
with one outstanding command and a bounded upload deadline.

Protocol version 1 uses a four-byte big-endian length followed by payload, maximum
131,072 bytes per frame. Metadata is strict UTF8 JSON with duplicate fields, unknown
fields, bool-as-integer, nonfinite numbers, excessive nesting and trailing content
rejected. Binary chunks have a distinct fixed tag and contain at most 65,536 original
bytes; do not base64 a whole 16 MiB page. Page-upload metadata declares an admitted
length; write exactly that many bytes into a fixed zeroblob/blob handle, then decode,
insert observations and commit before ACK. Reject short/long/trailing/out-of-order
chunks, replayed responses, unexpected opcodes and commands in a pending upload.
Strict sequence IDs bind responses to the sole outstanding operation. An error frame
is terminal; close the database and exit, with no later command/result accepted.

Fixed operations: initialize admitted non-secret scope/limits; reserve page request;
upload original page; record controlled list failure; record exact body or controlled
version failure; record cursor-cycle stop; finish diagnostics. The parent stores
credentials only in its immutable reader inputs and existing network-worker pipes.
They never enter storage worker argv, environment, protocol, database or diagnostics.

## Containment and cleanup

Fresh interpreter `-I -B -u`, fixed script/source-root arguments, private session,
close_fds, stdin/stdout pipes, stderr DEVNULL, fixed LANG/TZ only, no shell or preexec_fn.
Before input/importing application code, apply/read back Linux limits: address space
536,870,912 bytes; CPU 120 seconds total; 32 descriptors; core bytes zero; file bytes
1,073,741,824, lowered to the admitted spool ceiling before opening SQLite. READY and
initialized ACK have exact fixed schemas; acceptance independently reads OS limits
instead of trusting declarations alone. There is one storage worker and at most one
network worker at a time. No grandchildren, pools, cached workers or parallel GETs.

A single owner watchdog creates an exact Event used by unchanged R80/R83, polling
the caller's cancellation and absolute whole-attempt deadline at most every 50 ms.
Storage RPC also checks that same deadline/cancellation while doing nonblocking
bounded reads/writes. Each storage command has an additional 10-second deadline;
the original-page upload is one command, not a new deadline per chunk. Shutdown
allows at most two seconds to reap each owned worker and a bounded watchdog join.
These are application/OS containment limits, not real-time kernel guarantees.

On failure/cancellation attempt selector/pipe closure, signal only the still-owned
unreaped process group, bounded reaping, watchdog stop/join, then remove the owned
spool only after worker death is established. Never signal a PID after wait/poll has
released it. Attempt independent cleanup even if another cleanup fails, preserve
unexpected/fatal identities, and expose fixed `CLEANUP_FAILED` for unproved cleanup.
No normal return occurs while a worker, file, pipe, selector or watchdog remains.

On normal finish, require a bounded complete final frame, exact EOF, successful worker
exit, database/pipe/selector closure, verified owned directory removal, watchdog
termination and final cancellation/deadline checks before returning diagnostics.
If a deadline/resource/storage failure prevents retaining the whole attempt, raise
fixed `HistoryCollectionError` with no report or accepted prefix. Its contract is
incomplete evidence; the later reconciler must not treat it as an empty history.

## Reports and errors

Frozen `HistoryWitnessSummary`: witness_id, namespace_hash, terminal_reached,
page_attempts, admitted_pages, version_observations, delete_observations,
successful_reads, unavailable_reads, duplicate_body_deliveries, conflicting_locators.
Here delete_observations counts listed DeleteMarker entries; a GET returning a delete
marker increments unavailable_reads and the failed DELETE_OBSERVATION issue group.
Thus version_observations equals successful_reads plus unavailable_reads on a normal
return; combined listed observations equal version_observations plus delete_observations.
No key/version/body/endpoint/credential text appears in its repr or report.

Frozen `HistoryCollectionIssue`: code, severity (`failed`/`incomplete`), witness_id,
count, observation_ordinals (at most four representatives). Group by enrolled
witness UUID and code before display truncation, ordered by UUID bytes then ASCII
code. A list gap may have no observation ordinal. Count all groups and mark omitted
groups; truncation cannot change status. Fixed codes are DELETE_OBSERVATION and
LOCATOR_CONFLICT (failed); LIST_UNAVAILABLE, VERSION_UNAVAILABLE, INELIGIBLE_LOCATOR
and CURSOR_CYCLE (incomplete). A GET that proves a delete marker uses failed
DELETE_OBSERVATION while retaining that it originated as a listed version.

Frozen `HistoryCollectionReport`: status (`traversed`/`failed`/`incomplete`), scope
(`required-witness-provider-traversal`), witnesses (one summary per external witness),
issues, failed_issues, incomplete_issues, issues_omitted, admitted_total_bytes,
unproved_checks. Status is failed if any failed group exists, else incomplete if any
incomplete group exists, else traversed. `traversed` requires a terminal page and
resolved observation outcome for every required witness, but authenticates no body.
Empty provider history may be traversed; later bridge/lineage requirements still
cannot succeed on an empty history.

Unproved checks are fixed: body-format-and-signatures, global-history-consistency,
provider-non-omission, atomic-snapshot, historical-deletion-absence, witness-custody,
database-chain-agreement, freshness, rollback-memory-continuity, key-activation.

Fixed error classes: `HistoryCollectionInputError(ValueError)`;
`HistoryCollectionError(Exception)` with codes RESOURCE_LIMIT, RUNTIME_UNSUPPORTED,
WORKER_START_FAILED, WORKER_FAILED, PROTOCOL_INVALID, STORAGE_FAILED,
DEADLINE_EXCEEDED, CLEANUP_FAILED; `HistoryCollectionCancelled(BaseException)`.
Reject unknown error codes. Fixed messages omit raw provider/storage exceptions and
locations. Unexpected exceptions are not silently classified as ordinary gaps.

## Required acceptance

* Independent R78 namespace vectors; all inputs admitted before zero-I/O negatives;
  every mismatch/missing/duplicate/extra required witness rejected before A is read.
* Complete namespace traversal with original XML, all-version GETs, two-field opaque
  cursors, non-adjacent cycles, empty terminal pages, rejected empty truncated pages
  and literal null/control labels.
* Late duplicate and byte conflict beyond observation 4,096/page boundaries; healthy
  A cannot heal B's unavailable version, delete marker, failed listing or cursor gap.
  Unknown/malformed bodies remain present; no parser or naming policy filters them.
* Actual SQLite schema/BLOB indexes and fixed IPC, maximum 16 MiB page upload,
  complete 1,000-observation transaction, 64 KiB body, input/page/observation/file
  ceilings, process memory/CPU/FD limits, cancellation and upload/wall timeouts.
* Storage/process death after a committed prefix and during a pending transaction;
  no later read, summary, reopened spool or success prefix. Malformed/oversized/
  stale/trailing frames and output after finish rejected. Cleanup failures exercised.
* Independent file/FD observation during live transactions and sorts, including
  already-unlinked files; disk-journal and disk-sort mutation controls must be caught.
  Verify original package code, actual OS limits, read-only root, non-root identity,
  absent dev dependencies, exact image/build source and all owned cleanup.
* Separate scaling and provider evidence: >4,096 bodies through the real storage
  worker and collector with deterministic transport-boundary fixtures; real pinned
  provider with two required witnesses, multiple pages and actual R80/R83 reads.
  Never label mocked transport scaling as provider acceptance. Bound provider setup
  and runtime explicitly; no benchmark claim about 100,000 network reads.
* Use the existing mandatory runtime runner/build/proof manifest gates; no optional
  skipped acceptance. Relevant units, full available local gates and GitLab source/
  main checks are recorded with exact revisions. No new live integration is enabled.

## Deferred parent work

Global R77/R78 reconciliation and equivalence, >eight bridge pages, key/material/edge
indexes, externally protected enrollment integration, DB comparison, physical custody,
freshness/rollback memory, issuance/delivery, activation, rotation, restore and
source-denied recovered-stack proof remain open. A successful traversal does not
close issue #3 or those residuals. Update the existing residuals with this precise
increment only when implementation acceptance passes.

## Implementation and acceptance addendum — 2026-09-11

The approved technical design above is retained with repository-relative source
links and explicit attribution of coordination-only design evidence. The normative
decision is [R84](../../decisions-register.md#r84--required-witness-traversal-retains-observations-in-a-fresh-bounded-spool--2026-09-11);
current integration evidence belongs in [current status](../../current-status.md)
and [slice history](../../slice-history.md#s-audit-required-witness-collection--fresh-bounded-traversal-diagnostics).

Startup measurements justified checked-hash bytecode compilation at optimization
zero during the existing production image build, followed by a narrow removal of
unused trust/checkpoint/settings imports from R80/R83 startup. The unchanged shared
legacy verifier Protocol now has one private definition and the same runtime object
is re-exported by checkpoint/trust. Signature verification, validators, settings
behavior, required read imports, worker flags/protocols/limits and all trust policy
remain unchanged. The metadata and image/build-size costs are recorded in slice
history; these changes do not authorize worker reuse, parallel reads or relaxed
deadlines. MEMORY-spool abandonment and every open reconciliation/recovery contract
above remain binding.

Root verified all 921 affected unit tests and the complete new-case real-image proof
with 1,003 GETs, three original pages, separate synthetic scaling and actual OS/SQL/
IPC controls. The unchanged mandatory harness then passed all ten cases with zero
failures, errors or skips. All identities and owned cleanup were checked. Full local
repository gates then passed: 4,078 API tests, the production-image check, 2,357 web
tests, lint, types, builds, authority and site-data checks. Only the release-ceremony
digest-pin test was skipped. This acceptance checkpoint precedes Task 4 and whole-branch
reviews and GitLab source/main checks and merge; current status records their disposition.

# External audit verification

Run `verify-offhost --trust-descriptor` from a separate verifier machine to check the organizations,
public keys and witness locations recorded in an owner-controlled public file. The file defines the
expected evidence independently of the database being checked. Removing an organization or rerouting
a database sink cannot remove an enrolled obligation from this command. These instructions describe
the default live consumer; use [historical target inspection](#inspect-an-explicitly-selected-historical-target)
for an independently selected, closed older database.

The command is explicit and out of band. Existing nightly jobs, API calls and `verify-offhost` without
the option retain their database-discovered, single-key behavior.

R76 also defines a pure v2 envelope codec for future predecessor and key-history consumers.
This command and the descriptor below continue to use legacy checkpoints and descriptor version 1.
A valid v2 signature or transition proof does not establish a trusted bootstrap, complete lineage,
active key, witness coverage or recovery eligibility. The codec introduces no new command option
or enrollment procedure; those consumer changes remain separate work.

R77 adds an inactive pure evaluator for a bounded set of supplied v2 envelopes and explicit public
bootstrap/key pins. Its `consistent` result proves only continuity and edge authority within that
supplied graph. It does not prove bridge contents, collection from every required witness, verifier
custody, audit-row agreement, freshness or live key activation. An old prefix can remain consistent
without independently retained newer knowledge. This command, its descriptor and its successful
verification criteria are unchanged. A scalable complete-history reader and the bridge/collection
proofs must precede operational integration; do not split history into independently accepted windows.

R78 adds an inactive bridge evaluator for exact legacy evidence committed by an external bootstrap
root. It checks all declared pages and bodies, retained signatures and the common positive boundary
at every required witness. A consistent package can supply the original external R77 pin for a
separate graph evaluation. It cannot establish that the input contains every retained object:
matching omissions from the manifest and supplied observations remain undetectable here. The current
storage reader returns parsed dictionaries and cannot reconstruct exact original-byte commitments.
No new CLI option, descriptor version or enrollment procedure is activated. Raw version collection,
scalable global reconciliation and actual database-chain comparison remain prerequisites; preserve
the existing protected-file procedure below.

R79 now provides an inactive `read_raw_checkpoint_version` API for one explicit retained key/version.
It returns original entity bytes only when response identity exactly matches and the admitted body
fits within 65,536 bytes, after cleanup. It does not authenticate those bytes or associate them with
an enrolled witness; the future collector must preserve that independent identity for R78. The
existing operational reader still returns parsed dictionaries. This addition activates no CLI option,
descriptor change, enrollment replacement or collection procedure.

Actual acceptance against the pinned provider preserves retained non-null version bytes. That
provider omits returned `VersionId` for literal `null`, so the new API rejects it as `VERSION_MISMATCH`;
it never substitutes current-object data or treats missing identity as null. The tested delete-marker
and missing-version responses become `PROVIDER_FAILURE` through the SDK. These observations do not
change existing consumer compatibility or resolve the version-list-denial residual.

Before operational collection, supply complete per-witness enumeration, scalable global reconciliation
and enforced process resource/lifetime containment or a reviewed bounded HTTP adapter. The SDK can
buffer error bodies before this API owns a stream, HTTP framing can hide surplus octets, and blocking
or trickling IO can exceed its cooperative 15-second deadline. TLS verification uses the locked
certifi bundle (SDK bundle fallback if unavailable), not automatically the verifier's OS trust store.
The synthetic CA mount in disposable acceptance is not a production trust installation procedure.
Continue using the protected-file custody procedure below for the existing explicit verifier.

R80 adds the inactive Linux-only `read_raw_checkpoint_version_isolated` foundation. It preserves
R79's explicit inputs and exact bytes while putting the SDK operation in a private worker with
512MiB address space, 10 CPU seconds, 64 descriptors and zero core/file-growth allowances. Only a
bounded request reaches the worker after its ready handshake. No body or completed read error is
published before normal child exit, stdout EOF, parent cleanup and final cancellation/deadline checks.
The parent uses a 20-second watchdog and a two-second abnormal reap attempt. An unconfirmed reap
fails closed; kernel-stuck work can outlast those user-space bounds.

Actual unprivileged-image acceptance exercised retained provider bytes through unchanged R78,
verified/untrusted TLS, a finite oversized SDK error stream, trickle timeout, late cancellation and
actual allocation/file/CPU controls. A terminated worker's OS resources are distinct from successful
Python body/client cleanup. The direct R79 reader keeps its cooperative limits. This addition supplies
no new command or operator setting and changes no existing consumer. Complete independent witness
collection, bounded page/spool handling, global reconciliation and actual snapshot comparison still
precede operational integration. Protect enrollment and rollback knowledge separately; a successful
isolated GET proves neither complete history nor recovery readiness.

R81 adds `decode_checkpoint_version_page(body, *, bucket, org_id, key_marker=None,
version_id_marker=None)` as an inactive supplied-byte foundation. It accepts one complete scoped
UTF8 XML version page, preserves versions/delete markers/duplicates and opaque labels, and returns
untrusted observations plus the admitted provider cursor. Key fields receive exactly one strict
percent decode with literal plus preserved. Control keys and literal `null` versions are retained
for later accounting; they do not authorize a retained-object read.

The supplied body ceiling is 16,777,216 bytes and the combined observation ceiling is 1,000, with
independent XML/scalar/label limits in [R81](../decisions-register.md#r81--supplied-audit-version-pages-preserve-exact-observations-under-strict-utf8-xml-admission--2026-09-09).
Invalid calls raise the fixed input error; malformed body/grammar, size, scope and continuation
failures raise the enumerated decode error with no page. A later collector must keep these failures
as persistent listing gaps. A terminal flag is not complete-history evidence, and the supplied-byte
cap does not bound downloads. There is no new command or enrollment setting. Original-byte page
transport, every required witness, full namespace accounting, bounded spool storage, global cursor
cycles, snapshot comparison and independent recovery proofs still precede operational use.

## Establish the public enrollment

The repository owner approves the expected organization IDs, retained Ed25519 public keys and witness
locations through a trusted channel. Check those values independently of the database under examination;
exporting its current sink list and treating that list as trusted would recreate the selection weakness.
Keep the descriptor and its parent directories under the owner's control on the separate verifier
machine. Changes use that same controlled replacement procedure. No additional offline policy-signing
key is required.

This is the schema, with placeholders that must be replaced before use:

```json
{
  "format_version": 1,
  "descriptor_id": "<canonical-descriptor-uuid>",
  "organizations": [
    {
      "org_id": "<canonical-organization-uuid>",
      "public_keys": [
        {
          "key_id": "ed25519-sha256:<lowercase-sha256-of-raw-public-key>",
          "public_key": "<canonical-padded-base64-of-32-public-key-bytes>"
        }
      ],
      "witnesses": [
        {
          "witness_id": "<canonical-witness-uuid>",
          "kind": "worm_bucket",
          "endpoint": "https://<approved-witness-host>",
          "bucket": "<approved-checkpoint-bucket>",
          "region": "<approved-region>"
        }
      ]
    }
  ]
}
```

Use lowercase, hyphenated UUIDs. The key ID is derived from the exact raw 32-byte Ed25519 public key,
not its PEM text. The descriptor accepts 1–16 organizations, 1–8 public keys per organization and
1–4 witnesses per organization. Organization IDs and witness IDs must be unique; a public key may be
shared across organizations but may appear only once within one organization. Unknown properties,
duplicate JSON members and unsupported formats are rejected.

Witness endpoints use HTTPS with a DNS name or IP address, an optional valid port and no path other
than `/`. User information, query strings and fragments are rejected. Plain HTTP is limited to numeric
loopback addresses for a local witness; `http://localhost` and ordinary remote HTTP are rejected. TLS
certificate verification remains enabled. Bucket and region values are explicit; there is no default
endpoint, bucket or credential discovery in this mode.

The descriptor must be an absolute-path regular UTF-8 JSON file of at most 65,536 bytes. It cannot be a
symlink or writable by group/other users. A public file with mode `0444` can be read by the image's
unprivileged user. Protect the parent directories and the approved replacement procedure as well as
the file itself.

The report's `descriptor_sha256` identifies the exact file bytes used. It does not authenticate an
enrollment change or prevent rollback to an older owner-approved file. Retain approved descriptor
versions and their custody records outside the repository. Enrollment authorizes these public keys
for legacy verification; it does not activate a signing key or define key-rotation eras.

## Supply dedicated read credentials

Use a fresh database reader with CONNECT on the intended database, USAGE on the schema and SELECT on
`organization`, `audit_event` and `audit_checkpoint`. It must not inherit application, linker, owner or
administrative privileges. The external query path does not need access to the database sink inventory.
Verify actual denials in separate read-write transactions: INSERT, UPDATE, DELETE and TRUNCATE on both
audit tables, and UPDATE on `audit_checkpoint_sink`, must fail with insufficient-privilege SQLSTATE
`42501`. A `25006` error proves only that a transaction was read-only.

The witness reader needs current-object read/location/list plus `s3:ListBucketVersions` and
`s3:GetObjectVersion` on the enrolled checkpoint storage. Check actual allowed version reads and
denials of writes, deletes, version deletes, retention changes and governance bypass. The pinned
MinIO version has the limitation recorded in
[`RES-MINIO-VERSION-LIST-DENY`](../open-residuals.md#res-minio-version-list-deny); omitted action names
alone are not effective-permission evidence. A merged Compose policy does not update a live principal.

Provide exactly these reader settings through the verifier process environment:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Explicit `postgresql+psycopg` URL with user, nonempty password, host and database. |
| `AUDIT_SINK_READ_ACCESS_KEY` | Dedicated witness-reader access key. |
| `AUDIT_SINK_READ_SECRET_KEY` | Its secret key. |

The DB URL is limited to 8,192 characters and each witness credential to 4,096 characters. Values
must be nonempty and have no surrounding whitespace. The URL accepts only `sslmode`, `sslrootcert`
and `connect_timeout` query options. The connection
timeout must be a decimal value from 1 to 30 seconds; it defaults to 5 seconds. The supplied reader
credential pair must work at every enrolled witness. Protect credential delivery separately from the
public descriptor. Ordinary application `.env`, source-store credentials, writer credentials and
local private/public key files are not fallback sources for this command.

After validating its inputs, the strict CLI replaces its process with the same interpreter and
selected application code. It supplies only those three validated values plus fixed `HOME=/dev/null`,
`LC_ALL=C.UTF-8` and `TZ=UTC`. Inherited PostgreSQL, AWS, proxy, Python and loader environment settings
are not forwarded. The private worker validates that environment and reads the descriptor again;
the successful report identifies the bytes the worker actually checked. Each file read uses the
same file descriptor for safety checks and content.

This process boundary enforces the explicit environment inputs. It does not isolate an arbitrary
workstation's operating-system configuration, credentials or installed code. Keep using the separate
verifier runtime and custody rules below. The internal asynchronous scanner is not a supported
in-process API for obtaining that isolation.

## Run the isolated verifier

On the separate verifier machine, select an approved application image already present locally and
an absolute descriptor path. Have the controlled launcher supply the three reader variables above;
do not copy the installation's whole `.env` or shared secrets volume. The following invocation passes
the existing variable values without placing them as literal values in command arguments:

```bash
docker run --pull=never --rm --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --env DATABASE_URL \
  --env AUDIT_SINK_READ_ACCESS_KEY \
  --env AUDIT_SINK_READ_SECRET_KEY \
  --mount "type=bind,source=$AUDIT_TRUST_DESCRIPTOR,target=/run/easysynq/audit-trust.json,readonly" \
  "$EASYSYNQ_API_IMAGE" \
  /app/.venv/bin/python -m easysynq_api.cli.audit verify-offhost \
  --trust-descriptor /run/easysynq/audit-trust.json
```

`AUDIT_TRUST_DESCRIPTOR` and `EASYSYNQ_API_IMAGE` are launcher values, not new application settings.
The mount contains only the public descriptor file. Provide network access to the explicitly approved
database and HTTPS witness endpoints. The verifier requires no source-workspace mount, signing key,
application secrets volume or source-store credential.

## Interpret the live result

| Exit | Meaning |
| --- | --- |
| `0` | Every enrolled organization, local checkpoint and witness passed, with no extra database organization. |
| `1` | Integrity failure, unavailable evidence or an incomplete check. |
| `2` | Invalid descriptor or reader configuration. Ordinary command-line grammar errors use argparse stderr and exit 2. |

The explicit command emits one JSON report. Its mode is `external-legacy-v1`; it records descriptor
identity, checked and pending chain-row counts, witness reads, the extra-organization result and every
enrolled organization/witness. Known obligations remain visible after early configuration/runtime
failures. `attempted: false` and `status: incomplete` distinguish work that did not start; a partially
attempted check cannot pass.

Missing organizations and missing/invalid local checkpoints fail. An enrolled witness with no
checkpoint objects fails immediately; changing a database sink's grace fields does not excuse it.
If a database operation fails, feasible independent witness reads and signature checks continue,
while unavailable row comparisons remain incomplete. Pending unchained rows are counted but are
outside the chain's verified portion.

The report contains at most 20 reasons per bounded report level and 20 detailed chain breaks per
organization, with omitted counts. Store reports outside Git; their stable enrollment IDs are
installation evidence. A failed result calls for investigation of the reported integrity or
availability condition. It does not authorize deleting retained evidence or silently changing the
expected enrollment.

## Inspect an explicitly selected historical target

Use `--historical-target` only for an already restored inspection database whose writers are
closed and whose recovery point you selected independently. The flag cannot establish that the
chosen target is intentional or distinguish a deliberately old copy from loss beyond the
observed applicable anchors. It performs audit verification only; it does not restore an archive,
acknowledge a flagged restore, prove archive provenance or make the target eligible for service.

Keep the same protected public descriptor, separate verifier machine and dedicated readers.
The historical database reader additionally needs SELECT on exactly `org_id` and
`canonical_serialize_version` in `system_config`. After reviewing the effective role privileges,
the separately authorized provisioning operation is:

```sql
GRANT SELECT (org_id, canonical_serialize_version)
ON TABLE system_config TO "<historical-reader-role>";
```

Replace the role placeholder through the normal controlled provisioning procedure. Confirm that
the two-column read succeeds, while unrelated configuration reads and all configuration writes
fail with `42501` in read-write transactions, alongside the audit-table and witness denials above.
Table-wide SELECT or inherited owner/application rights are unnecessary. The ordinary live
external verifier does not need this additional grant. Merging code does not provision a live role.

Have the controlled launcher supply a `DATABASE_URL` for the closed historical target, then run:

```bash
docker run --pull=never --rm --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --env DATABASE_URL \
  --env AUDIT_SINK_READ_ACCESS_KEY \
  --env AUDIT_SINK_READ_SECRET_KEY \
  --mount "type=bind,source=$AUDIT_TRUST_DESCRIPTOR,target=/run/easysynq/audit-trust.json,readonly" \
  "$EASYSYNQ_API_IMAGE" \
  /app/.venv/bin/python -m easysynq_api.cli.audit verify-offhost \
  --trust-descriptor /run/easysynq/audit-trust.json --historical-target
```

The report mode is `external-historical-legacy-v1`. It adds these `historical` details without
changing the existing live report:

| Level | Field | Meaning |
| --- | --- | --- |
| Organization | `canonical_serialize_version` | This organization's observed integer serialization version; unsupported values fail verification, and malformed or unavailable values are null. |
| Organization | `linked_head_id` | Its greatest linked row ID in the observed snapshot. Pending rows cannot raise it. |
| Organization | `covered_through_id` | The minimum certified head across all required witnesses, or null without certifiable aggregate coverage. |
| Organization | `covered_rows`, `uncovered_linked_rows` | Actual linked row counts within and beyond that aggregate prefix; IDs can have gaps or interleave organizations. |
| Witness | `applicable_checkpoints` | Authenticated retained versions at or below the target head. Authentication alone does not prove a matching row. |
| Witness | `ahead_checkpoints`, `highest_ahead_id` | Authenticated versions above the target head, and their greatest ID. These bodies do not fill a target coverage gap. |
| Witness | `covered_through_id` | Its greatest matching applicable head after complete valid scanning and full-chain/local attestation. |

An unavailable value is null, not zero. If valid complete scans leave any required witness
without an applicable anchor, the aggregate head is null, covered rows are zero and all checked
linked rows remain uncovered. An invalid/incomplete scan or invalid/incomplete chain/local
attestation prevents certified coverage; observed authenticated checkpoint counts can still be
reported. A newest local checkpoint above the target head remains invalid: ahead classification
applies only to off-host evidence.

Exit 0 requires full coverage from every required witness for every enrolled organization,
successful full-chain/local verification, no pending rows, no missing/extra organization and no
incomplete checks or cleanup errors. A lower valid prefix is useful evidence, but reports
`HISTORICAL_COVERAGE_INCOMPLETE`, `verified: false` and exit 1. Pending rows separately report
`PENDING_ROWS_UNVERIFIED` and prevent exit 0 without being called chain corruption. Missing and
unsupported per-organization canonical metadata use `CANONICAL_VERSION_MISSING` and
`CANONICAL_VERSION_UNSUPPORTED`, including for an empty chain. Empty linked chains cannot pass
without attestation. Configuration and grammar errors use exit 2 as above.

Every eligible retained version must authenticate before it is classified. Old applicable
contradictions remain failures after re-anchoring; malformed or unknown-key bodies cannot be
ignored because their claimed heads appear newer. Later-only evidence does not attest the
restored target. Delete markers, denied version reads and incomplete traversal remain material.
There is no historical acknowledgment or caller-supplied coverage cutoff.

Historical verification uses one read-only REPEATABLE READ snapshot for all database observations,
with the same 300-second statement timeout and 900-second cooperative command budget. A failed
database snapshot is not restarted; feasible independent witness authentication continues while
unavailable comparisons remain incomplete. The snapshot does not stop other writers and does
not make object-store reads atomic with the database. Historical mode omits the live freshness
check; it cannot report current liveness. All retained-history caps and cleanup limitations below
still apply.

## Bounds and remaining limits

The whole command has a cooperative 900-second budget and each witness scan a cooperative 300-second
budget. Existing history limits allow fewer than 1,024 pages and fewer than 524,288 returned versions
or delete markers, with checkpoint bodies at most 65,536 bytes. Reaching a cap fails closed, including
on an otherwise terminal page. In live mode, the newest authenticated timestamp supplies the
existing 2,700-second liveness check; older retained contradictions remain failures in both modes.

Live database reads use fresh read-only READ COMMITTED transactions; historical database reads
share one read-only REPEATABLE READ snapshot. Both use a 300-second statement timeout. Database
and object-store observations are not an atomic snapshot together. In-flight provider
threads and database cleanup can outlast a cooperative timeout. The existing chain walk can still
accumulate an unbounded list of failures internally before the public report is capped.

The descriptor's owner custody remains the trust boundary. A compromised or rolled-back descriptor,
compromised enrolled signing key, or expired evidence can defeat conclusions this legacy format
cannot establish. The command adds no predecessor commitment, key-activation protocol or
source-independent restore proof. Keep
[`RES-AUDIT-CHECKPOINT-LINEAGE`](../open-residuals.md#res-audit-checkpoint-lineage),
[`RES-AUDIT-KEY-ROTATION`](../open-residuals.md#res-audit-key-rotation) and the source-independent recovery
closure contract open. Repository acceptance uses disposable synthetic services; live enrollment and
principal provisioning remain owner-operated deployment actions.

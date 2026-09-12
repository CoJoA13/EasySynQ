"""Test-only typed domain bridge; no SQL/code or preverified-state input.

Unlike provider traversal, pure R78 inputs can include foreign witnesses and gap
observations with their own ordinal. Seed those original domain outcomes directly,
then install the real immutable-evidence authorizer. This asserts no LIST closure.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import resource
import runpy
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


def _main() -> None:
    source = Path(__file__).resolve().parents[2] / "src"
    assert sys.argv[1:] == [str(source)]
    policy = runpy.run_path(
        str(source / "easysynq_api/services/audit/_history_reconciliation_worker.py")
    )
    policy["_apply_limits"]()
    sys.path.insert(0, str(source))
    from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit import bootstrap_bridge as bridge
    from easysynq_api.services.audit import legacy_checkpoint_compat as legacy
    from easysynq_api.services.audit._history_reconciliation_bridge import _BridgeKernel
    from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
    from easysynq_api.services.audit._history_reconciliation_store import _ReconciliationStore
    from easysynq_api.services.audit.history_reconciliation import HistoryReconciliationError

    read = policy["_read_exact"]
    size = int.from_bytes(read(sys.stdin.buffer, 4), "big")
    assert 0 < size <= 64 * 1024 * 1024
    value = json.loads(read(sys.stdin.buffer, size))
    assert sys.stdin.buffer.read(1) == b""
    wire.fields(value, {"kind", "scope", "root", "pages", "observations"})
    assert value["kind"] == "bridge"
    scope = protocol.decode_init({**value["scope"], "op": "INIT", "id": 1})
    assert scope.limits.maximum_observations <= 4096
    assert scope.limits.maximum_total_bytes <= 16 * 1024 * 1024
    assert scope.limits.maximum_bridge_pages <= 8
    root = None if value["root"] is None else bytes.fromhex(value["root"])
    pages = tuple(bridge.BridgePageObservation(bytes.fromhex(p)) for p in value["pages"])
    assert scope.root_length == (None if root is None else len(root))
    assert scope.page_lengths == tuple(len(p.body) for p in pages)
    assert type(value["observations"]) is list and len(value["observations"]) <= 4096
    observations: list[bridge.LegacyObservation] = []
    for item in value["observations"]:
        kind = item.get("kind")
        if kind == "gap":
            wire.fields(item, {"witness", "kind", "reason"})
            observations.append(bridge.WitnessCollectionGap(UUID(item["witness"]), item["reason"]))
        else:
            wire.fields(
                item,
                {"witness", "kind", "key", "version"} | ({"body"} if kind == "body" else set()),
            )
            locator = (UUID(item["witness"]), item["key"], item["version"])
            if kind == "body":
                observations.append(
                    bridge.LegacyBodyObservation(*locator, bytes.fromhex(item["body"]))
                )
            elif kind == "unavailable":
                observations.append(bridge.LegacyUnavailableObservation(*locator))
            else:
                assert kind == "delete"
                observations.append(bridge.LegacyDeleteObservation(*locator))
    assert bridge._preflight(
        scope.enrollment,
        root,
        pages,
        tuple(observations),
        bridge.BridgeLimits(4096, 16 * 1024 * 1024, scope.limits.maximum_issues),
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (scope.limits.maximum_spool_bytes,) * 2)
    store = _ReconciliationStore(scope)
    try:
        package = ([] if root is None else [("root", 0, root)]) + [
            ("page", i, p.body) for i, p in enumerate(pages)
        ]
        for kind, index, raw in package:
            store.package_begin(kind, index, len(raw))
            for offset in range(0, len(raw), wire.CHUNK_MAX):
                store.package_chunk(raw[offset : offset + wire.CHUNK_MAX])
            store.package_end()
        db = store._connection()
        for ordinal, item in enumerate(observations, 1):
            row = db.execute(
                "SELECT id FROM witnesses WHERE uuid=?", (item.witness_id.bytes,)
            ).fetchone()
            if row is None:
                witness = db.execute(
                    "INSERT INTO witnesses(uuid,namespace_hash,bucket,next_key,next_version) "
                    "VALUES(?,?,?,?,?)",
                    (item.witness_id.bytes, "0" * 64, "fixture", b"\0", b"\0"),
                ).lastrowid
            else:
                witness = row[0]
            body = item.body if isinstance(item, bridge.LegacyBodyObservation) else None
            gap = isinstance(item, bridge.WitnessCollectionGap)
            kind = (
                "gap"
                if gap
                else "legacy-marker"
                if isinstance(item, bridge.LegacyDeleteObservation)
                else "version"
                if body is not None
                else "legacy-unavailable"
            )
            outcome = (
                "gap"
                if gap
                else "body"
                if body is not None
                else "marker"
                if kind == "legacy-marker"
                else "unavailable"
            )
            db.execute(
                "INSERT INTO "
                "observations(ordinal,page,witness,key,version,kind,outcome,failure,body,digest) "
                "VALUES(?,0,?,?,?,?,?,?,?,?)",
                (
                    ordinal,
                    witness,
                    b"" if gap else item.object_key.encode(),
                    b"" if gap else item.version_id.encode(),
                    kind,
                    outcome,
                    item.reason if gap else None,
                    body,
                    None if body is None else hashlib.sha256(body).digest(),
                ),
            )
        # Test-only typed-domain sealing. No public/provider completeness result is manufactured.
        assert store.state == "collecting" and store._blob is None and store._pending is None
        store._ordinal = len(observations)
        store._total = sum(
            len(o.body) for o in observations if isinstance(o, bridge.LegacyBodyObservation)
        )
        store._budget()
        store._state = "sealed"
        db.set_authorizer(store._sealed_authorize)
        issues = _IssueIndex(store)
        kernel = _BridgeKernel(store, scope.enrollment, issues)
        decode_calls = 0
        authentication_calls = 0
        original_decode, original_authenticate = legacy._decode, legacy._authenticate

        def counted_decode(*args: Any, **kwargs: Any) -> Any:
            nonlocal decode_calls
            decode_calls += 1
            return original_decode(*args, **kwargs)

        def counted_authenticate(*args: Any, **kwargs: Any) -> Any:
            nonlocal authentication_calls
            authentication_calls += 1
            return original_authenticate(*args, **kwargs)

        legacy._decode, legacy._authenticate = counted_decode, counted_authenticate
        maximum_work = 0
        for phase in protocol.PHASES[:11]:
            done = False
            steps = 0
            while not done:
                before = store._identity_work if phase == "body-index" else kernel.work
                done = store.index_bodies_step() if phase == "body-index" else kernel.step(phase)
                after = store._identity_work if phase == "body-index" else kernel.work
                assert 0 <= after - before <= (1 if phase == "package-pages" else 64)
                maximum_work = max(maximum_work, after - before)
                steps += 1
                assert steps <= 100_000
            if phase == "body-index":
                # The explicit input type was LegacyBodyObservation. Preserve
                # that domain tag, including invalid evidence; authenticate and
                # decode only in the real kernel. The public mixed classifier
                # is separate and is never replaced by this fixture operation.
                db.execute("UPDATE raw_bodies SET format='legacy'")
        # Explain the exact literal SELECT statements in the executing module,
        # not a hand-copied SQL approximation. No caller can supply these queries.
        syntax = ast.parse(
            (source / "easysynq_api/services/audit/_history_reconciliation_bridge.py").read_text()
        )
        plans = []
        for node in ast.walk(syntax):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
                and node.args[0].value.startswith("SELECT ")
            ):
                sql = node.args[0].value
                plan = [
                    row[3]
                    for row in db.execute("EXPLAIN QUERY PLAN " + sql, (b"",) * sql.count("?"))
                ]
                assert not any("TEMP B-TREE" in item for item in plan), (sql, plan)
                plans.append(plan)
        failed, incomplete = issues.counts()
        displayed = issues.display(scope.limits.maximum_issues)
        result: dict[str, Any] = {
            "status": "failed" if failed else "incomplete" if incomplete else "consistent",
            "scope": "supplied-legacy-bootstrap-package",
            "usable": kernel.consistent,
            "witness_summaries": [
                dataclasses.asdict(w) | {"witness_id": str(w.witness_id)}
                for w in kernel.witness_summaries()
            ],
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "observation_indexes": [
                        r.index - 1 for r in i.references if r.kind == "observation"
                    ],
                    "page_indexes": [r.index for r in i.references if r.kind == "page"],
                }
                for i in displayed
            ],
            "failed_issues": failed,
            "incomplete_issues": incomplete,
            "issues_omitted": max(0, failed + incomplete - len(displayed)),
            "duplicate_body_observations": kernel.duplicate_bodies,
            "duplicate_page_observations": kernel.duplicate_pages,
            "established_checks": [
                "external-root-content-binding",
                "committed-page-and-locator-closure",
                "retained-legacy-signature-authentication",
                "supplied-observation-reconciliation",
                "per-witness-signed-boundary-binding",
            ]
            if kernel.consistent
            else [],
            "unproved_checks": [
                "operational-legacy-history-completeness",
                "witness-collection-completeness",
                "witness-custody",
                "audit-chain-comparison",
                "v2-lineage-consistency",
                "freshness",
                "rollback-memory-continuity",
                "operational-key-activation",
            ],
            "inspection": {
                "decode_calls": decode_calls,
                "authentication_calls": authentication_calls,
                "maximum_step_work": maximum_work,
                "plans": plans,
                "authenticated_raws": db.execute(
                    "SELECT count(*) FROM legacy_bodies WHERE authentication_state='authenticated'"
                ).fetchone()[0],
                "decoded_raws": db.execute("SELECT count(*) FROM legacy_bodies").fetchone()[0],
                "manifest_complete": kernel.manifest_complete,
            },
        }
    except HistoryReconciliationError as error:
        if type(error) is not HistoryReconciliationError:
            raise
        result = {"error": error.code}
    finally:
        store.close()
    raw = json.dumps(result, separators=(",", ":")).encode()
    assert 0 < len(raw) <= 16 * 1024 * 1024
    policy["_write"](sys.stdout.buffer, len(raw).to_bytes(4, "big") + raw)


if __name__ == "__main__":
    _main()

"""Typed, capacity-limited differential fixtures in fresh owned kernel processes.

This test-only protocol is deliberately absent from the packaged executable.
It carries original domain observations, never SQL or authenticated assertions.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
from easysynq_api.services.audit import bootstrap_bridge as bridge
from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness
from easysynq_api.services.audit.history_reconciliation import (
    HistoryReconciliationError,
    HistoryReconciliationLimits,
)
from easysynq_api.services.audit.lineage import AuditHead
from tests.unit.test_audit_bootstrap_bridge import _ReferenceCase

_INPUT_MAX = 64 * 1024 * 1024
_OUTPUT_MAX = 16 * 1024 * 1024
_WORKER = Path(__file__).with_name("audit_history_reconciliation_kernel_worker.py")
_SOURCE = Path(__file__).resolve().parents[2] / "src"


def _exchange(payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if not 0 < len(raw) <= _INPUT_MAX:
        raise ValueError("kernel fixture input exceeds shared capacity")
    outgoing = memoryview(len(raw).to_bytes(4, "big") + raw)
    output, errors = bytearray(), bytearray()
    directory = tempfile.mkdtemp(prefix="easysynq-kernel-fixture-")
    process = None
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed test-only executable and source path
            [sys.executable, "-I", "-B", "-u", str(_WORKER), str(_SOURCE)],
            cwd=directory,
            env={"LANG": "C.UTF-8", "TZ": "UTC"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        assert process.stderr is not None
        deadline = time.monotonic() + 90
        with selectors.DefaultSelector() as selector:
            for pipe, mode in (
                (process.stdin, selectors.EVENT_WRITE),
                (process.stdout, selectors.EVENT_READ),
                (process.stderr, selectors.EVENT_READ),
            ):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, mode)
            offset = 0
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError("kernel fixture deadline")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    pipe = key.fileobj
                    if pipe is process.stdin:
                        try:
                            offset += os.write(pipe.fileno(), outgoing[offset : offset + 65_536])
                        except BrokenPipeError:
                            offset = len(outgoing)
                        if offset == len(outgoing):
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        part = os.read(key.fd, 65_536)
                        if not part:
                            selector.unregister(pipe)
                            pipe.close()
                        else:
                            target = output if pipe is process.stdout else errors
                            cap = _OUTPUT_MAX + 4 if target is output else 65_536
                            if len(target) + len(part) > cap:
                                raise ValueError("kernel fixture output exceeds capacity")
                            target.extend(part)
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        assert code == 0 and not errors, (code, errors.decode(errors="replace"))
        assert len(output) >= 4
        length = int.from_bytes(output[:4], "big")
        assert 0 < length <= _OUTPUT_MAX and len(output) == length + 4
    finally:
        # Do not remove a database until the sole process is proved dead/reaped.
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        shutil.rmtree(directory)
        assert not os.path.exists(directory)
    result = json.loads(output[4:])
    assert type(result) is dict
    return result


def _bridge_payload(case: _ReferenceCase, maximum_issues: int) -> dict[str, Any]:
    limits = bridge.BridgeLimits(4096, 16 * 1024 * 1024, maximum_issues)
    if not bridge._preflight(
        case.enrollment, case.root_body, case.pages, case.observations, limits
    ):
        raise ValueError("kernel fixture exceeds shared capacity")
    scope = protocol._PublicScope(
        case.enrollment,
        tuple(
            _SpoolWitness(w.witness_id, w.namespace_hash, "fixture")
            for w in sorted(case.enrollment.witnesses, key=lambda item: item.witness_id.bytes)
        ),
        HistoryReconciliationLimits(
            maximum_pages=4096,
            maximum_observations=4096,
            maximum_bridge_pages=8,
            maximum_manifest_entries=4096,
            maximum_total_bytes=16 * 1024 * 1024,
            maximum_spool_bytes=256 * 1024 * 1024,
            maximum_wall_seconds=90,
            maximum_issue_groups=100_000,
            maximum_issues=maximum_issues,
        ),
        None if case.root_body is None else len(case.root_body),
        tuple(len(page.body) for page in case.pages),
    )
    observations = []
    for item in case.observations:
        record: dict[str, Any] = {"witness": str(item.witness_id)}
        if isinstance(item, bridge.WitnessCollectionGap):
            record.update(kind="gap", reason=item.reason)
        else:
            record.update(key=item.object_key, version=item.version_id)
            if isinstance(item, bridge.LegacyBodyObservation):
                record.update(kind="body", body=item.body.hex())
            else:
                record["kind"] = (
                    "delete" if isinstance(item, bridge.LegacyDeleteObservation) else "unavailable"
                )
        observations.append(record)
    return {
        "kind": "bridge",
        "scope": protocol.scope_payload(scope),
        "root": None if case.root_body is None else case.root_body.hex(),
        "pages": [page.body.hex() for page in case.pages],
        "observations": observations,
    }


def kernel_bridge(case: _ReferenceCase, *, maximum_issues: int = 32) -> bridge.BridgeEvaluation:
    value = _exchange(_bridge_payload(case, maximum_issues))
    return _bridge_result(case, value)


def _bridge_result(case: _ReferenceCase, value: dict[str, Any]) -> bridge.BridgeEvaluation:
    if set(value) == {"error"}:
        raise HistoryReconciliationError(value["error"])
    # Only map typed output fields and original ordinal indexes; no verdict is repaired.
    return bridge.BridgeEvaluation(
        value["status"],
        value["scope"],
        case.enrollment.stream.bootstrap if value["usable"] else None,
        tuple(
            bridge.BridgeWitnessSummary(
                UUID(w["witness_id"]),
                w["committed_locators"],
                AuditHead(**w["lowest_head"]),
                AuditHead(**w["highest_head"]),
            )
            for w in value["witness_summaries"]
        ),
        tuple(
            bridge.BridgeIssue(
                i["code"], i["severity"], tuple(i["observation_indexes"]), tuple(i["page_indexes"])
            )
            for i in value["issues"]
        ),
        value["failed_issues"],
        value["incomplete_issues"],
        value["issues_omitted"],
        value["duplicate_body_observations"],
        value["duplicate_page_observations"],
        tuple(value["established_checks"]),
        tuple(value["unproved_checks"]),
    )

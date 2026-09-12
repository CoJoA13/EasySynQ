"""Host checker rejects forged identity, transport, outcome and cleanup receipts."""

import copy
import json

import pytest

from tests.integration.audit_history_reconciliation_runtime_acceptance import (
    ReceiptError,
    check_receipt,
)

pytestmark = pytest.mark.unit


def _expected():
    return dict(
        case="provider-consistent",
        build_digest="a" * 64,
        proof_digest="b" * 64,
        installed_sources={"history_reconciliation.py": "c" * 64},
        counts={"provider": {"list": 2, "get": 11}, "synthetic": {"list": 0, "get": 0}},
        outcomes={
            "status": "consistent",
            "codes": [],
            "required_checkpoint_relation": "included",
            "path_length": 3,
            "used_epoch_count": 2,
            "tip": "d" * 64,
        },
        transport_digest="e" * 64,
        workers=1,
    )


def _receipt():
    expected = _expected()
    return dict(
        schema_version=1,
        case=expected["case"],
        build_digest=expected["build_digest"],
        proof_digest=expected["proof_digest"],
        runtime=dict(
            python="3.12.14",
            sqlite="3.40.1",
            uid=10001,
            module_root="/app/src/easysynq_api",
            installed_sources=expected["installed_sources"],
            dev_packages_absent=["mypy", "pytest", "ruff"],
        ),
        counts=expected["counts"],
        outcomes=expected["outcomes"],
        limits=dict(
            address_space=536870912,
            cpu_seconds=120,
            descriptors=32,
            core_bytes=0,
            file_bytes=268435456,
            command_seconds=10,
            frame_bytes=131072,
            result_bytes=65536,
        ),
        cleanup=dict(
            workers=1,
            reaped=True,
            pipes_closed=True,
            watchdog_joined=True,
            directories_removed=True,
            open_unlinked_absent=True,
            credentials_absent=True,
        ),
        transport_digest=expected["transport_digest"],
    )


def test_independent_expected_inventory_admits_exact_receipt():
    value = _receipt()
    assert check_receipt(json.dumps(value).encode(), _expected()) == value


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("case",), "synthetic-consistent"),
        (("case",), []),
        (("case",), {}),
        (("runtime",), []),
        (("counts",), None),
        (("cleanup",), []),
        (("build_digest",), "f" * 64),
        (("proof_digest",), "f" * 64),
        (("counts", "provider", "get"), 10),
        (("counts", "provider", "get"), True),
        (("counts", "provider", "list"), 1),
        (("counts", "synthetic", "get"), 11),
        (("counts", "synthetic", "get"), 0.0),
        (("transport_digest",), "f" * 64),
        (("outcomes", "status"), "incomplete"),
        (("outcomes", "path_length"), 2),
        (("outcomes", "used_epoch_count"), 1),
        (("outcomes", "tip"), "f" * 64),
        (("outcomes", "required_checkpoint_relation"), "missing"),
        (("runtime", "uid"), 0),
        (("runtime", "python"), "unexpected"),
        (("runtime", "sqlite"), "3.40"),
        (("runtime", "module_root"), "/mounted/src/easysynq_api"),
        (("runtime", "installed_sources", "history_reconciliation.py"), "f" * 64),
        (("runtime", "dev_packages_absent"), []),
        (("limits", "cpu_seconds"), 121),
        (("limits", "address_space"), 1073741824),
        (("cleanup", "workers"), 0),
        (("cleanup", "reaped"), False),
        (("cleanup", "pipes_closed"), False),
        (("cleanup", "watchdog_joined"), False),
        (("cleanup", "directories_removed"), False),
        (("cleanup", "open_unlinked_absent"), False),
        (("cleanup", "credentials_absent"), False),
    ],
)
def test_mutated_receipt_fails_for_its_changed_field(path, value):
    mutated = copy.deepcopy(_receipt())
    target = mutated
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ReceiptError, match="receipt"):
        check_receipt(json.dumps(mutated).encode(), _expected())


@pytest.mark.parametrize(
    "mode", ["unknown", "missing", "duplicate", "oversize", "trailing", "non-object"]
)
def test_receipt_wire_boundary_is_exact(mode):
    value = _receipt()
    raw = json.dumps(value).encode()
    if mode == "unknown":
        value["unexpected"] = True
        raw = json.dumps(value).encode()
    elif mode == "missing":
        del value["cleanup"]
        raw = json.dumps(value).encode()
    elif mode == "duplicate":
        raw = b'{"schema_version":1,' + raw[1:]
    elif mode == "oversize":
        raw += b" " * (65537 - len(raw))
    elif mode == "trailing":
        raw += b"{}"
    else:
        raw = b"[]"
    with pytest.raises(ReceiptError, match="receipt"):
        check_receipt(raw, _expected())


@pytest.mark.parametrize("mutation", ["remove-b", "version", "bytes", "repeat"])
def test_exact_transport_digest_rejects_changed_multiset(mutation):
    from tests.integration.audit_history_reconciliation_runtime_acceptance import transport_digest

    events = [
        ["A", "GET", "key", "version-A", "a" * 64],
        ["B", "GET", "key", "version-B", "b" * 64],
    ]
    expected = _expected() | dict(transport_digest=transport_digest(events))
    value = _receipt() | dict(transport_digest=expected["transport_digest"])
    assert check_receipt(json.dumps(value).encode(), expected)
    if mutation == "remove-b":
        events.pop()
    elif mutation == "repeat":
        events.append(events[-1])
    else:
        events[-1][3 if mutation == "version" else 4] = "changed"
    value["transport_digest"] = transport_digest(events)
    with pytest.raises(ReceiptError):
        check_receipt(json.dumps(value).encode(), expected)


def _seeded():
    from tests.integration.audit_history_reconciliation_runtime_acceptance import _seed_cases

    class Provider:
        counter = 0

        def put_object(self, **kwargs):
            self.counter += 1
            return {"VersionId": f"retained-{self.counter:08d}"}

    buckets = ["synthetic-provider-a", "synthetic-provider-b"]
    return _seed_cases(
        Provider(),
        buckets,
        [("fixture-a", "fixture-secret-a"), ("fixture-b", "fixture-secret-b")],
        {b: [] for b in buckets},
    )


@pytest.mark.parametrize("name", ["provider-missing", "provider-consistent", "provider-conflict"])
def test_provider_seed_binding_has_independent_expected_semantics(name, tmp_path, monkeypatch):
    import dataclasses
    from uuid import UUID

    from easysynq_api.services.audit.history_reconciliation import (
        collect_and_reconcile_checkpoint_history,
    )
    from tests.integration.audit_history_reconciliation_runtime_acceptance import (
        _expected,
        _original_events,
    )
    from tests.integration.audit_history_reconciliation_runtime_probe import _outcome
    from tests.unit.audit_history_reconciliation_vectors import (
        PREFIX,
        Delivery,
        replace_deliveries,
        sign_v2,
        synthetic_transport,
    )

    case, missing = _seeded()
    enrollment, root = case.args[:2]
    if name != "provider-missing":
        case = replace_deliveries(
            case, (*case.deliveries, dataclasses.replace(missing, version="retained-middle"))
        )
    if name == "provider-conflict":
        payload = json.loads(
            next(
                d.body
                for d in case.deliveries
                if "/v2/" in d.key and json.loads(d.body)["checkpoint"]["sequence"] == "3"
            )
        )["checkpoint"]
        payload.update(anchor_id=str(UUID(int=90000)), latest_row_hash="cd" * 32)
        case = replace_deliveries(
            case,
            (
                *case.deliveries,
                Delivery(
                    missing.witness, PREFIX + "zz-conflict", "retained-last", sign_v2(payload, 1)
                ),
            ),
        )
    assert case.args[0] == enrollment and case.args[1] == root
    with synthetic_transport(case, monkeypatch, tmp_path):
        report = collect_and_reconcile_checkpoint_history(*case.args)
    expected = _expected(
        case,
        name,
        {"build_input_sha256": "a" * 64, "proof_input_sha256": "b" * 64},
        _original_events(case),
    )
    assert _outcome(report) == expected["outcomes"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("kill-seal", "WORKER_FAILED"),
        ("kill-graph", "WORKER_FAILED"),
        ("kill-final", "WORKER_FAILED"),
        ("stale", "PROTOCOL_INVALID"),
        ("trailing", "PROTOCOL_INVALID"),
        ("cancel", "cancelled"),
        ("deadline", "DEADLINE_EXCEEDED"),
        ("cleanup", "CLEANUP_FAILED"),
    ],
)
def test_probe_faults_reach_real_worker_and_clean_it(fault, code, tmp_path, monkeypatch):
    import tempfile

    from easysynq_api.services.audit import history_reconciliation as public
    from tests.integration.audit_history_reconciliation_runtime_acceptance import (
        _config,
        _original_events,
    )
    from tests.integration.audit_history_reconciliation_runtime_probe import (
        _args,
        _observe,
        _transport,
    )
    from tests.unit.audit_history_reconciliation_vectors import large_case

    case = large_case(3, 5, 2)
    config = _config(
        case, "hostile", {"build_input_sha256": "a" * 64, "proof_input_sha256": "b" * 64}
    )
    config = json.loads(json.dumps(config))
    args = _args(config)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with _transport(config, args) as events, _observe(config, fault=fault) as workers:
        with pytest.raises(
            (public.HistoryReconciliationCancelled, public.HistoryReconciliationError)
        ) as caught:
            public.collect_and_reconcile_checkpoint_history(*args)
        assert (
            "cancelled"
            if isinstance(caught.value, public.HistoryReconciliationCancelled)
            else caught.value.code
        ) == code
        assert len(workers) == 1 and workers[0].fault_reached and workers[0].sampled
    assert sorted(events) == sorted(_original_events(case))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "fault",
    [
        "heap",
        "fd",
        "cpu",
        "file",
        "sql-policy",
        "sql-heap",
        "sql-file",
        "sql-journal",
        "sql-sort",
        "sql-attach",
    ],
)
def test_fixed_resource_controls_use_installed_worker_policy(fault, tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path

    from easysynq_api.services.audit import _history_reconciliation_worker as worker
    from tests.integration import audit_history_reconciliation_runtime_probe as probe
    from tests.integration.audit_history_reconciliation_runtime_acceptance import _config, _expected
    from tests.unit.audit_history_reconciliation_vectors import large_case

    case = large_case(3, 5, 2)
    record = {"build_input_sha256": "a" * 64, "proof_input_sha256": "b" * 64}
    config = _config(case, "hostile", record)
    with subprocess.Popen(  # noqa: S603 - fixed owned test process
        [
            sys.executable,
            "-I",
            "-B",
            "-u",
            probe.__file__,
            "--fault-worker",
            worker.__file__,
            str(Path(worker.__file__).resolve().parents[3]),
            fault,
        ],
        cwd=tmp_path,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            out, err = process.communicate(json.dumps(config["scope"]).encode(), timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        assert not err, err.decode()
        assert process.returncode == (-signal.SIGKILL if fault == "cpu" else 0)
        actual = json.loads(out) if fault.startswith("sql-") else "enforced"
        assert actual == _expected(case, "hostile", record, [])["outcomes"][fault]
    assert not Path(f"/proc/{process.pid}").exists()
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed

"""Bounded observation producer using only the immutable image's installed API."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import resource
import runpy
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import UUID


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        assert key not in result
        result[key] = value
    return result


def _fault_worker() -> int:
    worker, source, fault = sys.argv[2:]
    policy = runpy.run_path(worker)
    policy["_apply_limits"]()
    if fault in {"kill-final", "trailing", "stale"}:
        write = policy["_write"]

        def changed(stream: Any, raw: bytes) -> None:
            value = json.loads(raw[5:])
            if fault == "stale" and "progress" in value:
                value["id"] -= 1
                body = b"J" + _json(value)
                raw = len(body).to_bytes(4, "big") + body
            write(stream, raw)
            if "result" in value:
                if fault == "kill-final":
                    os.kill(os.getpid(), signal.SIGSTOP)
                elif fault == "trailing":
                    write(stream, b"x")

        policy["_main"].__globals__["_write"] = changed
    elif fault.startswith("sql-"):
        return _sql_worker(source, fault)
    else:
        assert fault in {"heap", "fd", "cpu", "file"}
        if fault == "heap":
            try:
                bytearray(600_000_000)
            except MemoryError:
                return 0
            raise AssertionError("address-space policy ineffective")
        if fault == "fd":
            import errno

            opened = []
            try:
                for _ in range(64):
                    try:
                        opened.append(os.open(os.devnull, os.O_RDONLY))
                    except OSError as error:
                        assert error.errno == errno.EMFILE and len(opened) < 32
                        return 0
                raise AssertionError("descriptor policy ineffective")
            finally:
                for fd in opened:
                    os.close(fd)
        if fault == "file":
            import errno

            assert resource.getrlimit(resource.RLIMIT_FSIZE) == (1073741824, 1073741824)
            resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            try:
                with open("bounded-file", "wb", buffering=0) as target:
                    target.write(b"x" * 4096)
                    target.write(b"x")
            except OSError as error:
                assert error.errno == errno.EFBIG and Path("bounded-file").stat().st_size == 4096
                return 0
            raise AssertionError("file ceiling ineffective")
        assert resource.getrlimit(resource.RLIMIT_CPU) == (120, 120)
        resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
        while True:
            pass
    sys.argv = [worker, source]
    return policy["_main"]()


def _sql_worker(source: str, fault: str) -> int:
    # Fixed test-only operations exercise the real installed store; the production
    # protocol continues to admit no SQL, policy overrides, paths or executables.
    sys.path.insert(0, source)
    from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
    from easysynq_api.services.audit._history_reconciliation_store import _ReconciliationStore
    from easysynq_api.services.audit.history_collection import HistoryCollectionError

    scope = json.loads(sys.stdin.buffer.read(65537), object_pairs_hook=_pairs)
    scope.update(root_length=None, page_lengths=[2097152] if fault == "sql-file" else [])
    if fault == "sql-file":
        scope["limits"]["maximum_spool_bytes"] = 1048576
    if fault in {"sql-journal", "sql-sort", "sql-attach"}:
        connect = sqlite3.connect

        class ChangedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if fault == "sql-journal" and sql == "PRAGMA journal_mode=memory":
                    sql = "PRAGMA journal_mode=delete"
                if fault == "sql-sort" and sql == "PRAGMA temp_store=2":
                    sql = "PRAGMA temp_store=1"
                return super().execute(sql, parameters)

            def setlimit(self, category, limit):
                if fault == "sql-attach" and category == sqlite3.SQLITE_LIMIT_ATTACHED:
                    limit = 1
                return super().setlimit(category, limit)

        def changed(*args, **kwargs):
            return connect(*args, factory=ChangedConnection, **kwargs)

        sqlite3.connect = changed
    admitted = protocol.decode_init(scope | {"op": "INIT", "id": 1})
    resource.setrlimit(resource.RLIMIT_FSIZE, (admitted.limits.maximum_spool_bytes,) * 2)
    store = None
    try:
        try:
            store = _ReconciliationStore(admitted)
        except HistoryCollectionError as error:
            assert (
                fault in {"sql-journal", "sql-sort", "sql-attach"}
                and error.code == "RUNTIME_UNSUPPORTED"
            )
            result = error.code
        else:
            db = store._connection()
            if fault == "sql-policy":
                denied = 0
                for sql in (
                    "ATTACH ':memory:' AS extra",
                    "PRAGMA temp_store=FILE",
                    "CREATE TABLE rogue(x)",
                    "DROP TABLE raw_bodies",
                    "SELECT load_extension('forbidden')",
                    "VACUUM",
                ):
                    try:
                        db.execute(sql)
                    except sqlite3.DatabaseError:
                        denied += 1
                    else:
                        raise AssertionError("SQL policy ineffective")
                db.set_authorizer(None)  # Read-only test introspection after denial proof.
                names = (
                    "page_size",
                    "max_page_count",
                    "journal_mode",
                    "temp_store",
                    "mmap_size",
                    "cache_size",
                    "hard_heap_limit",
                    "synchronous",
                    "trusted_schema",
                    "threads",
                    "busy_timeout",
                )
                result = dict(
                    denied=denied,
                    settings={n: db.execute("PRAGMA " + n).fetchone()[0] for n in names},
                    attached_limit=db.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED),
                )
            elif fault == "sql-heap":
                db.set_authorizer(None)
                assert db.execute("PRAGMA hard_heap_limit").fetchone()[0] == 67108864
                try:
                    db.execute("PRAGMA hard_heap_limit=1").fetchone()
                except MemoryError:
                    pass
                db.set_authorizer(store._authorize)
                try:
                    store.reserve_page(0, None, None)
                except HistoryCollectionError as error:
                    assert error.code == "RESOURCE_LIMIT" and store._closed and store._poisoned
                    result = error.code
                else:
                    raise AssertionError("SQL heap ceiling ineffective")
            elif fault == "sql-file":
                try:
                    store.package_begin("page", 0, 2097152)
                except HistoryCollectionError as error:
                    assert error.code == "RESOURCE_LIMIT" and store._closed and store._poisoned
                    result = error.code
                else:
                    raise AssertionError("SQL file ceiling ineffective")
            else:
                raise AssertionError("unsupported SQL fixture")
    finally:
        if store is not None:
            store.close()
    sys.stdout.buffer.write(_json(result))
    return 0


def _controls(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    import shutil

    from easysynq_api.services.audit import _history_reconciliation_worker as worker

    outcomes = {}
    for fault in (
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
    ):
        directory = tempfile.mkdtemp(prefix="reconciliation-control-")
        process = None
        try:
            with subprocess.Popen(  # noqa: S603 - fixed installed test executable
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-u",
                    __file__,
                    "--fault-worker",
                    worker.__file__,
                    "/app/src",
                    fault,
                ],
                cwd=directory,
                env={"LANG": "C.UTF-8", "TZ": "UTC"},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            ) as process:
                try:
                    out, err = process.communicate(_json(config["scope"]), timeout=10)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)
                assert not err and len(out) <= 65536
                assert process.returncode == (-signal.SIGKILL if fault == "cpu" else 0)
                outcomes[fault] = json.loads(out) if fault.startswith("sql-") else "enforced"
            assert process.stdin.closed and process.stdout.closed and process.stderr.closed
            assert not Path(f"/proc/{process.pid}").exists()
        finally:
            shutil.rmtree(directory)
            assert not Path(directory).exists()
    return outcomes, len(outcomes)


def _runtime() -> dict[str, Any]:
    import easysynq_api
    from easysynq_api.services.audit import history_reconciliation

    root = Path(easysynq_api.__file__).resolve().parent
    assert root == Path("/app/src/easysynq_api")
    audit = Path(history_reconciliation.__file__).resolve().parent
    absent = [name for name in ("mypy", "pytest", "ruff") if importlib.util.find_spec(name) is None]
    return dict(
        python=sys.version.split()[0],
        sqlite=sqlite3.sqlite_version,
        uid=os.getuid(),
        module_root=str(root),
        installed_sources={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(audit.glob("*.py"))
        },
        dev_packages_absent=absent,
    )


def _args(config: dict[str, Any]) -> tuple[Any, ...]:
    from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
    from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation
    from easysynq_api.services.audit.history_collection import RequiredHistoryWitness
    from easysynq_api.services.audit.sink import ExplicitHistoryReader

    scope = protocol.decode_init({**config["scope"], "op": "INIT", "id": 1})
    readers = []
    for item in config["readers"]:
        assert set(item) == {
            "witness_id",
            "endpoint",
            "bucket",
            "region",
            "access_key",
            "secret_key",
        }
        readers.append(
            RequiredHistoryWitness(
                UUID(item["witness_id"]),
                ExplicitHistoryReader(
                    *(item[k] for k in ("endpoint", "bucket", "region", "access_key", "secret_key"))
                ),
            )
        )
    assert scope.limits.maximum_wall_seconds <= 60
    return (
        scope.enrollment,
        bytes.fromhex(config["root"]),
        tuple(BridgePageObservation(bytes.fromhex(p)) for p in config["pages"]),
        tuple(readers),
        scope.limits,
    )


def _outcome(report: Any) -> dict[str, Any]:
    usable = report.usable
    return dict(
        status=report.status,
        codes=sorted([i.component, i.code] for i in report.issues),
        required_checkpoint_relation=report.required_checkpoint_relation,
        path_length=0 if usable is None else usable.path_length,
        used_epoch_count=0 if usable is None else usable.used_epoch_count,
        tip=None if usable is None else usable.tip.anchor_hash,
    )


def _event(
    witness: str, method: str, key: str | None, version: str | None, raw: bytes
) -> list[str]:
    return [witness, method, key or "", version or "", hashlib.sha256(raw).hexdigest()]


@contextlib.contextmanager
def _transport(config: dict[str, Any], args: tuple[Any, ...]):
    from easysynq_api.services.audit import isolated_raw, isolated_version_page
    from easysynq_api.services.audit.raw_transport import RawCheckpointVersion
    from easysynq_api.services.audit.version_page import decode_checkpoint_version_page
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage

    list_call = isolated_version_page.read_raw_checkpoint_version_page_isolated
    get_call = isolated_raw.read_raw_checkpoint_version_isolated
    events = []
    buckets = {w.reader.bucket: str(w.witness_id) for w in args[3]}
    pages, pending = {}, {}
    genuine = config["case"].startswith("provider-")
    if not genuine:
        for page in config["provider"]:
            bucket = args[3][page["witness"]].reader.bucket
            pages[bucket, page["key_marker"], page["version_marker"]] = bytes.fromhex(page["xml"])
            parsed = decode_checkpoint_version_page(
                bytes.fromhex(page["xml"]),
                bucket=bucket,
                org_id=args[0].stream.org_id,
                key_marker=page["key_marker"],
                version_id_marker=page["version_marker"],
            )
            assert len(parsed.versions) == len(page["bodies"]) and not parsed.delete_markers
            for ref, raw in zip(parsed.versions, page["bodies"], strict=True):
                pending.setdefault((bucket, ref.key, ref.version_id), deque()).append(
                    bytes.fromhex(raw)
                )

    def listed(reader: Any, org: UUID, **kwargs: Any) -> Any:
        if genuine:
            result = list_call(reader, org, **kwargs)
        else:
            raw = pages.pop((reader.bucket, kwargs["key_marker"], kwargs["version_id_marker"]))
            result = RawCheckpointVersionPage(
                raw,
                decode_checkpoint_version_page(
                    raw,
                    bucket=reader.bucket,
                    org_id=org,
                    key_marker=kwargs["key_marker"],
                    version_id_marker=kwargs["version_id_marker"],
                ),
            )
        events.append(
            _event(
                buckets[reader.bucket],
                "LIST",
                kwargs["key_marker"],
                kwargs["version_id_marker"],
                result.body,
            )
        )
        return result

    def read(reader: Any, ref: Any, **kwargs: Any) -> Any:
        result = (
            get_call(reader, ref, **kwargs)
            if genuine
            else RawCheckpointVersion(
                ref.key, ref.version_id, pending[reader.bucket, ref.key, ref.version_id].popleft()
            )
        )
        events.append(_event(buckets[reader.bucket], "GET", ref.key, ref.version_id, result.body))
        return result

    isolated_version_page.read_raw_checkpoint_version_page_isolated = listed
    isolated_raw.read_raw_checkpoint_version_isolated = read
    try:
        yield events
    finally:
        isolated_version_page.read_raw_checkpoint_version_page_isolated = list_call
        isolated_raw.read_raw_checkpoint_version_isolated = get_call


@contextlib.contextmanager
def _observe(config: dict[str, Any], *, fault: str = ""):
    from easysynq_api.services.audit import _history_reconciliation_session as module
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit import history_collection as collection

    original, owner_init, launch = (
        module._ReconciliationSession,
        collection._CollectionOwner.__init__,
        subprocess.Popen,
    )
    records, owners = [], []
    forbidden = tuple(
        item[k].encode() for item in config["readers"] for k in ("access_key", "secret_key")
    )

    class Observed(original):
        fault_reached = False

        def __enter__(self):
            records.append(self)
            self.directory = None
            self.owned = None
            self.sampled = False
            self.observed_limits = None
            result = super().__enter__()
            self.scan()
            pairs = {
                name: resource.prlimit(self._process.pid, kind)
                for name, kind in [
                    ("address_space", resource.RLIMIT_AS),
                    ("cpu_seconds", resource.RLIMIT_CPU),
                    ("descriptors", resource.RLIMIT_NOFILE),
                    ("core_bytes", resource.RLIMIT_CORE),
                    ("file_bytes", resource.RLIMIT_FSIZE),
                ]
            }
            assert all(soft == hard for soft, hard in pairs.values())
            self.observed_limits = {name: pair[0] for name, pair in pairs.items()}
            return result

        def _cleanup(self):
            self.directory = self._directory or self.directory
            self.owned = self._process or self.owned
            result = super()._cleanup()
            if fault == "cleanup" and not self.fault_reached:
                from easysynq_api.services.audit.history_collection import HistoryCollectionError

                self.fault_reached = True
                result.append(HistoryCollectionError("CLEANUP_FAILED"))
            return result

        def scan(self):
            self.directory, self.owned = self._directory, self._process
            path = Path(self.directory)
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
            assert stat.S_IMODE((path / "spool.sqlite3").stat().st_mode) == 0o600
            assert sorted(p.name for p in path.iterdir()) == ["spool.sqlite3"]
            raw = (path / "spool.sqlite3").read_bytes()
            assert all(key not in raw for key in forbidden)
            for name in ("cmdline", "environ"):
                assert all(
                    key not in Path(f"/proc/{self.owned.pid}/{name}").read_bytes()
                    for key in forbidden
                )
            assert (
                Path(f"/proc/{self.owned.pid}/task/{self.owned.pid}/children").read_text().strip()
                == ""
            )
            for fd in Path(f"/proc/{self.owned.pid}/fd").iterdir():
                target = os.readlink(fd)
                assert not target.endswith(" (deleted)")
                info = fd.stat()
                if stat.S_ISREG(info.st_mode):
                    assert target == str(path / "spool.sqlite3")
            self.sampled = True

        def _send(self, body, deadline):
            assert all(key not in body for key in forbidden)
            return super()._send(body, deadline)

        def _send_upload(self, body, deadline, request_id):
            assert all(key not in body for key in forbidden)
            return super()._send_upload(body, deadline, request_id)

        def _rpc(self, op, *args, **kwargs):
            prior = self._progress_phase
            result = super()._rpc(op, *args, **kwargs)
            if op == "SEAL":
                self.scan()
            if not self.fault_reached and (
                (fault == "kill-seal" and op == "SEAL")
                or (fault == "kill-graph" and op == "STEP" and prior == 12)
                or (fault == "kill-final" and op == "FINISH_RECONCILIATION")
            ):
                self.fault_reached = True
                os.killpg(self._process.pid, signal.SIGKILL)
                self._process.wait(timeout=2)
            if not self.fault_reached and fault in {"cancel", "deadline"} and op == "SEAL":
                self.fault_reached = True
                if fault == "cancel":
                    self._cancel.set()
                else:
                    owners[0].deadline = time.monotonic() - 1
            return result

        def _receive(self, deadline):
            raw = super()._receive(deadline)
            value = wire.metadata(raw)
            if not self.fault_reached and fault == "stale" and "progress" in value:
                self.fault_reached = True
            if fault == "trailing" and "result" in value:
                self.fault_reached = True
            return raw

    def initialize(self, *args, **kwargs):
        owner_init(self, *args, **kwargs)
        owners.append(self)

    def launched(argv, *args, **kwargs):
        if (
            fault in {"kill-final", "trailing", "stale"}
            and Path(argv[4]).name == "_history_reconciliation_worker.py"
        ):
            argv = [*argv[:4], __file__, "--fault-worker", *argv[4:], fault]
        return launch(argv, *args, **kwargs)

    module._ReconciliationSession = Observed
    collection._CollectionOwner.__init__ = initialize
    subprocess.Popen = launched
    try:
        yield records
    finally:
        module._ReconciliationSession = original
        collection._CollectionOwner.__init__ = owner_init
        subprocess.Popen = launch
        assert all(not owner._thread.is_alive() for owner in owners)
        for s in records:
            assert s.owned.returncode is not None and not Path(f"/proc/{s.owned.pid}").exists()
            assert s.owned.stdin.closed and s.owned.stdout.closed
            assert s._selector is None and s._directory is None and not Path(s.directory).exists()
        for fd in Path("/proc/self/fd").iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            assert not any(s.directory in target for s in records)


def _kernel(config: dict[str, Any]) -> dict[str, Any]:
    raw = _json(
        {k: config[k] for k in ("scope", "root", "pages", "provider")}
        | {"kind": "global", "mutant": ""}
    )
    directory = tempfile.mkdtemp(prefix="reconciliation-kernel-")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-u",
                "/app/tests/unit/audit_history_reconciliation_kernel_worker.py",
                "/app/src",
            ],
            cwd=directory,
            env={"LANG": "C.UTF-8", "TZ": "UTC"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, err = process.communicate(len(raw).to_bytes(4, "big") + raw, timeout=45)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
        assert process.stdin.closed and process.stdout.closed and process.stderr.closed
        assert not Path(f"/proc/{process.pid}").exists()
        assert process.returncode == 0 and not err and len(out) <= 16 * 1024 * 1024 + 4
        assert int.from_bytes(out[:4], "big") == len(out) - 4
        value = json.loads(out[4:])
        return dict(
            path_digest=hashlib.sha256(_json(value["path"])).hexdigest(),
            epochs_digest=hashlib.sha256(_json(value["epochs"])).hexdigest(),
            counts=value["counts"],
            measurements=value["measurements"],
            plans=value["plans"],
        )
    finally:
        import shutil

        shutil.rmtree(directory)
        assert not Path(directory).exists()


def main() -> int:
    if sys.argv[1:2] == ["--fault-worker"]:
        return _fault_worker()
    assert len(sys.argv) == 2
    path = Path(sys.argv[1])
    assert path.stat().st_size <= 128 * 1024 * 1024
    config = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
    assert set(config) == {
        "case",
        "build_digest",
        "proof_digest",
        "scope",
        "root",
        "pages",
        "readers",
        "provider",
        "writable",
        "kernel",
    }
    assert config["case"] in {
        "provider-missing",
        "provider-consistent",
        "provider-conflict",
        "synthetic-consistent",
        "synthetic-conflict",
        "hostile",
    }
    assert type(config["kernel"]) is bool
    for identity in ("build_digest", "proof_digest"):
        value = config[identity]
        assert (
            type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
        )
    assert config["writable"] == "/run/audit-history-reconciliation-write"
    tempfile.tempdir = config["writable"]
    assert not list(Path(tempfile.tempdir).iterdir())
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("probe deadline")))
    signal.alarm(90)
    from easysynq_api.services.audit import history_reconciliation as public

    args = _args(config)
    observations = []
    extra_workers = 0
    if config["case"] == "hostile":
        outcomes = {}
        events = []
        for fault in (
            "kill-seal",
            "kill-graph",
            "kill-final",
            "stale",
            "trailing",
            "cancel",
            "deadline",
            "cleanup",
        ):
            with (
                _transport(config, args) as original_events,
                _observe(config, fault=fault) as workers,
            ):
                try:
                    public.collect_and_reconcile_checkpoint_history(*args)
                except public.HistoryReconciliationCancelled:
                    outcomes[fault] = "cancelled"
                except public.HistoryReconciliationError as error:
                    outcomes[fault] = error.code
                else:
                    raise AssertionError("hostile worker yielded usable report")
                assert workers[0].fault_reached
            observations.extend(workers)
            events.extend(original_events)
        controls, extra_workers = _controls(config)
        outcomes.update(controls)
    else:
        with _transport(config, args) as events, _observe(config) as workers:
            report = public.collect_and_reconcile_checkpoint_history(*args)
            outcomes = _outcome(report)
        observations.extend(workers)
    limits = observations[0].observed_limits | dict(
        command_seconds=10, frame_bytes=131072, result_bytes=65536
    )
    assert all(
        s.observed_limits == observations[0].observed_limits and s.sampled for s in observations
    )
    assert not list(Path(tempfile.tempdir).iterdir())
    counts = dict(provider=dict(list=0, get=0), synthetic=dict(list=0, get=0))
    key = "provider" if config["case"].startswith("provider-") else "synthetic"
    counts[key] = dict(
        list=sum(e[1] == "LIST" for e in events), get=sum(e[1] == "GET" for e in events)
    )
    receipt = dict(
        schema_version=1,
        case=config["case"],
        build_digest=config["build_digest"],
        proof_digest=config["proof_digest"],
        runtime=_runtime(),
        counts=counts,
        outcomes=outcomes,
        limits=limits,
        cleanup=dict(
            workers=len(observations) + extra_workers + int(config["kernel"]),
            reaped=True,
            pipes_closed=True,
            watchdog_joined=True,
            directories_removed=True,
            open_unlinked_absent=True,
            credentials_absent=True,
        ),
        transport_digest=hashlib.sha256(_json(sorted(events))).hexdigest(),
    )
    # Kernel detail is separate observed evidence in a local file; stdout stays a
    # single bounded receipt. The host archives and checks every path/epoch hash,
    # crypto count, actual SQLite plan and measured resource bound independently.
    if config["kernel"]:
        detail = _kernel(config)
        Path(tempfile.tempdir, "kernel.json").write_bytes(_json(detail))
    raw = _json(receipt)
    assert len(raw) <= 65536 and all(
        item[k].encode() not in raw
        for item in config["readers"]
        for k in ("access_key", "secret_key")
    )
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - bounded safe subprocess diagnosis
        import traceback

        frames = traceback.extract_tb(error.__traceback__)[-8:]
        sys.stderr.write(
            _json(
                dict(
                    error=type(error).__name__,
                    frames=[[Path(frame.filename).name, frame.lineno] for frame in frames],
                )
            ).decode()
            + "\n"
        )
        raise SystemExit(1) from None

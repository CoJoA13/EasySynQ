"""Independent public, disposable fixtures; no production codec builds expectations.

This producer starts from the reviewed feasibility formulas, replacing its SQL
shortcuts with original provider XML and exact version bytes. The transport here
is explicitly synthetic; genuine TLS/provider receipts belong to runtime proof.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import datetime
import hashlib
import json
import tempfile
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# Imports below construct caller records only. No codec/evaluator creates bytes,
# signatures, hashes, ordering, counters, paths or expected decisions.
from easysynq_api.services.audit.bootstrap_bridge import (
    BridgeEnrollment,
    BridgePageObservation,
    BridgeWitnessPin,
    LegacyPublicMaterial,
)
from easysynq_api.services.audit.history_collection import RequiredHistoryWitness
from easysynq_api.services.audit.history_reconciliation import HistoryReconciliationLimits
from easysynq_api.services.audit.lineage import AuditHead, BootstrapPin, StreamEnrollment
from easysynq_api.services.audit.sink import ExplicitHistoryReader

ORG = UUID("11111111-1111-4111-8111-111111111111")
STREAM = UUID("22222222-2222-4222-8222-222222222222")
PREFIX = f"checkpoints/{ORG}/"
HEAD = {"latest_id": "1234", "latest_row_hash": "ab" * 32}
SIGNATURE = b"EasySynQ/AuditCheckpoint/v2/signature\0"
ANCHOR_HASH = b"EasySynQ/AuditCheckpoint/v2/hash\0"
TRANSITION_PROOF = b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0"


def commitment(kind: str, raw: bytes) -> str:
    return hashlib.sha256(f"EasySynQ/AuditLegacyBridge/v1/{kind}\0".encode() + raw).hexdigest()


def private(label: str) -> Ed25519PrivateKey:
    """Deliberately public deterministic test material; never installation keys."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(label.encode()).digest())


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(raw: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def sign_v2(payload: dict[str, Any], epoch: int) -> bytes:
    signature = private(f"synthetic v2 key {epoch}").sign(SIGNATURE + rfc8785.dumps(payload))
    digest = hashlib.sha256(ANCHOR_HASH + rfc8785.dumps(payload) + signature).hexdigest()
    return rfc8785.dumps(
        {"checkpoint": payload, "signature": b64(signature), "anchor_hash": digest}
    )


@dataclasses.dataclass(frozen=True)
class Delivery:
    witness: UUID
    key: str
    version: str
    body: bytes


@dataclasses.dataclass(frozen=True)
class OriginalPage:
    body: bytes
    deliveries: tuple[Delivery, ...]
    next_key: str | None
    next_version: str | None


@dataclasses.dataclass(frozen=True)
class LargeCase:
    args: tuple[Any, ...]
    deliveries: tuple[Delivery, ...]
    list_map: dict[tuple[str, str | None, str | None], OriginalPage]
    body_map: dict[tuple[str, str, str], tuple[bytes, ...]]
    expected_path: tuple[str, ...]
    expected_used_epochs: tuple[int, ...]
    legacy_deliveries: int
    v2_deliveries: int
    page_deliveries: int
    inputs: dict[str, int]
    invalid_deliveries: int = 0

    @property
    def list_deliveries(self) -> int:
        return len(self.list_map)

    @property
    def body_deliveries(self) -> int:
        return len(self.deliveries)

    @property
    def total_bytes(self) -> int:
        return (
            len(self.args[1])
            + sum(len(p.body) for p in self.args[2])
            + sum(len(p.body) for p in self.list_map.values())
            + sum(len(d.body) for d in self.deliveries)
        )


def transport_maps(
    readers: tuple[RequiredHistoryWitness, ...], deliveries: tuple[Delivery, ...]
) -> tuple[dict[tuple[str, str | None, str | None], OriginalPage], dict[Any, tuple[bytes, ...]]]:
    pages: dict[tuple[str, str | None, str | None], OriginalPage] = {}
    bodies: dict[Any, list[bytes]] = {}
    for required in readers:
        bucket = required.reader.bucket
        records = tuple(d for d in deliveries if d.witness == required.witness_id)
        key_marker = version_marker = None
        for start in range(0, max(1, len(records)), 1000):
            batch = records[start : start + 1000]
            truncated = start + len(batch) < len(records)
            next_key = f"{PREFIX}cursor-{start + 1000}" if truncated else None
            next_version = f"cursor-version-{start + 1000}" if truncated else None
            entries = "".join(
                f"<Version><Key>{quote(d.key, safe='')}</Key>"
                f"<VersionId>{quote(d.version, safe='')}</VersionId>"
                "<IsLatest>false</IsLatest></Version>"
                for d in batch
            )
            raw = (
                '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Name>{bucket}</Name><Prefix>{quote(PREFIX, safe='')}</Prefix>"
                f"<KeyMarker>{quote(key_marker or '', safe='')}</KeyMarker>"
                f"<VersionIdMarker>{quote(version_marker or '', safe='')}</VersionIdMarker>"
                "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
                f"<IsTruncated>{str(truncated).lower()}</IsTruncated>"
                f"<NextKeyMarker>{quote(next_key or '', safe='')}</NextKeyMarker>"
                f"<NextVersionIdMarker>{quote(next_version or '', safe='')}</NextVersionIdMarker>"
                f"{entries}</ListVersionsResult>"
            ).encode()
            pages[bucket, key_marker, version_marker] = OriginalPage(
                raw, batch, next_key, next_version
            )
            for d in batch:
                bodies.setdefault((bucket, d.key, d.version), []).append(d.body)
            key_marker, version_marker = next_key, next_version
    return pages, {locator: tuple(raws) for locator, raws in bodies.items()}


def large_case(v2_nodes: int, legacy_bodies: int, witnesses: int) -> LargeCase:
    if (
        not 1 <= witnesses <= 4
        or not witnesses <= legacy_bodies <= 4609
        or not 1 <= v2_nodes <= 8193
    ):
        raise ValueError("fixture dimensions out of range")
    period = 2 if v2_nodes == 3 else 64
    inputs = dict(
        v2_nodes=v2_nodes,
        legacy_bodies=legacy_bodies,
        witnesses=witnesses,
        transition_period=period,
    )
    pins, readers, namespaces = [], [], []
    for index in range(witnesses):
        witness = UUID(int=0x33333333333343338333333333333333 + index)
        namespace = dict(
            kind="worm_bucket",
            endpoint="https://fixture.invalid",
            bucket=f"synthetic-witness-{index + 1}",
            region="fixture-region",
            prefix=PREFIX,
        )
        digest = commitment("namespace", rfc8785.dumps(namespace))
        pins.append(BridgeWitnessPin(witness, digest))
        namespaces.append(namespace)
        readers.append(
            RequiredHistoryWitness(
                witness,
                ExplicitHistoryReader(
                    namespace["endpoint"],
                    namespace["bucket"],
                    namespace["region"],
                    "fixture-access",
                    "fixture-secret",
                ),
            )
        )
    legacy_key = private("synthetic legacy signing fixture")
    retained = [private(f"synthetic wrong legacy key {i}") for i in range(7)] + [legacy_key]
    material = tuple(
        sorted(
            (LegacyPublicMaterial(key_id(public_bytes(k)), public_bytes(k)) for k in retained),
            key=lambda m: m.key_id,
        )
    )
    initial_raw = public_bytes(private("synthetic v2 key 0"))
    originals, entries, counts = [], [], [0] * witnesses
    for index in range(legacy_bodies):
        slot = min(witnesses - 1, index * witnesses // legacy_bodies)
        witness = pins[slot].witness_id
        counts[slot] += 1
        timestamp = (
            datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(seconds=index)
        ).isoformat()
        payload = dict(
            org_id=str(ORG),
            latest_id=1234,
            latest_row_hash=HEAD["latest_row_hash"],
            timestamp=timestamp,
        )
        body = rfc8785.dumps(
            dict(checkpoint=payload, signature=b64(legacy_key.sign(rfc8785.dumps(payload))))
        )
        # R78's retained grammar requires a numeric basename prefix and hyphen.
        key, version = f"{PREFIX}{index:08d}-legacy.json", f"version-{index:08d}"
        originals.append(Delivery(witness, key, version, body))
        entries.append(
            dict(
                witness_id=str(witness),
                object_key=key,
                version_id=version,
                body_hash=commitment("raw-body", body),
                body_bytes=str(len(body)),
            )
        )
    pages, refs = [], []
    for start in range(0, len(entries), 512):
        index = len(pages)
        chunk = entries[start : start + 512]
        raw = rfc8785.dumps(
            dict(
                format_version=1,
                kind="legacy_bridge_page",
                org_id=str(ORG),
                stream_id=str(STREAM),
                page_index=str(index),
                entries=chunk,
            )
        )
        pages.append(BridgePageObservation(raw))
        refs.append(
            dict(
                page_index=str(index),
                entry_count=str(len(chunk)),
                page_hash=commitment("page", raw),
            )
        )
    root = rfc8785.dumps(
        dict(
            format_version=1,
            kind="legacy_bridge",
            org_id=str(ORG),
            stream_id=str(STREAM),
            initial_key_id=key_id(initial_raw),
            initial_public_key=b64(initial_raw),
            initial_key_epoch="0",
            audit_boundary=HEAD,
            legacy_key_ids=[m.key_id for m in material],
            witnesses=[
                dict(
                    witness_id=str(p.witness_id),
                    namespace_hash=p.namespace_hash,
                    entry_count=str(count),
                    lowest_head=HEAD,
                    highest_head=HEAD,
                )
                for p, count in zip(pins, counts, strict=True)
            ],
            entry_count=str(legacy_bodies),
            pages=refs,
        )
    )
    bootstrap_hash = commitment("root", root)
    bootstrap = BootstrapPin(
        bootstrap_hash,
        key_id(initial_raw),
        initial_raw,
        0,
        AuditHead(1234, HEAD["latest_row_hash"]),
    )
    enrollment = BridgeEnrollment(
        StreamEnrollment(ORG, STREAM, bootstrap, None), tuple(pins), material
    )
    path, epochs, graph = [], set(), []
    previous, epoch = bootstrap_hash, 0
    for sequence in range(1, v2_nodes + 1):
        transition = sequence % period == 0
        epochs.add(epoch)
        payload = dict(
            format_version=2,
            kind="key_transition" if transition else "anchor",
            org_id=str(ORG),
            stream_id=str(STREAM),
            anchor_id=str(UUID(int=10000 + sequence)),
            sequence=str(sequence),
            previous_anchor_hash=previous,
            key_id=key_id(public_bytes(private(f"synthetic v2 key {epoch}"))),
            key_epoch=str(epoch),
            latest_id=HEAD["latest_id"],
            latest_row_hash=HEAD["latest_row_hash"],
            timestamp="2026-01-01T00:00:00.000000Z",
        )
        if transition:
            successor = private(f"synthetic v2 key {epoch + 1}")
            payload.update(
                next_key_id=key_id(public_bytes(successor)),
                next_public_key=b64(public_bytes(successor)),
                next_key_epoch=str(epoch + 1),
            )
            payload["next_key_signature"] = b64(
                successor.sign(TRANSITION_PROOF + rfc8785.dumps(payload))
            )
        body = sign_v2(payload, epoch)
        previous = json.loads(body)["anchor_hash"]
        path.append(previous)
        graph.append((f"{PREFIX}v2/{sequence:08d}", "v2-version", body))
        if transition:
            epoch += 1
    deliveries = []
    for pin in pins:
        legacy = [d for d in originals if d.witness == pin.witness_id]
        reverse = [Delivery(pin.witness_id, *item) for item in reversed(graph)]
        # Every chain dependency arrives backwards; repeats are nonadjacent and
        # appear after every original, beyond LIST seams for the large vectors.
        deliveries.extend(legacy + reverse + [legacy[0], reverse[0], reverse[-1]])
    limits = HistoryReconciliationLimits(
        128, 100_000, 16, 100_000, 128 * 1024 * 1024, 256 * 1024 * 1024, 300, 100_000, 32
    )
    args = enrollment, root, tuple(pages), tuple(readers), limits
    list_map, body_map = transport_maps(tuple(readers), tuple(deliveries))
    return LargeCase(
        args,
        tuple(deliveries),
        list_map,
        body_map,
        tuple(path),
        tuple(sorted(epochs)),
        legacy_bodies + witnesses,
        (v2_nodes + 2) * witnesses,
        len(pages),
        inputs,
    )


@contextlib.contextmanager
def synthetic_transport(
    case: LargeCase, monkeypatch: Any, directory: Path
) -> Iterator[dict[str, int]]:
    from easysynq_api.services.audit import isolated_raw, isolated_version_page
    from easysynq_api.services.audit.raw_transport import RawCheckpointVersion
    from easysynq_api.services.audit.sink import CheckpointVersionRef, CheckpointVersionsPage
    from easysynq_api.services.audit.version_page_transport import RawCheckpointVersionPage

    pending = {locator: deque(raws) for locator, raws in case.body_map.items()}
    expected_pages = dict(case.list_map)
    receipt = {"list": 0, "get": 0}

    def list_page(
        reader: Any,
        org: Any,
        *,
        key_marker: Any = None,
        version_id_marker: Any = None,
        cancel: Any = None,
    ) -> Any:
        assert org == case.args[0].stream.org_id
        page = expected_pages.pop((reader.bucket, key_marker, version_id_marker))
        receipt["list"] += 1
        return RawCheckpointVersionPage(
            page.body,
            CheckpointVersionsPage(
                tuple(CheckpointVersionRef(d.key, d.version) for d in page.deliveries),
                (),
                page.next_key is not None,
                page.next_key,
                page.next_version,
            ),
        )

    def read_body(reader: Any, ref: Any, *, cancel: Any = None) -> Any:
        receipt["get"] += 1
        return RawCheckpointVersion(
            ref.key, ref.version_id, pending[reader.bucket, ref.key, ref.version_id].popleft()
        )

    with monkeypatch.context() as patch:
        patch.setattr(tempfile, "tempdir", str(directory))
        patch.setattr(isolated_version_page, "read_raw_checkpoint_version_page_isolated", list_page)
        patch.setattr(isolated_raw, "read_raw_checkpoint_version_isolated", read_body)
        yield receipt
    # Resource/cancellation tests may intentionally stop before transport closes;
    # successful semantic tests separately require exact receipt counts.


def kernel_payload(case: LargeCase, *, mutant: str = "") -> dict[str, Any]:
    """Typed original XML and exact bodies; no SQL, verdict or authenticated state."""
    from easysynq_api.services.audit import _history_reconciliation_protocol as protocol
    from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness

    enrollment, root, pages, readers, limits = case.args
    scope = protocol._PublicScope(
        enrollment,
        tuple(
            _SpoolWitness(p.witness_id, p.namespace_hash, r.reader.bucket)
            for p, r in zip(enrollment.witnesses, readers, strict=True)
        ),
        limits,
        len(root),
        tuple(len(p.body) for p in pages),
    )
    return {
        "kind": "global",
        "mutant": mutant,
        "scope": protocol.scope_payload(scope),
        "root": root.hex(),
        "pages": [p.body.hex() for p in pages],
        "provider": [
            {
                "witness": index,
                "key_marker": key,
                "version_marker": version,
                "xml": page.body.hex(),
                "bodies": [d.body.hex() for d in page.deliveries],
            }
            for index, reader in enumerate(readers)
            for (bucket, key, version), page in case.list_map.items()
            if bucket == reader.reader.bucket
        ],
    }


def frozen_vector() -> dict[str, Any]:
    """Stable specimen generated only from primitives and literal expectations."""
    case = large_case(3, 5, 2)
    records = {hashlib.sha256(d.body).hexdigest(): d.body.hex() for d in case.deliveries}
    original = next(d.body for d in case.deliveries if "/v2/" in d.key)
    signature_bad = json.loads(original)
    signature_bad["signature"] = b64(bytes(64))
    discriminator_bad = json.loads(original)
    discriminator_bad["checkpoint"]["format_version"] = False
    enrollment = json.loads(
        json.dumps(
            dataclasses.asdict(case.args[0]),
            default=lambda value: value.hex() if isinstance(value, bytes) else str(value),
        )
    )
    return {
        "schema_version": 1,
        "provenance": (
            "Public disposable synthetic inputs; independent RFC8785/SHA256/Ed25519 producer; "
            "no production encoders"
        ),
        "generator_inputs": case.inputs,
        "generator_inputs_sha256": hashlib.sha256(rfc8785.dumps(case.inputs)).hexdigest(),
        "public_enrollment": enrollment,
        "root_hex": case.args[1].hex(),
        "page_hex": [p.body.hex() for p in case.args[2]],
        "raw_by_sha256": records,
        "delivery_order": [
            {
                "witness": str(d.witness),
                "key": d.key,
                "version": d.version,
                "raw_sha256": hashlib.sha256(d.body).hexdigest(),
            }
            for d in case.deliveries
        ],
        "expected": {
            "status": "consistent",
            "path": list(case.expected_path),
            "used_epochs": list(case.expected_used_epochs),
            "legacy_deliveries": case.legacy_deliveries,
            "v2_deliveries": case.v2_deliveries,
            "bridge_pages": case.page_deliveries,
            "list_pages": case.list_deliveries,
            "total_bytes": case.total_bytes,
        },
        "negative": [
            {
                "name": "invalid-signature",
                "body_hex": rfc8785.dumps(signature_bad).hex(),
                "status": "failed",
                "component": "lineage",
                "code": "ENVELOPE_INVALID",
            },
            {
                "name": "invalid-v2-discriminator",
                "body_hex": rfc8785.dumps(discriminator_bad).hex(),
                "status": "failed",
                "component": "lineage",
                "code": "ENVELOPE_INVALID",
            },
        ],
    }


def replace_deliveries(case: LargeCase, deliveries: tuple[Delivery, ...]) -> LargeCase:
    pages, bodies = transport_maps(case.args[3], deliveries)
    legacy = invalid = 0
    for d in deliveries:
        try:
            payload = json.loads(d.body)["checkpoint"]
        except (ValueError, KeyError, TypeError):
            invalid += 1
        else:
            legacy += "format_version" not in payload
    return dataclasses.replace(
        case,
        deliveries=deliveries,
        list_map=pages,
        body_map=bodies,
        legacy_deliveries=legacy,
        v2_deliveries=len(deliveries) - legacy - invalid,
        invalid_deliveries=invalid,
    )


def rebind_package(
    case: LargeCase, pages: tuple[BridgePageObservation, ...], root: dict[str, Any]
) -> LargeCase:
    """Independently rebind changed public content and resign linked original v2 bytes."""
    root["pages"] = [
        dict(
            page_index=str(i),
            entry_count=str(len(json.loads(p.body)["entries"])),
            page_hash=commitment("page", p.body),
        )
        for i, p in enumerate(pages)
    ]
    raw = rfc8785.dumps(root)
    pin = dataclasses.replace(
        case.args[0].stream.bootstrap, commitment_hash=commitment("root", raw)
    )
    enrollment = dataclasses.replace(
        case.args[0], stream=dataclasses.replace(case.args[0].stream, bootstrap=pin)
    )
    originals = {
        int(json.loads(d.body)["checkpoint"]["sequence"]): d.body
        for d in case.deliveries
        if "/v2/" in d.key
    }
    replacements, path = {}, []
    previous = pin.commitment_hash
    for _sequence, original in sorted(originals.items()):
        payload = json.loads(original)["checkpoint"]
        payload["previous_anchor_hash"] = previous
        if payload["kind"] == "key_transition":
            del payload["next_key_signature"]
            payload["next_key_signature"] = b64(
                private(f"synthetic v2 key {payload['next_key_epoch']}").sign(
                    TRANSITION_PROOF + rfc8785.dumps(payload)
                )
            )
        body = sign_v2(payload, int(payload["key_epoch"]))
        previous = json.loads(body)["anchor_hash"]
        path.append(previous)
        replacements[original] = body
    changed = dataclasses.replace(
        case,
        args=(enrollment, raw, pages, *case.args[3:]),
        expected_path=tuple(path),
        page_deliveries=len(pages),
    )
    return replace_deliveries(
        changed,
        tuple(
            dataclasses.replace(d, body=replacements.get(d.body, d.body)) for d in case.deliveries
        ),
    )


def late_case(case: LargeCase, mutation: str) -> LargeCase:
    """Put contradictory original evidence beyond the old 4096-observation prefix."""
    from easysynq_api.services.audit.lineage import RequiredCheckpointPin

    records = list(case.deliveries)
    if mutation in {"page-order", "manifest-duplicate"}:
        pages = list(case.args[2])
        index = len(pages) - 2 if mutation == "page-order" else len(pages) - 1
        doc = json.loads(pages[index].body)
        if mutation == "page-order":
            doc["entries"][0], doc["entries"][1] = doc["entries"][1], doc["entries"][0]
        else:
            doc["entries"][-1] = json.loads(pages[0].body)["entries"][0]
        pages[index] = BridgePageObservation(rfc8785.dumps(doc))
        changed = rebind_package(case, tuple(pages), json.loads(case.args[1]))
    elif mutation == "missing-witness-copy":
        absent = f"{PREFIX}v2/00002048"
        witness = case.args[0].witnesses[-1].witness_id
        assert (
            next(i for i, d in enumerate(records, 1) if d.key == absent and d.witness == witness)
            > 4096
        )
        changed = replace_deliveries(
            case, tuple(d for d in records if (d.witness, d.key) != (witness, absent))
        )
    else:
        original = next(d for d in records if "/v2/00004097" in d.key)
        payload = json.loads(original.body)["checkpoint"]
        witness = case.args[0].witnesses[-1].witness_id
        if mutation == "raw-locator-variant":
            extra = dataclasses.replace(original, witness=witness, body=b" " + original.body)
        elif mutation == "legacy-head-conflict":
            payload = dict(
                org_id=str(ORG),
                latest_id=1234,
                latest_row_hash="cd" * 32,
                timestamp="2026-01-01T00:00:00+00:00",
            )
            body = rfc8785.dumps(
                dict(
                    checkpoint=payload,
                    signature=b64(
                        private("synthetic legacy signing fixture").sign(rfc8785.dumps(payload))
                    ),
                )
            )
            extra = Delivery(witness, f"{PREFIX}99999999-late.json", "late-version", body)
        else:
            assert mutation in {"fork", "anchor-id-reuse", "cross-format-conflict"}
            payload["anchor_id"] = str(UUID(int=10001 if mutation == "anchor-id-reuse" else 90000))
            if mutation == "cross-format-conflict":
                payload["latest_row_hash"] = "cd" * 32
            extra = Delivery(
                witness,
                f"{PREFIX}late-v2",
                "late-version",
                sign_v2(payload, int(payload["key_epoch"])),
            )
        records.append(extra)
        assert len(records) > 4096
        changed = replace_deliveries(case, tuple(records))
    # An early checkpoint is already included; later global failure must still win.
    enrollment = dataclasses.replace(
        changed.args[0],
        stream=dataclasses.replace(
            changed.args[0].stream,
            required_checkpoint=RequiredCheckpointPin(changed.expected_path[0], 1),
        ),
    )
    return dataclasses.replace(changed, args=(enrollment, *changed.args[1:]))

"""Exact identities and complete diagnostics against actual isolated SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_audit_history_reconciliation_worker import _init, _store_process

pytestmark = pytest.mark.unit

# Typed original observations are seeded only by this finite test-owned program.
# They are neither authenticated nor marked as a complete manifest/graph.
_SEED = """
from easysynq_api.services.audit import _history_reconciliation_store as module
def seed(values):
    db = store._connection()
    for ordinal,(key,body) in enumerate(values,1):
        db.execute("INSERT INTO observations(ordinal,page,witness,key,version,"
                   "kind,outcome,body,digest) "
                   "VALUES(?,1,0,?,?,'version','body',?,?)",
                   (ordinal,key.encode(),b"v",body,__import__("hashlib").sha256(body).digest()))
    db.execute("INSERT INTO pages(id,witness,page_index,cursor_key,cursor_version,state) "
               "VALUES(1,0,0,?,?,'admitted')", (b"\\0",b"\\0"))
    db.execute("UPDATE witnesses SET terminal=1 WHERE id=0")
    store._ordinal = len(values)
    store._total = sum(len(body) for _,body in values)
    store.seal(len(values))
"""


def _seeded_store_process(tmp_path: Path, program: str, init: dict | None = None) -> dict:
    return _store_process(tmp_path, _SEED + program, init)


def test_raw_hash_collisions_never_collapse_unequal_original_bytes(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
try:
    module._raw_digest = lambda body: b"x"*32
    seed([("z",b"first"),("y",b"other"),("a",b"first")])
    steps = 0
    while not store.index_bodies_step():
        steps += 1
        assert steps < 20
    db=store._connection()
    links = db.execute("SELECT ordinal,raw_id FROM body_deliveries ORDER BY ordinal").fetchall()
    reps = db.execute("SELECT representative_ordinal FROM raw_bodies ORDER BY raw_id").fetchall()
    formats = db.execute("SELECT format,decoded_state FROM raw_bodies").fetchall()
    result = {"links":links,"reps":reps,"formats":formats}
finally: store.close()
print(json.dumps(result))
""",
    )
    a, b, c = result["links"]
    assert [a[0], b[0], c[0]] == [1, 2, 3]
    assert a[1] == c[1] != b[1]
    assert result["reps"] == [[3], [2]]
    assert result["formats"] == [["invalid", None], ["invalid", None]]


def test_long_collision_bucket_resumes_without_a_guessed_identity(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
try:
    module._raw_digest = lambda body: b"x"*32
    values = [(f"key-{i:04}",i.to_bytes(2,"big")) for i in range(130)]
    values.extend([("duplicate-last",values[-1][1]),("duplicate-first",values[0][1])])
    seed(values)
    steps=0
    prior=0
    done=False
    while not done:
        done=store.index_bodies_step()
        work=store._identity_work-prior
        assert 0 <= work <= 64
        assert done or work > 0
        prior=store._identity_work
        steps+=1
        assert steps<1000
    db=store._connection()
    links=db.execute("SELECT raw_id FROM body_deliveries ORDER BY ordinal").fetchall()
    assert len(set(links[:130]))==130
    assert links[130]==links[129] and links[131]==links[0]
    assert store.index_bodies_step() is True
    assert store._identity_work==prior
    result={"count":len(links),"steps":steps,"work":prior}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result["count"] == 132
    assert result["steps"] > 130
    assert result["work"] > 130 * 64


def test_representative_uses_exact_domain_order_and_preserves_locator_bytes(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
try:
    seed([("é",b"same"),("e\\u0301",b"same"),("z",b"same"),("e\\u0301",b"same")])
    while not store.index_bodies_step(): pass
    db=store._connection()
    result={"count":db.execute("SELECT count(*) FROM raw_bodies").fetchone()[0],
            "representative":db.execute(
                "SELECT representative_ordinal FROM raw_bodies").fetchone()[0],
            "keys":[row[0].hex() for row in db.execute(
                "SELECT key FROM observations ORDER BY ordinal")]}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result == {
        "count": 1,
        "representative": 2,
        "keys": [s.encode().hex() for s in ("é", "e\u0301", "z", "e\u0301")],
    }


def test_issue_display_cap_preserves_complete_group_counts(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
import dataclasses
try:
    seed([])
    issues=_IssueIndex(store)
    for number in range(40):
        issues.add("composition","failed","CHECKPOINT_BODY_INVALID",(f"{number:064x}",),())
    issues.add("lineage","incomplete","EMPTY_GRAPH",(),())
    result={"counts":issues.counts(),"display":[dataclasses.asdict(i) for i in issues.display(1)]}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result["counts"] == [40, 1]
    assert result["display"] == [
        {
            "component": "composition",
            "code": "CHECKPOINT_BODY_INVALID",
            "severity": "failed",
            "witness_id": None,
            "count": 1,
            "references": [],
        }
    ]


def test_duplicate_reference_offers_cannot_crowd_out_a_contradiction(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex, _IssueRef
import dataclasses
try:
    seed([(str(i),b"original") for i in range(8)])
    issues=_IssueIndex(store)
    subject=("opaque-conflict",)
    for ordinal in (3,2,1):
        issues.add("lineage","failed","IMMUTABLE_LOCATOR_CONFLICT",subject,
                   (_IssueRef("observation",ordinal,("a","k","v","d"),b"first"),))
    issues.add("lineage","failed","IMMUTABLE_LOCATOR_CONFLICT",subject,
               (_IssueRef("observation",4,("b","k","v","d"),b"second"),))
    first=[r.index for r in issues.display(1)[0].references]
    issues.add("lineage","failed","IMMUTABLE_LOCATOR_CONFLICT",subject,
               (_IssueRef("observation",7,("0","k","v","d"),b"second"),))
    result={"first":first,"second":[r.index for r in issues.display(1)[0].references],
            "count":issues.display(1)[0].count,"groups":issues.counts()}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result == {"first": [1, 4], "second": [7, 1], "count": 1, "groups": [1, 0]}


def test_new_issue_group_overflow_is_terminal_but_updates_use_no_capacity(tmp_path: Path) -> None:
    init = _init()
    init["limits"]["maximum_issue_groups"] = 1
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
try:
    seed([])
    issues=_IssueIndex(store)
    issues.add("composition","failed","CHECKPOINT_BODY_INVALID",("one",),())
    issues.add("composition","failed","CHECKPOINT_BODY_INVALID",("one",),())
    assert issues.counts()==(1,0)
    try: issues.add("composition","failed","CHECKPOINT_BODY_INVALID",("two",),())
    except HistoryCollectionError as error: code=error.code
    else: raise AssertionError("group ceiling was not enforced")
    try: issues.counts()
    except HistoryCollectionError: unusable=True
    else: unusable=False
    result={"code":code,"closed":store._closed,"unusable":unusable}
finally: store.close()
print(json.dumps(result))
""",
        init,
    )
    assert result == {"code": "RESOURCE_LIMIT", "closed": True, "unusable": True}


def test_query_plans_use_ordinal_identity_and_issue_indexes(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
try:
    seed([("a",b"x")])
    while not store.index_bodies_step(): pass
    _IssueIndex(store).add("lineage","incomplete","EMPTY_GRAPH",(),())
    db=store._connection()
    plans={}
    plans["ordinal"]=db.execute("EXPLAIN QUERY PLAN SELECT ordinal FROM observations "
        "WHERE ordinal>? AND body IS NOT NULL ORDER BY ordinal LIMIT 64",(0,)).fetchall()
    plans["identity"]=db.execute("EXPLAIN QUERY PLAN SELECT r.raw_id,o.body FROM raw_bodies r "
        "JOIN observations o ON o.ordinal=r.representative_ordinal "
        "WHERE r.digest=? AND r.byte_length=? AND r.raw_id>? ORDER BY r.raw_id LIMIT 1",
        (b"x"*32,1,0)).fetchall()
    plans["issues"]=db.execute("EXPLAIN QUERY PLAN SELECT count FROM reconciliation_issues "
        "WHERE component=? AND severity=? AND code=? AND s0=? AND s1=? AND s2=? AND s3=?",
        ("lineage","incomplete","EMPTY_GRAPH","","","","")).fetchall()
    plans["display"]=db.execute("EXPLAIN QUERY PLAN SELECT component,severity,code "
        "FROM reconciliation_issues "
        "ORDER BY severity,component,code,s0,s1,s2,s3 LIMIT 1").fetchall()
finally: store.close()
print(json.dumps(plans))
""",
    )
    descriptions = {key: " ".join(row[3] for row in rows) for key, rows in result.items()}
    assert "INTEGER PRIMARY KEY" in descriptions["ordinal"]
    assert "raw_lookup" in descriptions["identity"]
    assert "INTEGER PRIMARY KEY" in descriptions["identity"]
    assert "sqlite_autoindex_reconciliation_issues" in descriptions["issues"]
    assert "issue_display" in descriptions["display"]
    assert all("TEMP B-TREE" not in description for description in descriptions.values())


def test_complete_severity_component_and_padded_numeric_order(tmp_path: Path) -> None:
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex,_IssueRef
try:
    seed([(str(i),b"x") for i in range(16)])
    issues=_IssueIndex(store)
    for number in (10,2):
        issues.add("bridge","failed","SIGNED_HEAD_CONFLICT",(f"{number:019d}",),
            (_IssueRef("observation",number,("a","b","c",str(number)),b"head"),))
    issues.add("lineage","failed","ENVELOPE_INVALID",("body",),())
    issues.add("bridge","incomplete","PAGE_MISSING",("0000",),())
    result={"counts":issues.counts(),"display":[[i.severity,i.component,i.code,
        [ref.index for ref in i.references]] for i in issues.display(8)]}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result == {
        "counts": [3, 1],
        "display": [
            ["failed", "bridge", "SIGNED_HEAD_CONFLICT", [2]],
            ["failed", "bridge", "SIGNED_HEAD_CONFLICT", [10]],
            ["failed", "lineage", "ENVELOPE_INVALID", []],
            ["incomplete", "bridge", "PAGE_MISSING", []],
        ],
    }


@pytest.mark.parametrize(
    "component,code,maximum",
    [
        ("bridge", "PAGE_CONFLICT", 2),
        ("bridge", "PAGE_INVALID", 1),
        ("bridge", "SIGNED_HEAD_CONFLICT", 2),
        ("bridge", "LEGACY_BODY_INVALID", 1),
        ("lineage", "LINEAGE_FORK", 2),
        ("lineage", "ENVELOPE_INVALID", 1),
        ("lineage", "AUDIT_HEAD_CONFLICT", 1),
        ("composition", "GLOBAL_SIGNED_HEAD_CONFLICT", 2),
    ],
)
def test_component_reference_cardinality_preserves_reference_rules(
    tmp_path: Path, component: str, code: str, maximum: int
) -> None:
    program = """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex,_IssueRef
try:
    seed([("a",b"x"),("b",b"y"),("c",b"z")])
    issues=_IssueIndex(store)
    for ordinal in (3,1,2):
        issues.add(COMPONENT,"failed",CODE,(),(_IssueRef("observation",ordinal,
                   (str(ordinal),"","",""),bytes([ordinal])),))
    result={"refs":[r.index for r in issues.display(1)[0].references]}
finally: store.close()
print(json.dumps(result))
""".replace("COMPONENT", repr(component)).replace("CODE", repr(code))
    assert _seeded_store_process(tmp_path, program) == {"refs": list(range(1, maximum + 1))}


def test_index_instances_share_capacity_and_preserve_collection_counts(tmp_path: Path) -> None:
    init = _init()
    init["limits"]["maximum_issue_groups"] = 1
    result = _seeded_store_process(
        tmp_path,
        """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
try:
    seed([])
    witness=str(store.scope.witnesses[0].witness_id)
    first,second=_IssueIndex(store),_IssueIndex(store)
    first.add("collection","incomplete","VERSION_UNAVAILABLE",(witness,),(),count=13)
    second.add("collection","incomplete","VERSION_UNAVAILABLE",(witness,),(),count=1)
    item=second.display(1)[0]
    assert str(item.witness_id)==witness
    result={"count":item.count,"groups":second.counts(),"private_keys":list(item.__slots__)}
finally: store.close()
print(json.dumps(result))
""",
        init,
    )
    assert result == {
        "count": 13,
        "groups": [0, 1],
        "private_keys": ["component", "code", "severity", "witness_id", "count", "references"],
    }


@pytest.mark.parametrize("operation", ["index", "add", "counts", "display"])
def test_index_operations_reject_unsealed_evidence_and_poison_store(
    tmp_path: Path, operation: str
) -> None:
    program = """
from easysynq_api.services.audit._history_reconciliation_issues import _IssueIndex
try:
    issues=_IssueIndex(store)
    operations={"index":store.index_bodies_step,
                "add":lambda:issues.add("lineage","incomplete","EMPTY_GRAPH",(),()),
                "counts":issues.counts,"display":lambda:issues.display(1)}
    try: operations[OPERATION]()
    except HistoryCollectionError as error: code=error.code
    else: raise AssertionError("unsealed evidence was indexed")
    result={"code":code,"closed":store._closed}
finally: store.close()
print(json.dumps(result))
""".replace("OPERATION", repr(operation))
    assert _store_process(tmp_path, program) == {"code": "PROTOCOL_INVALID", "closed": True}

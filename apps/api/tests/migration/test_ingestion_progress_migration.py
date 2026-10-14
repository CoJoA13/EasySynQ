"""Populated, disposable prior-head / progress-column / prior-head / head proof."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from easysynq_api.config import get_settings
from easysynq_api.readiness import MIGRATIONS_DIR

_PRIOR = "0094_backup_schedule_attempt"
_REVISION = "0095_ingest_batch_progress"
_HISTORIC = datetime(2026, 1, 15, 12, tzinfo=UTC)
_TABLES = (
    "organization",
    "app_user",
    "import_run",
    "import_file",
    "import_extract",
    "import_classification",
    "import_dupe_cluster",
    "import_version_family",
    "import_proposal_node",
    "audit_event",
)


def _snapshot(conn, org):
    snapshots = {}
    for table in _TABLES:
        predicate = "id=:org" if table == "organization" else "org_id=:org"
        # JSON preserves every seeded business field while intentionally excluding only
        # the operational column that this one-revision downgrade is supposed to lose.
        snapshots[table] = list(
            conn.execute(
                sa.text(
                    f"SELECT to_jsonb(t) - 'last_batch_progress_at' FROM {table} t "  # noqa: S608 - fixed tables
                    f"WHERE {predicate} ORDER BY id"
                ),
                {"org": org},
            ).scalars()
        )
    return snapshots


def _seed(conn, org, user):
    conn.execute(
        sa.text(
            "INSERT INTO organization(id,legal_name,short_code) "
            "VALUES (:org,'Synthetic progress','SYN-PROGRESS')"
        ),
        {"org": org},
    )
    conn.execute(
        sa.text(
            "INSERT INTO app_user(id,org_id,keycloak_subject,status,"
            "mfa_enrolled,is_guest,color_scheme) "
            "VALUES (:user,:org,'synthetic-progress-user','ACTIVE',false,false,'AUTO')"
        ),
        {"user": user, "org": org},
    )
    conn.execute(sa.text("SELECT easysynq_create_audit_partition(DATE '2026-01-01')"))
    run_ids = []
    for status in ("Deduping", "Failed"):
        rid, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        run_ids.append(rid)
        params = {
            "rid": rid,
            "org": org,
            "user": user,
            "status": status,
            "source_hash": status,
            "completed_at": _HISTORIC if status == "Failed" else None,
            "run_error": "synthetic failure" if status == "Failed" else None,
            "at": _HISTORIC,
            "first": first,
            "second": second,
        }
        conn.execute(
            sa.text(
                "INSERT INTO import_run(id,org_id,source_root,source_root_hash,status,lock_token,"
                "created_by,created_at,scan_started_at,completed_at,counts,error,"
                "classifier_version) "
                "VALUES (:rid,:org,'/synthetic/source',CAST(:source_hash AS text),"
                "CAST(:status AS import_run_status),'synthetic-token',:user,:at,:at,"
                "CAST(:completed_at AS timestamptz),'{\"total_files\": 2}',"
                "CAST(:run_error AS text),'synthetic-v1')"
            ),
            params,
        )
        for fid, path in ((first, "first.txt"), (second, "second.txt")):
            conn.execute(
                sa.text(
                    "INSERT INTO import_file(id,org_id,run_id,rel_path,filename,size_bytes,"
                    "included_candidate,scan_flags,created_at) "
                    'VALUES (:fid,:org,:rid,:path,:path,42,true,\'{"disposition":"included"}\',:at)'
                ),
                {**params, "fid": fid, "path": path},
            )
        conn.execute(
            sa.text(
                "INSERT INTO import_extract(id,org_id,run_id,file_id,status,full_text,created_at) "
                "VALUES (:id,:org,:rid,:first,'extracted','synthetic extracted text',:at)"
            ),
            {**params, "id": uuid.uuid4()},
        )
        conn.execute(
            sa.text(
                "INSERT INTO import_classification(id,org_id,run_id,file_id,classifier_version,"
                "kind,kind_conf,type_conf,clause_conf,process_conf,band,ambiguous,"
                "top2_margin,created_at) "
                "VALUES (:id,:org,:rid,:first,'synthetic-v1','UNKNOWN',0,0,0,0,'LOW',true,0,:at)"
            ),
            {**params, "id": uuid.uuid4()},
        )
        conn.execute(
            sa.text(
                "INSERT INTO import_dupe_cluster(id,org_id,run_id,method,member_file_ids,"
                "canonical_file_id,jaccard,created_at) "
                "VALUES (:id,:org,:rid,'exact',"
                "ARRAY[CAST(:first AS uuid),CAST(:second AS uuid)],:first,1,:at)"
            ),
            {**params, "id": uuid.uuid4()},
        )
        conn.execute(
            sa.text(
                "INSERT INTO import_version_family(id,org_id,run_id,family_key,base_name,"
                "ordered_member_file_ids,effective_file_id,created_at) "
                "VALUES (:id,:org,:rid,'synthetic-family','synthetic family',"
                "ARRAY[CAST(:first AS uuid),CAST(:second AS uuid)],:first,:at)"
            ),
            {**params, "id": uuid.uuid4()},
        )
        conn.execute(
            sa.text(
                "INSERT INTO import_proposal_node(id,org_id,run_id,file_id,"
                "target_ia_path,created_at) "
                "VALUES (:id,:org,:rid,:first,'_unmapped',:at)"
            ),
            {**params, "id": uuid.uuid4()},
        )
        conn.execute(
            sa.text(
                "INSERT INTO audit_event(org_id,occurred_at,actor_type,event_type,"
                'object_type,object_id,"after") '
                "VALUES (:org,:at,'system','IMPORT_RUN_STAGE_CHANGED','import_run',:rid,"
                "'{\"synthetic\": true}')"
            ),
            params,
        )
    return run_ids


def test_populated_ingestion_progress_roundtrip(migration_database_factory, monkeypatch):
    with migration_database_factory() as url:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_URL_SYNC", url)
        get_settings.cache_clear()
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        engine = sa.create_engine(url)
        org, user = uuid.uuid4(), uuid.uuid4()
        try:
            command.upgrade(config, _PRIOR)
            with engine.begin() as conn:
                run_ids = _seed(conn, org, user)
                before = _snapshot(conn, org)
                columns_before = {c["name"] for c in sa.inspect(conn).get_columns("import_run")}
                indexes_before = sa.inspect(conn).get_indexes("import_run")
            command.upgrade(config, _REVISION)
            with engine.begin() as conn:
                columns = {c["name"]: c for c in sa.inspect(conn).get_columns("import_run")}
                assert set(columns) == columns_before | {"last_batch_progress_at"}
                column = columns["last_batch_progress_at"]
                assert column["nullable"] is True and column["default"] is None
                assert isinstance(column["type"], sa.DateTime) and column["type"].timezone
                assert sa.inspect(conn).get_indexes("import_run") == indexes_before
                assert _snapshot(conn, org) == before
                assert conn.execute(
                    sa.text("SELECT last_batch_progress_at FROM import_run WHERE org_id=:org"),
                    {"org": org},
                ).scalars().all() == [None, None]
                # Exercise the real least-privilege grant as a non-owner, including UPDATE.
                conn.execute(sa.text("SET LOCAL ROLE easysynq_app"))
                assert conn.scalar(sa.text("SELECT current_user")) == "easysynq_app"
                conn.execute(
                    sa.text("UPDATE import_run SET last_batch_progress_at=:at WHERE org_id=:org"),
                    {"org": org, "at": _HISTORIC},
                )
                assert conn.execute(
                    sa.text("SELECT last_batch_progress_at FROM import_run WHERE org_id=:org"),
                    {"org": org},
                ).scalars().all() == [_HISTORIC, _HISTORIC]
            command.downgrade(config, _PRIOR)
            with engine.connect() as conn:
                assert {
                    c["name"] for c in sa.inspect(conn).get_columns("import_run")
                } == columns_before
                assert _snapshot(conn, org) == before
                assert len(before["import_run"]) == len(run_ids) == 2
            command.upgrade(config, _REVISION)
            with engine.connect() as conn:
                assert _snapshot(conn, org) == before
                assert conn.execute(
                    sa.text("SELECT last_batch_progress_at FROM import_run WHERE org_id=:org"),
                    {"org": org},
                ).scalars().all() == [None, None]
            command.check(config)
        finally:
            engine.dispose()
            get_settings.cache_clear()

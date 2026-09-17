import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from meldstore import (
    Catalog,
    ConflictError,
    ConstraintError,
    LocalStorage,
    Store,
    catalog_access,
    restore_backup,
)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_generic_consumer_has_sql_relationships_and_verified_retrieval(tmp_path, adapter):
    from examples.dataset_catalog import exercise

    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        assert exercise(store, tmp_path) == {
            "selected": ["document"], "retrieved": b"example document\n", "version": 2,
        }


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_generic_consumer_explicitly_removes_links_before_retirement(tmp_path, adapter):
    from examples.dataset_catalog import exercise

    db = tmp_path / "catalog.db"
    storage = LocalStorage(tmp_path / "objects")
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, storage)
        exercise(store, tmp_path)
        with pytest.raises(ConstraintError):
            store.delete("document", expected_version=2)
        with catalog.transaction() as tx:
            tx.sql("DELETE FROM collection_members WHERE blob_id=?", ("document",))
            store.delete("document", expected_version=2, tx=tx)
        assert store.deletion_status("document")["state"] == "pending"
        assert catalog.sql("SELECT * FROM collection_members", write=False) == []
    with Catalog(db, adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, storage)
        assert store.cleanup()[0]["state"] == "done"
        assert store.deletion_status("document")["state"] == "done"


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_cuas_correlations_select_whole_files_once(tmp_path, adapter):
    pytest.importorskip("pyarrow")
    from examples.cuas_catalog import exercise

    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        assert exercise(store, tmp_path) == {
            "correlations": 2, "recordings": ["radar", "truth"], "materializations": 2,
        }


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_cuas_sql_constraints_edits_half_open_queries_and_backup(tmp_path, adapter):
    pytest.importorskip("pyarrow")
    from examples.cuas_catalog import CORRELATIONS, RECORDING, fixture, instant

    db = tmp_path / "catalog.db"
    storage = LocalStorage(tmp_path / "objects")
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, storage)
        _, end = fixture(store, tmp_path)
        original = store.stat("radar")
        edited = store.update_metadata("radar", schema=RECORDING, changes={"annotation": "reviewed"}, expected_version=1)
        assert edited["digest"] == original["digest"]
        with pytest.raises(ConflictError):
            store.update_metadata("radar", schema=RECORDING, changes={"annotation": "stale"}, expected_version=1)
        with pytest.raises(ConstraintError):
            store.delete("radar", expected_version=2)
        with pytest.raises(ConstraintError):
            catalog.sql("UPDATE c_sensor_config SET details='changed'")
        with pytest.raises(ConstraintError):
            catalog.sql("INSERT INTO c_correlation VALUES('run','truth','truth-track','radar','radar-track-a','wrong')")
        with pytest.raises(ConstraintError):
            catalog.sql("INSERT INTO c_occurrence VALUES('truth','extra',?,?)",
                        (instant(end), instant(end + timedelta(seconds=1))))
        rows = catalog.sql(CORRELATIONS, ("run",), write=False)
        assert len(rows) == 2
        assert {row["aircraft_id"] for row in rows} == {"synthetic-aircraft"}
        with catalog_access(db), closing(sqlite3.connect(db)) as plain:
            assert len(plain.execute(CORRELATIONS, ("run",)).fetchall()) == 2
            assert plain.execute("PRAGMA foreign_key_check").fetchall() == []
        catalog.sql("UPDATE c_test_run SET start_time=?,end_time=? WHERE id='run'",
                    (instant(end), instant(end + timedelta(seconds=10))))
        assert catalog.sql(CORRELATIONS, ("run",), write=False) == []
    with Catalog(db, adapter=adapter, maintenance=True) as catalog:
        Store(catalog, storage).backup(tmp_path / "backup", application={"id": "fixture", "schema_revision": "1"})
    restored = restore_backup(tmp_path / "backup", tmp_path / "restored")
    with Catalog(restored["catalog"], adapter=adapter) as catalog:
        assert catalog.sql("SELECT count(*) AS n FROM c_occurrence", write=False) == [{"n": 3}]
        copy = Store(catalog, LocalStorage(restored["storage"]))
        assert copy.stat("radar")["metadata"]["annotation"] == "reviewed"
        with copy.materialize("truth") as path:
            assert path.read_bytes() == (tmp_path / "truth.parquet").read_bytes()


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("tracks", [[], ["one", "two"]])
def test_truth_cardinality_failure_rolls_back_all_application_rows(tmp_path, adapter, tracks):
    from examples.cuas_catalog import RECORDING, install, publish_recording

    source = tmp_path / "payload"
    source.write_bytes(b"fixture")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=1)
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        install(store)
        with catalog.transaction() as tx:
            tx.sql("INSERT INTO c_sensor VALUES('logger')")
            tx.sql("INSERT INTO c_sensor_config VALUES('config','logger','{}')")
            tx.sql("INSERT INTO c_aircraft VALUES('aircraft')")
            tx.sql("INSERT INTO c_aircraft_config VALUES('aircraft-config','aircraft','{}')")
        with pytest.raises(ConstraintError):
            publish_recording(store, source, id="truth", sensor_config="config", start=start, end=end,
                              tracks=[(id, start, end) for id in tracks], aircraft_config="aircraft-config")
        assert store.find(schema=RECORDING) == []
        assert catalog.sql("SELECT * FROM c_recording_source", write=False) == []
        assert catalog.sql("SELECT * FROM c_occurrence", write=False) == []


def test_radarnet_projection_preserves_ns_and_rounds_catalog_bounds_outward(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from examples.radarnet_input import inspect_recording
    from meldstore.storage import hash_file

    path = tmp_path / "input.parquet"
    pq.write_table(pa.table({"time": pa.array([1001, 1999, 2000], type=pa.timestamp("ns", tz="UTC")),
                             "sns_uuid": ["sensor"] * 3, "trk_uuid": ["one", "one", "two"]}), path)
    original = hash_file(path)
    result = inspect_recording(path)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert result["start"] == epoch + timedelta(microseconds=1)
    assert result["end"] == epoch + timedelta(microseconds=3)
    assert result["tracks"] == [("one", epoch + timedelta(microseconds=1), epoch + timedelta(microseconds=2)),
                                ("two", epoch + timedelta(microseconds=2), epoch + timedelta(microseconds=3))]
    assert hash_file(path) == original


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_generic_optional_structured_values_are_application_linked(tmp_path, adapter):
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from examples.dataset_catalog import exercise, exercise_formats

    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        exercise(store, tmp_path)
        assert exercise_formats(store) == ["array", "table"]
        assert catalog.sql("SELECT count(*) AS n FROM collection_members", write=False) == [{"n": 3}]


def test_plain_sql_consumer_without_any_site_packages(tmp_path):
    pytest.importorskip("pyarrow")
    from examples.cuas_catalog import CORRELATIONS, fixture

    db = tmp_path / "catalog.db"
    with Catalog(db) as catalog:
        fixture(Store(catalog, LocalStorage(tmp_path / "objects")), tmp_path)
        code = """
import sqlite3,sys,importlib.util
assert importlib.util.find_spec('melddb') is None
assert importlib.util.find_spec('meldstore') is None
connection=sqlite3.connect(sys.argv[1])
try:
    assert len(connection.execute(sys.argv[2], ('run',)).fetchall()) == 2
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
finally:
    connection.close()
"""
        with catalog_access(db):
            subprocess.run([sys.executable, "-I", "-S", "-c", code, str(db), CORRELATIONS],
                           check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("committed", [False, True])
def test_application_publication_survives_process_exit_as_one_unit(tmp_path, adapter, committed):
    from examples.cuas_catalog import RECORDING, install

    db, root = tmp_path / "catalog.db", tmp_path / "objects"
    source = tmp_path / "source"
    source.write_bytes(b"synthetic complete recording")
    with Catalog(db, adapter=adapter) as catalog:
        install(Store(catalog, LocalStorage(root)))
        catalog.sql("INSERT INTO c_sensor VALUES('sensor')")
        catalog.sql("INSERT INTO c_sensor_config VALUES('config','sensor','{}')")
    code = """
import os,sys
from datetime import datetime,timedelta,timezone
from examples.cuas_catalog import publish_recording
from meldstore import Catalog,Store,LocalStorage
with Catalog(sys.argv[1],adapter=sys.argv[4]) as catalog:
    store=Store(catalog,LocalStorage(sys.argv[2]))
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    end=start+timedelta(seconds=10)
    original=store.publish
    def stop(*args,**kwargs):
        original(*args,**kwargs)
        os._exit(71)
    if sys.argv[5]=='False': store.publish=stop
    publish_recording(store,sys.argv[3],id='radar',sensor_config='config',start=start,end=end,
                      tracks=[('one',start,end),('two',start,end)])
    os._exit(71)
"""
    result = subprocess.run([sys.executable, "-c", code, str(db), str(root), str(source), adapter, str(committed)],
                            capture_output=True, timeout=30)
    assert result.returncode == 71, result.stderr.decode()
    with Catalog(db, adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, LocalStorage(root))
        assert len(store.find(schema=RECORDING)) == int(committed)
        assert catalog.sql("SELECT count(*) AS n FROM c_occurrence", write=False) == [{"n": 2 if committed else 0}]
        report = store.reconcile(verify=True)
        assert len(report["prepared"]) == int(not committed)
        assert not report["missing"] and not report["corrupt"]


def test_workload_runner_uses_read_only_sources_and_removes_temporary_copies(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from tools.qualify_workload import run

    source = tmp_path / "source"
    source.mkdir()
    pq.write_table(pa.table({"time": pa.array([1001, 2001], type=pa.timestamp("ns", tz="UTC")),
                             "sns_uuid": ["one", "one"], "trk_uuid": ["a", "b"]}),
                   source / "2026-01-01_EchoK-000001.mt.parquet")
    output = tmp_path / "results" / "report.json"
    result = run(source, files=1, adapter="sqlite", output=output, min_bytes=0, max_bytes=100000)
    assert result["files"] == 1 and result["track_occurrences"] == 2
    assert result["metadata_query_calls"] == {"sql_api_calls": 100}
    assert result["originals_unchanged"]
    assert list(output.parent.iterdir()) == [output]
    assert str(source) not in output.read_text()

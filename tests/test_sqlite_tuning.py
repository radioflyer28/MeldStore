import sqlite3
import subprocess
import sys

import pytest

from meldstore import (
    BlobSchema,
    BusyError,
    Catalog,
    LocalStorage,
    SchemaConflictError,
    Store,
    Text,
    TransactionError,
    ValidationError,
    restore_backup,
)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_live_journal_policy_on_existing_catalog(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    sqlite3.connect(path).close()
    with Catalog(path, adapter=adapter) as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode") == [{"journal_mode": "wal"}]
        assert catalog.sql("SELECT * FROM pragma_synchronous") == [{"synchronous": 2}]
        assert catalog.sql("SELECT * FROM pragma_foreign_keys") == [{"foreign_keys": 1}]
    with Catalog(path, adapter=adapter, journal_mode="delete") as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode") == [{"journal_mode": "delete"}]
    with Catalog(path, adapter=adapter) as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode") == [{"journal_mode": "wal"}]


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_read_snapshot_does_not_block_writer(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter, timeout=0) as reader:
        reader.sql("CREATE TABLE sample(value INTEGER)")
        reader.sql("INSERT INTO sample VALUES(1)")
        with Catalog(path, adapter=adapter, timeout=0) as writer:
            with reader.transaction(write=False) as tx:
                assert tx.sql("SELECT value FROM sample") == [{"value": 1}]
                writer.sql("UPDATE sample SET value=2")
                assert tx.sql("SELECT value FROM sample") == [{"value": 1}]
            assert reader.sql("SELECT value FROM sample", write=False) == [{"value": 2}]
        with pytest.raises(TransactionError):
            with reader.transaction(write=False) as tx:
                with pytest.raises(ValidationError):
                    tx.sql("/* leading comment */ UPDATE sample SET value=3")
        assert reader.sql("SELECT value FROM sample", write=False) == [{"value": 2}]


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_store_reads_during_uncommitted_write_and_exclusive_gates(tmp_path, adapter):
    path, root = tmp_path / "catalog.db", tmp_path / "objects"
    schema = BlobSchema("dataset", {"label": Text()}, handlers=("bytes",))
    with Catalog(path, adapter=adapter, timeout=0) as reader:
        store = Store(reader, LocalStorage(root))
        store.install_schema(schema)
        store.put(b"data", schema=schema, metadata={"label": "old"}, handler="bytes", id="one")
        with Catalog(path, adapter=adapter, timeout=0) as writer:
            writing = Store(writer, LocalStorage(root))
            with writer.transaction() as tx:
                writing.update_metadata(
                    "one", schema=schema, changes={"label": "new"}, expected_version=1, tx=tx
                )
                assert store.stat("one")["metadata"]["label"] == "old"
                assert len(store.find(schema=schema, where={"label": "old"})) == 1
                assert store.get("one") == b"data"
            assert store.stat("one")["metadata"]["label"] == "new"
        with pytest.raises(BusyError):
            Catalog(path, adapter=adapter, maintenance=True)
        with sqlite3.connect(str(path) + ".meldstore-access") as gate:
            assert gate.execute("PRAGMA journal_mode").fetchone() == ("delete",)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_backup_stays_standalone_while_restore_adopts_wal(tmp_path, adapter):
    path, root = tmp_path / "catalog.db", tmp_path / "objects"
    with Catalog(path, adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, LocalStorage(root))
        store.install_schema(BlobSchema("empty", {}))
        store.install_query_indexes()
        store.backup(tmp_path / "backup", application={"id": "test", "schema_revision": "1"})
    result = restore_backup(tmp_path / "backup", tmp_path / "restored")
    with Catalog(result["catalog"], adapter=adapter) as restored:
        assert restored.sql("SELECT * FROM pragma_journal_mode") == [{"journal_mode": "wal"}]
        assert Store(restored, LocalStorage(result["storage"])).install_query_indexes() == []
    with sqlite3.connect(tmp_path / "backup" / "catalog.sqlite") as backup:
        assert backup.execute("PRAGMA journal_mode").fetchone() == ("delete",)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO sample VALUES(1)",
        "WITH x AS (SELECT 1) DELETE FROM sample",
        "-- comment\nCREATE TABLE other(x)",
        "PRAGMA query_only=OFF",
        "EXPLAIN PRAGMA cache_size=200",
        "EXPLAIN /* comment */ PRAGMA writable_schema=ON",
        "SELECT 1; DELETE FROM sample",
    ],
)
def test_read_transaction_rejects_mutations(tmp_path, adapter, statement):
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        catalog.sql("CREATE TABLE sample(value INTEGER)")
        with pytest.raises(ValidationError):
            catalog.sql(statement, write=False)
        assert catalog.sql("SELECT * FROM sample", write=False) == []


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_explicit_delete_policy_and_memory(tmp_path, adapter):
    with Catalog(tmp_path / "delete.db", adapter=adapter, journal_mode="delete") as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode") == [{"journal_mode": "delete"}]
    with Catalog(":memory:", adapter=adapter) as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode", write=False) == [
            {"journal_mode": "memory"}
        ]


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_journal_conflict_is_busy_and_releases_lease(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter) as writer:
        writer.sql("CREATE TABLE sample(value)")
        with writer.transaction():
            with pytest.raises(BusyError):
                Catalog(path, adapter=adapter, journal_mode="delete", timeout=0)
    with Catalog(path, adapter=adapter, maintenance=True, journal_mode="delete"):
        pass


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_explicit_index_upgrade_is_idempotent_and_preserves_application_objects(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(BlobSchema("dataset", {}))
        catalog.sql(
            "CREATE TABLE application(id INTEGER PRIMARY KEY, blob_id TEXT REFERENCES ms_blobs(id))"
        )
        catalog.sql("CREATE INDEX application_blob ON application(blob_id)")
        catalog.sql(
            "CREATE TRIGGER application_guard BEFORE DELETE ON application BEGIN SELECT RAISE(ABORT,'retained'); END"
        )
        before = catalog.sql(
            "SELECT name,sql FROM sqlite_master WHERE name LIKE 'application%' ORDER BY name"
        )
        assert store.install_query_indexes() == ["ms_gc_pending"]
        assert store.install_query_indexes() == []
        assert (
            catalog.sql(
                "SELECT name,sql FROM sqlite_master WHERE name LIKE 'application%' ORDER BY name"
            )
            == before
        )
        plan = catalog.sql(
            "EXPLAIN QUERY PLAN SELECT * FROM ms_gc WHERE storage_id=? AND state='pending' ORDER BY object_key LIMIT 25",
            ("root",),
            write=False,
        )
        assert any("ms_gc_pending" in row["detail"] for row in plan)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_statistics_and_checkpoint_are_explicit_exclusive_maintenance(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter) as catalog:
        with pytest.raises(TransactionError):
            catalog.maintain_sqlite()
    with Catalog(path, adapter=adapter, maintenance=True) as catalog:
        catalog.sql("CREATE TABLE sample(value INTEGER)")
        catalog.sql("CREATE INDEX sample_value ON sample(value)")
        catalog.sql("INSERT INTO sample VALUES(1),(2),(3)")
        result = catalog.maintain_sqlite(analyze=True, checkpoint="truncate")
        assert result["checkpoint"] == {"busy": 0, "log_frames": 0, "checkpointed_frames": 0}
        assert catalog.sql("SELECT stat FROM sqlite_stat1 WHERE idx='sample_value'") == [
            {"stat": "3 1"}
        ]
        assert catalog.maintain_sqlite()["statistics"] == "optimize"
        with pytest.raises(TransactionError):
            with catalog.transaction(write=False):
                catalog.maintain_sqlite()


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_reader_snapshot_survives_cross_process_writer(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter) as catalog:
        catalog.sql("CREATE TABLE sample(value)")
        catalog.sql("INSERT INTO sample VALUES(1)")
        with catalog.transaction(write=False) as tx:
            assert tx.sql("SELECT value FROM sample") == [{"value": 1}]
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from meldstore import Catalog; "
                    "c=Catalog(sys.argv[1],adapter=sys.argv[2],timeout=0); "
                    "c.sql('UPDATE sample SET value=2'); c.close()",
                    str(path),
                    adapter,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert child.returncode == 0, child.stderr
            assert tx.sql("SELECT value FROM sample") == [{"value": 1}]
        assert catalog.sql("SELECT value FROM sample", write=False) == [{"value": 2}]


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_checkpoint_reports_reader_pinning_and_recovers(tmp_path, adapter):
    path = tmp_path / "catalog.db"
    with Catalog(path, adapter=adapter, maintenance=True, timeout=0) as catalog:
        catalog.sql("CREATE TABLE sample(value BLOB)")
        catalog.sql("INSERT INTO sample VALUES(zeroblob(20000))")
        catalog.maintain_sqlite(checkpoint="truncate")
        # Intentionally noncooperative driver illustrates the documented limit of
        # access leases. Checkpoints must still report blocked progress honestly.
        raw = sqlite3.connect(path, autocommit=True)
        try:
            raw.execute("BEGIN")
            raw.execute("SELECT * FROM sample").fetchall()
            for _ in range(10):
                catalog.sql("UPDATE sample SET value=randomblob(20000)")
            result = catalog.maintain_sqlite()["checkpoint"]
            assert result["log_frames"] > result["checkpointed_frames"]
            assert path.with_name(path.name + "-wal").stat().st_size > 20000
            assert catalog.maintain_sqlite(checkpoint="truncate")["checkpoint"]["busy"] == 1
            raw.execute("ROLLBACK")
            assert catalog.maintain_sqlite(checkpoint="truncate")["checkpoint"] == {
                "busy": 0,
                "log_frames": 0,
                "checkpointed_frames": 0,
            }
        finally:
            raw.close()


@pytest.mark.parametrize("version", [(3, 49, 1), (3, 50, 4), (3, 51, 2)])
def test_unpatched_wal_runtime_rejected_before_creating_files(tmp_path, monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    path = tmp_path / "catalog.db"
    with pytest.raises(ValidationError, match="WAL-reset"):
        Catalog(path, adapter="sqlite")
    assert not path.exists()
    assert not path.with_name(path.name + ".meldstore-access").exists()
    with Catalog(path, adapter="sqlite", journal_mode="delete"):
        pass


@pytest.mark.parametrize("version", [(3, 44, 6), (3, 50, 7), (3, 51, 3), (3, 53, 1)])
def test_patched_runtime_and_backports_accepted(tmp_path, monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    with Catalog(tmp_path / "catalog.db", adapter="sqlite") as catalog:
        assert catalog.sql("SELECT * FROM pragma_journal_mode", write=False) == [
            {"journal_mode": "wal"}
        ]
        assert catalog.sql(
            "SELECT * FROM (WITH x AS (SELECT 7 AS value) SELECT * FROM x)", write=False
        ) == [{"value": 7}]


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_query_index_upgrade_rejects_name_conflict_without_replacing_it(tmp_path, adapter):
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(BlobSchema("dataset", {}))
        catalog.sql("CREATE INDEX ms_gc_pending ON ms_gc(state)")
        before = catalog.sql("SELECT sql FROM sqlite_master WHERE name='ms_gc_pending'")
        with pytest.raises(SchemaConflictError):
            store.install_query_indexes()
        assert catalog.sql("SELECT sql FROM sqlite_master WHERE name='ms_gc_pending'") == before

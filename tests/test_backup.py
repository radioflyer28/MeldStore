import json
import sqlite3
import subprocess
import sys
from contextlib import contextmanager

import pytest

from meldstore import (
    BlobSchema,
    Catalog,
    ConflictError,
    ConstraintError,
    IntegrityError,
    LocalStorage,
    MetadataMigration,
    Store,
    Text,
    TransactionError,
    ValidationError,
    restore_backup,
)

APP = {"id": "dataset-catalog", "schema_revision": "app-v1", "migration_package": "example-app"}


@pytest.fixture(params=["sqlite", "melddb"])
def setup(tmp_path, request):
    schema = BlobSchema("dataset", {"label": Text()}, handlers=("bytes", "file"))
    with Catalog(tmp_path / "source.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "source-objects"))
        store.install_schema(schema)
        store.put(
            b"survives\x00restore",
            schema=schema,
            metadata={"label": "example"},
            handler="bytes",
            id="one",
        )
        with catalog.transaction() as tx:
            tx.sql(
                "CREATE TABLE app_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, blob_id TEXT REFERENCES ms_blobs(id) ON DELETE RESTRICT, note TEXT, payload BLOB)"
            )
            tx.sql(
                "INSERT INTO app_runs VALUES(7,'one',?,?)", ("text\x00with ' quotes", b"\x00\xff")
            )
            tx.sql("CREATE INDEX app_blob ON app_runs(blob_id)")
            tx.sql(
                "CREATE VIEW app_join AS SELECT r.id,b.id AS blob_id FROM app_runs r JOIN ms_blobs b ON r.blob_id=b.id"
            )
            tx.sql(
                "CREATE TRIGGER app_note BEFORE UPDATE OF note ON app_runs WHEN NEW.note IS NULL BEGIN SELECT RAISE(ABORT,'note required'); END"
            )
    return tmp_path, request.param, schema


@contextmanager
def opened(setup, *, maintenance=True):
    path, adapter, _ = setup
    with Catalog(path / "source.db", adapter=adapter, maintenance=maintenance) as catalog:
        yield Store(catalog, LocalStorage(path / "source-objects"))


def test_backup_restore_both_adapters_and_sql_export(setup):
    path, adapter, schema = setup
    with opened(setup) as store:
        before = store.stat("one")
        manifest = store.backup(path / "backup", application=APP)
    assert manifest["application"] == APP
    assert manifest["objects"][0]["id"] == "one"
    result = restore_backup(path / "backup", path / "restored")
    # Change adapter on reopen: no conversion of catalog or domain data.
    with Catalog(
        result["catalog"], adapter="melddb" if adapter == "sqlite" else "sqlite"
    ) as catalog:
        store = Store(catalog, LocalStorage(result["storage"]))
        assert store.stat("one") == before
        assert store.get("one") == b"survives\x00restore"
        assert catalog.sql("SELECT * FROM app_join") == [{"id": 7, "blob_id": "one"}]
        assert catalog.sql("SELECT note,payload FROM app_runs") == [
            {"note": "text\x00with ' quotes", "payload": b"\x00\xff"}
        ]
        with pytest.raises(ConstraintError):
            store.delete("one", expected_version=1)
        with pytest.raises(ConstraintError):
            catalog.sql("UPDATE app_runs SET note=NULL")
        store.update_metadata(
            "one", schema=schema, changes={"label": "restored edit"}, expected_version=1
        )
    # Logical SQL export is executable without MeldStore or MeldDB and keeps NUL text.
    connection = sqlite3.connect(path / "export.db")
    try:
        connection.executescript((path / "backup" / "catalog.sql").read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT note,payload FROM app_runs").fetchall() == [
            ("text\x00with ' quotes", b"\x00\xff")
        ]
        assert connection.execute("SELECT * FROM app_join").fetchall() == [(7, "one")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE app_runs SET blob_id='absent'")
    finally:
        connection.close()
    with opened(setup) as store:
        assert store.stat("one") == before


def test_transfer_preserves_ids_and_source_and_new_root_ownership(setup):
    path, adapter, _ = setup
    with opened(setup) as store:
        result = store.transfer(path / "relocated", application=APP)
        assert store.get("one") == b"survives\x00restore"
    with Catalog(result["catalog"], adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(result["storage"]))
        assert store.get("one") == b"survives\x00restore"
    with Catalog(path / "wrong.db", adapter=adapter) as catalog:
        with pytest.raises(ValidationError, match="another catalog"):
            Store(catalog, LocalStorage(result["storage"]))


def test_requires_maintenance_and_explicit_application(setup):
    path, _, _ = setup
    with opened(setup, maintenance=False) as store:
        with pytest.raises(TransactionError):
            store.backup(path / "backup", application=APP)
    assert not (path / "backup").exists()
    with opened(setup) as store:
        with pytest.raises(ValidationError):
            store.backup(path / "backup", application={})
    assert not (path / "backup").exists()


@pytest.mark.parametrize("unfinished", ["prepared", "publishing", "cleanup"])
def test_unfinished_lifecycle_refused(setup, unfinished):
    path, _, schema = setup
    with opened(setup) as store:
        token = store.prepare(b"pending", schema=schema, handler="bytes")
        if unfinished != "prepared":
            with store.catalog.transaction() as tx:
                store.finalize(token, tx=tx, schema=schema, metadata={}, id="pending")
        if unfinished == "cleanup":
            store.delete("pending", expected_version=1)
        with pytest.raises(ConflictError, match="Resolve"):
            store.backup(path / "backup", application=APP)
        assert not (path / "backup" / "manifest.json").exists()


def test_migrated_metadata_and_retirement_history_survive(setup):
    path, adapter, schema = setup
    target = BlobSchema(
        "dataset", {"label": Text(), "annotation": Text()}, version=2, handlers=schema.handlers
    )
    with opened(setup) as store:
        store.migrate(
            MetadataMigration("v2", schema, target, "add", lambda row: dict(row, annotation="kept"))
        )
        store.put(b"removed", schema=target, metadata={}, handler="bytes", id="removed")
        store.delete("removed", expected_version=1)
        store.cleanup()
        before = store.stat("one")
        store.backup(path / "backup", application=APP)
    result = restore_backup(path / "backup", path / "restored")
    with Catalog(result["catalog"], adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(result["storage"]))
        assert store.stat("one") == before
        assert store.migration_status("v2")["state"] == "complete"
        assert store.deletion_status("removed")["state"] == "done"
        with pytest.raises(ConflictError):
            store.put(b"removed", schema=target, metadata={}, handler="bytes", id="removed")


@pytest.mark.parametrize(
    "damage", ["catalog", "sql", "payload", "missing_payload", "manifest", "path"]
)
def test_corruption_never_publishes_restored_catalog(setup, damage):
    path, _, _ = setup
    with opened(setup) as store:
        manifest = store.backup(path / "backup", application=APP)
    source = path / "backup"
    if damage in ("catalog", "sql"):
        (source / ("catalog.sqlite" if damage == "catalog" else "catalog.sql")).write_bytes(b"bad")
    elif damage in ("payload", "missing_payload"):
        payload = source / "storage" / manifest["objects"][0]["object_key"]
        if damage == "payload":
            payload.write_bytes(b"bad")
        else:
            payload.unlink()
    elif damage == "manifest":
        (source / "manifest.json").write_text('{"version":', encoding="utf-8")
    else:
        manifest["objects"][0]["object_key"] = "../../outside"
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError):
        restore_backup(source, path / "restored")
    assert not (path / "restored" / "catalog.sqlite").exists()


def test_existing_destination_and_overlap_rejected(setup):
    path, _, _ = setup
    with opened(setup) as store:
        store.backup(path / "backup", application=APP)
        with pytest.raises(ConflictError):
            store.backup(path / "backup", application=APP)
        with pytest.raises(ValidationError):
            store.backup(store.storage.root / "backup", application=APP)
    target = path / "existing"
    target.mkdir()
    sentinel = target / "catalog.sqlite"
    sentinel.write_bytes(b"do not overwrite")
    with pytest.raises(ConflictError):
        restore_backup(path / "backup", target)
    assert sentinel.read_bytes() == b"do not overwrite"


@pytest.mark.parametrize(
    "stage",
    ["backup_catalog", "backup_object", "backup_manifest", "restore_object", "restore_catalog"],
)
def test_process_interruption_only_leaves_unpublished_destination(setup, stage):
    path, adapter, _ = setup
    if stage.startswith("restore"):
        with opened(setup) as store:
            store.backup(path / "backup", application=APP)
    code = """
import importlib, os, sys
from pathlib import Path
from meldstore import Catalog, LocalStorage, Store, restore_backup
module = importlib.import_module('meldstore.backup')
path, adapter, stage = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
def interrupt(current):
    if current == stage:
        os._exit(71)
module._checkpoint = interrupt
if stage.startswith('restore'):
    restore_backup(path/'backup', path/'interrupted')
else:
    with Catalog(path/'source.db', adapter=adapter, maintenance=True) as catalog:
        Store(catalog, LocalStorage(path/'source-objects')).backup(
            path/'interrupted', application={'id':'test','schema_revision':'1'})
"""
    result = subprocess.run([sys.executable, "-c", code, str(path), adapter, stage], timeout=60)
    assert result.returncode == 71
    assert not (
        path
        / "interrupted"
        / ("catalog.sqlite" if stage.startswith("restore") else "manifest.json")
    ).exists()
    with opened(setup) as store:
        assert store.get("one") == b"survives\x00restore"
        if stage.startswith("backup"):
            store.backup(path / "retry-backup", application=APP)
    source = path / ("backup" if stage.startswith("restore") else "retry-backup")
    result = restore_backup(source, path / "fresh-retry")
    with Catalog(result["catalog"], adapter=adapter) as catalog:
        assert Store(catalog, LocalStorage(result["storage"])).get("one") == b"survives\x00restore"


def test_sql_export_preserves_cycles_generated_columns_sequences_and_rowids(setup):
    path, _, _ = setup
    with opened(setup) as store:
        with store.catalog.transaction() as tx:
            tx.sql(
                "CREATE TABLE app_a(id INTEGER PRIMARY KEY, b INTEGER REFERENCES app_b(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            tx.sql(
                "CREATE TABLE app_b(id INTEGER PRIMARY KEY, a INTEGER REFERENCES app_a(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            tx.sql("INSERT INTO app_a VALUES(1,2)")
            tx.sql("INSERT INTO app_b VALUES(2,1)")
            tx.sql(
                "CREATE TABLE app_generated(n INTEGER, doubled INTEGER GENERATED ALWAYS AS(n*2) STORED)"
            )
            tx.sql("INSERT INTO app_generated(rowid,n) VALUES(31,4)")
            tx.sql(
                "CREATE TABLE app_shadow(n INTEGER, rowid INTEGER GENERATED ALWAYS AS(n*3) STORED)"
            )
            tx.sql("INSERT INTO app_shadow(_rowid_,n) VALUES(44,5)")
            tx.sql("CREATE TABLE app_key(k TEXT PRIMARY KEY, n INTEGER) WITHOUT ROWID")
            tx.sql("INSERT INTO app_key VALUES('key',9)")
            tx.sql("INSERT INTO app_runs(id,blob_id) VALUES(100,'one')")
            tx.sql("DELETE FROM app_runs WHERE id=100")
        store.backup(path / "backup", application=APP)
    connection = sqlite3.connect(path / "logical.db")
    try:
        connection.executescript((path / "backup" / "catalog.sql").read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT rowid,n,doubled FROM app_generated").fetchall() == [
            (31, 4, 8)
        ]
        assert connection.execute("SELECT * FROM app_key").fetchall() == [("key", 9)]
        assert connection.execute("SELECT _rowid_,rowid,n FROM app_shadow").fetchall() == [
            (44, 15, 5)
        ]
        assert connection.execute(
            "INSERT INTO app_runs(blob_id) VALUES('one') RETURNING id"
        ).fetchone() == (101,)
    finally:
        connection.close()


def test_active_migration_and_multi_root_are_explicitly_refused(setup):
    path, _, schema = setup
    with opened(setup) as store:
        store.put(b"second", schema=schema, metadata={}, handler="bytes", id="two")
        target = BlobSchema(
            schema.name, {"label": Text(), "added": Text()}, version=2, handlers=schema.handlers
        )
        plan = MetadataMigration("v2", schema, target, "add", lambda row: dict(row, added="yes"))
        store.migrate(plan, batch_size=1, max_batches=1)
        with pytest.raises(ConflictError, match="migrations"):
            store.backup(path / "unfinished", application=APP)
        store.migrate(plan)
        other = Store(store.catalog, LocalStorage(path / "other-root"))
        other.put(b"third", schema=target, metadata={}, handler="bytes", id="three")
        with pytest.raises(ValidationError, match="one logical"):
            store.backup(path / "multiple", application=APP)


def test_empty_catalog_and_no_storage_io_in_sql_transaction(tmp_path, monkeypatch):
    with Catalog(tmp_path / "source.db", adapter="sqlite", maintenance=True) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        schema = BlobSchema("empty", {}, handlers=("bytes",))
        store.install_schema(schema)
        store.backup(tmp_path / "empty-backup", application=APP)
        store.put(b"x", schema=schema, metadata={}, handler="bytes")
        materialize = store.storage.materialize

        @contextmanager
        def checked(*args, **kwargs):
            assert catalog._active is None
            with materialize(*args, **kwargs) as path:
                yield path

        monkeypatch.setattr(store.storage, "materialize", checked)
        store.backup(tmp_path / "nonempty-backup", application=APP)
    result = restore_backup(tmp_path / "empty-backup", tmp_path / "empty-restore")
    with Catalog(result["catalog"], adapter="sqlite") as catalog:
        assert Store(catalog, LocalStorage(result["storage"])).find(schema=schema) == []


def test_snapshot_and_restore_refuse_corrupt_source_and_symlinks(setup):
    path, _, _ = setup
    with opened(setup) as store:
        before = store.stat("one")
        original = store.storage.root / before["object_key"]
        original.write_bytes(b"corruption")
        with pytest.raises(IntegrityError):
            store.backup(path / "corrupt", application=APP)
        assert not (path / "corrupt" / "manifest.json").exists()
        original.write_bytes(b"survives\x00restore")
        store.backup(path / "backup", application=APP)
    link = path / "linked"
    try:
        link.symlink_to(path / "backup", target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    with pytest.raises(ValidationError, match="symlinks"):
        restore_backup(link, path / "restored")


@pytest.mark.parametrize("change", ["version", "algorithm", "duplicate", "object-list", "identity"])
def test_invalid_manifest_refused_before_destination_creation(setup, change):
    path, _, _ = setup
    with opened(setup) as store:
        manifest = store.backup(path / "backup", application=APP)
    if change == "version":
        manifest["version"] = 2
    elif change == "algorithm":
        manifest["hash_algorithm"] = "sha256"
    elif change == "object-list":
        manifest["objects"] = []
    elif change == "identity":
        manifest["storage_id"] = "c" * 32
    text = json.dumps(manifest)
    if change == "duplicate":
        text = '{"version":1,' + text[1:]
    (path / "backup" / "manifest.json").write_text(text, encoding="utf-8")
    with pytest.raises((IntegrityError, ValidationError)):
        restore_backup(path / "backup", path / "restored")
    assert not (path / "restored").exists()

"""Opt-in real RustFS integration, launched by tools/s3_lab.py on a fresh bucket."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import obstore
import pytest

from meldstore import (
    BlobSchema,
    BusyError,
    Catalog,
    CommitError,
    ConflictError,
    ConstraintError,
    IntegrityError,
    LocalStorage,
    S3Storage,
    StorageError,
    Store,
    Text,
    ValidationError,
    restore_backup,
)
from meldstore.storage import hash_file

pytestmark = pytest.mark.skipif(
    not os.environ.get("MELDSTORE_S3_CONFIG"), reason="Run tools/s3_lab.py for disposable S3 qualification"
)


@pytest.fixture
def remote(tmp_path):
    config = json.loads(os.environ["MELDSTORE_S3_CONFIG"])
    assert config["endpoint"].startswith("http://127.0.0.1:")
    options = dict(bucket=os.environ["MELDSTORE_S3_BUCKET"], prefix="tests/" + uuid4().hex,
                   config=config, retry_config={"max_retries": 0},
                   client_options={"timeout": "3s"})
    storage = S3Storage(coordination_directory=tmp_path / "coordinator", **options)
    return storage, options


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_lifecycle_metadata_only_reads_backup_restore_and_cleanup(remote, tmp_path, adapter, monkeypatch):
    storage, options = remote
    schema = BlobSchema("dataset", {"label": Text()}, handlers=("bytes", "file"))
    db = tmp_path / "catalog.db"
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        store.install_query_indexes()
        original = store.put(b"remote\x00payload", schema=schema, metadata={"label": "a"}, handler="bytes", id="one")
        assert store.get("one") == b"remote\x00payload"
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                raise AssertionError("Metadata-only SQL must not call object storage")
            patch.setattr(obstore, "get", forbidden)
            patch.setattr(obstore, "list", forbidden)
            assert store.stat("one") == original
            assert store.find(schema=schema, where={"label": "a"}) == [original]
        catalog.sql("CREATE TABLE app_links(id TEXT REFERENCES ms_blobs(id) ON DELETE RESTRICT)")
        catalog.sql("INSERT INTO app_links VALUES('one')")
        with pytest.raises(ConstraintError):
            store.delete("one", expected_version=1)
        with pytest.raises(BusyError):
            Catalog(db, adapter=adapter, maintenance=True)
    storage = S3Storage(coordination_directory=storage.root, **options)
    with Catalog(db, adapter=adapter, maintenance=True) as catalog:
        store = Store(catalog, storage)
        report = store.reconcile(verify=True)
        assert len(report["ready"]) == 1
        assert not report["missing"] and not report["corrupt"]
        store.backup(tmp_path / "backup", application={"id": "test", "schema_revision": "1"})
        catalog.sql("DELETE FROM app_links")
        store.delete("one", expected_version=1)
        assert store.cleanup()[0]["state"] == "done"
        assert store.deletion_status("one")["state"] == "done"
    restored = restore_backup(tmp_path / "backup", tmp_path / "restored")
    with Catalog(restored["catalog"], adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(restored["storage"]))
        assert store.get("one") == b"remote\x00payload"
        assert catalog.sql("SELECT * FROM app_links") == [{"id": "one"}]


def test_competing_publications_are_create_only(remote, tmp_path):
    storage, _ = remote
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"a" * (6 * 1024 * 1024))
    second.write_bytes(b"b" * (6 * 1024 * 1024))
    key = "objects/" + uuid4().hex
    def upload(path):
        try:
            storage.upload(path, key)
            return path
        except StorageError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [path for path in pool.map(upload, (first, second)) if path]
    assert len(winners) == 1
    size, digest = hash_file(winners[0])
    with storage.materialize(key, byte_size=size, digest=digest) as result:
        assert hash_file(result) == (size, digest)
    with pytest.raises(StorageError):
        storage.upload(second if winners[0] == first else first, key)
    with storage.materialize(key, byte_size=size, digest=digest):
        pass


def test_empty_payload_and_missing_corrupt_objects(remote, tmp_path):
    storage, _ = remote
    empty = tmp_path / "empty"
    empty.touch()
    key = "objects/" + uuid4().hex
    storage.upload(empty, key)
    with pytest.raises(StorageError):
        storage.upload(empty, key)
    size, digest = hash_file(empty)
    with storage.materialize(key, byte_size=size, digest=digest) as path:
        assert path.read_bytes() == b""
    # Out-of-band mutation deliberately models a corrupt backend object.
    obstore.put(storage._store, key, b"damaged", mode="overwrite")
    with pytest.raises(IntegrityError):
        with storage.materialize(key, byte_size=size, digest=digest):
            pass
    storage.delete_object(key)
    storage.delete_object(key)
    with pytest.raises(IntegrityError):
        with storage.materialize(key, byte_size=size, digest=digest):
            pass


def test_distinct_coordinator_cannot_bypass_same_root_gate(remote, tmp_path):
    storage, options = remote
    with pytest.raises((StorageError, ConflictError)):
        S3Storage(coordination_directory=tmp_path / "different-coordinator", **options)
    with Catalog(tmp_path / "one.db") as catalog:
        Store(catalog, storage)
        with Catalog(tmp_path / "two.db") as other:
            with pytest.raises(ValidationError):
                Store(other, storage)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_offline_local_to_s3_preserves_catalog_and_source(remote, tmp_path, adapter):
    _, options = remote
    options = dict(options, prefix="transfer/" + uuid4().hex)
    schema = BlobSchema("dataset", {"label": Text()}, handlers=("bytes",))
    with Catalog(tmp_path / "source.db", adapter=adapter, maintenance=True) as catalog:
        source = Store(catalog, LocalStorage(tmp_path / "source"))
        source.install_schema(schema)
        original = source.put(b"moved", schema=schema, metadata={"label": "a"}, handler="bytes", id="one")
        catalog.sql("CREATE TABLE links(id TEXT REFERENCES ms_blobs(id))")
        catalog.sql("INSERT INTO links VALUES('one')")
        result = source.transfer_to_s3(tmp_path / "destination", application={"id": "test", "schema_revision": "1"}, **options)
        assert source.get("one") == b"moved"
    with Catalog(result["catalog"], adapter=adapter) as catalog:
        target = Store(catalog, S3Storage(coordination_directory=result["storage"], **options))
        assert target.stat("one") == original
        assert target.get("one") == b"moved"
        assert catalog.sql("SELECT * FROM links") == [{"id": "one"}]


def test_process_exit_before_finalization_is_reconciled(remote, tmp_path):
    storage, options = remote
    db = tmp_path / "catalog.db"
    schema = BlobSchema("dataset", {})
    with Catalog(db) as catalog:
        Store(catalog, storage).install_schema(schema)
    source = tmp_path / "source"
    source.write_bytes(b"interrupted")
    code = """
import json,os,sys
from meldstore import BlobSchema,Catalog,S3Storage,Store
options=json.loads(os.environ['MELDSTORE_S3_OPTIONS'])
with Catalog(sys.argv[1]) as catalog:
    store=Store(catalog,S3Storage(coordination_directory=sys.argv[2],**options))
    store.prepare_file(sys.argv[3],schema=BlobSchema('dataset',{}))
    os._exit(71)
"""
    result = subprocess.run([sys.executable, "-c", code, str(db), str(storage.root), str(source)],
                            env=dict(os.environ, MELDSTORE_S3_OPTIONS=json.dumps(options)),
                            capture_output=True, timeout=25)
    assert result.returncode == 71, result.stderr.decode()
    with Catalog(db, maintenance=True) as catalog:
        store = Store(catalog, storage)
        assert store.find(schema=schema) == []
        report = store.reconcile(verify=True)
        assert len(report["prepared"]) == 1


def test_large_file_streaming_roundtrip(remote, tmp_path):
    storage, _ = remote
    source = tmp_path / "large"
    with source.open("wb") as output:
        for _ in range(128):
            output.write(b"0123456789abcdef" * 65536)
    size, digest = hash_file(source)
    key = "objects/" + uuid4().hex
    storage.upload(source, key)
    with storage.materialize(key, byte_size=size, digest=digest) as downloaded:
        assert hash_file(downloaded) == (size, digest)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_resolves_without_another_upload(remote, tmp_path, monkeypatch, adapter, committed):
    storage, _ = remote
    db = tmp_path / "catalog.db"
    schema = BlobSchema("dataset", {}, handlers=("bytes",))
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        token = store.prepare(b"uncertain", schema=schema, handler="bytes")
        original = catalog._commit
        def fail(manager):
            if committed:
                original(manager)
            raise RuntimeError("lost commit acknowledgement")
        monkeypatch.setattr(catalog, "_commit", fail)
        with pytest.raises(CommitError):
            with catalog.transaction() as tx:
                store.finalize(token, schema=schema, metadata={}, id="one", tx=tx)
                store.publish("one", tx=tx)
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, storage)
        assert store.resolve("one", prepared=token, schema=schema, metadata={})["outcome"] == (
            "ready" if committed else "prepared"
        )
        assert len(store.find(schema=schema)) == int(committed)


def test_lost_copy_response_leaves_reportable_orphans(remote, tmp_path, monkeypatch):
    storage, _ = remote
    schema = BlobSchema("dataset", {}, handlers=("bytes",))
    with Catalog(tmp_path / "catalog.db", maintenance=True) as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        copy = obstore.copy
        def lost(*args, **kwargs):
            copy(*args, **kwargs)
            raise OSError("response lost after remote completion")
        with monkeypatch.context() as patch:
            patch.setattr(obstore, "copy", lost)
            with pytest.raises(StorageError):
                store.put(b"orphan", schema=schema, metadata={}, handler="bytes")
        assert store.find(schema=schema) == []
        report = store.reconcile(verify=True)
        assert len(report["orphans"]) == 1
        assert len(report["staging"]) == 1


def test_delete_failure_is_pending_and_retry_is_idempotent(remote, tmp_path, monkeypatch):
    storage, _ = remote
    schema = BlobSchema("dataset", {}, handlers=("bytes",))
    with Catalog(tmp_path / "catalog.db", maintenance=True) as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        store.put(b"delete", schema=schema, metadata={}, handler="bytes", id="one")
        store.delete("one", expected_version=1)
        def denied(*args, **kwargs):
            raise OSError("simulated transport failure")
        with monkeypatch.context() as patch:
            patch.setattr(obstore, "delete", denied)
            assert store.cleanup()[0]["state"] == "pending"
        assert store.cleanup()[0]["state"] == "done"
        assert store.cleanup() == []


def test_interrupted_transfer_never_publishes_destination_catalog(remote, tmp_path, monkeypatch):
    import importlib
    backup = importlib.import_module("meldstore.backup")
    _, options = remote
    options = dict(options, prefix="transfer/" + uuid4().hex)
    destination = tmp_path / "destination"
    schema = BlobSchema("dataset", {}, handlers=("bytes",))
    with Catalog(tmp_path / "source.db", maintenance=True) as catalog:
        source = Store(catalog, LocalStorage(tmp_path / "source"))
        source.install_schema(schema)
        source.put(b"preserved", schema=schema, metadata={}, handler="bytes", id="one")
        def fail(stage):
            if stage == "restore_object":
                raise OSError("interrupted after verified remote copy")
        monkeypatch.setattr(backup, "_checkpoint", fail)
        with pytest.raises(OSError):
            source.transfer_to_s3(destination, application={"id": "test", "schema_revision": "1"}, **options)
        assert not (destination / "catalog.sqlite").exists()
        assert source.get("one") == b"preserved"
        with pytest.raises(ConflictError):
            source.transfer_to_s3(destination, application={"id": "test", "schema_revision": "1"}, **options)


@pytest.mark.parametrize("handler", ["numpy.npz", "numpy.blosc2", "pandas.parquet", "polars.parquet", "pyarrow.parquet"])
def test_optional_formats_roundtrip_remote(remote, tmp_path, handler):
    storage, _ = remote
    module = handler.split(".")[0]
    library = pytest.importorskip(module)
    if handler == "numpy.blosc2":
        pytest.importorskip("blosc2")
    if handler == "pandas.parquet":
        pytest.importorskip("pyarrow")
    if module == "numpy":
        value = library.arange(24, dtype="int32").reshape(4, 6)
    elif module == "pyarrow":
        value = library.table({"value": [1, 2, 3]})
    else:
        value = library.DataFrame({"value": [1, 2, 3]})
    schema = BlobSchema("dataset", {}, handlers=(handler,))
    with Catalog(tmp_path / "catalog.db") as catalog:
        store = Store(catalog, storage)
        store.install_schema(schema)
        store.put(value, schema=schema, metadata={}, handler=handler, id="one")
        result = store.get("one")
        if module == "numpy":
            library.testing.assert_array_equal(result, value)
        else:
            assert result.equals(value)


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("consumer", ["dataset_catalog", "cuas_catalog"])
def test_s08_application_examples_on_real_s3(remote, tmp_path, adapter, consumer):
    import importlib

    if consumer == "cuas_catalog":
        pytest.importorskip("pyarrow")
    exercise = importlib.import_module("examples." + consumer).exercise
    storage, _ = remote
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        result = exercise(Store(catalog, storage), tmp_path)
        if consumer == "cuas_catalog":
            assert result == {"correlations": 2, "recordings": ["radar", "truth"], "materializations": 2}
        else:
            assert result["retrieved"] == b"example document\n"

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from meldstore import (
    BlobSchema,
    Catalog,
    ConflictError,
    IntegrityError,
    LocalStorage,
    StorageError,
    Store,
    TransactionError,
    ValidationError,
)


@pytest.fixture(params=["sqlite", "melddb"])
def stored(tmp_path, request):
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        schema = BlobSchema("dataset", {}, handlers=("bytes",))
        store.install_schema(schema)
        record = store.put(b"original bytes", schema=schema, metadata={}, handler="bytes", id="one")
        yield store, record


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("content", [b"", b"exact\x00file\xffbytes"])
def test_export_is_a_persistent_verified_copy_and_preserves_source(tmp_path, adapter, content):
    source = tmp_path / "original"
    source.write_bytes(content)
    destination = tmp_path / "exported"
    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        schema = BlobSchema("dataset", {})
        store.install_schema(schema)
        before = store.import_file(source, schema=schema, metadata={}, id="one")
        result = store.export_file("one", destination)
        assert isinstance(result, Path) and result == destination.resolve()
        assert destination.read_bytes() == content
        assert store.stat("one") == before
        destination.write_bytes(b"independent editable copy")
        with store.materialize("one") as verified:
            assert verified.read_bytes() == content
    assert source.read_bytes() == content
    assert destination.read_bytes() == b"independent editable copy"
    assert not list(tmp_path.glob(".meldstore-export-*"))


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_export_never_overwrites_existing_destination(stored, tmp_path, kind):
    store, record = stored
    destination = tmp_path / "occupied"
    if kind == "file":
        destination.write_bytes(b"keep existing data")
    else:
        destination.mkdir()
    with pytest.raises(ConflictError):
        store.export_file("one", destination)
    assert store.stat("one") == record
    if kind == "file":
        assert destination.read_bytes() == b"keep existing data"
    else:
        assert destination.is_dir()


def test_export_rejects_dangling_symlink(stored, tmp_path):
    store, _ = stored
    destination = tmp_path / "link"
    try:
        destination.symlink_to(tmp_path / "absent")
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ConflictError):
        store.export_file("one", destination)
    assert destination.is_symlink() and not (tmp_path / "absent").exists()


@pytest.mark.parametrize("damage", ["source", "copy", "copy_io", "publish_io"])
def test_export_failure_never_exposes_partial_destination(stored, tmp_path, monkeypatch, damage):
    store, record = stored
    destination = tmp_path / "exported"
    if damage == "source":
        (store.storage.root / record["object_key"]).write_bytes(b"corrupted data")
    elif damage in ("copy", "copy_io"):
        def bad_copy(incoming, outgoing, length):
            assert length == 1024 * 1024
            outgoing.write(b"bad")
            if damage == "copy_io":
                raise OSError("simulated disk failure")
        monkeypatch.setattr(shutil, "copyfileobj", bad_copy)
    else:
        def fail_link(*args, **kwargs):
            raise OSError("hard-link publication unavailable")
        monkeypatch.setattr(os, "link", fail_link)
    error = IntegrityError if damage in ("source", "copy") else StorageError
    with pytest.raises(error):
        store.export_file("one", destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".meldstore-export-*"))
    assert store.stat("one") == record


def test_export_requires_existing_parent_and_idle_catalog(stored, tmp_path):
    store, _ = stored
    with pytest.raises(StorageError):
        store.export_file("one", tmp_path / "missing" / "export")
    assert not (tmp_path / "missing").exists()
    with pytest.raises(TransactionError):
        with store.catalog.transaction(write=False):
            store.export_file("one", tmp_path / "during-transaction")
    assert not (tmp_path / "during-transaction").exists()


def test_export_cannot_create_managed_storage_or_sqlite_sidecar_files(stored):
    store, _ = stored
    for destination in (store.storage.root / "unexpected", Path(str(store.catalog.path) + "-journal"),
                        Path(str(store.catalog.path) + ".meldstore-access-wal")):
        with pytest.raises(ValidationError):
            store.export_file("one", destination)
        assert not destination.exists()


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_concurrent_exports_have_one_winner_without_overwrite(tmp_path, adapter, monkeypatch):
    db = tmp_path / "catalog.db"
    root = tmp_path / "objects"
    destination = tmp_path / "exported"
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(root))
        schema = BlobSchema("dataset", {}, handlers=("bytes",))
        store.install_schema(schema)
        store.put(b"winner bytes", schema=schema, metadata={}, handler="bytes", id="one")
    barrier = Barrier(2)
    link = os.link
    def simultaneous_link(*args, **kwargs):
        barrier.wait(timeout=10)
        return link(*args, **kwargs)
    monkeypatch.setattr(os, "link", simultaneous_link)
    def export():
        with Catalog(db, adapter=adapter) as catalog:
            try:
                Store(catalog, LocalStorage(root)).export_file("one", destination)
                return "success"
            except ConflictError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: export(), range(2)))
    assert sorted(results) == ["conflict", "success"]
    assert destination.read_bytes() == b"winner bytes"


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
@pytest.mark.parametrize("published", [False, True])
def test_export_process_exit_leaves_absent_or_complete_destination(tmp_path, adapter, published):
    db, root, destination = tmp_path / "catalog.db", tmp_path / "objects", tmp_path / "exported"
    with Catalog(db, adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(root))
        schema = BlobSchema("dataset", {}, handlers=("bytes",))
        store.install_schema(schema)
        before = store.put(b"complete bytes", schema=schema, metadata={}, handler="bytes", id="one")
    code = """
import os,sys
from meldstore import Catalog,LocalStorage,Store
original=os.link
def interrupt(*args,**kwargs):
    if sys.argv[5]=='True': original(*args,**kwargs)
    os._exit(73)
os.link=interrupt
with Catalog(sys.argv[1],adapter=sys.argv[4]) as catalog:
    Store(catalog,LocalStorage(sys.argv[2])).export_file('one',sys.argv[3])
"""
    result = subprocess.run([sys.executable, "-c", code, str(db), str(root), str(destination),
                             adapter, str(published)], capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode()
    assert destination.exists() == published
    if published:
        assert destination.read_bytes() == b"complete bytes"
    with Catalog(db, adapter=adapter) as catalog:
        assert Store(catalog, LocalStorage(root)).stat("one") == before


def test_export_missing_id_creates_nothing(stored, tmp_path):
    from meldstore import NotFoundError

    store, _ = stored
    with pytest.raises(NotFoundError):
        store.export_file("absent", tmp_path / "exported")
    assert not (tmp_path / "exported").exists()
    assert not list(tmp_path.glob(".meldstore-export-*"))


@pytest.mark.parametrize("adapter", ["sqlite", "melddb"])
def test_export_serialized_npz_copies_encoding_without_decoding(tmp_path, adapter):
    np = pytest.importorskip("numpy")
    from meldstore.storage import hash_file

    with Catalog(tmp_path / "catalog.db", adapter=adapter) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        schema = BlobSchema("array", {}, handlers=("numpy.npz",))
        store.install_schema(schema)
        record = store.put(np.arange(7), schema=schema, metadata={}, handler="numpy.npz", id="one")
        destination = store.export_file("one", tmp_path / "array.npz")
        assert hash_file(destination) == (record["byte_size"], record["digest"])
        with np.load(destination, allow_pickle=False) as encoded:
            assert len(encoded.files) == 1
            np.testing.assert_array_equal(encoded[encoded.files[0]], np.arange(7))

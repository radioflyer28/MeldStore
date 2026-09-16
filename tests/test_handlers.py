import subprocess
import sys
from dataclasses import replace

import pytest

from meldstore import (
    BlobSchema,
    Catalog,
    ConflictError,
    ConstraintError,
    Handler,
    HandlerRegistry,
    IntegrityError,
    LocalStorage,
    MetadataMigration,
    MissingDependencyError,
    PreparedFile,
    Store,
    Text,
    UnsupportedHandlerError,
    ValidationError,
)


@pytest.fixture(params=["sqlite", "melddb"])
def setup_store(tmp_path, request):
    schema = BlobSchema(
        "values",
        {"label": Text()},
        handlers=(
            "file",
            "bytes",
            "numpy.npz",
            "numpy.blosc2",
            "pandas.parquet",
            "polars.parquet",
            "pyarrow.parquet",
            "custom.text",
        ),
    )
    with Catalog(tmp_path / "catalog.db", adapter=request.param) as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        store.install_schema(schema)
        yield store, schema


@pytest.mark.parametrize("value", [b"", b"hello\x00\xff"])
def test_bytes_roundtrip_retry_and_file_passthrough(setup_store, tmp_path, value):
    store, schema = setup_store
    first = store.put(value, schema=schema, metadata={}, handler="bytes", id="bytes")
    assert store.get("bytes") == value
    assert first["handler_id"] == "bytes"
    assert first["descriptor"] == {}
    assert store.put(value, schema=schema, metadata={}, handler="bytes", id="bytes") == first
    assert len(list((store.storage.root / "objects").iterdir())) == 1
    source = tmp_path / "source"
    source.write_bytes(value)
    with pytest.raises(ConflictError):
        store.import_file(source, schema=schema, metadata={}, id="bytes")
    blob = store.import_file(source, schema=schema, metadata={}, id="file")
    assert blob["digest"] == first["digest"]
    with pytest.raises(UnsupportedHandlerError, match="materialize"):
        store.get("file")


def test_custom_versions_verified_before_decode_and_unavailable_on_reopen(setup_store):
    store, schema = setup_store
    calls = []

    def write(value, path):
        path.write_text(value, encoding="utf-8")
        return {"charset": "utf-8"}

    def read(path, descriptor):
        calls.append(path)
        return path.read_text(encoding=descriptor["charset"])

    store.handlers.register(Handler("custom.text", 2, write, read))
    blob = store.put("héllo", schema=schema, metadata={}, handler="custom.text", handler_version=2)
    assert store.get(blob["id"]) == "héllo"
    assert not calls[0].exists()
    with pytest.raises(ValidationError, match="already registered"):
        store.handlers.register(Handler("custom.text", 2, write, read))
    fresh = Store(store.catalog, store.storage)
    assert fresh.stat(blob["id"]) == blob
    with fresh.materialize(blob["id"]) as path:
        assert path.read_text(encoding="utf-8") == "héllo"
    with pytest.raises(UnsupportedHandlerError):
        fresh.get(blob["id"])
    (store.storage.root / blob["object_key"]).write_bytes(b"corrupt")
    with pytest.raises(IntegrityError):
        store.get(blob["id"])
    assert len(calls) == 1


def test_sealed_descriptor_token_binding_and_lifecycle(setup_store):
    store, schema = setup_store
    token = store.prepare(b"abc", schema=schema, handler="bytes")
    for altered in (
        replace(token, descriptor='{"bad":true}'),
        replace(token, encoding_version=2),
        PreparedFile(**{key: getattr(token, key) for key in PreparedFile.__dataclass_fields__}),
    ):
        with pytest.raises(ValidationError):
            with store.catalog.transaction() as tx:
                store.finalize(altered, tx=tx, schema=schema, metadata={}, id="x")
        with pytest.raises(ValidationError):
            store.discard_prepared(altered)
        with pytest.raises(ConflictError):
            store.resolve("x", prepared=altered, schema=schema, metadata={})
    for sql in ("UPDATE ms_encodings SET descriptor='{}'", "DELETE FROM ms_encodings"):
        with pytest.raises(ConstraintError):
            store.catalog.sql(sql)
    with store.catalog.transaction() as tx:
        store.finalize(token, tx=tx, schema=schema, metadata={}, id="x")
        store.publish("x", tx=tx)
    assert store.resolve("x", prepared=token, schema=schema, metadata={})["outcome"] == "ready"
    store.delete("x", expected_version=1)
    assert store.resolve("x", prepared=token, schema=schema, metadata={})["outcome"] == "retired"
    pending = store.prepare(b"unused", schema=schema, handler="bytes")
    store.discard_prepared(pending)
    assert store.resolve("unused", prepared=pending, schema=schema, metadata={}) == {
        "outcome": "discarded"
    }


def test_legacy_file_cannot_gain_encoding_after_journal(setup_store, tmp_path):
    store, schema = setup_store
    source = tmp_path / "source"
    source.write_bytes(b"test")
    token = store.prepare_file(source, schema=schema)
    with pytest.raises(ConstraintError):
        store.catalog.sql("INSERT INTO ms_encodings VALUES(?, 'bytes', 1, '{}')", (token.token,))


def test_bad_descriptor_validation_and_missing_dependencies(setup_store, monkeypatch):
    store, schema = setup_store

    def write(value, path):
        path.write_bytes(b"test")
        return {"bad": float("nan")}

    store.handlers.register(Handler("custom.text", 1, write, lambda *args: None))
    with pytest.raises(ValidationError, match="descriptor"):
        store.prepare(None, schema=schema, handler="custom.text")
    monkeypatch.setitem(sys.modules, "pandas", None)
    with pytest.raises(MissingDependencyError, match=r"meldstore\[parquet\]"):
        store.prepare(None, schema=schema, handler="pandas.parquet")
    assert store.find(schema=schema) == []
    assert not (store.storage.root / "objects").exists()


def test_core_import_does_not_load_optional_packages():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import meldstore; "
            "assert not {'numpy','pandas','polars','pyarrow','blosc2'} & sys.modules.keys()",
        ],
        check=True,
    )


@pytest.mark.parametrize("handler", ["numpy.npz", "numpy.blosc2"])
@pytest.mark.parametrize("case", ["scalar", "empty", "strided", "boolean", "complex"])
def test_array_dtype_shape_values(setup_store, handler, case):
    np = pytest.importorskip("numpy")
    if handler == "numpy.blosc2":
        pytest.importorskip("blosc2")
    values = {
        "scalar": np.array(42, dtype="int16"),
        "empty": np.empty((0, 3), dtype="float32"),
        "strided": np.arange(48, dtype="int64").reshape(6, 8)[::2, ::-2],
        "boolean": np.array([True, False]),
        "complex": np.array([1 + 2j, 3 - 4j], dtype="complex128"),
    }
    value = values[case]
    store, schema = setup_store
    blob = store.put(value, schema=schema, metadata={}, handler=handler)
    restored = store.get(blob["id"])
    assert restored.shape == value.shape and restored.dtype == value.dtype
    np.testing.assert_array_equal(restored, value, strict=True)


@pytest.mark.parametrize("handler", ["numpy.npz", "numpy.blosc2"])
def test_object_arrays_rejected_without_upload(setup_store, handler):
    np = pytest.importorskip("numpy")
    store, schema = setup_store
    for value in (np.array([object()]), np.array([(object(),)], dtype=[("x", object)])):
        with pytest.raises(ValidationError, match="without objects"):
            store.put(value, schema=schema, metadata={}, handler=handler)
    assert store.find(schema=schema) == []


@pytest.mark.parametrize("index", ["range", "named", "multi", "empty"])
def test_pandas_preservation(setup_store, index):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    frame = pd.DataFrame(
        {
            "i": pd.array([1, None, 3], dtype="Int64"),
            "b": pd.array([True, None, False], dtype="boolean"),
            "s": pd.array(["one", None, "three"], dtype="string"),
            "category": pd.Categorical(
                ["a", None, "b"], categories=["b", "a", "unused"], ordered=True
            ),
            "time": pd.date_range("2025-11-02 00:00", periods=3, freq="h", tz="America/New_York"),
        }
    )
    if index == "named":
        frame.index = pd.Index(["x", "y", "z"], name="sample")
    elif index == "multi":
        frame.index = pd.MultiIndex.from_tuples(
            [("a", 1), ("a", 2), ("b", 1)], names=["group", "n"]
        )
    elif index == "empty":
        frame = frame.iloc[:0]
    store, schema = setup_store
    blob = store.put(frame, schema=schema, metadata={}, handler="pandas.parquet")
    pd.testing.assert_frame_equal(store.get(blob["id"]), frame, check_exact=True)


def test_lossy_numeric_categories_rejected(setup_store):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    store, schema = setup_store
    with pytest.raises(ValidationError, match="lossless"):
        store.put(
            pd.DataFrame({"x": pd.Categorical([1, 2])}),
            schema=schema,
            metadata={},
            handler="pandas.parquet",
        )


@pytest.mark.parametrize("empty", [False, True])
def test_pyarrow_metadata_and_nested_values(setup_store, empty):
    pa = pytest.importorskip("pyarrow")
    schema = pa.schema(
        [pa.field("values", pa.list_(pa.int64()), metadata={b"unit": b"count"})],
        metadata={b"application": b"test"},
    )
    table = pa.Table.from_pylist([{"values": [1, 2]}, {"values": None}], schema=schema)
    if empty:
        table = table.slice(0, 0)
    store, declaration = setup_store
    blob = store.put(table, schema=declaration, metadata={}, handler="pyarrow.parquet")
    restored = store.get(blob["id"])
    assert restored.equals(table)
    assert restored.schema.equals(table.schema, check_metadata=True)


@pytest.mark.parametrize("empty", [False, True])
def test_polars_preservation(setup_store, empty):
    pl = pytest.importorskip("polars")
    from polars.testing import assert_frame_equal

    frame = pl.DataFrame(
        {
            "i": [1, None, 3],
            "f": [float("nan"), None, 1.5],
            "nested": [[1, 2], None, []],
            "category": ["a", None, "b"],
        }
    )
    frame = frame.with_columns(pl.col("category").cast(pl.Enum(["a", "b", "unused"])))
    if empty:
        frame = frame.head(0)
    store, schema = setup_store
    blob = store.put(frame, schema=schema, metadata={}, handler="polars.parquet")
    assert_frame_equal(store.get(blob["id"]), frame, check_exact=True)
    with pytest.raises(ValidationError, match="eager"):
        store.prepare(frame.lazy(), schema=schema, handler="polars.parquet")


def test_encoded_metadata_migration_preserves_decoder(setup_store, monkeypatch):
    store, schema = setup_store
    before = store.put(b"stable", schema=schema, metadata={}, handler="bytes", id="stable")
    target = BlobSchema(
        schema.name, {"label": Text(), "new": Text()}, version=2, handlers=schema.handlers
    )
    plan = MetadataMigration("v2", schema, target, "add", lambda row: dict(row, new="added"))
    store.migrate(plan)
    store.install_schema(target)
    assert store.get("stable") == b"stable"
    after = store.stat("stable")
    assert after["digest"] == before["digest"]
    assert after["schema_version"] == 2 and after["handler_version"] == 1

    def fail(*args, **kwargs):
        pytest.fail("Metadata query attempted payload I/O")

    monkeypatch.setattr(store.storage, "materialize", fail)
    store.handlers = HandlerRegistry(builtins=False)
    assert store.find(schema=target) == [after]


def test_s02_catalog_additive_upgrade(tmp_path):
    from meldstore import payload_catalog

    schema = BlobSchema("legacy", {}, handlers=("file", "bytes"))
    source = tmp_path / "source"
    source.write_bytes(b"legacy")
    with Catalog(tmp_path / "db", adapter="sqlite") as catalog:
        with catalog.transaction() as tx:
            catalog.install_schema(schema, tx=tx)
            payload_catalog.install(schema, tx)
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        before = store.import_file(source, schema=schema, metadata={}, id="old")
        store.install_schema(schema)
        assert store.stat("old") == before
        store.put(b"new", schema=schema, metadata={}, handler="bytes", id="new")
    with Catalog(tmp_path / "db", adapter="sqlite") as catalog:
        store = Store(catalog, LocalStorage(tmp_path / "objects"))
        assert store.get("new") == b"new"
        assert store.stat("old") == before


def test_npz_decoder_never_unpickles(tmp_path):
    np = pytest.importorskip("numpy")
    path = tmp_path / "unsafe.npz"
    np.savez(path, value=np.array([{"not": "plain array"}], dtype=object))
    codec = HandlerRegistry().get("numpy.npz")
    with pytest.raises(ValueError, match="allow_pickle=False"):
        codec.read(path, {"format": "npz", "member": "value", "pickle": False})


def test_writer_rewrites_header_then_hashes_and_cleans_failure(setup_store):
    store, schema = setup_store
    paths = []

    def write(value, path):
        paths.append(path)
        with path.open("w+b") as output:
            output.write(b"oldbody")
            output.seek(0)
            output.write(b"new")
        if value == "fail":
            raise ValueError("interrupted writer")
        return {}

    store.handlers.register(Handler("custom.text", 1, write, lambda path, _: path.read_bytes()))
    with pytest.raises(ValueError, match="interrupted"):
        store.prepare("fail", schema=schema, handler="custom.text")
    assert not paths[0].exists()
    assert store.find(schema=schema) == []
    record = store.put("ok", schema=schema, metadata={}, handler="custom.text")
    assert store.get(record["id"]) == b"newbody"
    assert not paths[1].exists()


def test_allowlist_and_active_transaction_reject_before_write(setup_store):
    from meldstore import TransactionError

    store, _ = setup_store
    schema = BlobSchema("only_file", {}, handlers=("file",))
    store.install_schema(schema)
    with pytest.raises(ValidationError, match="not allowed"):
        store.prepare(b"x", schema=schema, handler="bytes")
    with pytest.raises(TransactionError, match="cannot commit"):
        with store.catalog.transaction():
            with pytest.raises(TransactionError):
                store.prepare(b"x", schema=schema, handler="bytes")

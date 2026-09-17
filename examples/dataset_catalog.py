"""Domain-neutral application: explicit SQL relationships and verified documents.

Run from a source checkout: uv run python -m examples.dataset_catalog
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from meldstore import BlobSchema, Catalog, Index, LocalStorage, Store, Text

SCHEMA = BlobSchema("dataset", {"title": Text(required=True), "category": Text()},
                    indexes=(Index("category"),), handlers=("file", "bytes"))


def exercise(store, directory):
    """Populate a fresh catalog; no cUAS imports or optional format dependencies."""
    store.install_schema(SCHEMA)
    store.catalog.sql("CREATE TABLE collections(id TEXT PRIMARY KEY NOT NULL)")
    store.catalog.sql("""CREATE TABLE collection_members(
        collection_id TEXT NOT NULL REFERENCES collections(id),
        blob_id TEXT NOT NULL REFERENCES ms_blobs(id) ON DELETE RESTRICT,
        PRIMARY KEY(collection_id,blob_id))""")
    path = Path(directory) / "document.txt"
    path.write_bytes(b"example document\n")
    prepared = store.prepare_file(path, schema=SCHEMA)
    with store.catalog.transaction() as tx:
        store.finalize(prepared, schema=SCHEMA,
                       metadata={"title": "Document", "category": "manual"}, id="document", tx=tx)
        tx.sql("INSERT INTO collections VALUES(?)", ("reference",))
        tx.sql("INSERT INTO collection_members VALUES(?,?)", ("reference", "document"))
        store.publish("document", tx=tx)
    selected = store.catalog.sql("""SELECT b.id FROM ms_blobs b
        JOIN collection_members m ON m.blob_id=b.id
        WHERE m.collection_id=? AND b.state='ready' ORDER BY b.id""", ("reference",), write=False)
    record = store.update_metadata("document", schema=SCHEMA, changes={"title": "Annotated"},
                                   expected_version=1)
    with store.materialize("document") as verified:
        content = verified.read_bytes()
    return {"selected": [row["id"] for row in selected], "retrieved": content,
            "version": record["version"]}


def exercise_formats(store):
    """Optional structured values; call after exercise, with numpy/arrow extras."""
    import numpy as np
    import pyarrow as pa

    schema = BlobSchema("structured_dataset", {"title": Text(required=True)},
                        handlers=("numpy.npz", "pyarrow.parquet"))
    store.install_schema(schema)
    values = {"array": (np.arange(12).reshape(3, 4), "numpy.npz"),
              "table": (pa.table({"sample": [1, 2, 3]}), "pyarrow.parquet")}
    for id, (value, handler) in values.items():
        token = store.prepare(value, schema=schema, handler=handler)
        with store.catalog.transaction() as tx:
            store.finalize(token, schema=schema, metadata={"title": id}, id=id, tx=tx)
            tx.sql("INSERT INTO collection_members VALUES(?,?)", ("reference", id))
            store.publish(id, tx=tx)
    np.testing.assert_array_equal(store.get("array"), values["array"][0])
    assert store.get("table").equals(values["table"][0])
    return sorted(values)


def main():
    with TemporaryDirectory(prefix="meldstore-dataset-example-") as directory:
        root = Path(directory)
        with Catalog(root / "catalog.db") as catalog:
            print(exercise(Store(catalog, LocalStorage(root / "objects")), root))


if __name__ == "__main__":
    main()
